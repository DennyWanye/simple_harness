"""AIPhone 2026-11-02 交接单第一件：写文件的步骤在交接后断线，尝试记 LOST（用量未知）；规划器
此前有一次回复被拒，系统自己的原地重做让位；规划器按过时的说明宣布运行阻塞，等的条件是
"证据更新 / 计划改版"。用户在界面上点"带话重试"，系统只记了一笔接管，没有唤醒规划器，也没有
重试授权：执行者与规划器都被门禁拦住，任务一直显示进行中，恢复报告却说"已恢复"。

这里按产品入口走完整条链：人工接管是唤醒来源；接管回执说清是排队还是被拦；排队的重做以
"人工接管"来源进决定管道，经同一套准入开新尝试并解除阻塞；快照 waiting_on 与恢复报告如实
报出阻塞。反例：真实外部操作未决时接管回执为"被拦"，不开新尝试。
"""

import asyncio
import json
from typing import Any

import pytest
from h1i_seed import committed, events, resume, run_until
from h1i_seed import planner as seed_planner

from agent_orchestrator.orchestrator.planning_runtime_block import (
    pending_block,
    waiting_on_rows,
    wake_blocks,
)
from agent_orchestrator.orchestrator.planning_selection import native_decision
from agent_orchestrator.orchestrator.stop_conditions import HUMAN_RETRY_ORIGIN, SYSTEM_RETRY_ORIGIN
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _leaf(loop: Any, mission: Any) -> str:
    from h1i_seed import root_task

    [task] = [
        task for task in loop.store.list_tasks(mission.id) if task.id != root_task(mission.id)
    ]
    return task.id


def _aiphone_planner(state: dict[str, Any]):
    """规划器：第一次回复格式坏（让系统原地重做让位），之后对运行环境请求一律宣布阻塞，
    等的条件照 AIPhone 那局：evidence_updated 与 plan_revision_changed。"""

    def planner(request: Any) -> Any:
        state["asked"] += 1
        if state["asked"] == 1:
            return "bad JSON"
        package = package_of(request)
        runtime = [
            entry
            for entry in package.get("repair_requests") or ()
            if entry["request"]["trigger_source"] == "RUNTIME_UNAVAILABLE"
        ]
        if not runtime:
            return seed_planner(request)
        subject = next(s for s in package["planning_subjects"] if s["task_id"] == state["task_id"])
        body = {
            "schema_version": 1,
            "decision_type": "REPAIR",
            "subject_key": subject["subject_key"],
            "rationale": "按规则用量未知、外部效果未决的尝试不能原样重试。",
            "reason_refs": [],
            "assumptions": [],
            "uncertainties": [],
            "alternatives": [],
            "replan_triggers": [],
            "payload": {
                "repair_kind": "DECLARE_RUNTIME_BLOCKED",
                "repair_request_id": runtime[0]["request_id"],
                "blockers": [{"code": "OTHER", "detail": "用量与产出都未确定。"}],
                "resumable_if": ["evidence_updated", "plan_revision_changed"],
            },
        }
        return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"

    return planner


def test_a_human_retry_wakes_the_runtime_block_and_redoes_the_lost_step(tmp_path):
    state: dict[str, Any] = {"asked": 0}

    async def case():
        provider = LayeredScriptedProvider(planner=_aiphone_planner(state))
        async with committed(
            tmp_path, key="human-retry-wakes", provider=provider, max_planning_attempts=6
        ) as seed:
            loop, mission, product = seed.loop, seed.mission, seed.product
            task_id = state["task_id"] = _leaf(loop, mission)
            [first] = loop.store.list_attempts(task_id)
            first_intent = loop.store.get_intent_for_subject(first.id)
            await loop._give_up_blocked_assured_attempt(
                first_intent, detail={"waited_seconds": 600}
            )
            assert loop.store.get_attempt(first.id).failure["reason"] == "provider_outcome_unknown"
            resume(product, hold=("worker",))
            await run_until(
                product, lambda: pending_block(loop.store, mission.id) is not None, timeout=30
            )
            block = pending_block(loop.store, mission.id)
            assert block.payload["resumable_if"] == ["evidence_updated", "plan_revision_changed"]
            # AIPhone 看到的卡死形态：时钟推进、重启都不是变化源
            assert not wake_blocks(loop)
            asked_before = provider.asked.count("planner")

            # 公开快照如实报出阻塞与合法恢复入口
            snapshot = product.control.snapshot(mission.id)["snapshot"]
            [held] = [row for row in snapshot["waiting_on"] if row["kind"] == "runtime_block"]
            assert held["decision_id"] == block.payload["decision_id"]
            assert held["woken"] is False
            assert held["task_ids"] == [task_id]
            assert held["recovery"]["takeover"] == "retry_with_note"
            assert waiting_on_rows(loop.store, mission.id) == [held]

            # 人工接管：回执说清这次重做已排队；同号重复点击原样返回，不再记一笔
            record = product.control.takeover(
                task_id,
                "retry_with_note",
                basis="线路断了，不是步骤做错",
                note="写 b.md=beta 并校验",
                command_id="opt226-retry-1",
            )
            assert record["disposition"] == "retry_queued", record
            assert record["blocker"] is None
            again = product.control.takeover(
                task_id,
                "retry_with_note",
                basis="线路断了，不是步骤做错",
                note="写 b.md=beta 并校验",
                command_id="opt226-retry-1",
            )
            assert again == record
            assert len(loop.store.list_overrides(mission.id)) == 1
            budget_before = loop.store.get_task(task_id).budget.max_attempts

            # 接管是唤醒来源：主循环叫醒阻塞，以人工来源原样重做，经同一套准入开出新尝试
            await run_until(
                product, lambda: len(loop.store.list_attempts(task_id)) >= 2, timeout=30
            )
            first_again, second = loop.store.list_attempts(task_id)
            assert second.retry_of == first.id
            assert str(first_again.status) == "LOST"  # 旧账不动，用量按上限保留
            assert pending_block(loop.store, mission.id) is None
            assert loop.store.get_task(task_id).budget.max_attempts == budget_before
            prepared = events(loop, mission.id, "NativePlanningDecisionPrepared")
            assert [e.payload["origin"] for e in prepared] == [HUMAN_RETRY_ORIGIN]
            # 账本记着这次重做是为哪笔接管提交的：同一笔接管不会再提交第二次
            assert prepared[0].payload["override_id"] == record["override_id"]
            evaluated = [
                e.payload
                for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                if e.payload.get("decision_origin") == HUMAN_RETRY_ORIGIN
            ]
            assert evaluated and evaluated[-1]["status"] == "COMMITTED", evaluated
            # 规划器没有被再问：重做是人的决定，不是模型的判断
            assert provider.asked.count("planner") == asked_before
            assert not [
                row
                for row in product.control.snapshot(mission.id)["snapshot"]["waiting_on"]
                if row["kind"] == "runtime_block"
            ]

    asyncio.run(case())


def test_a_human_retry_is_recorded_as_blocked_when_a_step_action_is_unsettled(tmp_path):
    """反例：这一步提议的外部动作还没结案（真实外部效果未决）。接管回执为 blocked，
    说明阻塞原因与下一步；不开新尝试、不唤醒成一次重做。"""

    state: dict[str, Any] = {"asked": 0}

    async def case():
        provider = LayeredScriptedProvider(planner=_aiphone_planner(state))
        async with committed(
            tmp_path, key="human-retry-blocked", provider=provider, max_planning_attempts=6
        ) as seed:
            loop, mission, product = seed.loop, seed.mission, seed.product
            task_id = state["task_id"] = _leaf(loop, mission)
            [first] = loop.store.list_attempts(task_id)
            first_intent = loop.store.get_intent_for_subject(first.id)
            await loop._give_up_blocked_assured_attempt(
                first_intent, detail={"waited_seconds": 600}
            )
            resume(product, hold=("worker",))
            await run_until(
                product, lambda: pending_block(loop.store, mission.id) is not None, timeout=30
            )
            import agent_orchestrator.orchestrator.human_commits as human_commits

            original = human_commits.pending_step_action
            human_commits.pending_step_action = lambda store, tid: (
                {"action_key": "connector:move:1", "state": "HANDED_OFF"}
                if tid == task_id
                else original(store, tid)
            )
            try:
                record = product.control.takeover(
                    task_id,
                    "retry_with_note",
                    basis="再做一次",
                    command_id="opt226-blocked-1",
                )
            finally:
                human_commits.pending_step_action = original
            assert record["disposition"] == "blocked", record
            assert record["blocker"] == "step_action_unsettled"
            assert record["next_step"] == "resolve_unknown"
            assert len(loop.store.list_attempts(task_id)) == 1

    asyncio.run(case())


def _package_with_takeover(*, takeover_at: float, planning_failures: list[dict[str, Any]]):
    import copy

    from test_runtime_lost_native_retry import PACKAGE

    package = copy.deepcopy(PACKAGE)
    package["views"]["failures"] = [
        *planning_failures,
        {
            "source": "attempt",
            "reason": "LOST",
            "attempt_review_ref": {
                "kind": "attempt",
                "id": "task-742189979604bf7b96ea9335250e4a2c:attempt-2",
                "version": 3,
                "content_hash": "c" * 64,
            },
            "repeat_count": 1,
            "last_seen": "100.0",
            "findings": [],
        },
    ]
    package["human_takeovers"] = [
        {
            "override_id": "override:takeover:task-742189979604bf7b96ea9335250e4a2c:1",
            "action": "takeover_retry_with_note",
            "task_id": "task-742189979604bf7b96ea9335250e4a2c",
            "basis": "线路断了",
            "note": "再做一次",
            "at": takeover_at,
        }
    ]
    return package


def test_the_human_retry_outranks_an_earlier_refused_planner_reply_but_submits_only_once():
    refused = {
        "source": "planning",
        "reason": "unparseable",
        "attempt_review_ref": None,
        "repeat_count": 1,
        "last_seen": "150.0",
        "findings": [None],
    }
    override = "override:takeover:task-742189979604bf7b96ea9335250e4a2c:1"
    # 规划器之前被拒过：系统自己的原地重做让位，但人的"带话重试"不让位
    package = _package_with_takeover(takeover_at=200.0, planning_failures=[refused])
    chosen = native_decision(package)
    assert chosen is not None and chosen.origin == HUMAN_RETRY_ORIGIN
    assert chosen.override_id == override
    assert chosen.document["payload"]["repair_kind"] == "RETRY_SAME_METHOD"
    assert chosen.document["payload"]["failed_attempt_id"] == (
        "task-742189979604bf7b96ea9335250e4a2c:attempt-2"
    )
    # 同一笔接管账本里已经提交过（批了或拒了都算）：不再提交，交给规划器；不看包里会被裁掉的失败索引
    assert native_decision(package, submitted_takeovers={override}) is None
    # 接管早于这次失败的尝试：那是对上一次的话，不作数
    assert (
        native_decision(_package_with_takeover(takeover_at=50.0, planning_failures=[refused]))
        is None
    )
    # 真实外部操作未决：人说重做也要先核对
    package = _package_with_takeover(takeover_at=200.0, planning_failures=[refused])
    package["repair_requests"][0]["impact"]["unresolved_operations"] = ["op-1"]
    assert native_decision(package) is None
    # 没有接管、没有规划失败：仍是系统自己的原地重做
    package = _package_with_takeover(takeover_at=200.0, planning_failures=[])
    package.pop("human_takeovers")
    chosen = native_decision(package)
    assert chosen is not None and chosen.origin == SYSTEM_RETRY_ORIGIN
    assert chosen.override_id is None
