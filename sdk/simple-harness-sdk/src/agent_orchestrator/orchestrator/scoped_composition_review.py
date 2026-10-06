# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Reconstruct a compound's frozen content projection from persisted authority."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.htn import TaskForm
from ..contracts.operation_completion import OccurrenceCompletionScopeV1, PlanRevisionPinV1
from ..contracts.resolution import (
    AllExpr,
    Criterion,
    CriterionExpr,
    CriterionOrigin,
    EvaluationKind,
    RequiredEvidencePolicy,
    RequirementClass,
    RequirementsRevision,
)
from ..storage.htn_store import HtnStore
from ..storage.store import Store
from .operation_completion import OperationCompletionError, OperationCompletionReader


@dataclass(frozen=True, slots=True)
class ScopedCompositionProjection:
    requirements: RequirementsRevision
    scope: OccurrenceCompletionScopeV1
    criteria: tuple[Criterion, ...]
    expression: CriterionExpr | AllExpr


def read_compound_projection(
    store: Store, mission_id: str, occurrence_id: str, task_id: str
) -> ScopedCompositionProjection:
    """Read one compound projection; callers cannot supply its criteria."""

    with store.read_view():
        htn = HtnStore(store)
        active = htn.active_plan_revision(mission_id)
        if active is None:
            raise OperationCompletionError("OP_EFFECT_SCOPE_STALE", "no active Plan")
        members = [
            item
            for item in htn.list_plan_memberships(mission_id, active.revision)
            if str(item.occurrence_id) == str(occurrence_id)
        ]
        binding = htn.task_semantics_of(mission_id, task_id)
        if (
            len(members) != 1
            or str(members[0].task_id) != task_id
            or binding is None
            or binding.form is not TaskForm.COMPOUND
        ):
            raise OperationCompletionError(
                "OP_COMPLETION_SCOPE_UNRESOLVED", "no exact planned compound Task"
            )
        reader = OperationCompletionReader(store)
        scope = reader.read_scope(
            mission_id,
            PlanRevisionPinV1(revision=int(active.revision), snapshot_hash=active.snapshot_hash),
            str(occurrence_id),
        )
        requirements = htn.get_requirements_revision(
            mission_id, int(scope.requirements_ref.revision)
        )
        root_catalogue = {item.criterion_id: item for item in requirements.criteria}
        declared_local = set(binding.goal_signature.coverage_criteria) - set(root_catalogue)
        criteria: list[Criterion] = []
        for criterion_id in scope.content_criterion_ids:
            if criterion_id in root_catalogue:
                criteria.append(root_catalogue[criterion_id])
                continue
            if criterion_id not in declared_local:
                raise OperationCompletionError(
                    "OP_COMPLETION_SCOPE_UNRESOLVED",
                    "compound Scope contains no Task-declared local criterion",
                )
            criteria.append(
                Criterion(
                    criterion_id=criterion_id,
                    revision=1,
                    origin=CriterionOrigin.DERIVED,
                    statement=(
                        f"{criterion_id} is covered by the accepted children of {occurrence_id}"
                    ),
                    requirement_class=RequirementClass.REQUIRED_OUTCOME,
                    evaluation_kind=EvaluationKind.SEMANTIC,
                    required_evidence_policy=RequiredEvidencePolicy(
                        required_check_ids=(), independence_required=False
                    ),
                )
            )
        if not criteria:
            raise OperationCompletionError(
                "OP_COMPLETION_SCOPE_UNRESOLVED", "compound Scope has no content projection"
            )
        frozen_criteria = tuple(criteria)
        expression: CriterionExpr | AllExpr = CriterionExpr(frozen_criteria[0].criterion_id)
        if len(frozen_criteria) > 1:
            expression = AllExpr(
                children=tuple(CriterionExpr(item.criterion_id) for item in frozen_criteria)
            )
        return ScopedCompositionProjection(requirements, scope, frozen_criteria, expression)


def root_content_projection(
    requirements: RequirementsRevision, scope: OccurrenceCompletionScopeV1
) -> ScopedCompositionProjection:
    """任务根的**内容**投影：根终审（MISSION_FINAL）判的就是它（Assurance 1.1 §7.2，2026-10-06 车道 O）。

    根要求经人确认的完成映射分成两份：内容判据与效果判据。每项效果只判一次——由它自己的结果审阅
    （OPERATION_OUTCOME）判，效果验收就是它的证明——所以根终审只判内容判据，效果的状态随审查包
    作为事实给审阅员看，收敛与否由收尾核对。公式是内容判据的 ALL（部署给任务要求的也是 ALL）。
    完成映射一条内容判据都没有（只有动作的任务）时，根终审仍判整份要求——根绑定对这种任务也是
    "全部判据都算内容"（``deployment/root.root_binding``）。
    """
    catalogue = {item.criterion_id: item for item in requirements.criteria}
    content_ids = tuple(scope.content_criterion_ids)
    missing = [criterion_id for criterion_id in content_ids if criterion_id not in catalogue]
    if missing:
        raise OperationCompletionError(
            "OP_COMPLETION_SCOPE_UNRESOLVED",
            "root Scope names content criteria the requirements do not hold: " + ", ".join(missing),
        )
    if not content_ids:
        return ScopedCompositionProjection(
            requirements, scope, tuple(requirements.criteria), requirements.success_expression
        )
    criteria = tuple(catalogue[criterion_id] for criterion_id in content_ids)
    expression: CriterionExpr | AllExpr = CriterionExpr(criteria[0].criterion_id)
    if len(criteria) > 1:
        expression = AllExpr(children=tuple(CriterionExpr(item.criterion_id) for item in criteria))
    return ScopedCompositionProjection(requirements, scope, criteria, expression)


__all__ = ("ScopedCompositionProjection", "read_compound_projection", "root_content_projection")
