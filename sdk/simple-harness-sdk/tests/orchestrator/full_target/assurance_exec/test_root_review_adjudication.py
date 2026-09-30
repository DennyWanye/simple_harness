# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""最终审查两次判不下来 → 问人裁决 → 通过则根目标结论成立（2026-09-30 真机第 5 局）。

步骤级"判不下来"借用结果挂起 + 复核审批；根终审没有可挂起的结果，改走规划问题通道
（阻塞、两个选项）：问题由根审查协调器的 AWAITING_PERSON 状态触发，答案由编排循环消费成
同一种裁决回执，证书 / 验收公式 / 完成度读取都认它。此前根终审判不下来被当"没通过"，
没有阻断性意见就无法修计划，任务原地转圈直到失败。

走 AssuredRuntime：真实存储 / 提交 / Agent 运行时 / 审阅消费者 / 导入器 / 根审查协调器；
只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import MethodType

from agent_orchestrator.contracts.resolution import ReviewVerdict
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.assurance_check_policy import lossless_scope_mapping
from agent_orchestrator.orchestrator.assurance_purpose_reviews import purpose_review_key
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.hierarchical_dispatch import HierarchicalDispatch
from agent_orchestrator.orchestrator.plan_commits import PlanPrincipal
from agent_orchestrator.orchestrator.root_review import RootReviewStatus
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore

SDK_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SDK_ROOT / "scripts/assurance_seams"))

ACCEPT_REPLY = {"schema_version": 2, "verdict": "ACCEPT", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "PASS", "evidence_ids": [], "reason": "fixture",
     "limitations": []}], "findings": []}
INCONCLUSIVE_REPLY = {"schema_version": 2, "verdict": "INCONCLUSIVE", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "UNKNOWN", "evidence_ids": [],
     "reason": "cannot tell from the disclosed evidence", "limitations": ["evidence insufficient"]}],
    "findings": []}


def _bind(orch, *names):
    for name in names:
        setattr(orch, name, MethodType(getattr(Orchestrator, name), orch))


async def _root_after_two_inconclusive_final_reviews(rt):
    """Leaf accepted, MISSION_FINAL policy approved, root cut, reviewed INCONCLUSIVE twice."""
    from _assured_fixture import TENANT

    mission_id = rt.mission.id
    verdict, record = await rt.run_critic()
    assert verdict.passed
    rt.record_critic_layer(record)
    rt.settle_fixture_worker()
    rt.accept_now()
    requirements_ref, scope_ref, mapping = lossless_scope_mapping(
        rt.commit, mission_id=mission_id, scope_id=rt.scope_ref.pin.id, purpose="MISSION_FINAL")
    rt.commit.approve_assurance_check_policy(
        tenant_id=TENANT, mission_id=mission_id, command_id="policy:mission-final",
        principal=Principal("host-authenticated-user"), requirements_ref=requirements_ref,
        completion_scope=scope_ref, candidate_mapping=mapping, purpose="MISSION_FINAL")
    dispatch = HierarchicalDispatch(rt.store, rt.commit)
    assert dispatch.root_review_ready(mission_id)
    _bind(rt.orch, "_root_review", "_ask_person_to_adjudicate_root", "_ask_person_to_adjudicate",
          "_next_planning_ordinal")
    coordinator = rt.orch._root_review(rt.mission, dispatch)
    package = coordinator.cut(mission_id, now_ms=int(rt.store.now * 1000))
    rt.runner.ensure_mission_final(rt.mission, package=package, dispatch=dispatch)
    key = purpose_review_key("MISSION_FINAL", mission_id, str(package.package_id))
    await rt.drive_review(key)  # INCONCLUSIVE → a second opinion is requested, not imported
    await rt.drive_review(key)  # the second reviewer: INCONCLUSIVE again → official record
    ordinals = [row[0] for row in rt.store.connection.execute(
        "SELECT ordinal FROM assurance_review_invocations WHERE mission_id=? AND review_key=? ORDER BY ordinal",
        (mission_id, key))]
    assert ordinals == [1, 2]
    state = coordinator.state(mission_id)
    assert state.status is RootReviewStatus.AWAITING_PERSON, state
    assert state.record is not None and state.record.verdict is ReviewVerdict.INCONCLUSIVE
    return dispatch, coordinator, state


def test_two_inconclusive_final_reviews_ask_the_person_and_a_pass_resolves_the_root(tmp_path):
    async def case():
        from _assured_fixture import TENANT, AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY, INCONCLUSIVE_REPLY, INCONCLUSIVE_REPLY], content_only=True) as rt:
            mission_id = rt.mission.id
            dispatch, coordinator, state = await _root_after_two_inconclusive_final_reviews(rt)
            # the loop asks the person once, through the planning-question channel
            assert rt.orch._ask_person_to_adjudicate_root(rt.mission, dispatch, coordinator, state) is True
            questions = PlanningHumanStore(rt.store)
            [row] = [q for q in questions.list(mission_id) if q["state"] == "PENDING"]
            assert row["request"]["payload"]["blocking"] is True
            assert [o["key"] for o in row["request"]["payload"]["options"]] == ["pass", "fail"]
            assert row["request"]["repair_context"]["kind"] == "review_adjudication"
            assert rt.orch._ask_person_to_adjudicate_root(rt.mission, dispatch, coordinator, state) is False  # waiting
            questions.answer(decision_id=row["decision_id"], tenant_id=TENANT, principal=Principal("user-1"),
                             answer="pass", expected_version=row["version"], nonce="n-1")
            assert rt.orch._ask_person_to_adjudicate_root(rt.mission, dispatch, coordinator, state) is True
            receipt = rt.store.get_receipt("assurance-review-adjudicated:" + str(state.record.record_id))
            assert receipt is not None and receipt["decision"] == "pass" and receipt["target_id"] == state.task_id
            assert coordinator.state(mission_id).status is RootReviewStatus.READY
            outcome = dispatch.attempt_root_resolution(
                mission_id,
                principal=PlanPrincipal(principal_id="runner-fixture", scope_id="mission",
                                        manager_epoch=dispatch.semantics().epoch(mission_id, "mission")),
                command_id=mission_id + ":root-resolution", source={"trigger": "test"})
            assert outcome.committed, (outcome.reason, outcome.detail)
            assert dispatch.terminal(mission_id)
    asyncio.run(case())


def test_a_fail_ruling_on_the_final_review_opens_the_repair_branch_not_the_resolution(tmp_path):
    async def case():
        from _assured_fixture import TENANT, AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY, INCONCLUSIVE_REPLY, INCONCLUSIVE_REPLY], content_only=True) as rt:
            mission_id = rt.mission.id
            dispatch, coordinator, state = await _root_after_two_inconclusive_final_reviews(rt)
            rt.orch._ask_person_to_adjudicate_root(rt.mission, dispatch, coordinator, state)
            questions = PlanningHumanStore(rt.store)
            [row] = [q for q in questions.list(mission_id) if q["state"] == "PENDING"]
            questions.answer(decision_id=row["decision_id"], tenant_id=TENANT, principal=Principal("user-1"),
                             answer="fail", expected_version=row["version"], nonce="n-2")
            rt.orch._ask_person_to_adjudicate_root(rt.mission, dispatch, coordinator, state)
            assert coordinator.state(mission_id).status is RootReviewStatus.REVIEW_REJECTED
            outcome = dispatch.attempt_root_resolution(
                mission_id,
                principal=PlanPrincipal(principal_id="runner-fixture", scope_id="mission",
                                        manager_epoch=dispatch.semantics().epoch(mission_id, "mission")),
                command_id=mission_id + ":root-resolution", source={"trigger": "test"})
            assert not outcome.committed
    asyncio.run(case())
