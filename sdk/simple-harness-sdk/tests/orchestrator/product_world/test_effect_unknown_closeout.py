# SPDX-License-Identifier: Apache-2.0
"""危险效果结果不明时不收尾（Assurance 原计划 §7.2 默认成功策略；补齐清单 V01 / 保证 C-30 / A48）。

原计划：根结论（业务要求已满足）可以先形成，任务仍 ACTIVE；危险 / 必需效果 UNKNOWN → 收尾态
``BLOCKED_UNKNOWN``，不写完成、不发完成通知；结果核对出来（人裁定或对账）之后才收尾。

第 2 批车道 O（2026-10-06，A48 按原计划重排）：根终审只判根范围的内容判据（效果判据由各自的结果审阅判、
效果状态作为事实进终审包）；终审开门与判定不等效果验收，只等效果不在途（已验收或结果不明）；效果由
收尾核对收敛。

用例：
* 收尾四档策略本身（纯函数）：效果不明一档排在"预留未结 / 用量不明"之前，也不被"按上限结清"吞掉。
* 收尾评估里"只差结果不明的效果"不算范围未满足（纯函数）。
* 产品同形世界：发布交出去、链接其实成功但回执丢了 → 动作结果不明 → 终审已开、根结论已成、判定已记，
  任务仍不完成、没有"任务完成"事件、Host 没收到完成通知、收尾 ``BLOCKED_UNKNOWN``、文件没有再发一次。
* 人裁定"已生效"后才完成，且只完成一次、通知一次（第 1 批车道 E 修复缺陷 1 后转绿）。
* 没有人裁定、回执也空的"成功"动作：结果审阅不无限推迟，判定已记也不算合法等待，卡死检测具名停下。
* 收尾行上的状态词必须是 ``BLOCKED_UNKNOWN``、理由 ``EFFECT_UNKNOWN``、点名那项效果（原计划的字面要求）。
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.assurance.codec import decode
from agent_orchestrator.contracts.resolution import ReviewPurpose
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.assurance_consumers import AssuranceCloseoutConsumer
from agent_orchestrator.orchestrator.assurance_final_writer import recorded_judgment
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.storage.operation_completion_store import OperationCompletionStore
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.operation_intent_store import OperationIntentStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TARGET = "reports/weekly.md"
PUBLISH = "action:file_publish.publish:" + TARGET


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ------------------------------------------------------------------ 四档策略（纯函数）
def _decide(**overrides: Any) -> dict[str, Any]:
    consumer = object.__new__(AssuranceCloseoutConsumer)  # 只用 _drain_decision，不碰库
    consumer.store = SimpleNamespace(get_intent=lambda _intent_id: None)  # 开着的调用都不是"等不到的原调用"
    facts: dict[str, Any] = {"reasons": [], "unknown": [], "open_operations": [], "open_intents": [],
                             "open_reservations": [], "usage_fully_known": True}
    facts.update(overrides)
    return consumer._drain_decision("mission-x", **facts)


def test_the_default_success_policy_blocks_on_an_unknown_effect():
    """原计划 §7.2 四档：依据失效→NOT_READY；危险效果不明→BLOCKED_UNKNOWN；写者 / 费用未收敛→DRAINING；
    其余→READY。效果不明时即使没有别的欠账也不就绪，而且不走"按上限结清"（2026-09-26 的上限规则只
    结费用，不结效果）。

    **改坏检验**：去掉 ``_drain_decision`` 里 ``elif unknown`` 这一档 → 效果不明被当成就绪 → 变红。"""
    assert _decide() == {"state": "READY", "reasons": []}
    assert _decide(reasons=["ROOT_RESOLUTION_NOT_ACCEPT"]) == {
        "state": "NOT_READY", "reasons": ["ROOT_RESOLUTION_NOT_ACCEPT"]}
    blocked = _decide(unknown=["publish-weekly"])
    assert (blocked["state"], blocked["reasons"]) == ("BLOCKED_UNKNOWN", ["EFFECT_UNKNOWN"])
    assert "usage_counted_at_upper_bound" not in blocked
    # 效果不明优先于"预留未结 / 用量不明"，不会被按上限结清
    with_money = _decide(unknown=["publish-weekly"], open_reservations=["m:assurance:k:1"],
                         usage_fully_known=False)
    assert (with_money["state"], with_money["reasons"]) == ("BLOCKED_UNKNOWN", ["EFFECT_UNKNOWN"])
    assert "usage_counted_at_upper_bound" not in with_money
    # 依据失效比效果不明更靠前
    assert _decide(reasons=["EVIDENCE_STALE"], unknown=["publish-weekly"])["state"] == "NOT_READY"
    # 效果已知、只剩开着的调用：排水
    assert _decide(open_intents=["intent-1"]) == {"state": "DRAINING", "reasons": ["OPEN_INTENTS"]}


def test_a_root_short_only_of_an_unknown_effect_is_blocked_unknown_not_scope_unmet():
    """第 1 批偏差 2（B 口径）：收尾评估里，根范围没满足但**只差结果不明的效果**（内容已就绪、每项必需效果
    不是已验收就是结果不明）→ 不记 ``ROOT_SCOPE_UNMET``，由 ``_drain_decision`` 报 ``BLOCKED_UNKNOWN``；
    内容没好、效果还在等申请单 / 审阅的，照旧是 ``ROOT_SCOPE_UNMET``。

    **改坏检验**：``unmet_only_by_unknown_effects`` 恒 False（所有未满足的根都落 ROOT_SCOPE_UNMET）→ 变红。"""
    from agent_orchestrator.orchestrator.assurance_consumers import unmet_only_by_unknown_effects as only_unknown

    ready = SimpleNamespace(content_ready=True, effects_ready=False, complete=False)
    assert only_unknown(ready, {"publish-weekly": "RECONCILIATION_REQUIRED"})
    assert only_unknown(ready, {"a": "ACCEPTED", "b": "RECONCILIATION_REQUIRED"})
    # 内容没好：范围未满足，不是效果不明
    assert not only_unknown(SimpleNamespace(content_ready=False), {"publish-weekly": "RECONCILIATION_REQUIRED"})
    # 效果在等审阅 / 申请单 / 执行：不是"结果不明"
    for state in ("AWAITING_OUTCOME_REVIEW", "AWAITING_INTENT", "EXECUTING", "AWAITING_APPROVAL", "SCOPE_STALE"):
        assert not only_unknown(ready, {"publish-weekly": state}), state
        assert not only_unknown(ready, {"a": "RECONCILIATION_REQUIRED", "b": state}), state
    # 没有必需效果、或全部已验收：没有不明可言
    assert not only_unknown(ready, {}) and not only_unknown(ready, {"a": "ACCEPTED"})


# ------------------------------------------------------------------ 产品同形世界
def _confirm_completion(world: Any, mission_id: str) -> dict[str, Any]:
    workspace = world.control.snapshot(mission_id)["snapshot"]["operation_workspace"]
    assert workspace["state"] == "CONFIRMATION_REQUIRED", workspace
    actions = [c["id"] for c in workspace["criteria"] if c["statement"].startswith("action:")]
    content = [c["id"] for c in workspace["criteria"] if c["required"] and c["id"] not in actions]
    [obligation] = workspace["obligations"]
    milestone = next(m for m in workspace["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    ref = workspace["requirements_ref"]
    return world.control.approve_operation_completion_spec({
        "mission_id": mission_id, "command_id": "confirm-publish-completion",
        "expected_requirements_ref": ref,
        "proposal": {
            "schema_version": 1, "mission_id": mission_id,
            "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
            "mode": "REQUIRED_EFFECTS", "content_criterion_ids": content,
            "effects": [{
                "effect_key": "publish-weekly", "source_slot_key": "publish-weekly",
                "obligation_id": obligation["id"], "criterion_ids": actions,
                "required_milestone": milestone["id"],
                "milestone_policy_ref": milestone["milestone_policy_ref"],
                "evidence_policy_ref": milestone["evidence_policy_ref"],
            }],
        },
    })


def _lose_the_first_reply(monkeypatch: Any) -> None:
    """链接其实成功了、回执在路上丢了：连接器第一次发布抛错，文件已经落盘。"""
    import agent_orchestrator.runtime.connectors_publish as connectors_publish

    real_link = os.link
    linked = {"n": 0}

    def link(src, dst, **kwargs):  # type: ignore[no-untyped-def]
        real_link(src, dst, **kwargs)
        if "dst_dir_fd" in kwargs and str(dst).startswith("weekly."):
            linked["n"] += 1
            if linked["n"] == 1:
                raise OSError(5, "Input/output error (the link applied, its reply was lost)")

    monkeypatch.setattr(connectors_publish.os, "link", link)


def _events(world: Any, mission_id: str, kind: str) -> list[Any]:
    return [e for e in world.store.list_events(mission_id) if e.type == kind]


def _closeout(world: Any, mission_id: str) -> tuple[str | None, list[str], list[str]]:
    row = world.store.connection.execute(
        "SELECT state, check_body_json FROM assurance_closeouts WHERE mission_id=?", (mission_id,)).fetchone()
    if row is None:
        return None, [], []
    body = decode(row["check_body_json"])
    return str(row["state"]), list(body.get("reasons") or []), list(body.get("pending_effect_keys") or [])


async def _publish_with_an_unknown_result(world: Any, published: Any, key: str) -> tuple[str, dict[str, Any]]:
    mission_id = world.create({"goal": "写一份周报 reports/weekly.md 并发布",
                               "success_criteria": ["file:" + TARGET, PUBLISH],
                               "idempotency_key": key})["mission_id"]
    await world.drain()
    _confirm_completion(world, mission_id)
    card = None
    for _ in range(30):
        await world.drain()
        cards = [a for a in world.control.approvals(mission_id) if a.get("state") == "PENDING"]
        if cards and world.store.list_actions(mission_id):
            card = cards[0]
            break
    assert card is not None, "no approval card"
    world.control.decide(card["request_id"], "approve")
    for _ in range(10):
        await world.drain(timeout=5)
        if any(a.get("needs_human") for a in world.store.list_actions(mission_id)):
            break
    [action] = world.store.list_actions(mission_id)
    assert action["state"] == "UNKNOWN" and action["needs_human"], action
    assert len(list(published.rglob("*.md"))) == 1  # 文件其实已经发布出去了，只是结果没核实
    return mission_id, action


def _rounds_and_intents(world: Any, mission_id: str) -> tuple[int, int]:
    """这个任务的尝试数（回合）与系统操作意图数。"""
    attempts = world.store.connection.execute(
        "SELECT COUNT(*) FROM attempts WHERE mission_id=?", (mission_id,)).fetchone()[0]
    return int(attempts), len(OperationIntentStore(world.store).for_mission(mission_id))


def _assert_held_open(world: Any, mission_id: str) -> None:
    """结果不明期间该成立的事：任务 ACTIVE、没有"任务完成"事件、收尾没定稿、没有完成通知（Host 一条也没收到）。"""
    mission = world.store.get_mission(mission_id)
    assert str(mission.status.value) == "ACTIVE", (mission.status, mission.final_report)
    assert not _events(world, mission_id, "MissionCompleted")
    state, _reasons, _unknown = _closeout(world, mission_id)
    assert state not in {"READY", "FINALIZED"}, state
    assert not _events(world, mission_id, "AssuranceStatusNotificationRequested")
    assert not [n for n in world.notices if n.get("mission_id") == mission_id]
    # 每一次收尾评估都明说"没就绪"，没有一次把它评成就绪
    states = {e.payload.get("state") for e in _events(world, mission_id, "AssuranceCloseoutEvaluated")}
    assert states and not states & {"READY", "FINALIZED"}, states


def test_an_unknown_publish_result_holds_the_mission_open(tmp_path, monkeypatch):
    """发布的结果不明 → 终审照开、根结论照成、判定照记（业务要求已满足），但任务不完成（ACTIVE）、
    没有"任务完成"事件、收尾停在 ``BLOCKED_UNKNOWN``、Host 没收到完成通知，再空转几轮也一样；
    发布出去的那份文件也没有被再发一次。

    **改坏检验**：收尾不看效果状态（``_evaluate_locked`` 里把 ``unknown`` 当空、``unmet`` 当空）→
    结果不明时也评成就绪 → 变红；``_judge_with_actions`` 把效果判成已满足并由判定直接完成 → 变红；
    ``root_review_ready`` 又等 ``effects_ready`` → 根结论不成 → 变红。"""
    _lose_the_first_reply(monkeypatch)

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id, action = await _publish_with_an_unknown_result(world, published, "unknown-holds")
            before = _rounds_and_intents(world, mission_id)
            assert before[0] >= 1 and before[1] == 1, before  # 结果不明：只有原来那一份申请单，没有重交
            for _ in range(5):  # 结果不明期间再空转几轮：一样不完成
                await world.drain(timeout=5)
            _assert_held_open(world, mission_id)
            # 夜间 N3-22：空转期间不开新回合、不出新意图（效果在途时不判定、不重交）
            assert _rounds_and_intents(world, mission_id) == before
            # 终审开了、根结论成了、判定记了（原计划 §7.2：根结论先于效果收敛）；收尾点名这项效果
            assert [k for k in world.store.connection.execute(
                "SELECT review_key FROM assurance_review_bindings WHERE mission_id=?", (mission_id,))
                if str(k[0]).startswith("assurance-mission-final:")]
            [resolution] = HtnStore(world.store).list_goal_resolutions(mission_id)
            assert str(resolution.verdict) == "ACCEPT" and str(resolution.validity) == "CURRENT"
            # 根结论只复述内容判据；效果判据由结果审阅判、收尾核对，不在根结论里
            assert [c.criterion_id for c in resolution.criteria] == ["c-user-1"]
            judgment = recorded_judgment(world.store.get_mission(mission_id))
            assert judgment is not None and judgment["met"] is True
            [publish] = [j for j in world.store.get_mission(mission_id).final_report["success_criteria"]
                         if j["criterion"] == PUBLISH]
            # 判定记的是判定时的动作状态：效果在途（已批准 / 已交接）时不判定，所以只能是结果不明（夜间 N3-22）
            assert publish["judge"] == "assurance_closeout" and publish["met"] is True
            assert publish["action_state"] == "UNKNOWN"
            assert _closeout(world, mission_id) == ("BLOCKED_UNKNOWN", ["EFFECT_UNKNOWN"], ["publish-weekly"])
            # 终审包只判内容判据，效果状态作为事实在包里
            [package] = HtnStore(world.store).list_review_packages(mission_id, purpose=ReviewPurpose.MISSION_FINAL)
            assert [c.criterion_id for c in package.criteria] == ["c-user-1"]
            [fact] = [dict(row) for row in package.effect_facts]  # 切包时的状态快照（事实，不是判据）
            assert (fact["effect_key"], fact["complete"]) == ("publish-weekly", False) and fact["state"]
            [still] = world.store.list_actions(mission_id)
            assert still["action_key"] == action["action_key"] and still["state"] == "UNKNOWN"
            assert len(list(published.rglob("*.md"))) == 1  # 没有再发一次

    asyncio.run(case())


def test_a_person_ruling_the_unknown_publish_applied_lets_the_mission_complete(tmp_path, monkeypatch):
    """结果核对出来（人去发布目录看过，裁定"已生效"）之后，任务才完成：只完成一次、收尾行定稿、Host 收到
    恰好一条完成通知（通知的事件就是那条"任务完成"）。

    第 1 批车道 E（缺陷 1 修复）：裁定写进动作行一份"人裁定回执"（谁、何时、依据），结果审阅读到的是这份
    回执，观察事实里写明来源是人裁定、没有执行过任何登记检查；审阅员判它够不够，验收走和连接器回执同一条
    使用证书路径。动作不再标"等人"。

    **改坏检验**：``override_action_outcome`` 裁"已生效"时不写回执 → 结果审阅拒绝（来源缺失）→ 任务不完成 → 变红。"""
    _lose_the_first_reply(monkeypatch)

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id, action = await _publish_with_an_unknown_result(world, published, "unknown-ruled")
            _assert_held_open(world, mission_id)
            ruled = world.control.resolve_unknown(action["action_key"], outcome="succeeded", basis="我去发布目录看过，周报在")
            assert ruled["state"] == "SUCCEEDED"
            mission = await world.run_until_settled(mission_id, rounds=20)
            deferred = [e.payload for e in _events(world, mission_id, "OperationOutcomeDeferred")]
            assert str(mission.status.value) == "COMPLETED", (mission.status, deferred[-1:] or mission.final_report)
            [completed] = _events(world, mission_id, "MissionCompleted")
            assert _closeout(world, mission_id) == ("FINALIZED", [], [])
            notices = [n for n in world.notices if n.get("mission_id") == mission_id]
            assert [n.get("event_id") for n in notices] == [completed.id]
            assert len(list(published.rglob("*.md"))) == 1
            # 动作行上是人裁定回执（谁、凭什么），不再标"等人"；裁定本身在册
            [settled] = world.store.list_actions(mission_id)
            [override] = world.store.list_overrides(mission_id)
            after = settled["receipt"]["after"]
            assert settled["state"] == "SUCCEEDED" and not settled["needs_human"]
            assert (after["kind"], after["override_id"], after["basis"]) == (
                "human_ruling", override["override_id"], "我去发布目录看过，周报在")
            # 结果审阅读到的观察事实就是这份裁定：来源写明是人裁定、没有执行过任何登记检查；验收走同一条路
            [intent] = OperationIntentStore(world.store).for_mission(mission_id)
            [binding] = OperationCompletionStore(world.store).list_outcome_bindings_for_intent(
                mission_id, intent["intent_id"])
            observation = world.store.get_receipt(binding["document"].source_receipt_refs[0].id)
            facts = observation["facts"]
            assert observation["receipt"] == settled["receipt"]
            assert (facts["milestone_source"], facts["executed_check_ids"]) == ("HUMAN_RULING", [])
            assert facts["ruling"]["override_id"] == override["override_id"]
            [accepted] = _events(world, mission_id, "OperationOutcomeAccepted")
            assert accepted.payload["outcome_binding_id"] == binding["binding_id"]
            assert not [e for e in _events(world, mission_id, "OperationOutcomeDeferred")
                        if e.payload.get("reason") == "OP_OUTCOME_SOURCE_UNAVAILABLE"]

    asyncio.run(case())


def _no_change_planner(asked: list[dict[str, Any]]):
    """规划器：卡死确认交来的"没有可派发的工作"请求记下来、回"不改"；别的照常。"""
    from agent_orchestrator.testing.fixtures import package_of
    from agent_orchestrator.testing.scripted_replies import decision, planner_reply

    def planner(request: Any) -> Any:
        package = package_of(request)
        mine = [entry for entry in package.get("repair_requests") or ()
                if entry["request"].get("trigger_source") == "NO_DISPATCHABLE_WORK"]
        if not mine:
            return planner_reply(request)
        asked.append(mine[0])
        refs = set(mine[0]["request"]["trigger_refs"])
        subject = next((s["subject_key"] for s in package["planning_subjects"] if s["task_id"] in refs),
                       package["planning_subjects"][0]["subject_key"])
        return decision(subject, "NO_CHANGE", {"reason": "这件事要用户来定。"}, "不改计划。")

    return planner


@pytest.mark.replay_audit_exempt("用例直接改动作行（成功却没有回执）造'回执读不出来'的局面")
def test_a_succeeded_action_without_a_receipt_is_a_named_stop_not_an_endless_deferral(tmp_path, monkeypatch):
    """没有人裁定、回执也空的"成功"动作（写方出错或库被改过）：结果审阅不能每轮 ``OperationOutcomeDeferred``
    "等一等"地无限推迟——读不出回执是具名的来源缺失（``OP_OUTCOME_SOURCE_UNAVAILABLE``），不算合法等待，
    卡死检测接手，具名停下（``no_dispatchable_work``），停机报告里 ``outcome_source_unavailable`` 点名那份申请单；
    全程没有再发布、没有"任务完成"。

    **改坏检验**：``_execution_sources`` 对无回执的成功动作仍报 ``OP_OUTCOME_PENDING``（或
    ``_has_pending_operation_completion`` 不看 ``_outcome_source_refusals``）→ 任务一直 ACTIVE → 变红。"""
    _lose_the_first_reply(monkeypatch)
    asked: list[dict[str, Any]] = []

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        provider = LayeredScriptedProvider(planner=_no_change_planner(asked))
        async with product_world(tmp_path / "root", provider, connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id, action = await _publish_with_an_unknown_result(world, published, "unknown-no-receipt")
            _assert_held_open(world, mission_id)
            with world.store.transaction():  # 一个出错的写方：成功了，回执却没写
                world.store.put_action({**world.store.get_action(action["action_key"]),
                                        "state": "SUCCEEDED", "receipt": None, "needs_human": False})
            mission = await world.run_until_settled(mission_id, rounds=40)
            reasons = {e.payload.get("reason") for e in _events(world, mission_id, "OperationOutcomeDeferred")}
            assert "OP_OUTCOME_SOURCE_UNAVAILABLE" in reasons and "OP_OUTCOME_PENDING" not in reasons, reasons
            assert str(mission.status.value) == "FAILED", (mission.status, reasons, mission.final_report)
            detail = mission.final_report["detail"]
            [row] = detail["outcome_source_unavailable"]
            assert (row["reason"], row["effect_key"]) == ("OP_OUTCOME_SOURCE_UNAVAILABLE", "publish-weekly")
            # 规划器这次问不成：它那边读操作台账，一个"成功却没有匹配回执"的动作读不出来（planning_operations
            # ``success_without_matching_receipt``），按现有规则"读不了时问不成、照旧判停"——停机报告如实写明没问过。
            assert asked == [] and detail["planner_asked"] is None, (asked, detail)
            assert not _events(world, mission_id, "MissionCompleted")
            assert len(list(published.rglob("*.md"))) == 1

    asyncio.run(case())


def test_the_closeout_row_names_the_unknown_effect_as_blocked_unknown(tmp_path, monkeypatch):
    """原计划 §7.2 的字面要求：危险 / 必需效果 UNKNOWN → ``assurance_closeouts.state='BLOCKED_UNKNOWN'``，
    理由 ``EFFECT_UNKNOWN``，收尾行里列出那个效果；``BLOCKED_UNKNOWN`` 不是终态——任务仍 ACTIVE、没有
    "任务完成"。

    **改坏检验**：``_drain_decision`` 去掉 ``elif unknown`` 一档 → 评成 DRAINING / READY → 变红；
    ``_require_root_resolution`` 改回要求根 ``complete``（含效果）→ 判定不成、收尾 ``MISSION_JUDGMENT_MISSING``
    → 变红。"""
    _lose_the_first_reply(monkeypatch)

    async def case():
        published = tmp_path / "published"
        published.mkdir()
        connector = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
        policy = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), connectors={"file_publish": connector},
                                 deployment_policy=policy) as world:
            mission_id, _action = await _publish_with_an_unknown_result(world, published, "unknown-named")
            for _ in range(5):
                await world.drain(timeout=5)
            evaluations = [(e.payload.get("state"), e.payload.get("reasons"))
                           for e in _events(world, mission_id, "AssuranceCloseoutEvaluated")]
            state, reasons, unknown = _closeout(world, mission_id)
            assert (state, reasons, unknown) == ("BLOCKED_UNKNOWN", ["EFFECT_UNKNOWN"], ["publish-weekly"]), evaluations
            assert str(world.store.get_mission(mission_id).status.value) == "ACTIVE"
            assert not _events(world, mission_id, "MissionCompleted")
            assert "BLOCKED_UNKNOWN" in {s for s, _ in evaluations} and not {s for s, _ in evaluations} & {"READY", "FINALIZED"}

    asyncio.run(case())
