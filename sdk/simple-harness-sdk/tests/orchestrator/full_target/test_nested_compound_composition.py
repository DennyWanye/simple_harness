# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3l / defect N7: a non-root compound in composition_review must form a resolution.

Grok H-L4-M3-r0: ``code.fix-by-assessed-revert`` refined ``code.assess-regression``
with ``code.assess-by-reading``; both assess leaves were accepted; the assess
compound entered ``composition_review``; nothing formed a GoalResolution for it;
``revert`` stayed ``WAITING_ORDER``; the Mission died ``no_dispatchable_work``.

Only the root ``MISSION_FINAL`` review had a coordinator.  Inner compounds have
none.  These tests pin the missing path: two-level methods, last inner leaf
accepted, successor gated on ORDER — currently stalls, after the fix COMPLETED.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

from htn_world import method, param, step  # noqa: E402
from scripted_plans import apply_scripted_plan  # noqa: E402
from test_htn_end_to_end import (  # noqa: E402
    HIERARCHICAL_SEMANTICS,
    ROOT_DUTY,
    ROOT_TASK,
    World,
    _accept_leaf,
    build_world,
)
from test_nested_compound_refinement import _proposal  # noqa: E402

from agent_orchestrator.contracts import TaskStatus  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.resolution import CriterionVerdict  # noqa: E402
from agent_orchestrator.graph.eligibility import ReadinessReason  # noqa: E402
from agent_orchestrator.orchestrator.composition_review import (  # noqa: E402
    CompositionAcceptanceAssembly,
)
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import (  # noqa: E402
    CompoundPhase,
    OccurrenceOutcome,
)
from agent_orchestrator.orchestrator.resolution_commits import (  # noqa: E402
    GOAL_RESOLUTION_COMMITTED,
)
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import (  # noqa: E402
    RoleScriptedProvider,
    critic_step,
    envelope_step,
)


def _outer():
    """Root → compound assess, then a primitive revert gated on ORDER."""

    return method(
        "plan.assessed",
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step("assess", "plan.subgoal", TaskForm.COMPOUND, {"subject": param("subject")}),
            step(
                "revert",
                "plan.act",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
        ),
        ordering=(("assess", "revert"),),
        links=(("c-root", "assess", "c-sub"),),
        finalizer="revert",
    )


def _inner():
    return method(
        "plan.assess-by-reading",
        "plan.subgoal",
        parameter_schema="plan.subgoal.params",
        steps=(
            step(
                "leaf",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
        ),
        links=(("c-sub", "leaf", "c-leaf-verified"),),
        finalizer="leaf",
    )


def _task_of(world: World, signature: str) -> str:
    for spec in world.network().occurrences:
        binding = world.network().binding_for_occurrence(spec.occurrence_id)
        if str(binding.goal_signature.signature_id) == signature:
            return str(spec.task_id)
    raise AssertionError(f"no occurrence for {signature}")


def _world(tmp_path, *, key: str) -> World:
    """Both refinements committed; the inner leaf accepted; revert still waiting."""

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = build_world(evidence, key=key, mode=HIERARCHICAL_SEMANTICS)
    env = world.env
    env.register_type(
        "plan.subgoal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-sub",),
        domain="plan",
    )
    env.register_type(
        "plan.act",
        parameters=(("subject", "string"),),
        outputs=(("verdict", "plan.verdict"),),
        capabilities=("plan.read",),
        criteria=("c-done",),
        domain="plan",
    )
    outer, inner = _outer(), _inner()
    for contract in (outer, inner):
        receipt = env.admit(contract)
        assert receipt.admitted, receipt.problems
        HtnStore(world.store).register_method(
            contract, env.registry.registration(contract.method_ref())
        )
    first = apply_scripted_plan(world.dispatch,
        world.mission.id,
        _proposal(
            outer,
            goal_id=ROOT_TASK,
            obligation_id=ROOT_DUTY,
            revision=0,
            proposal_id="prop-outer",
        ),
        principal=world.principal,
        command_id="cmd-outer",
    )
    assert first.committed, first.last_reason
    world.dispatch.advance_compound_phases(world.mission.id)
    assess = _task_of(world, "plan.subgoal")
    network = world.network()
    assess_spec = next(spec for spec in network.occurrences if str(spec.task_id) == assess)
    second = apply_scripted_plan(world.dispatch,
        world.mission.id,
        _proposal(
            inner,
            goal_id=assess,
            obligation_id=str(assess_spec.obligation_id),
            revision=int(network.plan_revision),
            proposal_id="prop-inner",
        ),
        principal=world.principal,
        command_id="cmd-inner",
    )
    assert second.committed, second.last_reason
    world.dispatch.advance_compound_phases(world.mission.id)
    world.admit_demand()
    world.dispatch.issue_input_witnesses(world.mission.id, world.network(), now_ms=1_000_000)
    world.dispatch.issue_start_witnesses(world.mission.id, world.network(), now_ms=1_000_000)
    leaf = _task_of(world, "plan.leaf")
    # 带协议绑定的世界里 ``_accept_leaf`` 按生产顺序走完一次真实尝试：结果提交后步骤行
    # 已经是 COMPLETED、带着验收的结果编号，不用再手工推行状态。
    _accept_leaf(world, task_id=leaf, now_ms=1_000_000)
    task = world.store.get_task(leaf)
    assert task is not None
    assert task.status is TaskStatus.COMPLETED and task.accepted_result_id == "result-1"
    world.dispatch.advance_compound_phases(world.mission.id)
    return world


def _accepting_reviewer(request: Any) -> str:
    shown = json.loads(
        next(m.content for m in reversed(request.messages) if str(m.role).endswith("user"))
    )
    return (
        "<critic_verdict>"
        + json.dumps(
            {
                "verdict": "PASS",
                "findings": [],
                "mission_criteria": [
                    {"criterion": item["criterion_id"], "met": True, "reason": "scripted"}
                    for item in shown["criteria"]
                ],
            }
        )
        + "</critic_verdict>"
    )


def _revert_envelope(request: Any) -> str:
    return envelope_step(
        summary="reverted",
        artifacts=["a.md"],
        claims=["the successor ran"],
    )(request)


def _run(world: World, tmp_path, *, cycles: int = 80) -> dict[str, Any]:
    evidence = Path(tmp_path) / "evidence"
    world.store.close()
    provider = RoleScriptedProvider(
        {
            "worker": [
                ("workspace_write_file", {"path": "a.md", "content": "done\n"}),
                _revert_envelope,
                ("workspace_write_file", {"path": "a.md", "content": "done\n"}),
                _revert_envelope,
            ],
            "critic": [
                critic_step(verdict="PASS", criteria_met=True),
                critic_step(verdict="PASS", criteria_met=True),
            ],
            "root_reviewer": [_accepting_reviewer],
        }
    )

    async def case() -> dict[str, Any]:
        config = OrchestratorConfig(
            evidence_root=evidence, max_concurrency=2, test_timeout_seconds=30
        )
        async with Orchestrator(config, provider, poll_interval=0.02) as loop:
            world.env.semantics = HtnStore(loop.store)
            loop.install_hierarchical(planning=world.env)
            await asyncio.wait_for(loop.run(max_cycles=cycles), timeout=60)
            mission = loop.store.get_mission(world.mission.id)
            assert mission is not None
            events = list(loop.store.list_events(world.mission.id))
            return {
                "status": mission.status,
                "stop_reason": mission.stop_reason,
                "report": dict(mission.final_report or {}),
                "types": [item.type for item in events],
                "resolutions": [
                    dict(item.payload)
                    for item in events
                    if item.type == GOAL_RESOLUTION_COMMITTED
                ],
                "roles": dict(provider.by_role),
                "progress": list(loop.progress_log),
                "readiness": (
                    None
                    if loop._hierarchical is None
                    else {
                        str(occ): str(report.reason)
                        for occ, report in loop._hierarchical.read(world.mission.id).reports.items()
                    }
                ),
            }

    return asyncio.run(case())


def test_the_fixture_really_parks_the_successor_on_waiting_order(tmp_path) -> None:
    """The defect shape, asserted rather than assumed: assess is composition_review,
    revert is WAITING_ORDER, nothing is dispatchable."""

    world = _world(tmp_path, key="p23l-n7-shape")
    view = world.dispatch.read(world.mission.id)
    assess = _task_of(world, "plan.subgoal")
    revert = _task_of(world, "plan.act")
    assess_occ = next(
        spec.occurrence_id for spec in view.network.occurrences if str(spec.task_id) == assess
    )
    revert_occ = next(
        spec.occurrence_id for spec in view.network.occurrences if str(spec.task_id) == revert
    )
    from agent_orchestrator.orchestrator.hierarchical_dispatch import next_compound_phase

    phase = next_compound_phase(
        view.network.occurrence(assess_occ),
        view.network,
        view.reports[assess_occ],
        child_outcomes={
            binding.occurrence_id: view.outcomes.get(binding.occurrence_id)
            for binding in view.network.adopted_children(assess_occ)
        },
        resolved=assess_occ in view.resolved,
    )
    assert phase is CompoundPhase.COMPOSITION_REVIEW, phase
    assert view.reports[revert_occ].reason is ReadinessReason.WAITING_ORDER, (
        view.reports[revert_occ]
    )
    assert assess_occ not in view.resolved
    world.store.close()


def _assembly(world: World) -> CompositionAcceptanceAssembly:
    return CompositionAcceptanceAssembly(
        world.store, world.service, dispatch=world.dispatch
    )


def test_composition_does_not_fill_pass_when_the_linked_leaf_criterion_is_missing(
    tmp_path,
) -> None:
    """P1-2 / AER I05 / mutant M5: any PASS on the child is not the linked criterion.

    The inner leaf's official review names ``c-leaf-verified``.  Asking whether it
    covers ``c-reproduced`` must be False — the deleted fallback used to say True.
    A parent criterion with no mapping must stay uncovered, form no GoalResolution,
    and name ``composition_criterion_uncovered``.
    """

    world = _world(tmp_path, key="p23l-n7-m5")
    view = world.dispatch.read(world.mission.id)
    assess_occ = next(
        spec.occurrence_id
        for spec in view.network.occurrences
        if str(view.network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
        == "plan.subgoal"
    )
    leaf_occ = next(
        spec.occurrence_id
        for spec in view.network.occurrences
        if str(view.network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
        == "plan.leaf"
    )
    assembly = _assembly(world)
    gating = list(view.network.adopted_children(assess_occ))
    accepted = assembly._accepted_children(world.mission.id, gating)
    leaf_ids = accepted[str(leaf_occ)]
    assert assembly._child_covers(world.mission.id, leaf_ids, "c-leaf-verified") is True
    assert assembly._child_covers(world.mission.id, leaf_ids, "c-reproduced") is False, (
        "a PASS on a different leaf criterion must not cover the linked name (AER I05)"
    )
    world.store.close()


def test_accepted_children_do_not_mix_sibling_acceptances_on_a_shared_duty(
    tmp_path,
) -> None:
    """P1-2: CURRENT Acceptances are keyed by the child occurrence's own task."""

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = build_world(evidence, key="p23l-n7-siblings", mode=HIERARCHICAL_SEMANTICS)
    env = world.env
    env.register_type(
        "plan.subgoal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-sub",),
        domain="plan",
    )
    env.register_type(
        "plan.act",
        parameters=(("subject", "string"),),
        outputs=(("verdict", "plan.verdict"),),
        capabilities=("plan.read",),
        criteria=("c-done",),
        domain="plan",
    )
    outer = _outer()
    inner = method(
        "plan.assess-by-reading",
        "plan.subgoal",
        parameter_schema="plan.subgoal.params",
        steps=(
            step(
                "facts",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
            step(
                "reproduce",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
        ),
        links=(
            ("c-sub", "facts", "c-leaf-verified"),
            ("c-sub", "reproduce", "c-reproduced"),
        ),
        finalizer="reproduce",
    )
    for contract in (outer, inner):
        receipt = env.admit(contract)
        assert receipt.admitted, receipt.problems
        HtnStore(world.store).register_method(
            contract, env.registry.registration(contract.method_ref())
        )
    first = apply_scripted_plan(world.dispatch,
        world.mission.id,
        _proposal(outer, goal_id=ROOT_TASK, obligation_id=ROOT_DUTY, revision=0, proposal_id="o"),
        principal=world.principal,
        command_id="cmd-o",
    )
    assert first.committed, first.last_reason
    world.dispatch.advance_compound_phases(world.mission.id)
    assess = _task_of(world, "plan.subgoal")
    network = world.network()
    assess_spec = next(spec for spec in network.occurrences if str(spec.task_id) == assess)
    second = apply_scripted_plan(world.dispatch,
        world.mission.id,
        _proposal(
            inner,
            goal_id=assess,
            obligation_id=str(assess_spec.obligation_id),
            revision=int(network.plan_revision),
            proposal_id="i",
        ),
        principal=world.principal,
        command_id="cmd-i",
    )
    assert second.committed, second.last_reason
    world.dispatch.advance_compound_phases(world.mission.id)
    world.admit_demand()
    world.dispatch.issue_input_witnesses(world.mission.id, world.network(), now_ms=1_000_000)
    world.dispatch.issue_start_witnesses(world.mission.id, world.network(), now_ms=1_000_000)
    view = world.dispatch.read(world.mission.id)
    children = list(view.network.adopted_children(assess_spec.occurrence_id))
    assert len(children) == 2, children
    facts_occ = next(b.occurrence_id for b in children if b.slot_key == "facts")
    reproduce_occ = next(b.occurrence_id for b in children if b.slot_key == "reproduce")
    facts_task = str(view.network.occurrence(facts_occ).task_id)
    _accept_leaf(world, task_id=facts_task, now_ms=1_000_000)
    assembly = _assembly(world)
    accepted = assembly._accepted_children(world.mission.id, children)
    assert str(facts_occ) in accepted
    assert str(reproduce_occ) not in accepted, (
        "a sibling's CURRENT Acceptance on the shared duty must not stand in for "
        f"reproduce: {accepted}"
    )
    world.store.close()


def _bare_world(tmp_path, *, key: str) -> tuple[World, Any]:
    """Nested compound whose goal signature has no coverage_criteria.

    Root coverage hangs on the revert leaf, so admission does not need the
    inner compound to carry a parent criterion.  The inner method has no
    criterion_links.  ``_criteria`` therefore synthesises ``c-composition``.

    带协议绑定的世界里这样的计划提交不了（见下面的测试），所以这里只把世界和两个做法
    准备好，返回世界和外层做法，由测试自己去提交。
    """

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = build_world(evidence, key=key, mode=HIERARCHICAL_SEMANTICS)
    env = world.env
    env.register_type(
        "plan.bare",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        domain="plan",
    )
    env.register_type(
        "plan.act",
        parameters=(("subject", "string"),),
        outputs=(("verdict", "plan.verdict"),),
        capabilities=("plan.read",),
        criteria=("c-done",),
        domain="plan",
    )
    outer = method(
        "plan.assessed-bare",
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step("assess", "plan.bare", TaskForm.COMPOUND, {"subject": param("subject")}),
            step(
                "revert",
                "plan.act",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
        ),
        ordering=(("assess", "revert"),),
        links=(("c-root", "revert", "c-done"),),
        finalizer="revert",
    )
    inner = method(
        "plan.assess-by-reading-bare",
        "plan.bare",
        parameter_schema="plan.bare.params",
        steps=(
            step(
                "leaf",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
        ),
        # Contract forbids empty criterion_links; this link is not a coverage
        # criterion of ``plan.bare``, so composition still synthesises
        # ``c-composition``.
        links=(("c-orphan", "leaf", "c-leaf-verified"),),
        finalizer="leaf",
    )
    for contract in (outer, inner):
        receipt = env.admit(contract)
        assert receipt.admitted, receipt.problems
        HtnStore(world.store).register_method(
            contract, env.registry.registration(contract.method_ref())
        )
    return world, outer


def _inner_occurrence(world: World, signature: str):
    view = world.dispatch.read(world.mission.id)
    return next(
        spec.occurrence_id
        for spec in view.network.occurrences
        if str(view.network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
        == signature
    )


def test_c_composition_without_coverage_does_not_form_accept(tmp_path) -> None:
    """AER I05/I07: child acceptances are not a PASS for unmapped ``c-composition``.

    Before the fix ``_outcomes`` wrote PASS whenever ``accepted`` was non-empty,
    so ``resolve_ready`` formed an ACCEPT GoalResolution.  After: UNKNOWN with
    ``composition_criterion_uncovered``, no resolution.
    """

    # 带协议绑定的世界里这道保护提前到了计划提交：一个既不声明判据、也没被上级做法链接
    # 的复合子目标，证明不了自己对完成有任何贡献，计划在冻结完成范围时就被整笔拒绝——
    # 根本走不到组合审查，更不会形成 ACCEPT。旧世界里它能提交，靠组合审查写 UNKNOWN 兜住。
    from agent_orchestrator.planning.htn.completion_scopes import (
        CompletionScopeCompilationError,
    )

    world, outer = _bare_world(tmp_path, key="p23m-i07-uncovered")
    with pytest.raises(CompletionScopeCompilationError) as refused:
        apply_scripted_plan(world.dispatch,
            world.mission.id,
            _proposal(outer, goal_id=ROOT_TASK, obligation_id=ROOT_DUTY, revision=0,
                      proposal_id="o"),
            principal=world.principal,
            command_id="cmd-o",
        )
    assert "OP_COMPLETION_SCOPE_UNRESOLVED" in str(refused.value)
    assert "has no provable completion contribution" in str(refused.value)
    refused_occurrence = str(refused.value).split("'")[1]
    assert refused_occurrence.startswith("occ-")
    semantics = HtnStore(world.store)
    assert semantics.active_plan_revision(world.mission.id) is None, "the whole plan is refused"
    assert semantics.list_goal_resolutions(world.mission.id) == ()
    assert [
        item for item in semantics.list_review_packages(world.mission.id)
        if str(item.package_id).startswith("pkg-compose-")
    ] == []
    world.store.close()


def test_linked_assess_by_reading_still_forms_a_resolution(tmp_path) -> None:
    """Control: M3 ``assess-by-reading`` has criterion_links; the main path stays."""

    world = _world(tmp_path, key="p23m-i07-linked")
    occ = _inner_occurrence(world, "plan.subgoal")
    formed = _assembly(world).resolve_ready(world.mission.id)
    assert formed, "a linked inner compound must still resolve"
    resolutions = HtnStore(world.store).list_goal_resolutions(world.mission.id)
    assert resolutions, resolutions
    assert all(item.verdict is CriterionVerdict.PASS for item in resolutions[0].criteria)
    view = world.dispatch.read(world.mission.id)
    assert view.outcomes.get(occ) is OccurrenceOutcome.ACCEPTED
    world.store.close()
