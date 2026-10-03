# SPDX-License-Identifier: Apache-2.0
"""审查没有结论 → 记成"判不下来"并问人（HTN 补齐阶段 C 第 3 条）。

审阅员的回复回来了但两次都无法采用（格式不对、引用了没给它看的证据……）：第 2 次回复按
"没有可采用的回复"导入成一份正式的判不下来记录，之后与"审阅员自己说判不下来"走同一个
出口——问人裁决。调用没回来（基础设施问题）不在这里，见 test_review_call_unanswered.py。

**改坏检验**：消费者里把第 2 次格式错误重新送回"格式用完" → 第一条变红（没有正式记录、没有裁决题）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.api.planning_answers import answer_planning_question
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, review_input, review_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _unusable(purpose: str, reply: Any, calls: dict[str, int]) -> Any:
    def reviewer(request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        if str((data.get("package") or {}).get("purpose")) == purpose:
            calls[purpose] = calls.get(purpose, 0) + 1
            return reply(data)
        return review_reply(data)

    return reviewer


def _questions(world: Any, mission_id: str, prefix: str) -> list[dict[str, Any]]:
    return [row for row in PlanningHumanStore(world.store).list(mission_id)
            if str(row["decision_id"]).startswith(prefix)]


def _answer(world: Any, mission: Any, row: dict[str, Any], answer: str) -> None:
    answer_planning_question(
        world.loop, tenant_id=mission.tenant_id, principal=world.deployment.principal,
        decision_id=row["decision_id"], answer=answer, expected_version=row["version"],
        nonce="n-" + row["decision_id"])


def _imported(world: Any, mission_id: str) -> list[dict[str, Any]]:
    return [event.payload for event in world.store.list_events(mission_id)
            if event.type == "AssuranceReviewImported"]


def test_final_review_without_usable_reply_asks_person(tmp_path):
    async def case():
        calls: dict[str, int] = {}
        provider = LayeredScriptedProvider(
            reviewer=_unusable("MISSION_FINAL", lambda data: "我看过了，没有问题。", calls))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "final-no-verdict"})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=8)
            assert mission.status.value == "ACTIVE"
            assert calls["MISSION_FINAL"] == 2  # the reply and its one repair; never a third
            [row] = _questions(world, mission_id, "adjudicate-root:")
            assert row["state"] == "PENDING"
            record = HtnStore(world.store).get_review_record(row["request"]["repair_context"]["record_id"]).record
            assert record.verdict.value == "INCONCLUSIVE"
            assert all(str(item.verdict) == "UNKNOWN" and item.limitations[0].startswith("REVIEW_NO_USABLE_REPLY:")
                       for item in record.criteria)
            assert not [e for e in world.store.list_events(mission_id) if e.type == "AssuranceReviewFormatExhausted"]
            _answer(world, mission, row, "pass")
            mission = await world.run_until_settled(mission_id, rounds=8)
            assert mission.status.value == "COMPLETED"

    asyncio.run(case())


def _extra_field(data: Any) -> str:
    body = json.loads(review_reply(data))
    body["confidence"] = "high"  # a field with a value the reply shape does not have
    return json.dumps(body, ensure_ascii=False)


async def _publish_until_outcome_question(tmp_path, key: str):  # type: ignore[no-untyped-def]
    from agent_orchestrator.governance.policies import DeploymentPolicy
    from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
    from test_operation import PUBLISH, TARGET, _confirm_completion

    published = tmp_path / "published"
    published.mkdir()
    connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
    policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
    calls: dict[str, int] = {}
    provider = LayeredScriptedProvider(reviewer=_unusable("OPERATION_OUTCOME", _extra_field, calls))
    context = product_world(tmp_path / "root", provider, connectors={"file_publish": connector},
                            deployment_policy=policy)
    world = await context.__aenter__()
    mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                               "success_criteria": ["file:" + TARGET, PUBLISH],
                               "idempotency_key": key})["mission_id"]
    await world.drain()
    _confirm_completion(world, mission_id)
    approvals: list[dict[str, Any]] = []
    for _ in range(20):
        await world.drain()
        approvals = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
        if approvals:
            break
    world.control.decide(approvals[0]["request_id"], "approve")
    for _ in range(20):
        await world.drain()
        if _questions(world, mission_id, "adjudicate-outcome:"):
            break
    assert calls["OPERATION_OUTCOME"] == 2
    return context, world, mission_id


def _accepted(world: Any, mission_id: str) -> list[Any]:
    return [e for e in world.store.list_events(mission_id) if e.type == "OperationOutcomeAccepted"]


def test_outcome_review_without_usable_reply_asks_person(tmp_path):
    async def case():
        context, world, mission_id = await _publish_until_outcome_question(tmp_path, "outcome-pass")
        try:
            mission = world.store.get_mission(mission_id)
            [row] = _questions(world, mission_id, "adjudicate-outcome:")
            assert row["state"] == "PENDING" and not _accepted(world, mission_id)
            _answer(world, mission, row, "pass")
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert len(_accepted(world, mission_id)) == 1
            assert mission.status.value == "COMPLETED", mission.final_report
        finally:
            await context.__aexit__(None, None, None)

    asyncio.run(case())


def test_outcome_review_ruled_fail_is_not_accepted_and_is_named_in_the_stop(tmp_path):
    async def case():
        context, world, mission_id = await _publish_until_outcome_question(tmp_path, "outcome-fail")
        try:
            mission = world.store.get_mission(mission_id)
            [row] = _questions(world, mission_id, "adjudicate-outcome:")
            _answer(world, mission, row, "fail")
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert not _accepted(world, mission_id)
            record_id = row["request"]["repair_context"]["record_id"]
            assert {"kind": "outcome", "record_id": record_id, "ruling": "fail"} in \
                world.loop._inconclusive_reviews(mission_id)
            assert mission.status.value != "COMPLETED"
        finally:
            await context.__aexit__(None, None, None)

    asyncio.run(case())


def test_composition_ruling_survives_epoch_change(tmp_path):
    """中间目标的组合审查两次回复都无法采用 → 裁决题；作用域纪元在回答前变了 → 题目仍待回答，
    不收回、不上报"没有结论"（HTN 补齐阶段 D 偏差单 6：题目只绑计划修订号与要求修订号）。

    原用例靠纪元变化让题目过期、再断言"没有结论"修复请求；那条过期上报路径现在只能由计划或
    要求修订号变化触发，留待有真实写方后补。"""
    from test_sub_goal import planner

    async def case():
        calls: dict[str, int] = {}
        provider = LayeredScriptedProvider(planner=planner,
                                           reviewer=_unusable("COMPOSITION", lambda data: "无法给出结论", calls))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": "composition-stale",
                                       "success_criteria": ["file:notes/a.md", "file:NOTES.md"]})["mission_id"]
            for _ in range(20):
                await world.drain()
                if _questions(world, mission_id, "adjudicate-compound:"):
                    break
            [row] = _questions(world, mission_id, "adjudicate-compound:")
            assert calls["COMPOSITION"] == 2 and row["state"] == "PENDING"
            HtnStore(world.store).bump_epoch(mission_id, "mission", bumped_by="test-epoch")
            for _ in range(4):
                await world.drain(timeout=10)
            [row] = _questions(world, mission_id, "adjudicate-compound:")
            assert row["state"] == "PENDING"
            record_id = row["request"]["repair_context"]["record_id"]
            assert not [e for e in world.store.list_events(mission_id)
                        if e.type == "PlanningRepairRequested"
                        and e.payload.get("source_key") == "composition-review:" + record_id]

    asyncio.run(case())


def test_a_root_ruling_question_made_stale_by_an_amendment_goes_to_the_planner(tmp_path):
    """最终审查两位都判不下来、正在问人；用户中途改了要求，这道题就过期了，不再等人；旧的最终审查
    随旧要求作废，规划器收到"要求已更新"（带改了哪几条），不另收针对旧审查的"没有结论"（阶段 E 欠的
    断言，HTN 补齐 F1；与裁决原写的"交'没有结论'修复请求"不同，见 F1 偏差单 1）。

    **改坏检验**：改要求时不让等人的题目过期 → 题目仍"在等" → 变红。"""
    from agent_orchestrator.testing.scripted_replies import planner_reply

    seen: list[dict[str, Any]] = []

    def planner(request: Any) -> Any:
        from agent_orchestrator.testing.fixtures import package_of

        package = package_of(request)
        seen.extend(entry["request"] for entry in package.get("repair_requests") or ())
        return planner_reply(request)

    async def case():
        calls: dict[str, int] = {}
        provider = LayeredScriptedProvider(
            planner=planner, reviewer=_unusable("MISSION_FINAL", lambda data: "我看过了，没有问题。", calls))
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                       "idempotency_key": "final-stale-ruling"})["mission_id"]
            await world.run_until_settled(mission_id, rounds=8)
            [row] = _questions(world, mission_id, "adjudicate-root:")
            assert row["state"] == "PENDING"
            latest = HtnStore(world.store).latest_requirements_revision(mission_id)
            world.control.amend_requirements({
                "mission_id": mission_id, "command_id": "amend-stale-ruling",
                "expected_requirements_ref": {"id": str(latest.revision_id), "revision": int(latest.revision),
                                              "content_hash": latest.content_hash()},
                "changes": [{"op": "add", "statement": "file:MORE.md"}], "reason": "用户补充",
                "source": {"kind": "MAIN_AGENT", "run_id": "r", "call_id": "c", "permission_mode": "auto"}})
            for _ in range(8):
                await world.drain()
            [row] = _questions(world, mission_id, "adjudicate-root:")
            assert row["state"] == "STALE"  # 不再等人
            # 旧的最终审查随旧要求作废，规划器收到的是"要求已更新"（带改了哪几条），不再单独报这道题
            updates = [item for item in seen if item.get("trigger_source") == "REQUIREMENTS_UPDATE"]
            assert updates and updates[0]["context"]["changes"]["added"] == ["c-user-2"]
            # 一条路：旧终审随旧要求作废，不再为旧记录发"没有结论"
            record_id = row["request"]["repair_context"]["record_id"]
            assert not [e for e in world.store.list_events(mission_id) if e.type == "PlanningRepairRequested"
                        and e.payload.get("source_key") == "root-review:" + record_id]

    asyncio.run(case())
