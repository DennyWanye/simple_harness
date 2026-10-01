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
    content_criteria = tuple(key for key, statement in zip(criteria, mission.success_criteria, strict=True)
                             if not statement.startswith("action:"))
    # 2026-09-29（plans/2026-09-28-system-operations）：发布等操作由系统在任务层面准备，
    # 规划器只安排内容。根任务要求规划器覆盖的只有内容要求；根的完整要求清单
    # （initialize_root 的 requirement_refs）仍含全部要求，义务与已批准效果照旧挂接。
    signature = GoalSignature("desktop.user-goal", 1, params, outputs, mission.goal,
                              content_criteria or criteria)
    preparation = GoalSignature("desktop.prepare-delivery", 1, params, outputs,
        mission.goal, content_criteria)
    continuation = GoalSignature("desktop.continue-delivery", 1, params, outputs,
        "Continue from an accepted upstream delivery: " + mission.goal, content_criteria)
    ports = (PortSpec("delivery", outputs),)
    # 内容步骤不再有申请单端口：申请单由系统按已批准效果生成（2026-09-29）。
    preparation_ports = ports
    # These are actual workspace operations available to this deployment. External
    # effects still require an OperationIntent, review and the original connector.
    # 2026-10-01（HTN 精简 片 B）：中间目标按层注册。类型不声明判据——子目标负责哪几条要求，是
    # 上级做法用链接交给它的（原编号、用户原话），审阅也按那几条审。层级决定能往下放什么：
    # 根目标 0 层，第一层子目标的做法里还能放第二层子目标，第二层只能放普通步骤；层数上限
    # 就是这里注册了几层，由 SDK 的注册检查保证，Host 不另写计数。子目标不声明端口：它下面的
    # 步骤写进同一个工作区，后续步骤靠先后顺序接着做。
    levels = {"desktop.user-goal": 0, "desktop.sub-goal-1": 1, "desktop.sub-goal-2": 2}
    for name, form in (("desktop.user-goal", TaskForm.COMPOUND),
                       ("desktop.sub-goal-1", TaskForm.COMPOUND),
                       ("desktop.sub-goal-2", TaskForm.COMPOUND),
                       ("desktop.prepare-delivery", TaskForm.PRIMITIVE),
                       ("desktop.continue-delivery", TaskForm.PRIMITIVE)):
        goal_signature = preparation if form is TaskForm.PRIMITIVE else signature
        level = levels.get(name)
        if level:
            goal_signature = GoalSignature(
                name, 1, params, outputs,
                "A part of the user's goal; its goal parameter says what this part has to achieve.", ())
        # Add a separately identified consumer type. Previously admitted methods
        # keep the exact prepare-delivery v1 declaration and need no new input.
        input_ports = ()
        if name == "desktop.continue-delivery":
            goal_signature = continuation
            input_ports = (PortSpec("delivery", outputs),)
        declared_ports = preparation_ports if form is TaskForm.PRIMITIVE else (() if level else ports)
        body = {"name": name, "form": str(form), "signature": goal_signature.to_json(),
                "ports": [p.to_json() for p in declared_ports]}
        if input_ports:
            body["input_ports"] = [p.to_json() for p in input_ports]
        if level:
            body["refinement_level"] = level
        ref = VersionedRef(name, 1, content_hash_of(body))
        world.catalog.register(TaskTypeSpec(
            task_type_ref=ref, form=form, goal_signature=goal_signature,
            input_ports=input_ports, output_ports=declared_ports,
            parameter_schema_ref=params, output_schema_ref=outputs,
            operator_ref=(VersionedRef("desktop.workspace-worker", 1,
                content_hash_of({"tools": list(mission.allowed_tools)}))
                if form is TaskForm.PRIMITIVE else None),
            required_capabilities=("workspace.prepare",) if form is TaskForm.PRIMITIVE else (),
            side_effect_kind=SideEffectKind.LOCAL_WRITE if form is TaskForm.PRIMITIVE else SideEffectKind.NONE,
            reversible=True, domain="desktop", refinement_level=level,
        ))
    offered = set(mission.allowed_tools)
    world.records = capability_records(world.catalog, capability_layers={"workspace.prepare": None},
        unauthorized=() if {"workspace_read_file", "workspace_write_file", "workspace_list"} <= offered
        else ("workspace.prepare",))
    return world


def root_requirements(mission: Any, principal: Any) -> Any:
    """Revision 1 of the user's explicit criteria: ``req-<mission>-1`` / ``c-user-<n>``.

    The same document is the approved Requirements builder of the Assurance
    factory (plan S02), so the assured lane and this root initialization agree
    byte for byte and neither writes a second body.
    """
    from agent_orchestrator.contracts.resolution import (
        AllExpr, Criterion, CriterionExpr, CriterionOrigin, EvaluationKind,
        RequirementClass, RequirementsRevision,
    )

    refs = tuple(f"c-user-{i + 1}" for i in range(len(mission.success_criteria)))
    criteria = tuple(Criterion(identifier, 1, CriterionOrigin.USER_EXPLICIT, statement,
        RequirementClass.REQUIRED_OUTCOME, EvaluationKind.SEMANTIC)
        for identifier, statement in zip(refs, mission.success_criteria, strict=True))
    return RequirementsRevision(
        revision_id=f"req-{mission.id}-1", mission_id=mission.id, revision=1, criteria=criteria,
        success_expression=AllExpr(tuple(CriterionExpr(c.criterion_id) for c in criteria)),
        authority_subject=principal.principal_id,
    )


def initialize_root(loop: Any, mission: Any, principal: Any) -> None:
    """Join the caller's create transaction; replay never inserts another root.

    An assured Mission's factory already wrote revision 1 inside the same create
    transaction; that body must be byte-identical and is never inserted twice.
    """
    from agent_orchestrator.contracts.htn import ContractRevision, ObligationId, TaskRef, TaskSemanticBindingV1
    from agent_orchestrator.contracts.obligations import Obligation
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
        requirement_refs=tuple(f"c-user-{i + 1}" for i in range(len(mission.success_criteria))),
        semantic_scope="mission",
    )
    # Root requirements are reviewed over accepted contributions. Concrete
    # file/pytest statements are projected to leaf checks by the materializer;
    # naming those checks as root executions would require invented receipts.
    requirements = root_requirements(mission, principal)
    assert tuple(c.criterion_id for c in requirements.criteria) == tuple(binding.requirement_refs)
    existing = htn.latest_requirements_revision(mission.id)
    if existing is not None and (existing.revision != 1
                                 or existing.content_hash() != requirements.content_hash()):
        raise RuntimeError("root requirements already exist with a different body")
    with loop.store.transaction():
        ObligationStore(loop.store).register(Obligation(
            obligation_id=ObligationId(duty_id), mission_id=mission.id,
            requirement_refs=binding.requirement_refs, goal_signature_id=definition.goal_signature.signature_id,
        ), recursion_fuel=8)
        loop.commit.admit_obligation_demand(mission.id, ObligationId(duty_id),
            principal=principal.principal_id, requester={"kind": "mission_root"},
            evidence={"mission_id": mission.id, "requirement_refs": list(binding.requirement_refs)})
        htn.put_task_semantics(mission.id, binding)
        if existing is None:
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
