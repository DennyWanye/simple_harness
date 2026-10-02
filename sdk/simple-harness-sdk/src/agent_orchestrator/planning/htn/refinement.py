# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The planning frontier and the evidence it is waiting for (§7.2, TG §6, ADR-07).

Which method refines a goal is the planner's judgement — an LLM names it in a
``RefineOperation`` and the compiler (:mod:`.compiler`) checks and grounds it.
This module keeps only the two orderly questions the Harness answers itself:

:func:`planning_frontier`
    which compound occurrences of the adopted plan no method has refined yet;
:func:`unknown_predicates` / :func:`evidence_requests`
    which atoms of a condition are UNKNOWN right now, and which registered
    read-only observer occurrence could answer each one.  An unknown precondition
    is a reason to *look*, never a reason to dispatch the real work and see what
    happens (ADR-07).

Nothing here calls a model, writes a store or dispatches anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...contracts.evidence_state import EvidenceSnapshot, TruthValue
from ...contracts.htn import (
    ObligationId,
    OccurrenceId,
    OccurrenceSpec,
    Requiredness,
    TaskForm,
    TaskRef,
    TaskSemanticBindingV1,
)
from ...contracts.semantic_base import VersionedRef, content_hash_of
from ...graph.task_network import TaskNetworkSnapshot
from ...knowledge.predicates import PredicateRegistry
from .applicability import evaluate_condition
from .grounding import derive_id
from .registry import TaskTypeCatalog, TaskTypeSpec, iter_predicates


@dataclass(frozen=True, slots=True)
class FrontierItem:
    """One open position the planner may work on next (implementation design §6)."""

    occurrence_id: OccurrenceId
    task_id: TaskRef
    obligation_id: ObligationId
    bindings: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, spec: OccurrenceSpec, **bindings: Any) -> FrontierItem:
        return cls(
            occurrence_id=spec.occurrence_id,
            task_id=spec.task_id,
            obligation_id=spec.obligation_id,
            bindings=dict(bindings),
        )


def planning_frontier(network: TaskNetworkSnapshot) -> tuple[FrontierItem, ...]:
    """Compound occurrences in the adopted plan that no method has refined yet.

    The *planning* frontier of implementation design §6 — deliberately not the
    execution frontier, which is ``graph/eligibility.py``'s job (P2.1c) and answers
    a different question with different inputs.
    """

    projection = network.execution_projection()
    out: list[FrontierItem] = []
    for occurrence_id in sorted(projection.projected_occurrences, key=str):
        spec = network.occurrence(occurrence_id)
        if spec.form is not TaskForm.COMPOUND:
            continue
        if network.adopted_instance_for(occurrence_id) is not None:
            continue
        out.append(FrontierItem.of(spec))
    return tuple(out)


# --------------------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceOccurrenceRequest:
    """A read-only occurrence that would answer one UNKNOWN proposition.

    §7.2 / ADR-07: an unknown precondition is a reason to *look*, never a reason to
    dispatch the real work and see what happens.  The occurrence it proposes is
    always a read-only observer type, so producing the evidence cannot change the
    answer.
    """

    proposition_key: str
    predicate_ref: VersionedRef
    for_occurrence: OccurrenceId
    observer: TaskTypeSpec | None
    occurrence: OccurrenceSpec | None
    binding: TaskSemanticBindingV1 | None
    reason: str

    @property
    def satisfiable(self) -> bool:
        """Is there a registered observer that could answer this at all?"""

        return self.observer is not None


@dataclass(frozen=True, slots=True)
class UnknownProposition:
    proposition_key: str
    predicate_ref: VersionedRef
    truth: TruthValue


def unknown_predicates(
    conditions: Sequence[Any],
    *,
    parameters: Mapping[str, Any],
    predicates: PredicateRegistry,
    snapshot: EvidenceSnapshot,
    now_ms: int | None = None,
) -> tuple[UnknownProposition, ...]:
    """The atoms of ``conditions`` whose truth is UNKNOWN right now.

    Evaluated atom by atom rather than read off the aggregate: the aggregate says
    the expression is UNKNOWN, and what an evidence request needs is *which*
    proposition to go and observe.
    """

    out: list[UnknownProposition] = []
    seen: set[str] = set()
    for atom in iter_predicates(conditions):
        evaluation = evaluate_condition(
            atom,
            registry=predicates,
            snapshot=snapshot,
            parameters=parameters,
            now_ms=now_ms,
            path="precondition",
        )
        if evaluation.truth is not TruthValue.UNKNOWN:
            continue
        keys = evaluation.unknown_propositions or (
            f"{atom.predicate_ref.id}@{atom.predicate_ref.version}#unresolved",
        )
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            out.append(
                UnknownProposition(
                    proposition_key=key,
                    predicate_ref=atom.predicate_ref,
                    truth=evaluation.truth,
                )
            )
    return tuple(out)


def evidence_requests(
    unknowns: Sequence[UnknownProposition],
    *,
    for_occurrence: OccurrenceId,
    obligation_id: ObligationId,
    catalog: TaskTypeCatalog,
    semantic_scope: str,
    contract_revision: int = 1,
) -> tuple[EvidenceOccurrenceRequest, ...]:
    """Turn UNKNOWN propositions into read-only occurrences that could answer them."""

    out: list[EvidenceOccurrenceRequest] = []
    for unknown in unknowns:
        observers = catalog.observers_for(unknown.predicate_ref)
        observer = observers[0] if observers else None
        if observer is None:
            out.append(
                EvidenceOccurrenceRequest(
                    proposition_key=unknown.proposition_key,
                    predicate_ref=unknown.predicate_ref,
                    for_occurrence=for_occurrence,
                    observer=None,
                    occurrence=None,
                    binding=None,
                    reason=(
                        f"no registered read-only observer can produce "
                        f"{unknown.predicate_ref.id!r}; the precondition stays UNKNOWN "
                        "(ADR-07: that is not a FALSE)"
                    ),
                )
            )
            continue
        occurrence_id = OccurrenceId(
            derive_id("occ", "evidence", for_occurrence, unknown.proposition_key)
        )
        task_id = TaskRef(derive_id("task", "evidence", for_occurrence, unknown.proposition_key))
        duty = ObligationId(derive_id("obl", "evidence", obligation_id, unknown.proposition_key))
        binding = TaskSemanticBindingV1(
            task_id=task_id,
            obligation_id=duty,
            contract_revision=contract_revision,  # type: ignore[arg-type]
            contract_hash=content_hash_of(
                {
                    "observer": observer.task_type_ref.to_json(),
                    "proposition": unknown.proposition_key,
                }
            ),
            form=TaskForm.PRIMITIVE,
            goal_signature=observer.goal_signature,
            typed_parameters={"proposition_key": unknown.proposition_key},
            input_ports=observer.input_ports,
            output_ports=observer.output_ports,
            operator_ref=observer.operator_ref,
            semantic_scope=semantic_scope,
            capability_requirements=observer.required_capabilities,
            resource_reads=observer.resource_reads,
            resource_writes=(),
            side_effect_kind=observer.side_effect_kind,
        )
        out.append(
            EvidenceOccurrenceRequest(
                proposition_key=unknown.proposition_key,
                predicate_ref=unknown.predicate_ref,
                for_occurrence=for_occurrence,
                observer=observer,
                occurrence=OccurrenceSpec(
                    occurrence_id=occurrence_id,
                    task_id=task_id,
                    obligation_id=duty,
                    form=TaskForm.PRIMITIVE,
                    requiredness=Requiredness.CONDITIONAL,
                ),
                binding=binding,
                reason=(
                    f"observe {unknown.predicate_ref.id!r} before deciding; the observer is "
                    "read-only, so looking cannot change the answer"
                ),
            )
        )
    return tuple(out)


__all__ = (
    "EvidenceOccurrenceRequest",
    "FrontierItem",
    "UnknownProposition",
    "evidence_requests",
    "planning_frontier",
    "unknown_predicates",
)
