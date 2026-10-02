# SPDX-License-Identifier: Apache-2.0
"""用户任务的根：要求书、根任务初始化、开工条件（2026-10-03 从 Host 搬入，HTN 补齐阶段 A′）。

只有一份要求书构造（``user_requirements``）：保证通道的建任务工厂与根初始化逐字节一致，谁都
不写第二份。规划世界（桌面类型等产品领域知识）由部署传入 ``world_factory``，根目标的类型名与
任务号前缀也由部署给出；这些名字是已有任务的身份，部署原样沿用。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts.htn import ContractRevision, ObligationId, TaskRef, TaskSemanticBindingV1
from ..contracts.obligations import Obligation
from ..contracts.resolution import (
    AllExpr,
    Criterion,
    CriterionExpr,
    CriterionOrigin,
    EvaluationKind,
    RequirementClass,
    RequirementsRevision,
)
from ..contracts.semantic_base import TypedRef, TypedRefKind, content_hash_of
from ..storage.htn_store import HtnStore
from ..storage.obligation_store import ObligationStore

ROOT_RECURSION_FUEL = 8


def criterion_ids(mission: Any) -> tuple[str, ...]:
    return tuple(f"c-user-{i + 1}" for i in range(len(mission.success_criteria)))


def user_requirements(mission: Any, principal: Any) -> RequirementsRevision:
    """Revision 1 of the user's explicit criteria: ``req-<mission>-1`` / ``c-user-<n>``,
    every criterion required, combined with "all of" (also for a single criterion)."""

    refs = criterion_ids(mission)
    criteria = tuple(Criterion(identifier, 1, CriterionOrigin.USER_EXPLICIT, statement,
        RequirementClass.REQUIRED_OUTCOME, EvaluationKind.SEMANTIC)
        for identifier, statement in zip(refs, mission.success_criteria, strict=True))
    return RequirementsRevision(
        revision_id=f"req-{mission.id}-1", mission_id=mission.id, revision=1, criteria=criteria,
        success_expression=AllExpr(tuple(CriterionExpr(c.criterion_id) for c in criteria)),
        authority_subject=principal.principal_id,
    )


def initialize_root(
    loop: Any, mission: Any, principal: Any, *,
    world_factory: Callable[[Any, Any], Any], root_type: str, task_prefix: str, duty_prefix: str,
) -> None:
    """Join the caller's create transaction; replay never inserts another root.

    An assured Mission's factory already wrote revision 1 inside the same create
    transaction; that body must be byte-identical and is never inserted twice.
    """
    from ..orchestrator.hierarchical_dispatch import is_hierarchical

    if not is_hierarchical(mission):
        return
    htn = HtnStore(loop.store)
    task_id, duty_id = task_prefix + mission.id, duty_prefix + mission.id
    if htn.latest_task_semantics(task_id) is not None:
        return
    world = world_factory(loop, mission)
    definition = next(t for t in world.catalog.task_types() if t.task_type_ref.id == root_type)
    parameters = {"goal": mission.goal}
    binding = TaskSemanticBindingV1(
        task_id=TaskRef(task_id), obligation_id=ObligationId(duty_id), contract_revision=ContractRevision(1),
        contract_hash=content_hash_of({"task_type": definition.to_json(), "parameters": parameters}),
        form=definition.form, goal_signature=definition.goal_signature, typed_parameters=parameters,
        output_ports=definition.output_ports, requirement_refs=criterion_ids(mission), semantic_scope="mission",
    )
    # Root requirements are reviewed over accepted contributions. Concrete file/pytest
    # statements are projected to leaf checks by the materializer; naming those checks as
    # root executions would require invented receipts.
    requirements = user_requirements(mission, principal)
    existing = htn.latest_requirements_revision(mission.id)
    if existing is not None and (existing.revision != 1 or existing.content_hash() != requirements.content_hash()):
        raise RuntimeError("root requirements already exist with a different body")
    with loop.store.transaction():
        ObligationStore(loop.store).register(Obligation(
            obligation_id=ObligationId(duty_id), mission_id=mission.id,
            requirement_refs=binding.requirement_refs, goal_signature_id=definition.goal_signature.signature_id,
        ), recursion_fuel=ROOT_RECURSION_FUEL)
        loop.commit.admit_obligation_demand(mission.id, ObligationId(duty_id),
            principal=principal.principal_id, requester={"kind": "mission_root"},
            evidence={"mission_id": mission.id, "requirement_refs": list(binding.requirement_refs)})
        htn.put_task_semantics(mission.id, binding)
        if existing is None:
            htn.insert_requirements_revision(requirements)


def install_planning(loop: Any, world_factory: Callable[[Any, Any], Any]) -> None:
    """Install the deployment's per-Mission planning world; a Mission starts planning once
    its completion mapping is confirmed."""
    from ..orchestrator.operation_completion import (
        OperationCompletionError,
        OperationCompletionReader,
    )

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

    loop.install_hierarchical_deployment(lambda mission: world_factory(loop, mission), start_gate=ready)


__all__ = ("ROOT_RECURSION_FUEL", "criterion_ids", "initialize_root", "install_planning", "user_requirements")
