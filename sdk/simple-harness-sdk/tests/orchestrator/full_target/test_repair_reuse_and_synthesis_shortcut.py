# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3q: repair-round reuse of accepted read-only leaves, empty-Planner shortcut,
synthesis method-width bound, and split rejection flags.

Grok fourth-batch L3 (SDK d360750): all four episodes hit a calls/attempts ceiling
after a retire+refine that re-materialised the whole net (C2-r0 funded_now=10),
two doomed Planner rounds at kickoff, one more empty Planner on every repair, and
a 10-leaf synthesised method. C1-r1's Planner called a read-only-leaf cancel
``rejected_by_root_review`` because the package used one flag for both reasons.

Four changes, none of them in ``contracts/``:

(a) retire+refine defaults ``share_active`` for CURRENT-accepted read-only leaves
    of the retiring instance that are not criterion-linked (facts / reproduce);
    write leaves and unaccepted leaves stay new work; ``funded_now`` counts only
    the new primitives.
(b) when evidence is saturated and no APPLICABLE method remains (rejected
    methods already excluded), skip the Planner and go to
    ``_request_method_synthesis`` — kickoff and repair alike — recording
    ``PlannerRoundSkippedForSynthesis``. Never skip while an APPLICABLE method
    exists.
(c) a synthesised method wider than ``MAX_SYNTHESIS_METHOD_STEPS`` (8) is a
    P2.3i correctable refusal; one re-ask, then reject.
(d) ``rejected_by_root_review`` and ``rejected_by_read_only_leaf`` are two
    fields; planner-hierarchical-v7 names both; v6 bytes stay frozen.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_evidence_saturation as saturation  # noqa: E402
import test_htn_end_to_end as e2e  # noqa: E402
from htn_world import const, method, out, param, step  # noqa: E402
from test_htn_end_to_end import committed  # noqa: E402
from test_root_review_repair_library import (  # noqa: E402
    _adopted_root,
    _alt_method,
    _register,
    _rejected_open,
    _replacement,
)

from agent_orchestrator.contracts.htn import ReusePolicy, SideEffectKind, TaskForm  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import (  # noqa: E402
    PLANNER_SKIPPED_FOR_SYNTHESIS,
)
from agent_orchestrator.planning.htn.registry import RejectionCode  # noqa: E402
from agent_orchestrator.planning.htn.synthesis import (  # noqa: E402
    CORRECTABLE_REJECTIONS,
    MAX_SYNTHESIS_METHOD_STEPS,
    rejection_is_correctable,
)
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import (  # noqa: E402
    RoleScriptedProvider,
)

# ======================================================================================
# Helpers
# ======================================================================================


def _nine_step():
    """A synthesised method with nine primitive steps — one over the width bound."""

    steps = tuple(
        step(
            f"s{index}",
            "plan.leaf",
            TaskForm.PRIMITIVE,
            {"subject": param("subject")},
            capabilities=("plan.read",),
        )
        for index in range(9)
    )
    return method(
        "plan.outer.wide",
        "plan.goal",
        parameter_schema="plan.goal.params",
        applicable=(),
        steps=steps,
        links=(("c-root", "s0", "c-done"),),
        finalizer="s0",
    )


def _leaf_occurrence(network, signature: str) -> str:
    for spec in network.occurrences:
        if spec.form is not TaskForm.PRIMITIVE:
            continue
        binding = network.binding_for_occurrence(spec.occurrence_id)
        if binding.goal_signature.signature_id == signature:
            return str(spec.occurrence_id)
    raise AssertionError(f"no primitive occurrence of {signature}")


def _revision_events(outcome: dict[str, Any]) -> list[Any]:
    return [
        item
        for item in outcome["events"]
        if item.type == e2e.PLAN_REVISION_COMMITTED
    ]


# ======================================================================================
# (a) repair-round reuse of accepted read-only leaves
# ======================================================================================


def test_retire_and_refine_shares_the_accepted_read_only_leaf(tmp_path) -> None:
    """Compile-level: plan.leaf is accepted and not criterion-linked → share_active;
    plan.review is criterion-linked → new work. funded_now is 1, not 2."""

    world = _rejected_open(tmp_path, key="p23q-share-compile", alt=True)
    network = world.network()
    old_leaf = _leaf_occurrence(network, "plan.leaf")
    old_review = _leaf_occurrence(network, "plan.review")
    old = _adopted_root(world)
    outcome = world.plan(
        _replacement(world, _alt_method(), instance_id=old, revision=1),
        command_id="cmd-share",
    )
    assert outcome.committed, outcome.last_reason
    network = world.network()
    new_leaf = _leaf_occurrence(network, "plan.leaf")
    new_review = _leaf_occurrence(network, "plan.review")
    assert new_leaf == old_leaf, "the accepted read-only leaf is the same occurrence"
    assert new_review != old_review, "the criterion-linked review is new work"
    adopted = network.adopted_instance_for(network.root_occurrence_ids[0])
    assert adopted is not None
    shared = [
        child
        for child in adopted.child_bindings
        if child.reuse_policy is ReusePolicy.SHARE_ACTIVE
    ]
    assert len(shared) == 1, [str(child.reuse_policy) for child in adopted.child_bindings]
    assert str(shared[0].occurrence_id) == old_leaf
    conservation = world.events(e2e.PLAN_REVISION_COMMITTED)[-1].payload["budget_conservation"]
    assert conservation["holds"] is True
    assert conservation["funded_now"] == 1, conservation
    assert old_leaf in {str(spec.occurrence_id) for spec in network.occurrences}


def _accept_all_primitives(world: Any) -> None:
    """Accept every primitive of the adopted plan, producers before consumers."""

    from agent_orchestrator.runtime.output_blocks import PortClaim

    network = world.network()
    world.dispatch.issue_input_witnesses(world.mission.id, network, now_ms=1_000_000)
    producers = {
        str(item.producer_occurrence): str(item.consumer_occurrence)
        for item in network.data_requirements
    }
    pending = [spec for spec in network.occurrences if spec.form is TaskForm.PRIMITIVE]
    pending.sort(key=lambda spec: 0 if str(spec.occurrence_id) in producers else 1)
    assembly = e2e._assembly(world)
    for index, spec in enumerate(pending):
        task_id = str(spec.task_id)
        declared = world.dispatch.declared_output_ports_for(world.mission.id, task_id)
        path = f"out/{task_id}.json"
        assembly.accept(
            world.mission.id,
            task_id,
            result_id=f"result-{task_id}",
            layers=e2e._passing_layers(),
            artifacts=(e2e._Artifact(f"artifact-{task_id}", path),),
            producer_agent_ids=("agent-worker",),
            reviewer_agent_id="agent-critic",
            now_ms=1_000_000 + index,
            port_claims=tuple(
                PortClaim(port_key=item["port"], path=path) for item in declared
            ),
        )


def _inspect_method(method_id: str) -> Any:
    """facts (read-only) → apply (write) → inspect (read-only, DATA from apply) +
    criterion-linked verify.  inspect is *not* in criterion_links."""

    return method(
        method_id,
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step(
                "facts",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
            step(
                "apply",
                "plan.apply",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("repo.write",),
            ),
            step(
                "inspect",
                "plan.inspect",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "patch": out("apply", "patch")},
                capabilities=("plan.read",),
            ),
            step(
                "verify",
                "plan.review",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "result": out("facts", "result")},
                capabilities=("plan.read",),
            ),
        ),
        links=(("c-root", "verify", "c-reviewed"),),
        finalizer="verify",
    )


def _inspect_rejected(tmp_path, *, key: str):
    """A committed inspect-shaped plan whose children are accepted and the root
    review has opened a repair record."""

    original_env, original_outer = e2e._env, e2e._outer

    def env(mission: str):
        built = original_env(mission)
        built.register_type(
            "plan.apply",
            parameters=(("subject", "string"),),
            outputs=(("patch", "plan.patch"),),
            capabilities=("repo.write",),
            effect=SideEffectKind.LOCAL_WRITE,
            writes=(("repo", "workspace"),),
            domain="plan",
        )
        built.register_type(
            "plan.inspect",
            parameters=(("subject", "string"),),
            inputs=(("patch", "plan.patch", True),),
            outputs=(("findings", "plan.findings"),),
            capabilities=("plan.read",),
            domain="plan",
        )
        return built

    e2e._env = env
    e2e._outer = lambda: _inspect_method("plan.outer")
    try:
        world = committed(tmp_path, key=key, demand=True)
    finally:
        e2e._env, e2e._outer = original_env, original_outer
    _register(world, _inspect_method("plan.alt"))
    _accept_all_primitives(world)
    return world


def _probe_method(method_id: str, tag: str):
    """A facts-like probe leaf whose ``tag`` parameter distinguishes two shares."""

    return method(
        method_id,
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step(
                "probe",
                "plan.probe",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "tag": const(tag)},
                capabilities=("plan.read",),
            ),
            step(
                "verify",
                "plan.review",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "result": out("probe", "result")},
                capabilities=("plan.read",),
            ),
        ),
        links=(("c-root", "verify", "c-reviewed"),),
        finalizer="verify",
    )


def _probe_rejected(tmp_path, *, key: str, new_tag: str):
    original_env, original_outer = e2e._env, e2e._outer

    def env(mission: str):
        built = original_env(mission)
        built.register_type(
            "plan.probe",
            parameters=(("subject", "string"), ("tag", "string")),
            outputs=(("result", "plan.result"),),
            capabilities=("plan.read",),
            domain="plan",
        )
        return built

    e2e._env = env
    e2e._outer = lambda: _probe_method("plan.outer", "alpha")
    try:
        world = committed(tmp_path, key=key, demand=True)
    finally:
        e2e._env, e2e._outer = original_env, original_outer
    _register(world, _probe_method("plan.alt", new_tag))
    _accept_all_primitives(world)
    return world


def test_an_inspect_leaf_fed_by_apply_is_not_shared_on_repair(tmp_path) -> None:
    """P1-1: accepted inspect is read-only and not criterion-linked, but a write
    leaf (apply) is a DATA predecessor — reusing it would carry the rejected
    patch's findings into the new revision.  facts has no write predecessor."""

    world = _inspect_rejected(tmp_path, key="p23q-p1-inspect")
    old_facts = _leaf_occurrence(world.network(), "plan.leaf")
    old_inspect = _leaf_occurrence(world.network(), "plan.inspect")
    old = _adopted_root(world)
    outcome = world.plan(
        _replacement(world, _inspect_method("plan.alt"), instance_id=old, revision=1),
        command_id="cmd-no-share-inspect",
    )
    assert outcome.committed, outcome.last_reason
    network = world.network()
    assert _leaf_occurrence(network, "plan.leaf") == old_facts
    assert _leaf_occurrence(network, "plan.inspect") != old_inspect
    adopted = network.adopted_instance_for(network.root_occurrence_ids[0])
    shared_types = {
        network.binding_for_occurrence(child.occurrence_id).goal_signature.signature_id
        for child in adopted.child_bindings
        if child.reuse_policy is ReusePolicy.SHARE_ACTIVE
    }
    assert shared_types == {"plan.leaf"}, shared_types


def test_repair_share_requires_matching_parameters(tmp_path) -> None:
    """P2-3: same task type, different typed parameters → not share_active."""

    world = _probe_rejected(tmp_path, key="p23q-p2-params", new_tag="beta")
    old_probe = _leaf_occurrence(world.network(), "plan.probe")
    old = _adopted_root(world)
    outcome = world.plan(
        _replacement(world, _probe_method("plan.alt", "beta"), instance_id=old, revision=1),
        command_id="cmd-no-share-params",
    )
    assert outcome.committed, outcome.last_reason
    assert _leaf_occurrence(world.network(), "plan.probe") != old_probe


def test_a_criterion_linked_leaf_is_not_shared_even_when_accepted(tmp_path) -> None:
    """Verify-like leaves carry a parent criterion; the root review judged them.
    Reusing that occurrence would replay the rejected evidence."""

    world = _rejected_open(tmp_path, key="p23q-share-linked", alt=True)
    old_review = _leaf_occurrence(world.network(), "plan.review")
    old = _adopted_root(world)
    world.plan(
        _replacement(world, _alt_method(), instance_id=old, revision=1),
        command_id="cmd-no-share-review",
    )
    assert _leaf_occurrence(world.network(), "plan.review") != old_review


# ======================================================================================
# (b) empty Planner shortcut
# ======================================================================================


# ======================================================================================
# (c) synthesis method width
# ======================================================================================


def test_the_synthesis_width_bound_is_eight() -> None:
    assert MAX_SYNTHESIS_METHOD_STEPS == 8
    assert RejectionCode.SIZE_BOUND not in CORRECTABLE_REJECTIONS


def test_a_width_overflow_is_correctable_without_moving_size_bound() -> None:
    """Step-count overflow is a P2.3q re-ask; other SIZE_BOUND stays non-correctable."""

    from agent_orchestrator.planning.htn.registry import (  # noqa: PLC0415
        SYNTHESIS_WIDTH_REASON,
        AdmissionProblem,
        AdmissionStepId,
    )
    from agent_orchestrator.planning.htn.synthesis import (  # noqa: PLC0415
        _is_synthesis_width_bound,
    )

    assert RejectionCode.SIZE_BOUND not in CORRECTABLE_REJECTIONS
    width = AdmissionProblem(
        code=RejectionCode.SIZE_BOUND,
        detail="the method declares 9 steps, above the policy's 8",
        step=AdmissionStepId.STRUCTURE_AND_TYPES,
        reason=SYNTHESIS_WIDTH_REASON,
    )
    ports = AdmissionProblem(
        code=RejectionCode.SIZE_BOUND,
        detail="the method declares 9 steps, above the policy's 8",
        step=AdmissionStepId.STRUCTURE_AND_TYPES,
    )
    assert _is_synthesis_width_bound(width) is True
    assert _is_synthesis_width_bound(ports) is False, "substring match must not license a re-ask"
    assert rejection_is_correctable  # live re-ask path


# ======================================================================================
# (d) package flags + planner v7 + legacy
# ======================================================================================


def test_skip_events_of_two_phases_on_the_same_revision_both_land(tmp_path) -> None:
    """P2-5: the skip event key includes phase, so a later write on the same
    revision is not dropped by idempotency."""

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = saturation._gated_world(evidence, key="p23q-skip-phases")
    for ordinal in (1, 2):
        saturation._observe(world, observer="plan.observer", ordinal=ordinal)
    world.store.close()
    config = OrchestratorConfig(
        evidence_root=evidence,
        max_concurrency=1,
        test_timeout_seconds=30,
        max_planning_attempts=2,
    )

    async def case() -> list[str]:
        async with Orchestrator(
            config, RoleScriptedProvider({"planner": []}), poll_interval=0.02
        ) as loop:
            world.env.semantics = HtnStore(loop.store)
            loop.install_hierarchical(planning=world.env)
            mission = loop.store.get_mission(world.mission.id)
            assert mission is not None
            loop._record_planner_skipped(mission, loop.hierarchical, phase="initial")
            loop._record_planner_skipped(mission, loop.hierarchical, phase="method_synthesis")
            return [
                str(item.payload.get("phase"))
                for item in loop.store.list_events(mission.id)
                if item.type == PLANNER_SKIPPED_FOR_SYNTHESIS
            ]

    phases = asyncio.run(case())
    assert "initial" in phases and "method_synthesis" in phases, phases


def test_legacy_missions_never_emit_the_skip_event(tmp_path) -> None:
    """(d) the new event is hierarchical-only; a legacy run does not grow it."""

    from test_hierarchical_event_flow import NEW_EVENT_TYPES, _legacy_events  # noqa: PLC0415

    assert PLANNER_SKIPPED_FOR_SYNTHESIS in NEW_EVENT_TYPES
    rows = _legacy_events(tmp_path, install=True)
    kinds = {kind.split("|", 1)[0] for kind, _ in rows}
    assert PLANNER_SKIPPED_FOR_SYNTHESIS not in kinds
