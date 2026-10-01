# SPDX-License-Identifier: Apache-2.0
"""新做法审阅的检查策略：部署按"规划主体任务"无损投影并批准（HTN 精简 片 A 第 1 项）。

评估阻断 1（2026-10-01）：规划器一提做法，新做法审阅就因为查不到已批准的检查策略而开不
起来（CHECK_POLICY_UNRESOLVED），这次提做法被记成"需要授权"拒掉——Host 只为内容、最终
审查、操作申请、操作结果四类审阅批准策略，没有"新做法审阅"这一类；SDK 也没有给它用的
无损映射。这里钉住：规划主体任务的无损映射是什么，以及按它批准后新做法审阅开得起来。
"""
from __future__ import annotations

import asyncio

import pytest

from _method_plan_world import METHOD_ACCEPT, seed_planning_subject

from agent_orchestrator.assurance.checks import CriterionPolicy
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.assurance_check_policy import (
    lossless_planning_subject_mapping,
)

CONTENT_REPLY = {"schema_version": 2, "verdict": "ACCEPT", "assessments": [
    {"criterion_id": "criterion-report", "verdict": "PASS", "evidence_ids": [], "reason": "fixture",
     "limitations": []}], "findings": []}


def _ensure(rt, task_id, pin):
    with rt.store.transaction():  # production calls this inside the decision's own transaction
        return rt.runner.ensure_method_plan(rt.mission, task_id=task_id, method_ref=pin,
                                            producer_agent_ids=("fixture-planner",))


def test_the_planning_subject_mapping_is_lossless_and_its_approval_opens_the_method_review(tmp_path):
    async def case():
        from _assured_fixture import TENANT, AssuredRuntime

        async with AssuredRuntime(tmp_path, [CONTENT_REPLY, METHOD_ACCEPT], content_only=True) as rt:
            task_id, pin, _ = seed_planning_subject(rt)
            with pytest.raises(AssuranceError) as refused:
                _ensure(rt, task_id, pin)
            assert refused.value.code == "CHECK_POLICY_UNRESOLVED"

            requirements_ref, subject_ref, mapping = lossless_planning_subject_mapping(
                rt.commit, mission_id=rt.mission.id, task_id=task_id)
            assert subject_ref.kind == "task" and subject_ref.pin.id == task_id
            assert requirements_ref.kind == "requirements"
            # the criteria the method review will be judged on: the requirements this goal covers
            assert mapping == (CriterionPolicy("criterion-report", "SEMANTIC", ()),)

            rt.commit.approve_assurance_check_policy(
                tenant_id=TENANT, mission_id=rt.mission.id,
                command_id="host-check-policy:method-plan:" + task_id,
                principal=Principal("host-authenticated-user"), requirements_ref=requirements_ref,
                planning_subject=subject_ref, purpose="METHOD_PLAN", candidate_mapping=mapping,
                approval_source="HOST_LOSSLESS_AUTO")
            invocation = _ensure(rt, task_id, pin)
            assert invocation is not None
            rows = rt.store.connection.execute(
                "SELECT review_key FROM assurance_review_invocations WHERE mission_id=? "
                "AND review_key LIKE 'assurance-method-plan:%'", (rt.mission.id,)).fetchall()
            assert len(rows) == 1
    asyncio.run(case())


def test_a_task_that_is_not_a_planning_subject_has_no_mapping(tmp_path):
    async def case():
        from _assured_fixture import AssuredRuntime

        async with AssuredRuntime(tmp_path, [CONTENT_REPLY], content_only=True) as rt:
            with pytest.raises(AssuranceError) as unknown:
                lossless_planning_subject_mapping(rt.commit, mission_id=rt.mission.id, task_id="task-nobody")
            assert unknown.value.code == "CHECK_POLICY_UNRESOLVED"
            # the fixture's own root is a primitive step: nothing is planned *for* it
            with pytest.raises(AssuranceError) as primitive:
                lossless_planning_subject_mapping(
                    rt.commit, mission_id=rt.mission.id, task_id="task-completion-root")
            assert primitive.value.code == "CHECK_POLICY_UNRESOLVED"
    asyncio.run(case())
