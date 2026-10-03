"""OC2 red oracle: nested local composition criteria must survive scope compilation.

A two-level plan: the root (``plan.goal``, the one approved requirement ``c-root``) is
refined by a method that hangs ``c-root`` on a compound sub-goal (``plan.subgoal``,
local ``c-sub``); the sub-goal is refined by a second method that maps ``c-sub`` to its
leaf's local review criterion ``c-leaf-verified``.  The latter two are composition
identities, not root requirements.  This test preserves that distinction.

2026-10-03（HTN 补齐阶段 A′，分诊表：改 E）：此前借端到端枢纽世界手工提交两版计划、手工验收叶子，
再从库里读完成范围；那条搭建（裸 ``CommitService`` 建任务）在产品上已建不出来。这里与
``test_completion_scope_compiler`` 同样直接测函数：两次细化都由真实的细化编译器编出第 2 版计划，
覆盖记录照两个做法的判据链接写（产品提交时由 ``carried_criteria_in_revision`` 从同一批链接读出），
对它调完成范围编译器，断言局部组合链不被改写成根要求。
"""

from __future__ import annotations

import sys
from pathlib import Path

_FULL_TARGET = Path(__file__).resolve().parents[1]
for _extra in (_FULL_TARGET, _FULL_TARGET / "fixtures" / "htn"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from htn_world import method, param, root_network, step, task_binding  # noqa: E402
from test_plan_commits import ROOT_DUTY, ROOT_TASK, _env  # noqa: E402

from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.operation_completion import (  # noqa: E402
    CompletionMode,
    OperationCompletionRequirementsV1,
    PlanRevisionPinV1,
)
from agent_orchestrator.orchestrator.accepted_outputs import CarriedCriterion  # noqa: E402
from agent_orchestrator.planning.htn.applicability import assess_method  # noqa: E402
from agent_orchestrator.planning.htn.compiler import compile_refinement_bundle  # noqa: E402
from agent_orchestrator.planning.htn.completion_scopes import (  # noqa: E402
    _plan_identity,
    compile_completion_scopes,
)
from agent_orchestrator.planning.htn.grounding import ground_method  # noqa: E402

MISSION = "mission-oc2-nested"


def _outer():
    """Root → compound ``assess`` (carries ``c-root`` as its local ``c-sub``), then a
    primitive ``revert`` gated on ORDER."""

    return method(
        "plan.assessed", "plan.goal", parameter_schema="plan.goal.params",
        steps=(
            step("assess", "plan.subgoal", TaskForm.COMPOUND, {"subject": param("subject")}),
            step("revert", "plan.act", TaskForm.PRIMITIVE, {"subject": param("subject")},
                 capabilities=("plan.read",)),
        ),
        ordering=(("assess", "revert"),),
        links=(("c-root", "assess", "c-sub"),),
        finalizer="revert",
    )


def _inner():
    """The sub-goal's own method: its local ``c-sub`` → the leaf's ``c-leaf-verified``."""

    return method(
        "plan.assess-by-reading", "plan.subgoal", parameter_schema="plan.subgoal.params",
        steps=(step("leaf", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("plan.read",)),),
        links=(("c-sub", "leaf", "c-leaf-verified"),),
        finalizer="leaf",
    )


def _refine(env, network, binding, contract, *, occurrence=None):
    report = assess_method(binding, contract, env.snapshot(), env.capabilities(), registry=env.predicates)
    extra = {} if occurrence is None else {"goal_occurrence_id": occurrence, "plan_revision": network.plan_revision}
    draft = ground_method(binding, contract, {}, report, catalog=env.catalog, schemas=env.schemas, **extra)
    return compile_refinement_bundle(draft, network, method=contract, catalog=env.catalog, schemas=env.schemas,
                                     registry=env.registry, requirements_revision=1).network


def _signature(network, occurrence) -> str:
    return str(network.binding_for_occurrence(occurrence).goal_signature.signature_id)


def _content_only_root_spec() -> OperationCompletionRequirementsV1:
    """The approved requirements: content only, the root criterion ``c-root`` alone."""

    return OperationCompletionRequirementsV1.from_json({
        "schema_version": 1, "mission_id": MISSION,
        "requirements_ref": {"id": f"req-{MISSION}-1", "revision": 1, "content_hash": "a" * 64},
        "mode": CompletionMode.CONTENT_ONLY, "content_criterion_ids": ["c-root"], "effects": [],
    })


def test_oc2_nested_local_chain_is_not_rewritten_as_root_requirement() -> None:
    env = _env(MISSION)
    env.register_type("plan.subgoal", form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                      criteria=("c-sub",), domain="plan")
    env.register_type("plan.act", parameters=(("subject", "string"),), outputs=(("verdict", "plan.verdict"),),
                      capabilities=("plan.read",), criteria=("c-done",), domain="plan")
    outer, inner = _outer(), _inner()
    for contract in (outer, inner):
        assert env.admit(contract).admitted
    root = task_binding(env, "plan.goal", task_id=ROOT_TASK, obligation=ROOT_DUTY, parameters={"subject": "alpha"})
    first = _refine(env, root_network(env, root), root, outer)
    assess = next(item for item in first.occurrences if _signature(first, item.occurrence_id) == "plan.subgoal")
    plan = _refine(env, first, first.binding_for_occurrence(assess.occurrence_id), inner,
                   occurrence=assess.occurrence_id)
    assert plan.plan_revision == 2

    root_occurrence = plan.root_occurrence_ids[0]
    inner_occurrence = next(item for item in plan.occurrences if _signature(plan, item.occurrence_id) == "plan.subgoal")
    leaf_occurrence = next(item for item in plan.occurrences if _signature(plan, item.occurrence_id) == "plan.leaf")
    # The two methods' criterion links, landed on their occurrences.
    carried = (
        CarriedCriterion(parent_task_id=ROOT_TASK, parent_criterion_id="c-root",
                         occurrence_id=inner_occurrence.occurrence_id, task_id=str(inner_occurrence.task_id),
                         leaf_criterion_id="c-sub", evidence_requirement="the assessment carries the root"),
        CarriedCriterion(parent_task_id=str(inner_occurrence.task_id), parent_criterion_id="c-sub",
                         occurrence_id=leaf_occurrence.occurrence_id, task_id=str(leaf_occurrence.task_id),
                         leaf_criterion_id="c-leaf-verified", evidence_requirement="the leaf verified the subject"),
    )
    pin = PlanRevisionPinV1(revision=plan.plan_revision, snapshot_hash=_plan_identity(plan))
    scopes = {scope.occurrence_id: scope
              for scope in compile_completion_scopes(_content_only_root_spec(), plan, carried, plan_ref=pin)}

    assert scopes[str(root_occurrence)].content_criterion_ids == ("c-root",)
    assert scopes[str(inner_occurrence.occurrence_id)].content_criterion_ids == ("c-sub",)
    assert scopes[str(leaf_occurrence.occurrence_id)].content_criterion_ids == ("c-leaf-verified",)
    # The local chain stays local: only the sub-goal owes ``c-sub``, only the leaf owes
    # ``c-leaf-verified``, and neither is promoted onto the root.
    assert [key for key, scope in scopes.items() if "c-sub" in scope.content_criterion_ids] == [
        str(inner_occurrence.occurrence_id)]
    assert [key for key, scope in scopes.items() if "c-leaf-verified" in scope.content_criterion_ids] == [
        str(leaf_occurrence.occurrence_id)]
