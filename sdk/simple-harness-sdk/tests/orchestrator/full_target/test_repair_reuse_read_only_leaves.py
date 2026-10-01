# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Repair-round reuse of accepted read-only leaves.

A retire+refine used to re-materialise the whole net.  It now defaults
``share_active`` for CURRENT-accepted read-only leaves of the retiring instance that
are not criterion-linked (facts / reproduce); write leaves and unaccepted leaves stay
new work, and ``funded_now`` counts only the new primitives.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

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

# ======================================================================================
# Helpers
# ======================================================================================


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
