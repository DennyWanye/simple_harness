# SPDX-License-Identifier: BUSL-1.1
"""Desktop-owned HTN declarations and atomic user-Mission initialization.

User criteria remain explicit requirements. Completion mapping and planning
authorization are confirmed through the existing authenticated UI commands.
No model, observer result or approval is fabricated during assembly.
"""
from __future__ import annotations

from typing import Any


def planning_world(loop: Any, mission: Any) -> Any:
    from agent_orchestrator.contracts.htn import GoalSignature, PortSpec, SideEffectKind, TaskForm
    from agent_orchestrator.contracts.semantic_base import VersionedRef, content_hash_of
    from agent_orchestrator.planning.htn.registry import ObjectSchema, SchemaField, TaskTypeSpec
    from agent_orchestrator.planning.htn.world import build_planning_world, capability_records
    from agent_orchestrator.storage.htn_store import HtnStore

    htn = HtnStore(loop.store)
    world = build_planning_world(mission.id, domains=(), semantics=htn, observers=())
    criteria = tuple(f"c-user-{i + 1}" for i in range(len(mission.success_criteria)))
    params_body = {"fields": [{"name": "goal", "type": "string", "required": True}]}
    params = VersionedRef("desktop.goal-parameters", 1, content_hash_of(params_body))
    outputs = VersionedRef("desktop.workspace-outputs", 1, content_hash_of({"fields": []}))
    world.schemas.register(ObjectSchema(params, (SchemaField("goal", "string"),)))
    world.schemas.register(ObjectSchema(outputs))
    signature = GoalSignature("desktop.user-goal", 1, params, outputs, mission.goal, criteria)
    content_criteria = tuple(key for key, statement in zip(criteria, mission.success_criteria, strict=True)
                             if not statement.startswith("action:"))
    preparation = GoalSignature("desktop.prepare-delivery", 1, params, outputs,
        mission.goal, content_criteria)
    ports = (PortSpec("delivery", outputs),)
    preparation_ports = ports + ((PortSpec("action_candidate", outputs),)
        if any(c.startswith("action:") for c in mission.success_criteria) else ())
    # These are actual workspace operations available to this deployment. External
    # effects still require an OperationIntent, review and the original connector.
    for name, form in (("desktop.user-goal", TaskForm.COMPOUND),
                       ("desktop.prepare-delivery", TaskForm.PRIMITIVE)):
        goal_signature = preparation if form is TaskForm.PRIMITIVE else signature
        declared_ports = preparation_ports if form is TaskForm.PRIMITIVE else ports
        body = {"name": name, "form": str(form), "signature": goal_signature.to_json(),
                "ports": [p.to_json() for p in declared_ports]}
        ref = VersionedRef(name, 1, content_hash_of(body))
        world.catalog.register(TaskTypeSpec(
            task_type_ref=ref, form=form, goal_signature=goal_signature, output_ports=declared_ports,
            parameter_schema_ref=params, output_schema_ref=outputs,
            operator_ref=(VersionedRef("desktop.workspace-worker", 1,
                content_hash_of({"tools": list(mission.allowed_tools)}))
                if form is TaskForm.PRIMITIVE else None),
            required_capabilities=("workspace.prepare",) if form is TaskForm.PRIMITIVE else (),
            side_effect_kind=SideEffectKind.LOCAL_WRITE if form is TaskForm.PRIMITIVE else SideEffectKind.NONE,
            reversible=True, domain="desktop",
        ))
    offered = set(mission.allowed_tools)
    world.records = capability_records(world.catalog, capability_layers={"workspace.prepare": None},
        unauthorized=() if {"workspace_read_file", "workspace_write_file", "workspace_list"} <= offered
        else ("workspace.prepare",))
    return world


def initialize_root(loop: Any, mission: Any, principal: Any) -> None:
    """Join the caller's create transaction; replay never inserts another root."""
    from agent_orchestrator.contracts.htn import ContractRevision, ObligationId, TaskRef, TaskSemanticBindingV1
    from agent_orchestrator.contracts.obligations import Obligation
    from agent_orchestrator.contracts.resolution import (
        AllExpr, Criterion, CriterionExpr, CriterionOrigin, EvaluationKind,
        RequirementClass, RequirementsRevision,
    )
    from agent_orchestrator.contracts.semantic_base import content_hash_of
    from agent_orchestrator.orchestrator.hierarchical_dispatch import is_hierarchical
    from agent_orchestrator.storage.htn_store import HtnStore
    from agent_orchestrator.storage.obligation_store import ObligationStore

    if not is_hierarchical(mission):
        return
    htn = HtnStore(loop.store)
    task_id, duty_id = "desktop-root-" + mission.id, "desktop-duty-" + mission.id
    if htn.latest_task_semantics(task_id) is not None:
        return
    world = planning_world(loop, mission)
    definition = next(t for t in world.catalog.task_types() if t.task_type_ref.id == "desktop.user-goal")
    parameters = {"goal": mission.goal}
    binding = TaskSemanticBindingV1(
        task_id=TaskRef(task_id), obligation_id=ObligationId(duty_id), contract_revision=ContractRevision(1),
        contract_hash=content_hash_of({"task_type": definition.to_json(), "parameters": parameters}),
        form=definition.form, goal_signature=definition.goal_signature, typed_parameters=parameters,
        output_ports=definition.output_ports,
        requirement_refs=definition.goal_signature.coverage_criteria, semantic_scope="mission",
    )
    # Root requirements are reviewed over accepted contributions. Concrete
    # file/pytest statements are projected to leaf checks by the materializer;
    # naming those checks as root executions would require invented receipts.
    criteria = tuple(Criterion(identifier, 1, CriterionOrigin.USER_EXPLICIT, statement,
        RequirementClass.REQUIRED_OUTCOME, EvaluationKind.SEMANTIC)
        for identifier, statement in zip(binding.requirement_refs, mission.success_criteria, strict=True))
    requirements = RequirementsRevision(
        revision_id=f"req-{mission.id}-1", mission_id=mission.id, revision=1, criteria=criteria,
        success_expression=AllExpr(tuple(CriterionExpr(c.criterion_id) for c in criteria)),
        authority_subject=principal.principal_id,
    )
    with loop.store.transaction():
        ObligationStore(loop.store).register(Obligation(
            obligation_id=ObligationId(duty_id), mission_id=mission.id,
            requirement_refs=binding.requirement_refs, goal_signature_id=definition.goal_signature.signature_id,
        ), recursion_fuel=8)
        loop.commit.admit_obligation_demand(mission.id, ObligationId(duty_id),
            principal=principal.principal_id, requester={"kind": "mission_root"},
            evidence={"mission_id": mission.id, "requirement_refs": list(binding.requirement_refs)})
        htn.put_task_semantics(mission.id, binding)
        htn.insert_requirements_revision(requirements)


def install(loop: Any) -> None:
    from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
    from agent_orchestrator.orchestrator.operation_completion import OperationCompletionError, OperationCompletionReader
    from agent_orchestrator.storage.htn_store import HtnStore

    def ready(mission: Any) -> bool:
        requirements = HtnStore(loop.store).latest_requirements_revision(mission.id)
        if requirements is None:
            return False
        try:
            OperationCompletionReader(loop.store).read_requirements(mission.id, TypedRef(
                kind=TypedRefKind.REQUIREMENTS, id=str(requirements.revision_id),
                revision=requirements.revision, content_hash=requirements.content_hash()))
        except OperationCompletionError as error:
            if error.code == "OP_REQUIREMENT_MAPPING_MISSING":
                return False
            raise
        return True

    loop.install_hierarchical_deployment(lambda mission: planning_world(loop, mission), start_gate=ready)
