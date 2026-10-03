# SPDX-License-Identifier: Apache-2.0
"""用户任务的根：要求书、根任务初始化、开工条件（2026-10-03 从 Host 搬入，HTN 补齐阶段 A′）。

只有一份要求书构造（``user_requirements``）：保证通道的建任务工厂与根初始化逐字节一致，谁都
不写第二份。规划世界（桌面类型等产品领域知识）由部署传入 ``world_factory``，根目标的类型名与
任务号前缀也由部署给出；这些名字是已有任务的身份，部署原样沿用。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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


def build_requirements(mission_id: str, revision: int, entries: Any, principal_id: str,
                       credential: str | None = None) -> RequirementsRevision:
    """The one rule for a user's requirements: ``req-<mission>-<n>``, each entry
    ``(criterion id, entry revision, statement)`` a required outcome, combined with "all of"
    (also for a single one).  Revision 1 and every amendment are built here."""

    criteria = tuple(Criterion(identifier, int(entry_revision), CriterionOrigin.USER_EXPLICIT, statement,
        RequirementClass.REQUIRED_OUTCOME, EvaluationKind.SEMANTIC)
        for identifier, entry_revision, statement in entries)
    return RequirementsRevision(
        revision_id=f"req-{mission_id}-{int(revision)}", mission_id=mission_id, revision=int(revision),
        criteria=criteria,
        success_expression=AllExpr(tuple(CriterionExpr(c.criterion_id) for c in criteria)),
        authority_subject=principal_id, amendment_credential_ref=credential,
    )


def user_requirements(mission: Any, principal: Any) -> RequirementsRevision:
    """Revision 1 of the user's explicit criteria, from the charter: ``c-user-<n>`` by position."""

    return build_requirements(
        mission.id, 1,
        tuple((identifier, 1, statement)
              for identifier, statement in zip(criterion_ids(mission), mission.success_criteria, strict=True)),
        principal.principal_id)


def current_criteria(store: Any, mission: Any) -> tuple[tuple[str, str], ...]:
    """``(criterion id, statement)`` of the Mission's requirements as they stand: the latest
    requirements revision.  Before revision 1 is written (inside the create transaction) the
    charter is the only input, numbered the way revision 1 will number it."""

    latest = HtnStore(store).latest_requirements_revision(mission.id)
    if latest is None:
        return tuple(zip(criterion_ids(mission), mission.success_criteria, strict=True))
    return tuple((str(item.criterion_id), str(item.statement)) for item in latest.criteria)


def current_statements(store: Any, mission: Any) -> tuple[str, ...]:
    """The statements of the Mission's requirements as they stand, in order."""
    return tuple(statement for _, statement in current_criteria(store, mission))


def apply_changes(previous: RequirementsRevision, changes: Any, *,
                  highest_used: int) -> tuple[tuple[str, int, str], ...]:
    """The entries of the next revision: untouched entries carried as they are, a rewrite
    keeps its id and raises the entry revision, a removal drops the entry, an addition gets
    ``c-user-<highest_used + 1>`` — ``highest_used`` is the highest number any revision of this
    Mission ever used, so an id is never reused.  Raises ``ValueError``
    naming what is wrong (``AMEND_EMPTY`` / ``AMEND_UNKNOWN_CRITERION`` / ``AMEND_DUPLICATE``)."""

    if not changes:
        raise ValueError("AMEND_EMPTY: no change was given")
    entries = {str(item.criterion_id): (int(item.revision), str(item.statement)) for item in previous.criteria}
    order = [str(item.criterion_id) for item in previous.criteria]
    touched: set[str] = set()
    highest = int(highest_used)
    for change in changes:
        if not isinstance(change, Mapping) or change.get("op") not in {"add", "rewrite", "remove"}:
            raise ValueError("AMEND_EMPTY: each change is {op: add|rewrite|remove, ...}")
        operation = change["op"]
        statement = str(change.get("statement") or "").strip()
        if operation == "add":
            if set(change) != {"op", "statement"} or not statement:
                raise ValueError("AMEND_EMPTY: an addition is {op, statement}")
            highest += 1
            name = f"c-user-{highest}"
            entries[name] = (1, statement)
            order.append(name)
            continue
        name = str(change.get("criterion_id") or "")
        if name not in entries or name in touched:
            raise ValueError(f"AMEND_UNKNOWN_CRITERION: {name!r} is not a current requirement"
                             " (or is changed twice)")
        touched.add(name)
        if operation == "remove":
            if set(change) != {"op", "criterion_id"}:
                raise ValueError("AMEND_EMPTY: a removal is {op, criterion_id}")
            del entries[name]
            order.remove(name)
        else:
            if set(change) != {"op", "criterion_id", "statement"} or not statement:
                raise ValueError("AMEND_EMPTY: a rewrite is {op, criterion_id, statement}")
            if statement == entries[name][1]:
                raise ValueError(f"AMEND_DUPLICATE: {name!r} already says exactly that")
            entries[name] = (entries[name][0] + 1, statement)
    if not order:
        raise ValueError("AMEND_EMPTY: a Mission keeps at least one requirement")
    statements = [entries[name][1] for name in order]
    if len(set(statements)) != len(statements):
        raise ValueError("AMEND_DUPLICATE: two requirements would say the same thing")
    return tuple((name, entries[name][0], entries[name][1]) for name in order)


def goal_parameters(mission: Any) -> dict[str, Any]:
    """The product's root parameters: the user's goal, verbatim."""
    return {"goal": mission.goal}


def initialize_root(
    loop: Any, mission: Any, principal: Any, *,
    world_factory: Callable[[Any, Any], Any], root_type: str, task_prefix: str, duty_prefix: str,
    root_parameters: Callable[[Any], dict[str, Any]] = goal_parameters,
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
    parameters = root_parameters(mission)
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


__all__ = ("ROOT_RECURSION_FUEL", "apply_changes", "build_requirements", "criterion_ids", "current_criteria", "current_statements", "goal_parameters", "initialize_root", "install_planning", "user_requirements")
