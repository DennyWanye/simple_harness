# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""最终审查打回 → 一条通用修复请求交规划器（HTN 精简 片 0 第 2 步，2026-10-01）。

此前根终审打回走专用路径：只挑"阻断级"意见、先替规划器退掉根目标的做法并取消在跑的步骤、
再开一轮专用规划。保证通道上打回不带"阻断级"标记，专用路径什么都不做，任务原地停到
"没有可派发的工作"。

现在：Harness 只把事实如实记成一条修复请求（审阅员的全部意见、人的裁决、这是第几次、上限
是多少），做法不动、步骤不动，由规划器在重试 / 换做法 / 补步骤 / 问人里选；Harness 只保留
"次数用完就停"。

走 AssuredRuntime：真实存储 / 提交 / Agent 运行时 / 审阅消费者 / 导入器 / 根审查协调器；
只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import MethodType

from agent_orchestrator.contracts.models import TaskStatus
from agent_orchestrator.contracts.resolution import ReviewVerdict
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.assurance_check_policy import lossless_scope_mapping
from agent_orchestrator.orchestrator.assurance_purpose_reviews import purpose_review_key
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.hierarchical_dispatch import (
    HierarchicalDispatch,
    append_hierarchical_event,
)
from agent_orchestrator.orchestrator.planning_repair_requests import (
    pending_requests,
    record_request,
)
from agent_orchestrator.orchestrator.root_review import RootReviewStatus

SDK_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SDK_ROOT / "scripts/assurance_seams"))

ACCEPT_REPLY = {"schema_version": 2, "verdict": "ACCEPT", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "PASS", "evidence_ids": [], "reason": "fixture",
     "limitations": []}], "findings": []}
#: 打回，但没有任何"阻断级"意见——旧专用路径在这种回复上什么都不做。
REWORK_REPLY = {"schema_version": 2, "verdict": "REWORK", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "FAIL", "evidence_ids": [],
     "reason": "the report has no summary section",
     "limitations": ["summary section missing"]}],
    "findings": []}

LOOP_METHODS = (
    "_root_review", "_request_root_review_repair", "_root_review_repairs",
    "_root_review_repairs_are_exhausted", "_root_review_stop_detail",
    "_final_review_unreadable_detail", "_exhausted_reviews", "_root_review_request_keys",
)


def _bind(orch, *names):
    for name in names:
        setattr(orch, name, MethodType(getattr(Orchestrator, name), orch))


async def _root_after_a_rework_final_review(rt, *, max_repairs: int = 1):
    """Leaf accepted, MISSION_FINAL policy approved, root cut, reviewed REWORK once."""
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
    rt.orch._config.max_root_review_repairs = max_repairs
    _bind(rt.orch, *LOOP_METHODS)
    rt.orch._review_record_findings = Orchestrator._review_record_findings
    coordinator = rt.orch._root_review(rt.mission, dispatch)
    package = coordinator.cut(mission_id, now_ms=int(rt.store.now * 1000))
    rt.runner.ensure_mission_final(rt.mission, package=package, dispatch=dispatch)
    await rt.drive_review(purpose_review_key("MISSION_FINAL", mission_id, str(package.package_id)))
    state = coordinator.state(mission_id)
    assert state.status is RootReviewStatus.REVIEW_REJECTED, state
    assert state.record is not None and state.record.verdict is ReviewVerdict.REWORK
    return dispatch, state


def _plan_shape(rt, dispatch):
    network = dispatch.network(rt.mission.id)
    return (
        int(network.plan_revision),
        sorted(str(item) for item in network.adopted_instance_ids),
        sorted((task.id, str(task.status)) for task in rt.store.list_tasks(rt.mission.id)),
    )


def test_a_rework_final_review_becomes_one_generic_request_and_touches_nothing_else(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY, REWORK_REPLY], content_only=True) as rt:
            mission_id = rt.mission.id
            dispatch, state = await _root_after_a_rework_final_review(rt)
            before = _plan_shape(rt, dispatch)

            assert rt.orch._request_root_review_repair(rt.mission, dispatch, state) is True

            [pending] = pending_requests(rt.store, mission_id)
            assert pending["source_key"] == "root-review:" + str(state.record.record_id)
            request = pending["request"]
            assert request["trigger_source"] == "VERIFIER_ACCEPTANCE_REJECT"
            context = request["context"]
            assert context["source"] == "root_review"
            assert context["record_id"] == str(state.record.record_id)
            assert context["package_id"] == str(state.package.package_id)
            assert context["verdict"] == "REWORK"
            # every finding the reviewer filed, not only the ones Harness would call blocking
            [finding] = context["findings"]
            assert finding["criterion_id"] == "criterion-report"
            assert "summary section missing" in " ".join(finding["limitations"])
            # the facts about the bound, and nothing that tells the Planner what to do
            assert context["repair_round"] == 1 and context["max_repairs"] == 1
            assert "must" not in str(context).lower() and "hint" not in context

            # Harness did not act on the Planner's behalf: same revision, same adopted
            # method, no step cancelled, and no round opened by a dedicated record
            assert _plan_shape(rt, dispatch) == before
            assert not any(task.status is TaskStatus.CANCELLED for task in rt.store.list_tasks(mission_id))
            assert not [e for e in rt.store.list_events(mission_id) if e.type == "PlanningRejected"]

            # one request per official record
            assert rt.orch._request_root_review_repair(rt.mission, dispatch, state) is False
            assert len(pending_requests(rt.store, mission_id)) == 1
            # the bound is spent, but the request is still the Planner's to answer
            assert rt.orch._root_review_repairs_are_exhausted(rt.mission, dispatch) is False
    asyncio.run(case())


def test_the_bound_is_per_mission_and_spending_it_is_the_named_stop(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY, REWORK_REPLY], content_only=True) as rt:
            mission_id = rt.mission.id
            dispatch, state = await _root_after_a_rework_final_review(rt)
            # an earlier final review of this Mission was already handed to the Planner once
            assert record_request(
                dispatch, mission_id, event_type="VerifierAcceptanceRejected",
                trigger_refs=(mission_id,), source_key="root-review:rec-earlier",
                detail={"source": "root_review", "record_id": "rec-earlier"})
            assert rt.orch._root_review_repairs(mission_id) == 1

            assert rt.orch._request_root_review_repair(rt.mission, dispatch, state) is False

            assert [row["source_key"] for row in pending_requests(rt.store, mission_id)] == [
                "root-review:rec-earlier"]
            # while that earlier request waits for the Planner the Mission is not spent;
            # once it is answered and the review still stands rejected, it is
            assert rt.orch._root_review_repairs_are_exhausted(rt.mission, dispatch) is False
            [earlier] = pending_requests(rt.store, mission_id)
            append_hierarchical_event(
                rt.store, "PlanningRepairAddressed", mission_id, key="decision-earlier",
                payload={"decision_id": "decision-earlier", "decision_type": "REPAIR",
                         "status": "COMMITTED", "subject_key": None,
                         "repair_request_ids": [earlier["request_id"]]})
            assert rt.orch._root_review_repairs_are_exhausted(rt.mission, dispatch) is True
            detail = rt.orch._root_review_stop_detail(rt.mission, dispatch)["root_review"]
            assert detail["status"] == "REVIEW_REJECTED"
            assert detail["repairs_used"] == 1 and detail["max_root_review_repairs"] == 1
            assert detail["findings"] and detail["findings"][0]["criterion_id"] == "criterion-report"
    asyncio.run(case())


def test_a_mission_allowed_no_repair_gets_no_request(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime
        async with AssuredRuntime(tmp_path, [ACCEPT_REPLY, REWORK_REPLY], content_only=True) as rt:
            dispatch, state = await _root_after_a_rework_final_review(rt, max_repairs=0)
            assert rt.orch._request_root_review_repair(rt.mission, dispatch, state) is False
            assert pending_requests(rt.store, rt.mission.id) == []
    asyncio.run(case())
