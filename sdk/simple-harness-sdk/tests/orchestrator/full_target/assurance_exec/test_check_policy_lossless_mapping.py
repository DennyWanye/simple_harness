# SPDX-License-Identifier: Apache-2.0
"""部署按完成范围无损投影并批准检查策略（plan §5.1；2026-10-03 迁到产品同形世界）。

Host 真实模型第 2 局（2026-09-23）：生产上没人批准每个范围的检查策略，所有内容审阅都卡在
CHECK_POLICY_UNRESOLVED。现在部署职责从原始要求无损投影出策略，以系统身份代批。这里在产品主循环
真提交了第一版计划（范围已冻结、部署已代批）的世界里，钉住投影是什么、代批记成谁、以及对外操作的
两类审阅（申请单审阅、结果审阅）投影在承担效果的那一步上。

原"无损投影逐条写明原文且批准成功""最终审查的映射覆盖整份要求"两条：前者由整圈用例覆盖（不批准
内容与终审策略就到不了 COMPLETED），后者并入下面"操作审阅"那条的一句断言（分诊表：删）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_orchestrator.assurance.checks import CriterionPolicy
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.orchestrator.assurance_check_policy import lossless_scope_mapping
from agent_orchestrator.storage.htn_store import HtnStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operation_completion"))

from publish_world import plan_committed, root_scope  # noqa: E402


def _committed(tmp_path, body) -> None:
    async def run() -> None:
        async with plan_committed(tmp_path) as case:
            body(case, case.world.loop.commit, root_scope(case))

    asyncio.run(run())


def test_host_auto_approval_is_recorded_as_system_not_human(tmp_path):
    """2026-09-25 主流程优化条目 4：部署代批无损映射，记为系统，不冒充人。"""

    def body(case, commit, scope_row) -> None:
        rows = case.store.connection.execute(
            "SELECT actor_type, actor_id, payload_json FROM events WHERE mission_id=? "
            "AND type='AssuranceCheckPolicyApproved'", (case.mission_id,)).fetchall()
        assert rows
        for actor_type, actor_id, payload_json in rows:
            payload = json.loads(payload_json)
            assert (actor_type, actor_id) == ("system", "host:assurance-check-policy-projector")
            assert payload["approval_source"] == "HOST_LOSSLESS_AUTO"
            assert payload["on_behalf_of_principal_id"] == case.world.deployment.principal.principal_id
        # 同一条命令重放就是同一份批准（来源不在回执正文里）；不认识的来源按名拒绝。
        scope_id = scope_row["scope_id"]
        requirements_ref, scope_ref, mapping = lossless_scope_mapping(commit, mission_id=case.mission_id,
                                                                      scope_id=scope_id)
        owner = case.world.deployment.principal
        tenant = case.world.deployment.tenant_id
        before = case.store.connection.total_changes
        again = commit.approve_assurance_check_policy(
            tenant_id=tenant, mission_id=case.mission_id, command_id="host-check-policy:" + scope_id,
            principal=owner, requirements_ref=requirements_ref, completion_scope=scope_ref,
            candidate_mapping=mapping, approval_source="HOST_LOSSLESS_AUTO")
        assert again.kind == "check_policy"
        assert case.store.connection.total_changes == before
        with pytest.raises(AssuranceError) as refused:
            commit.approve_assurance_check_policy(
                tenant_id=tenant, mission_id=case.mission_id, command_id="x", principal=owner,
                requirements_ref=requirements_ref, completion_scope=scope_ref, candidate_mapping=mapping,
                approval_source="ROBOT")
        assert refused.value.code == "CHECK_POLICY_APPROVAL_INVALID"

    _committed(tmp_path, body)


def test_unknown_scope_is_unresolved_never_invented(tmp_path):
    def body(case, commit, scope_row) -> None:
        with pytest.raises(AssuranceError) as refused:
            lossless_scope_mapping(commit, mission_id=case.mission_id, scope_id="op-completion-scope-none")
        assert refused.value.code == "CHECK_POLICY_UNRESOLVED"

    _committed(tmp_path, body)


def test_unknown_purpose_is_refused(tmp_path):
    def body(case, commit, scope_row) -> None:
        with pytest.raises(AssuranceError) as refused:
            lossless_scope_mapping(commit, mission_id=case.mission_id, scope_id=scope_row["scope_id"],
                                   purpose="METHOD_PLAN")
        assert refused.value.code == "CHECK_POLICY_APPROVAL_INVALID"

    _committed(tmp_path, body)


def test_operation_reviews_are_projected_on_the_effect_owner_and_approved(tmp_path):
    """NEXT-TG-1.0（2026-09-27）：有保证通道的发布在提交时因两类操作审阅没人批准策略而被拒。现在
    它们投影在承担效果的那一步（根）的范围上，部署已经批准；不归它管的效果、没给效果的结果审阅、
    带效果的申请单审阅都按名拒绝。最终审查的映射覆盖整份要求。"""

    from agent_orchestrator.orchestrator.operation_proposal_review import ACTION_PROPOSAL_CRITERIA

    def body(case, commit, scope_row) -> None:
        scope_id = scope_row["scope_id"]
        assert scope_row["document"].owned_effect_keys == ("publish-weekly",)
        _, derived, mapping = lossless_scope_mapping(commit, mission_id=case.mission_id, scope_id=scope_id,
                                                     purpose="ACTION_PROPOSAL")
        assert derived.pin.id == scope_id
        assert mapping == tuple(CriterionPolicy(c, "SEMANTIC", ()) for c in sorted(ACTION_PROPOSAL_CRITERIA))
        _, _, mapping = lossless_scope_mapping(commit, mission_id=case.mission_id, scope_id=scope_id,
                                               purpose="OPERATION_OUTCOME", effect_key="publish-weekly")
        assert mapping and all(row.mode == "SEMANTIC" for row in mapping)
        # 部署代批的回执就在库里（回执号按产品那一份的命令身份算）。
        from agent_orchestrator.assurance.codec import fingerprint

        for command_id in ("host-check-policy:action-proposal:" + scope_id,
                           f"host-check-policy:operation-outcome:{scope_id}:publish-weekly",
                           "host-check-policy:mission-final:" + scope_id):
            receipt_id = "assurance-check-policy-approval:" + fingerprint({
                "mission": case.mission_id, "tenant": case.world.deployment.tenant_id,
                "principal": case.world.deployment.principal.principal_id, "command": command_id})
            assert case.store.get_receipt(receipt_id) is not None, command_id
        requirements_ref, _, final = lossless_scope_mapping(commit, mission_id=case.mission_id, scope_id=scope_id,
                                                            purpose="MISSION_FINAL")
        requirements = HtnStore(case.store).get_requirements_revision(case.mission_id, requirements_ref.pin.revision)
        # 2026-10-06（Assurance §7.2，车道 O）：根终审只判根范围的内容判据；效果判据（这里的发布）由结果
        # 审阅判，不在根终审的策略里。
        assert {row.criterion_id for row in final} == set(scope_row["document"].content_criterion_ids)
        assert {row.criterion_id for row in final} < {c.criterion_id for c in requirements.criteria}
        for purpose, effect_key, code in (
            ("OPERATION_OUTCOME", "effect-x", "CHECK_POLICY_UNRESOLVED"),  # 不归这一步管
            ("OPERATION_OUTCOME", None, "CHECK_POLICY_APPROVAL_INVALID"),  # 结果审阅要指明效果
            ("ACTION_PROPOSAL", "publish-weekly", "CHECK_POLICY_APPROVAL_INVALID"),
        ):
            with pytest.raises(AssuranceError) as refused:
                lossless_scope_mapping(commit, mission_id=case.mission_id, scope_id=scope_id,
                                       purpose=purpose, effect_key=effect_key)
            assert refused.value.code == code, (purpose, effect_key, refused.value.code)

    _committed(tmp_path, body)


def test_the_proposal_review_criteria_are_judged_not_claimed_as_checks():
    """Ruling 方案 C: the four proposal points are restated facts the code already
    enforces, so the reviewer judges them (SEMANTIC); declaring registered checks made
    the policy unapprovable and overstated the record."""
    from agent_orchestrator.contracts.resolution import EvaluationKind
    from agent_orchestrator.orchestrator.operation_proposal_review import (
        ACTION_PROPOSAL_CRITERIA,
        _criteria,
    )

    criteria = _criteria()
    assert tuple(c.criterion_id for c in criteria) == ACTION_PROPOSAL_CRITERIA
    for criterion in criteria:
        assert criterion.evaluation_kind is EvaluationKind.SEMANTIC
        assert criterion.required_evidence_policy.required_check_ids == ()
