# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Grounding a method against a compound task (§18.3 ``ground_method``).

Pure: type-check the bindings, derive stable identities for the slots, decide
which slots may bind an already existing goal occurrence, and return a
:class:`~agent_orchestrator.contracts.htn.MethodInstanceDraft`.  No store, no
clock, no model, no side effects — calling it twice with the same inputs returns
the same draft, byte for byte, which is what makes the occurrence identities
usable as a key.

Identity (TG §3.1, implementation design §5.2 step 3): every slot's occurrence,
task and derived obligation come from ``(instance_id, slot_key)`` and nothing
else.  Re-grounding the same method against the same goal with the same
parameters therefore lands on the same nodes, so a re-plan that keeps a slot keeps
its node rather than minting a twin.

Sharing (§8.3, TG §12; TaskGraph 补全第三批): a slot *is* an existing step only when
the Planner named that step on its refining decision.  Nothing is matched by
signature or by looking alike; the order checks (same task type and scope, an effect
that may be shared at all) are :func:`named_share_refusal`, and the TaskGraph sharing
gate checks the inputs.  A goal whose type writes outside the orchestrator is never
shared: two sends are two sends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ...contracts.evidence_state import PreconditionPhase, PreconditionWitnessRecord, TruthValue
from ...contracts.htn import (
    ResourceRef,
    Binding,
    ChildBinding,
    MethodContract,
    MethodInstanceDraft,
    MethodInstanceId,
    MethodOccurrenceBinding,
    MethodRef,
    MethodStep,
    ObligationId,
    ObligationRelation,
    OccurrenceId,
    OutputValue,
    ParameterValue,
    PlanRevision,
    PreconditionRef,
    Requiredness,
    ReusePolicy,
    SideEffectKind,
    TaskForm,
    TaskRef,
    TaskSemanticBindingV1,
    condition_digest,
)
from ...contracts.models import ContractError
from ...contracts.semantic_base import (
    TypedRef,
    VersionedRef,
    content_hash_of,
    enum_of,
    identifier,
    text,
)
from .applicability import ApplicabilityReport, ApplicabilityStatus
from .registry import (
    READ_ONLY_EFFECTS,
    SchemaCatalog,
    TaskTypeCatalog,
    TaskTypeSpec,
    iter_values,
)


class GroundingError(ContractError):
    """The method cannot be grounded against this task as asked.

    A ``ContractError`` because every cause is a malformed or inapplicable
    *request* — wrong parameter type, unregistered task type, a precondition the
    world says is false — and callers already handle that class uniformly.
    """


class ParameterBindingsError(GroundingError):
    """The candidate bindings fail the registered method parameter schema."""


def derive_id(role: str, *parts: object) -> str:
    """A stable, collision-free derived identity.

    Hashing the JSON of the parts rather than concatenating them means a slot key
    containing the separator cannot collide with another slot (the same reason
    :mod:`...graph.task_network` length-prefixes its node ids).
    """

    digest = content_hash_of([role, *[str(part) for part in parts]])
    return f"{role}-{digest[:32]}"


def instance_identity(
    *,
    goal_id: TaskRef,
    goal_occurrence_id: OccurrenceId,
    method_ref: MethodRef,
    parameters_digest: str,
) -> MethodInstanceId:
    """The identity of one grounding.  Same inputs, same instance."""

    return MethodInstanceId(
        derive_id(
            "mi",
            goal_id,
            goal_occurrence_id,
            method_ref.method_id,
            method_ref.version,
            method_ref.content_hash,
            parameters_digest,
        )
    )


@dataclass(frozen=True, slots=True)
class SlotIdentity:
    """The derived identities of one slot of one method instance."""

    slot_key: str
    occurrence_id: OccurrenceId
    task_id: TaskRef
    obligation_id: ObligationId


def slot_identity(
    instance_id: MethodInstanceId,
    slot_key: str,
    parent_obligation: ObligationId,
    relation: ObligationRelation = ObligationRelation.REFINES_PARENT,
) -> SlotIdentity:
    """Derive a slot's occurrence, task and duty from ``(instance, slot)``.

    §6.1 decides the duty, and it is the reason splitting a task does not mint a
    fresh retry allowance: a ``refines_parent`` step *is* part of the parent's
    responsibility and therefore carries the parent's ``obligation_id``, so its
    failures, its spend and its share of the recursion fuel all land on the one
    account.  Only an ``independent_authorized`` step — work a method may add on
    its own authority — opens a derived duty of its own.

    The occurrence and the task are always fresh and always derived from
    ``(instance_id, slot_key)``: TG §12 keeps every occurrence its own position
    even when several of them answer to one duty.
    """

    slot = identifier(slot_key, "slot_key")
    relation = enum_of(ObligationRelation, relation, "obligation_relation")
    duty = (
        parent_obligation
        if relation is ObligationRelation.REFINES_PARENT
        else ObligationId(derive_id("obl", parent_obligation, instance_id, slot))
    )
    return SlotIdentity(
        slot_key=slot,
        occurrence_id=OccurrenceId(derive_id("occ", instance_id, slot)),
        task_id=TaskRef(derive_id("task", instance_id, slot)),
        obligation_id=duty,
    )


# --------------------------------------------------------------------------------------
# Sharing (§8.3, TG §12)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SharingSignature:
    """TG §12, as order only: what a named existing step and the slot naming it must
    agree on before the slot may *be* that step.

    Whether the two are the same piece of work is the Planner's call — it named the
    step (``RefineOperation.reuse``, TaskGraph 补全第三批).  What is checked here is
    that the naming is admissible: the same task type, the same authority and
    semantic scope and domain, and an effect that may be shared at all.  The goal's
    wording and its inputs are not compared here; the inputs are checked against the
    existing step by the TaskGraph sharing gate (``graph.taskgraph_sharing``).
    """

    goal_type_ref: VersionedRef
    authority_scope: str
    semantic_scope: str
    domain_semantics: str
    side_effect_kind: SideEffectKind
    effect_identity: str | None = None

    @classmethod
    def of(cls, spec: TaskTypeSpec, *, authority_scope: str, semantic_scope: str) -> SharingSignature:
        return cls(
            goal_type_ref=spec.task_type_ref,
            authority_scope=identifier(authority_scope, "authority_scope"),
            semantic_scope=identifier(semantic_scope, "semantic_scope"),
            domain_semantics=spec.domain or "",
            side_effect_kind=spec.side_effect_kind,
            effect_identity=spec.effect_identity,
        )


def named_share_refusal(existing: SharingSignature, slot: SharingSignature) -> str | None:
    """Why a slot may not be the existing step it named — or ``None`` when it may."""

    differing = [name for name in ("goal_type_ref", "authority_scope", "semantic_scope",
                                   "domain_semantics", "side_effect_kind", "effect_identity")
                 if getattr(existing, name) != getattr(slot, name)]
    if differing:
        return "the named step differs in " + ", ".join(differing) + "; it is another kind of work"
    if existing.side_effect_kind not in READ_ONLY_EFFECTS and existing.effect_identity is None:
        return ("the named step writes outside the orchestrator; two such actions are two "
                "actions and are never shared (§8.3)")
    return None


@dataclass(frozen=True, slots=True)
class SharedGoalEntry:
    """One existing step a later slot may name: running, accepted under the current
    requirements, or accepted under earlier requirements and awaiting the reviewer's
    judgement under the current ones (the TaskGraph plan sources decide which are eligible)."""

    occurrence_id: OccurrenceId
    task_id: TaskRef
    obligation_id: ObligationId
    signature: SharingSignature
    #: The exact Acceptance this occurrence's result was accepted under, when there
    #: is one.  A typed reference, not a bare id: TG decision 9 binds reuse to a
    #: specific acceptance and an id prefix does not establish which kind of thing
    #: it names.
    acceptance_ref: TypedRef | None = None
    #: The acceptance is of an earlier requirements revision: the step is kept and the
    #: same result is reviewed again under the current ones (TaskGraph 补全第四批).
    carried: bool = False

    def __post_init__(self) -> None:
        if self.carried and self.acceptance_ref is None:
            raise ContractError("a carried shared step names the acceptance it was kept with")
        if self.acceptance_ref is not None and not isinstance(self.acceptance_ref, TypedRef):
            raise ContractError("shared_goal_entry.acceptance_ref must be a TypedRef")


def slot_criteria(method: MethodContract, slot: str) -> frozenset[str]:
    """The criteria a method hands to one of its steps — the same reading as the
    system's carried criteria (a link without a step lands on the finalizer; a link
    without a child criterion id is carried under the parent's id)."""

    composition = method.composition
    return frozenset(
        str(link.child_criterion_id or link.parent_criterion_id)
        for link in composition.criterion_links
        if (link.child_step or composition.finalizer_step) == slot)


def occurrence_criteria(network: Any, definition_of: Any, occurrence_id: OccurrenceId) -> frozenset[str]:
    """Every criterion the adopted plan currently hands to one step (all the methods
    that hold it).  ``definition_of``: a method reference → its contract."""

    adopted = set(network.adopted_instance_ids)
    out: set[str] = set()
    for instance in network.method_instances:
        if instance.instance_id not in adopted:
            continue
        for child in instance.child_bindings:
            if child.occurrence_id != occurrence_id:
                continue
            method = definition_of(instance.method_ref)
            if method is None:
                raise GroundingError(f"the method holding {occurrence_id} is unavailable")
            out |= slot_criteria(method, str(child.slot_key))
    return frozenset(out)


class ReuseRefused(GroundingError):
    """A named reuse the order checks refuse; the message states the facts, the
    Planner decides what to do (TaskGraph 补全第三批)."""

    code = "REUSE_NOT_ALLOWED"


# --------------------------------------------------------------------------------------
# ground_method (§18.3)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SlotPlan:
    """What grounding decided about one slot, before the draft is assembled."""

    step: MethodStep
    identity: SlotIdentity
    spec: TaskTypeSpec
    requiredness: Requiredness
    reuse_policy: ReusePolicy
    bound_occurrence_id: OccurrenceId
    bound_task_id: TaskRef
    bound_obligation_id: ObligationId
    signature: SharingSignature
    acceptance_ref: TypedRef | None = None
    arguments: Mapping[str, Any] = None  # type: ignore[assignment]

    @property
    def shared(self) -> bool:
        return self.bound_occurrence_id != self.identity.occurrence_id


def requiredness_of(step: MethodStep) -> Requiredness:
    """§6.1: a step that refines the parent duty is required by this method.

    ``INDEPENDENT_AUTHORIZED`` is the "authorised optional" branch: work a method
    may legitimately add, which does not gate the parent.  A planner cannot turn a
    user's required outcome into an optional one, because the relation is a field
    of the immutable method definition, not a choice made at grounding time.
    """

    if step.obligation_relation is ObligationRelation.REFINES_PARENT:
        return Requiredness.REQUIRED
    return Requiredness.OPTIONAL_AUTHORIZED


def resolve_arguments(
    step: MethodStep, parameters: Mapping[str, Any], *, path: str
) -> dict[str, Any]:
    """Resolve a step's argument expressions against the grounded parameters.

    ``OutputValue`` is left symbolic: a step output does not exist at grounding
    time and becomes a DATA requirement in the compiler, not a value here.
    """

    out: dict[str, Any] = {}
    for name, argument in step.arguments.items():
        # 2026-09-28 真机：合成方法的发布步骤把两个上游产出放进一个 delivery 列表。数据流
        # （method_data_flow，准入/编译/导出共用）早已逐个读出嵌套产出并变成 DATA 需求，
        # 只有这里把嵌套的产出当成必须当场有值而拒绝——同一原生决定被拒三次、任务失败。
        # 含产出的参数整体留作符号，与顶层产出同等对待。
        if any(isinstance(node, OutputValue) for node in iter_values(argument)):
            continue
        out[name] = _resolve_value(argument, parameters, path=f"{path}.{name}")
    return out


def _resolve_value(value: Any, parameters: Mapping[str, Any], *, path: str) -> Any:
    from ...contracts.htn import ArrayValue, ConstantValue, ObjectValue

    if isinstance(value, ConstantValue):
        return value.value
    if isinstance(value, ParameterValue):
        if value.name not in parameters:
            raise GroundingError(f"{path}: no bound parameter named {value.name!r}")
        return parameters[value.name]
    if isinstance(value, ObjectValue):
        return {
            key: _resolve_value(item, parameters, path=f"{path}.{key}")
            for key, item in value.fields.items()
        }
    if isinstance(value, ArrayValue):
        return [
            _resolve_value(item, parameters, path=f"{path}[{position}]")
            for position, item in enumerate(value.items)
        ]
    if isinstance(value, OutputValue):
        raise GroundingError(
            f"{path}: a step output has no value at grounding time; it becomes a DATA "
            "requirement in the compiler"
        )
    raise GroundingError(f"{path}: unsupported value expression")


def ground_method(
    task: TaskSemanticBindingV1,
    method: MethodContract,
    bindings: Mapping[str, Any],
    assessment: ApplicabilityReport,
    *,
    catalog: TaskTypeCatalog,
    schemas: SchemaCatalog,
    reuse: Mapping[str, SharedGoalEntry] | None = None,
    plan_revision: PlanRevision = PlanRevision(0),
    goal_occurrence_id: OccurrenceId | None = None,
    world_snapshot_id: str | None = None,
) -> MethodInstanceDraft:
    """§18.3: type-check, bind variables, derive identities.  No database effects.

    Refuses rather than repairs.  An inapplicable assessment, a parameter of the
    wrong type, a step whose task type is not registered — each is a
    :class:`GroundingError`, because a draft built on any of them would carry a
    precondition witness for a world that does not hold.
    """

    if not isinstance(task, TaskSemanticBindingV1):
        raise GroundingError("ground_method expects a TaskSemanticBindingV1")
    if not isinstance(method, MethodContract):
        raise GroundingError("ground_method expects a MethodContract")
    if not isinstance(assessment, ApplicabilityReport):
        raise GroundingError("ground_method expects an ApplicabilityReport")
    if task.form is not TaskForm.COMPOUND:
        raise GroundingError(
            f"task {task.task_id!s} is primitive; only a compound task is refined by a "
            "method (§6.2)"
        )
    goal_type = catalog.resolve(method.goal_type_ref)
    if goal_type is None:
        raise GroundingError(
            f"goal type {method.goal_type_ref.id!r} v{method.goal_type_ref.version} is not "
            "registered at that content hash"
        )
    if goal_type.goal_signature.signature_id != task.goal_signature.signature_id:
        raise GroundingError(
            f"method {method.method_id!r} decomposes goal signature "
            f"{goal_type.goal_signature.signature_id!r}, but the task is "
            f"{task.goal_signature.signature_id!r}"
        )

    if assessment.status is not ApplicabilityStatus.APPLICABLE:
        raise GroundingError(
            f"method {method.method_id!r} is {assessment.status!s} for task "
            f"{task.task_id!s}; a method is grounded only where it applies (§7.2)"
        )
    gate = assessment.authorization
    if method.applicable_when and (gate is None or not gate.allowed):
        raise GroundingError(
            f"method {method.method_id!r} has preconditions whose TRUE is not "
            "evidence-backed; UNKNOWN and CONFLICT never open a gate (§6.6 rule 2)"
        )

    parameters = _grounded_parameters(task, method, bindings, schemas)
    parameters_digest = content_hash_of(
        [{"name": key, "value": parameters[key]} for key in sorted(parameters)]
    )
    parent_occurrence = (
        goal_occurrence_id if goal_occurrence_id is not None else OccurrenceId(str(task.task_id))
    )
    instance_id = instance_identity(
        goal_id=task.task_id,
        goal_occurrence_id=parent_occurrence,
        method_ref=method.method_ref(),
        parameters_digest=parameters_digest,
    )

    plans = plan_slots(
        task,
        method,
        parameters,
        instance_id=instance_id,
        catalog=catalog,
        reuse=reuse or {},
    )
    child_bindings = tuple(
        ChildBinding(
            instance_id=instance_id,
            slot_key=plan.identity.slot_key,
            occurrence_id=plan.bound_occurrence_id,
            obligation_id=plan.bound_obligation_id,
            requiredness=plan.requiredness,
            reuse_policy=plan.reuse_policy,
            goal_occurrence_id=plan.bound_occurrence_id,
            acceptance_ref=plan.acceptance_ref
            if plan.reuse_policy is ReusePolicy.REUSE_ACCEPTED
            else None,
        )
        for plan in plans
    )

    # Every applicable_when is TRUE here: ALL is TRUE only when every member is, and
    # the assessment above is APPLICABLE.  Freezing the digest with that truth is
    # what the pre-dispatch START witnesses compare the world against (§6.6 rule 3).
    witnesses = tuple(
        PreconditionWitnessRecord(
            condition_digest=condition_digest(condition),
            phase=PreconditionPhase.SELECT,
            truth=TruthValue.TRUE,
        )
        for condition in method.applicable_when
    )

    return MethodInstanceDraft(
        instance_id=instance_id,
        goal_id=task.task_id,
        obligation_id=task.obligation_id,
        method_ref=method.method_ref(),
        grounded_parameters=tuple(
            Binding(name=key, value=parameters[key]) for key in sorted(parameters)
        ),
        world_snapshot_id=world_snapshot_id,
        precondition_witnesses=witnesses,
        assumption_refs=method.basis_refs,
        child_bindings=child_bindings,
        plan_revision=plan_revision,
        goal_occurrence_id=parent_occurrence,
    )


def _grounded_parameters(
    task: TaskSemanticBindingV1,
    method: MethodContract,
    bindings: Mapping[str, Any],
    schemas: SchemaCatalog,
) -> dict[str, Any]:
    """Merge the task's typed parameters with the caller's bindings and type-check."""

    if not isinstance(bindings, Mapping):
        raise GroundingError("ground_method expects a mapping of parameter bindings")
    merged: dict[str, Any] = dict(task.typed_parameters)
    for name, value in bindings.items():
        merged[identifier(name, "binding name")] = value
    schema = schemas.resolve(method.parameter_schema_ref)
    if schema is None:
        raise GroundingError(
            f"parameter schema {method.parameter_schema_ref.id!r} "
            f"v{method.parameter_schema_ref.version} is not registered at that content hash"
        )
    # Only the declared fields travel into the instance.  Anything else the task
    # happens to carry is not a parameter of *this* method, and silently passing it
    # through would make the parameters digest depend on unrelated state.
    surplus = sorted(set(merged) - {item.name for item in schema.fields})
    scoped = {name: merged[name] for name in merged if name not in surplus}
    check = schema.check(scoped)
    if not check.ok:
        raise ParameterBindingsError(
            f"method {method.method_id!r} parameters do not type-check: "
            + "; ".join(check.messages())
        )
    return scoped


def plan_slots(
    task: TaskSemanticBindingV1,
    method: MethodContract,
    parameters: Mapping[str, Any],
    *,
    instance_id: MethodInstanceId,
    catalog: TaskTypeCatalog,
    reuse: Mapping[str, SharedGoalEntry],
) -> tuple[SlotPlan, ...]:
    """Decide, per slot, whether it creates work or *is* an existing step.

    A slot is an existing step only when the Planner named it (``reuse``: step name →
    the existing step, resolved by the caller from the eligible sharing sources).
    Nothing is matched by signature: whether two pieces of work are one is the
    Planner's judgement; here only the order is checked (:func:`named_share_refusal`).
    """

    unknown = sorted(set(reuse) - {step.local_id for step in method.steps})
    if unknown:
        raise ReuseRefused(f"reuse names step(s) {unknown} that this method does not have")
    plans: list[SlotPlan] = []
    for step in method.steps:
        spec = catalog.resolve(step.task_type_ref)
        if spec is None:
            raise GroundingError(
                f"step {step.local_id!r} names task type {step.task_type_ref.id!r} "
                f"v{step.task_type_ref.version}, which is not registered"
            )
        if spec.form is not step.form:
            raise GroundingError(
                f"step {step.local_id!r} declares form {step.form!s} but task type "
                f"{step.task_type_ref.id!r} is {spec.form!s}"
            )
        identity = slot_identity(
            instance_id, step.local_id, task.obligation_id, step.obligation_relation
        )
        arguments = resolve_arguments(step, parameters, path=f"step[{step.local_id}]")
        signature = SharingSignature.of(
            spec, authority_scope=task.semantic_scope, semantic_scope=task.semantic_scope)
        entry = reuse.get(step.local_id)
        if entry is None:
            plans.append(
                SlotPlan(
                    step=step,
                    identity=identity,
                    spec=spec,
                    requiredness=requiredness_of(step),
                    reuse_policy=ReusePolicy.NEW_WORK,
                    bound_occurrence_id=identity.occurrence_id,
                    bound_task_id=identity.task_id,
                    bound_obligation_id=identity.obligation_id,
                    signature=signature,
                    arguments=arguments,
                )
            )
            continue
        if step.form is not TaskForm.PRIMITIVE:
            raise ReuseRefused(f"step {step.local_id!r} is a sub-goal; only an ordinary step can be shared")
        refusal = named_share_refusal(entry.signature, signature)
        if refusal is not None:
            raise ReuseRefused(f"step {step.local_id!r} cannot be {entry.occurrence_id}: {refusal}")
        policy = (ReusePolicy.REUSE_ACCEPTED if entry.acceptance_ref is not None
                  else ReusePolicy.SHARE_ACTIVE)
        plans.append(
            SlotPlan(
                step=step,
                identity=identity,
                spec=spec,
                requiredness=requiredness_of(step),
                reuse_policy=policy,
                bound_occurrence_id=entry.occurrence_id,
                bound_task_id=entry.task_id,
                bound_obligation_id=entry.obligation_id,
                signature=signature,
                acceptance_ref=entry.acceptance_ref,
                arguments=arguments,
            )
        )
    return tuple(plans)


#: 写入目标的命名空间：任务工作区里的一个文件路径。
WORKSPACE_FILE = "workspace_file"


def child_task_bindings(
    draft: MethodInstanceDraft,
    method: MethodContract,
    task: TaskSemanticBindingV1,
    *,
    catalog: TaskTypeCatalog,
    schemas: SchemaCatalog,
    reuse: Mapping[str, SharedGoalEntry] | None = None,
    write_targets: Mapping[str, Sequence[str]] | None = None,
) -> tuple[TaskSemanticBindingV1, ...]:
    """The semantic bindings of the slots this draft *creates*.

    ``write_targets``（阶段 D）：步骤名 → 这一步按做法负责写出的文件路径（做法把 ``file:X``
    要求链接到了它）。记成这一步的写入目标，两个没有先后的步骤写同一个文件由已有的资源
    冲突检查在编译期退回。

    A shared slot binds an occurrence that already exists, so it contributes no
    binding here: TG §3.2 keeps ``TaskSemanticBindingV1`` the single authority for
    a task, and minting a second one for a borrowed occurrence is how a shared
    sub-goal acquires two owners.
    """

    parameters = {binding.name: binding.value for binding in draft.grounded_parameters}
    plans = plan_slots(
        task,
        method,
        parameters,
        instance_id=draft.instance_id,
        catalog=catalog,
        reuse=reuse or {},
    )
    out: list[TaskSemanticBindingV1] = []
    for plan in plans:
        if plan.shared:
            continue
        out.append(task_binding_for(plan, draft, task, schemas=schemas,
                                    writes=tuple((write_targets or {}).get(plan.identity.slot_key, ()))))
    return tuple(out)


def task_binding_for(
    plan: SlotPlan,
    draft: MethodInstanceDraft,
    parent: TaskSemanticBindingV1,
    *,
    schemas: SchemaCatalog,
    writes: Sequence[str] = (),
) -> TaskSemanticBindingV1:
    """Build the semantic binding of one newly created slot."""

    spec = plan.spec
    del schemas  # the shape is already checked; kept in the signature for symmetry
    contract_payload = {
        "task_type": spec.task_type_ref.to_json(),
        "parameters": {key: plan.arguments[key] for key in sorted(plan.arguments)},
        "instance": str(draft.instance_id),
        "slot": plan.identity.slot_key,
    }
    return TaskSemanticBindingV1(
        task_id=plan.identity.task_id,
        obligation_id=plan.identity.obligation_id,
        contract_revision=parent.contract_revision,
        contract_hash=content_hash_of(contract_payload),
        form=spec.form,
        goal_signature=spec.goal_signature,
        typed_parameters=dict(plan.arguments),
        requirement_refs=parent.requirement_refs,
        input_ports=spec.input_ports,
        output_ports=spec.output_ports,
        operator_ref=spec.operator_ref,
        semantic_scope=parent.semantic_scope,
        capability_requirements=spec.required_capabilities,
        precondition_refs=tuple(
            PreconditionRef(
                condition_digest=witness.condition_digest, phase=PreconditionPhase.SELECT
            )
            for witness in draft.precondition_witnesses
        )
        if spec.form is TaskForm.PRIMITIVE
        else (),
        occurrence_binding=MethodOccurrenceBinding(
            method_instance_id=draft.instance_id,
            occurrence_id=plan.identity.occurrence_id,
            slot_key=plan.identity.slot_key,
        ),
        resource_reads=spec.resource_reads if spec.form is TaskForm.PRIMITIVE else (),
        resource_writes=(
            (*spec.resource_writes,
             *(ResourceRef(WORKSPACE_FILE, path) for path in dict.fromkeys(writes)
               if (WORKSPACE_FILE, path) not in {ref.key for ref in spec.resource_writes}))
            if spec.form is TaskForm.PRIMITIVE else ()),
        side_effect_kind=spec.side_effect_kind if spec.form is TaskForm.PRIMITIVE else None,
    )


def data_flows(method: MethodContract) -> tuple[tuple[str, str, str, str], ...]:
    """``(producer_step, output_port, consumer_step, input_port)`` for each DATA link.

    This is the single reading of "a step argument reads another step's output";
    the admission check, the compiler and the HDDL export all consult it, so none
    of them can disagree about what the method's data flow is.
    """

    out: list[tuple[str, str, str, str]] = []
    for step in method.steps:
        for name, argument in step.arguments.items():
            for node in iter_values(argument):
                if isinstance(node, OutputValue) and node.step != step.local_id:
                    entry = (node.step, node.port, step.local_id, name)
                    if entry not in out:
                        out.append(entry)
    return tuple(out)


def describe_draft(draft: MethodInstanceDraft) -> str:
    """A short, deterministic description for diagnostics and journals."""

    return text(
        f"{draft.method_ref.method_id}@{draft.method_ref.version} refines "
        f"{draft.goal_id!s} at {draft.effective_goal_occurrence_id!s} "
        f"({len(draft.child_bindings)} slots)",
        "description",
    )


__all__ = (
    "GroundingError",
    "ParameterBindingsError",
    "ReuseRefused",
    "SharedGoalEntry",
    "SharingSignature",
    "SlotIdentity",
    "SlotPlan",
    "child_task_bindings",
    "data_flows",
    "derive_id",
    "describe_draft",
    "ground_method",
    "named_share_refusal",
    "occurrence_criteria",
    "slot_criteria",
    "instance_identity",
    "plan_slots",
    "requiredness_of",
    "resolve_arguments",
    "slot_identity",
    "task_binding_for",
)
