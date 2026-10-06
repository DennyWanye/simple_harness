# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.1: alternatives are OR, a method's slots are AND, and sharing keeps identity.

§8.1: two methods offered for one goal are *alternatives*.  Choosing one does not
dispatch the other and does not block the root, and the slots inside the chosen
method are all necessary without being ordered.

§8.3 / TG §12, TaskGraph 补全第三批: a slot *is* an existing step only when the
Planner named that step on its refining decision (``reuse``).  Nothing is shared by
signature or by looking alike.  The Harness checks the order: the same task type and
scope, an effect that may be shared at all, an ordinary step (not a sub-goal); and one
branch leaving keeps the edges the other branch still needs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "htn"))

from htn_world import (  # noqa: E402
    BUDGET,
    Env,
    acceptance_ref,
    atom,
    ground_draft,
    method,
    out,
    param,
    ref,
    root_network,
    step,
    task_binding,
)

from agent_orchestrator.contracts.evidence_state import TruthValue  # noqa: E402
from agent_orchestrator.contracts.htn import (  # noqa: E402
    ReusePolicy,
    SideEffectKind,
    TaskForm,
)
from agent_orchestrator.graph.projection_validation import (  # noqa: E402
    validate_execution_projection,
    validate_refinement_acyclic,
)
from agent_orchestrator.graph.task_network import TaskNetworkSnapshot  # noqa: E402
from agent_orchestrator.planning.htn.applicability import (  # noqa: E402
    ApplicabilityStatus,
    assess_method,
)
from agent_orchestrator.planning.htn.compiler import (  # noqa: E402
    CompilationRefused,
    compile_refinement_bundle,
)
from agent_orchestrator.planning.htn.grounding import (  # noqa: E402
    GroundingError,
    ReuseRefused,
    SharedGoalEntry,
    SharingSignature,
    ground_method,
    named_share_refusal,
)


def or_env() -> Env:
    env = Env()
    env.register_predicate("demo.primary-ready", (("subject", "string"),))
    env.register_predicate("demo.fallback-ready", (("subject", "string"),))
    env.register_type(
        "demo.goal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-done",),
        domain="demo",
    )
    env.register_type(
        "demo.goal2",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-done2",),
        domain="demo",
    )
    env.register_type(
        "demo.shared-read",
        parameters=(("subject", "string"),),
        outputs=(("facts", "demo.facts"),),
        capabilities=("demo.read",),
        effect=SideEffectKind.EXTERNAL_READ,
        domain="demo",
    )
    env.register_type(
        "demo.private-read",
        parameters=(("subject", "string"),),
        outputs=(("facts", "demo.facts"),),
        capabilities=("demo.read",),
        domain="demo",
    )
    env.register_type(
        "demo.notify",
        parameters=(("subject", "string"),),
        outputs=(("receipt", "demo.receipt"),),
        capabilities=("demo.write",),
        effect=SideEffectKind.EXTERNAL_EVENT_WRITE,
        reversible=False,
        domain="demo",
    )
    for name, out_port in (("demo.a", "ra"), ("demo.b", "rb")):
        env.register_type(
            name,
            parameters=(("subject", "string"),),
            inputs=(("facts", "demo.facts", True),),
            outputs=((out_port, f"demo.{out_port}"),),
            capabilities=("demo.read",),
            domain="demo",
        )
    return env


def alternative(
    method_id: str,
    predicate: str,
    tail: str,
    *,
    goal: str = "demo.goal",
    criterion: str = "c-done",
    shared: str = "demo.shared-read",
):
    return method(
        method_id,
        goal,
        parameter_schema=f"{goal}.params",
        applicable=(atom(predicate, {"subject": param("subject")}),),
        steps=(
            step(
                "shared",
                shared,
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("demo.read",),
            ),
            step(
                tail,
                f"demo.{tail}",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "facts": out("shared", "facts")},
                capabilities=("demo.read",),
            ),
        ),
        links=((criterion, tail, f"c-{tail}"),),
        finalizer=tail,
    )


def or_pair(env: Env):
    primary = alternative("demo.primary", "demo.primary-ready", "a")
    fallback = alternative("demo.fallback", "demo.fallback-ready", "b")
    env.admit(primary)
    env.admit(fallback)
    return primary, fallback


# ============================================================================ OR
#
# Choosing between alternatives is the planner's judgement (an LLM names the method
# in its RefineOperation).  What the Harness owns, and what these tests pin, is that
# a refuted alternative cannot be grounded, and that the chosen one compiles into a
# network where the loser contributes nothing and blocks nothing.


def test_the_refuted_alternative_is_reported_as_refuted_and_cannot_be_grounded() -> None:
    env = or_env()
    primary, _ = or_pair(env)
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.FALSE)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    binding = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    report = assess_method(
        binding, primary, env.snapshot(), env.capabilities(), registry=env.predicates
    )
    assert report.status is ApplicabilityStatus.PRECONDITION_FALSE
    with pytest.raises(GroundingError, match="grounded only where it applies"):
        ground_draft(env, binding, primary, root_network(env, binding))


def test_the_refuted_alternative_contributes_no_occurrence() -> None:
    env = or_env()
    primary, fallback = or_pair(env)
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.FALSE)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    binding = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    network = root_network(env, binding)
    draft = ground_draft(env, binding, fallback, network)
    bundle = compile_refinement_bundle(
        draft,
        network,
        method=fallback,
        catalog=env.catalog,
        schemas=env.schemas,
        registry=env.registry,
    )
    assert all(
        draft.method_ref.method_id != primary.method_id for draft in bundle.delta.method_instances
    )
    assert len(bundle.delta.occurrences) == 2


def test_the_root_is_not_blocked_by_the_alternative_that_lost() -> None:
    env = or_env()
    _, fallback = or_pair(env)
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.FALSE)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    binding = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    network = root_network(env, binding)
    draft = ground_draft(env, binding, fallback, network)
    bundle = compile_refinement_bundle(
        draft,
        network,
        method=fallback,
        catalog=env.catalog,
        schemas=env.schemas,
        registry=env.registry,
    )
    report = validate_execution_projection(bundle.network.execution_projection(), BUDGET)
    assert report.ok, [problem.detail for problem in report.problems]


def test_only_one_alternative_is_adopted_per_occurrence() -> None:
    env = or_env()
    _, fallback = or_pair(env)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    binding = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    network = root_network(env, binding)
    draft = ground_draft(env, binding, fallback, network)
    bundle = compile_refinement_bundle(
        draft,
        network,
        method=fallback,
        catalog=env.catalog,
        schemas=env.schemas,
        registry=env.registry,
    )
    assert len(bundle.network.adopted_instance_ids) == 1


# =========================================================================== AND


def test_the_slots_of_one_method_are_all_required() -> None:
    env = or_env()
    _, fallback = or_pair(env)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    binding = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    network = root_network(env, binding)
    draft = ground_draft(env, binding, fallback, network)
    alternative_view = network  # keep the pre-compilation network for contrast
    bundle = compile_refinement_bundle(
        draft,
        alternative_view,
        method=fallback,
        catalog=env.catalog,
        schemas=env.schemas,
        registry=env.registry,
    )
    view = bundle.network.refinement_view()
    only = view.alternatives_for(binding.task_id)[0]
    assert len(only.required_children) == 2
    assert only.optional_children == ()


def test_and_children_with_no_declared_order_run_in_parallel() -> None:
    env = or_env()
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.TRUE)
    contract = method(
        "demo.two-independent",
        "demo.goal",
        parameter_schema="demo.goal.params",
        applicable=(atom("demo.primary-ready", {"subject": param("subject")}),),
        steps=(
            step(
                "left",
                "demo.private-read",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("demo.read",),
            ),
            step(
                "right",
                "demo.shared-read",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("demo.read",),
            ),
        ),
        links=(("c-done", "left", "c-left"),),
        finalizer="left",
    )
    env.admit(contract)
    binding = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    network = root_network(env, binding)
    report = assess_method(
        binding, contract, env.snapshot(), env.capabilities(), registry=env.predicates
    )
    draft = ground_method(binding, contract, {}, report, catalog=env.catalog, schemas=env.schemas)
    bundle = compile_refinement_bundle(
        draft,
        network,
        method=contract,
        catalog=env.catalog,
        schemas=env.schemas,
        registry=env.registry,
    )
    assert bundle.delta.order_constraints == ()
    assert bundle.delta.data_requirements == ()


# ======================================================================= sharing


def signature(env: Env, task_type: str = "demo.shared-read", *, scope: str | None = None) -> SharingSignature:
    spec = env.catalog.require(ref(task_type))
    return SharingSignature.of(spec, authority_scope=scope or env.mission, semantic_scope=scope or env.mission)


def two_root_network(env: Env, *roots) -> TaskNetworkSnapshot:
    """A network with several independent root goals, so neither is an orphan."""

    from agent_orchestrator.contracts.htn import OccurrenceSpec

    return TaskNetworkSnapshot(
        mission_id=env.mission,
        plan_revision=0,
        occurrences=tuple(
            OccurrenceSpec(
                occurrence_id=str(binding.task_id),
                task_id=binding.task_id,
                obligation_id=binding.obligation_id,
                form=binding.form,
            )
            for binding in roots
        ),
        task_bindings=tuple(roots),
        root_occurrence_ids=tuple(str(binding.task_id) for binding in roots),
        required_obligations=tuple(binding.obligation_id for binding in roots),
    )


def _refine(env: Env, root, contract, network, *, reuse=None, retire=()):
    report = assess_method(root, contract, env.snapshot(), env.capabilities(), registry=env.predicates)
    draft = ground_method(root, contract, {}, report, catalog=env.catalog, schemas=env.schemas,
                          reuse=reuse, plan_revision=network.plan_revision)
    bundle = compile_refinement_bundle(draft, network, method=contract, catalog=env.catalog,
                                       schemas=env.schemas, registry=env.registry, reuse=reuse,
                                       retire_instance_ids=tuple(retire))
    return draft, bundle


def _entry(network: TaskNetworkSnapshot, occurrence_id, env: Env, *, acceptance=None) -> SharedGoalEntry:
    binding = network.binding_for_occurrence(occurrence_id)
    return SharedGoalEntry(occurrence_id=occurrence_id, task_id=binding.task_id,
                           obligation_id=binding.obligation_id, signature=signature(env),
                           acceptance_ref=acceptance)


def two_consumers(env: Env):
    """The first branch reads once; the second branch names that reading step."""

    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.TRUE)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    first = alternative("demo.first", "demo.primary-ready", "a")
    second = alternative(
        "demo.second", "demo.fallback-ready", "b", goal="demo.goal2", criterion="c-done2"
    )
    env.admit(first)
    env.admit(second)
    root_one = task_binding(env, "demo.goal", task_id="task-one", obligation="obl-one",
                            parameters={"subject": "alpha"})
    root_two = task_binding(env, "demo.goal2", task_id="task-two", obligation="obl-two",
                            parameters={"subject": "alpha"})
    draft_one, bundle_one = _refine(env, root_one, first, two_root_network(env, root_one, root_two))
    slot = next(item for item in draft_one.child_bindings if item.slot_key == "shared")
    _, bundle_two = _refine(env, root_two, second, bundle_one.network,
                            reuse={"shared": _entry(bundle_one.network, slot.occurrence_id, env)})
    return bundle_two.network, str(draft_one.instance_id), str(slot.occurrence_id), (root_one, root_two)


def test_a_named_shared_step_exists_once_with_two_consumers() -> None:
    env = or_env()
    network, _, shared, _ = two_consumers(env)
    assert sum(1 for spec in network.occurrences if str(spec.occurrence_id) == shared) == 1
    assert len(network.refinement_view().parents_of[shared]) == 2
    second = next(draft for draft in network.method_instances if draft.method_ref.method_id == "demo.second")
    slot = next(item for item in second.child_bindings if item.slot_key == "shared")
    assert str(slot.occurrence_id) == shared and slot.reuse_policy is ReusePolicy.SHARE_ACTIVE
    owners = [binding for binding in network.task_bindings if binding.occurrence_binding is not None
              and str(binding.occurrence_binding.occurrence_id) == shared]
    assert len(owners) == 1  # no second Task binding for a borrowed step
    assert validate_execution_projection(network.execution_projection(), BUDGET).ok
    assert validate_refinement_acyclic(network).ok


def test_without_a_name_the_same_step_is_done_twice() -> None:
    """Nothing is shared by signature: an identical step the Planner did not name is new work."""

    env = or_env()
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.TRUE)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    first = alternative("demo.first", "demo.primary-ready", "a")
    second = alternative("demo.second", "demo.fallback-ready", "b", goal="demo.goal2", criterion="c-done2")
    env.admit(first)
    env.admit(second)
    root_one = task_binding(env, "demo.goal", task_id="task-one", obligation="obl-one", parameters={"subject": "alpha"})
    root_two = task_binding(env, "demo.goal2", task_id="task-two", obligation="obl-two", parameters={"subject": "alpha"})
    _, bundle_one = _refine(env, root_one, first, two_root_network(env, root_one, root_two))
    _, bundle_two = _refine(env, root_two, second, bundle_one.network)
    reads = [spec for spec in bundle_two.network.occurrences
             if str(bundle_two.network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
             == "demo.shared-read"]
    assert len(reads) == 2


@pytest.mark.parametrize("leaving", ["first", "second"])
def test_one_branch_changing_its_method_keeps_the_shared_step_and_the_other_branch_edges(leaving: str) -> None:
    """TaskGraph 补全第三批（改坏：编译器合并改回"碰到就删"→ 变红）：一个分支换做法，只撤它
    自己对共用步骤的需求；共用步骤留着，它到留下那个分支下游的数据边也留着。两个方向都测：
    首建方换做法、共用方换做法。"""

    env = or_env()
    network, first_instance, shared, (root_one, root_two) = two_consumers(env)
    second_instance = next(str(draft.instance_id) for draft in network.method_instances
                           if draft.method_ref.method_id == "demo.second")
    root, retire, staying_tail, goal, criterion = (
        (root_one, first_instance, "b", "demo.goal", "c-done") if leaving == "first"
        else (root_two, second_instance, "a", "demo.goal2", "c-done2"))
    replacement = method(
        f"demo.replacement-{leaving}", goal, parameter_schema=f"{goal}.params",
        steps=(step("solo", "demo.private-read", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("demo.read",)),),
        links=((criterion, "solo", "c-solo"),), finalizer="solo")
    env.admit(replacement)
    try:
        _, bundle = _refine(env, root, replacement, network, retire=(retire,))
    except CompilationRefused as refused:  # 留下分支的下游丢了输入边，整份编译被拒
        bundle = refused
    assert not isinstance(bundle, CompilationRefused), bundle
    after = bundle.network
    assert any(str(spec.occurrence_id) == shared for spec in after.occurrences)
    assert len(after.refinement_view().parents_of[shared]) == 1
    staying = next(spec.occurrence_id for spec in after.occurrences
                   if str(after.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
                   == f"demo.{staying_tail}")
    assert any(str(item.producer_occurrence) == shared and item.consumer_occurrence == staying
               for item in after.data_requirements)
    assert validate_execution_projection(after.execution_projection(), BUDGET).ok


def _three_levels(env: Env, *, share_inner_read: bool):
    """root one → outer（一个子目标 part）→ part 采用 inner（shared 读 + a）；root two 的做法
    second 在 share_inner_read 时点名共用 inner 的 shared 那一步。返回（网络, outer 实例, inner 实例,
    part 出现, inner 的 shared 出现, root one）。"""

    env.register_type("demo.part", form=TaskForm.COMPOUND, parameters=(("subject", "string"),), domain="demo")
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.TRUE)
    env.say("demo.fallback-ready", {"subject": "alpha"}, TruthValue.TRUE)
    outer = method("demo.outer", "demo.goal", parameter_schema="demo.goal.params",
                   steps=(step("part", "demo.part", TaskForm.COMPOUND, {"subject": param("subject")}),),
                   links=(("c-done", "part", "c-done"),))
    inner = alternative("demo.inner", "demo.primary-ready", "a", goal="demo.part", criterion="c-done")
    second = alternative("demo.second", "demo.fallback-ready", "b", goal="demo.goal2", criterion="c-done2")
    for contract in (outer, inner, second):
        env.admit(contract)
    root_one = task_binding(env, "demo.goal", task_id="task-one", obligation="obl-one", parameters={"subject": "alpha"})
    root_two = task_binding(env, "demo.goal2", task_id="task-two", obligation="obl-two", parameters={"subject": "alpha"})
    draft_outer, bundle = _refine(env, root_one, outer, two_root_network(env, root_one, root_two))
    [part] = [item.occurrence_id for item in draft_outer.child_bindings]
    sub_goal = bundle.network.binding_for_occurrence(part)
    report = assess_method(sub_goal, inner, env.snapshot(), env.capabilities(), registry=env.predicates)
    draft_inner = ground_method(sub_goal, inner, {}, report, catalog=env.catalog, schemas=env.schemas,
                                plan_revision=bundle.network.plan_revision, goal_occurrence_id=part)
    bundle = compile_refinement_bundle(draft_inner, bundle.network, method=inner, catalog=env.catalog,
                                       schemas=env.schemas, registry=env.registry)
    shared = next(item.occurrence_id for item in draft_inner.child_bindings if item.slot_key == "shared")
    reuse = {"shared": _entry(bundle.network, shared, env)} if share_inner_read else None
    _, bundle = _refine(env, root_two, second, bundle.network, reuse=reuse)
    return bundle.network, draft_outer.instance_id, draft_inner.instance_id, part, shared, root_one


@pytest.mark.parametrize("share_inner_read", [False, True])
def test_replacing_a_method_retires_the_refinement_of_the_sub_goal_it_orphans(share_inner_read: bool) -> None:
    """夜间 N6（TaskGraph 代码级计划 §5.3 ``retired_targets``）：上级换做法时，被换掉的做法实例细化出
    的子目标没有别人持有，就离开计划；它下面采用的做法实例随之退役（写进增量的 retired_instance_ids，
    提交据此记 RETIRED、收回执行权）。此前它留在网络里，合并报 ``refines unknown task``。
    子目标下面被另一个分支点名共用的步骤照"共用保留"留下，连同它到那个分支的数据边。

    **改坏检验**：``_retirement_closure`` 只返回点名的那一个 → 合并被拒 → 变红。"""

    env = or_env()
    network, outer, inner, part, shared, root_one = _three_levels(env, share_inner_read=share_inner_read)
    replacement = method(
        "demo.replacement-outer", "demo.goal", parameter_schema="demo.goal.params",
        steps=(step("solo", "demo.private-read", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("demo.read",)),),
        links=(("c-done", "solo", "c-solo"),), finalizer="solo")
    env.admit(replacement)
    _, bundle = _refine(env, root_one, replacement, network, retire=(outer,))
    assert set(bundle.delta.retired_instance_ids) == {outer, inner}
    after = bundle.network
    live = {spec.occurrence_id for spec in after.occurrences}
    assert part not in live
    assert {draft.instance_id for draft in after.method_instances}.isdisjoint({outer, inner})
    inner_a = [spec.occurrence_id for spec in network.occurrences
               if str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id) == "demo.a"]
    assert inner_a and not set(inner_a) & live
    assert (shared in live) is share_inner_read
    if share_inner_read:
        staying = next(spec.occurrence_id for spec in after.occurrences
                       if str(after.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id) == "demo.b")
        assert len(after.refinement_view().parents_of[shared]) == 1
        assert any(item.producer_occurrence == shared and item.consumer_occurrence == staying
                   for item in after.data_requirements)
    assert all(item.before in live and item.after in live for item in after.order_constraints)
    assert all(item.producer_occurrence in live and item.consumer_occurrence in live
               for item in after.data_requirements)
    assert validate_execution_projection(after.execution_projection(), BUDGET).ok
    assert validate_refinement_acyclic(after).ok


def test_an_edge_into_a_replaced_method_from_another_branch_leaves_with_it() -> None:
    """TaskGraph 补全第三批（合并规则①：任一端离开网络的边就删；改坏 TG3-08 → 变红）：别的分支
    的步骤连到本分支某一步的边（改接过输入、跨分支连线就是这种形状），本分支换做法后那一步不在了，
    这条边跟着走，不留悬空的边；另一个分支原封不动。"""
    from dataclasses import replace as _replace

    from agent_orchestrator.contracts.htn import OrderConstraint
    from agent_orchestrator.contracts.models import ContractError

    env = or_env()
    network, first_instance, shared, (root_one, _root_two) = two_consumers(env)

    def tail(name: str):
        return next(spec.occurrence_id for spec in network.occurrences
                    if str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
                    == f"demo.{name}")

    leaving, staying = tail("a"), tail("b")
    template = network.order_constraints[0] if network.order_constraints else None
    crossing = (_replace(template, before=staying, after=leaving) if template is not None
                else OrderConstraint(before=staying, after=leaving))
    network = _replace(network, order_constraints=(*network.order_constraints, crossing))
    replacement = method(
        "demo.replacement-crossing", "demo.goal", parameter_schema="demo.goal.params",
        steps=(step("solo", "demo.private-read", TaskForm.PRIMITIVE, {"subject": param("subject")},
                    capabilities=("demo.read",)),),
        links=(("c-done", "solo", "c-solo"),), finalizer="solo")
    env.admit(replacement)
    try:
        _, bundle = _refine(env, root_one, replacement, network, retire=(first_instance,))
    except (CompilationRefused, ContractError) as refused:  # 悬空的边让整份编译被拒
        bundle = refused
    assert not isinstance(bundle, Exception), bundle
    after = bundle.network
    live = {spec.occurrence_id for spec in after.occurrences}
    assert leaving not in live and staying in live
    assert all(item.before in live and item.after in live for item in after.order_constraints)
    assert all(item.producer_occurrence in live and item.consumer_occurrence in live
               for item in after.data_requirements)
    assert any(str(item.producer_occurrence) == shared and item.consumer_occurrence == staying
               for item in after.data_requirements)


# ============================================================== sharing refusals


def test_a_different_scope_is_refused_with_the_field_named() -> None:
    env = or_env()
    refusal = named_share_refusal(signature(env), signature(env, scope="mission-2"))
    assert refusal is not None and "semantic_scope" in refusal


def test_a_different_type_is_refused() -> None:
    env = or_env()
    refusal = named_share_refusal(signature(env), signature(env, "demo.private-read"))
    assert refusal is not None and "goal_type_ref" in refusal


def test_a_side_effecting_step_is_never_shared() -> None:
    env = or_env()
    notify = signature(env, "demo.notify")
    refusal = named_share_refusal(notify, notify)
    assert refusal is not None and "two such actions are two actions" in refusal


def test_an_identical_order_is_admissible() -> None:
    env = or_env()
    assert named_share_refusal(signature(env), signature(env)) is None


def _single_root(env: Env):
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.TRUE)
    contract = alternative("demo.names", "demo.primary-ready", "a")
    env.admit(contract)
    root = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    other = task_binding(env, "demo.shared-read", task_id="task-shared", obligation="obl-shared",
                         parameters={"subject": "alpha"})
    from agent_orchestrator.contracts.htn import OccurrenceSpec

    network = root_network(env, root, extra_occurrences=(OccurrenceSpec(
        occurrence_id="occ-shared", task_id=other.task_id, obligation_id=other.obligation_id,
        form=TaskForm.PRIMITIVE),), extra_bindings=(other,))
    entry = SharedGoalEntry(occurrence_id="occ-shared", task_id=other.task_id,
                            obligation_id=other.obligation_id, signature=signature(env))
    return root, contract, network, entry


def _ground(env: Env, root, contract, reuse):
    report = assess_method(root, contract, env.snapshot(), env.capabilities(), registry=env.predicates)
    return ground_method(root, contract, {}, report, catalog=env.catalog, schemas=env.schemas, reuse=reuse)


def test_naming_a_step_the_method_does_not_have_is_refused() -> None:
    env = or_env()
    root, contract, _, entry = _single_root(env)
    with pytest.raises(ReuseRefused, match="does not have"):
        _ground(env, root, contract, {"nope": entry})


def test_a_sub_goal_cannot_be_named() -> None:
    env = or_env()
    env.register_type("demo.sub", form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                      criteria=("c-sub",), domain="demo")
    env.say("demo.primary-ready", {"subject": "alpha"}, TruthValue.TRUE)
    contract = method(
        "demo.with-sub", "demo.goal", parameter_schema="demo.goal.params",
        applicable=(atom("demo.primary-ready", {"subject": param("subject")}),),
        steps=(step("sub", "demo.sub", TaskForm.COMPOUND, {"subject": param("subject")}),),
        links=(("c-done", "sub", "c-sub"),), finalizer="sub")
    env.admit(contract)
    root = task_binding(env, "demo.goal", parameters={"subject": "alpha"})
    entry = SharedGoalEntry(occurrence_id="occ-x", task_id="task-x", obligation_id="obl-x",
                            signature=signature(env, "demo.sub"))
    with pytest.raises(ReuseRefused, match="sub-goal"):
        _ground(env, root, contract, {"sub": entry})


def test_an_accepted_step_is_reused_under_its_exact_acceptance() -> None:
    """TG decision 9: reuse binds a specific Acceptance, not "some earlier result"."""

    env = or_env()
    root, contract, _, entry = _single_root(env)
    from dataclasses import replace

    draft = _ground(env, root, contract, {"shared": replace(entry, acceptance_ref=acceptance_ref("acceptance-24"))})
    slot = next(item for item in draft.child_bindings if item.slot_key == "shared")
    assert slot.occurrence_id == "occ-shared"
    assert slot.reuse_policy is ReusePolicy.REUSE_ACCEPTED and slot.acceptance_ref == acceptance_ref("acceptance-24")


def test_a_running_step_is_shared_with_no_acceptance() -> None:
    env = or_env()
    root, contract, _, entry = _single_root(env)
    draft = _ground(env, root, contract, {"shared": entry})
    slot = next(item for item in draft.child_bindings if item.slot_key == "shared")
    assert slot.reuse_policy is ReusePolicy.SHARE_ACTIVE and slot.acceptance_ref is None


def test_the_contract_refuses_an_acceptance_on_a_slot_that_shares_live_work() -> None:
    from agent_orchestrator.contracts.htn import ChildBinding
    from agent_orchestrator.contracts.models import ContractError

    with pytest.raises(ContractError, match="only REUSE_ACCEPTED"):
        ChildBinding(
            instance_id="mi-1",
            slot_key="shared",
            occurrence_id="occ-1",
            obligation_id="obl-1",
            reuse_policy=ReusePolicy.SHARE_ACTIVE,
            acceptance_ref=acceptance_ref("acceptance-25"),
        )
