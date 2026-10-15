# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Module-level definitions of the hierarchical dispatcher: plan views, outcomes, refusals and event helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from ...artifacts.input_bindings import AcceptedOutputsIndex, ResolutionResult
from ...contracts import ContractError, Event, TaskStatus, ids
from ...contracts.error_table import RoundFaultCode
from ...contracts.evidence_state import ValidityWitness
from ...contracts.htn import OccurrenceId, OccurrenceSpec, TaskForm, TaskSemanticBindingV1
from ...contracts.resolution import (
    CriterionVerdict,
    RequirementsRevision,
    ResolutionCriterion,
    ReviewPackage,
    ReviewRecord,
)
from ...contracts.semantic_base import (
    EvidenceRef,
    EvidenceRefKind,
    TypedRef,
    TypedRefKind,
    content_hash_of,
)
from ...graph.eligibility import (
    ActivePlanView,
    EligiblePrimitiveTask,
    ExecutionFrontier,
    OccurrenceOutcome,
    PlanningFrontier,
    ReadinessReason,
    ReadinessReport,
    TaskView,
)
from ...graph.projection_validation import GraphIntegrityError
from ...graph.task_network import GATING_REQUIREDNESS, TaskNetworkSnapshot
from ...planning.htn.grounding import SharedGoalEntry, SharingSignature
from ...storage.htn_store import PlanCommitReceipt
from ..plan_commits import HIERARCHICAL_SEMANTICS, semantics_of

if TYPE_CHECKING:
    from ...contracts import Mission
    from ...storage.store import Store


#: The two event types this module appends.  Both are *new* names: §18.5 rule 3
#: lets the hierarchical mode add event types and forbids it to change the bytes
#: of an existing one, so nothing here is ever written on a legacy Mission.
PLAN_COMMIT_REFUSED = "PlanCommitRefused"
PLAN_INTEGRITY_FAILED = "PlanIntegrityFailed"
DISPATCH_INTERCEPTED = "HierarchicalDispatchIntercepted"
COMPOUND_PHASE_CHANGED = "CompoundPhaseChanged"
#: P2.3c part 2: one occurrence was *not* dispatched this cycle, and the structured
#: reason why.  A separate name from ``DISPATCH_INTERCEPTED`` because that one is the
#: safety refusal of a compound and this one is the ordinary "not yet" of the readiness
#: gate; an operator reading a Mission that is not moving has to be able to tell
#: "the plan is wrong" from "the producer has not finished".
DISPATCH_WITHHELD = "HierarchicalDispatchWithheld"
#: P2.3c part 2: the Mission's root GoalResolution was offered and refused.  Recorded
#: because "the Mission did not complete" is not a diagnosis — which rule refused it is.
ROOT_RESOLUTION_REFUSED = "RootGoalResolutionRefused"
#: P2.3c part 2c (review F1): a hierarchical Mission reached the scheduler on a
#: deployment that never installed this assembly.  The gates live here and the
#: occurrence rows are written by the Commit Service, so the two can be out of step;
#: when they are, the Mission is refused rather than handed to the legacy allocator,
#: and this is the record of the refusal.  One event per Mission — the condition is a
#: deployment fact, so it does not change from cycle to cycle and repeating it every
#: cycle would drown the log it is supposed to explain.
ASSEMBLY_MISSING = "HierarchicalAssemblyMissing"
#: P2.3c part 2c: the loop went idle while this Mission still had occurrences every
#: gate withheld.  It is a *record*, not a verdict: the Mission is left exactly where
#: it is (no status change, nothing cancelled), because "this process has nothing left
#: to do" and "this Mission can never progress" are different statements and only the
#: first one is known here — a demand admitted, an approval granted or an observation
#: recorded from outside would make the very same plan runnable.  What it does end is
#: the silence: the withheld reasons are written down where an operator reads them
#: rather than left to be re-derived from a Mission that simply stopped moving.
MISSION_STALLED = "HierarchicalMissionStalled"
#: P2.3c part 2c: a licence could not be recorded because the consumer already holds a
#: START row for this scope epoch and support revision.  It is a *storage* limit, not a
#: judgement about the work — and it is recorded rather than swallowed, because the
#: occurrence that needed the licence then waits for a reason nobody could otherwise see.
WITNESS_KEY_TAKEN = "HierarchicalWitnessKeyTaken"

#: P2.3c part 3a.  The root ``MISSION_FINAL`` review package was cut, and an earlier
#: one was retired.  The two names live *here*, beside the reader that has to honour
#: them (:meth:`HierarchicalDispatch.live_root_review_package`), rather than in
#: ``orchestrator.root_review`` which writes them: "which review does the root
#: resolve from" is a question this module already answers, and an answer that could
#: not see a supersede record would resolve from an anchor the world has moved past.
ROOT_REVIEW_CUT = "HierarchicalRootReviewCut"
ROOT_REVIEW_SUPERSEDED = "HierarchicalRootReviewSuperseded"
#: G1 (Host acceptance runner): why each registered method was refused for each still
#: open goal, at the plan revision the Planner was asked against.  The four-axis
#: report was computed for the *prompt* and thrown away, so after a run nobody could
#: say why a method had not been chosen — the runner had to re-derive it with a probe.
#: One record per ``(mission, plan_revision)`` while the library is unchanged.  A method
#: the Planner proposes joins the library *without* moving the plan revision, so the key
#: grows ``:library:{n}`` with the number of proposals; otherwise the round after a
#: proposal would reuse the assessment made before it.
METHOD_APPLICABILITY_ASSESSED = "MethodApplicabilityAssessed"
# H4: the adapter result is a durable handoff record.  The model/compiler may act
# later, but the trigger, program-computed impact and admitted action survive a
# process restart as one idempotent event.
REPAIR_DECISION_DISPATCHED = "RepairDecisionDispatched"

#: How many refusals one :data:`METHOD_APPLICABILITY_ASSESSED` payload carries.  Far
#: larger than the prompt's own cap (that one protects the model's attention; this one
#: only stops a pathological registry from writing an unbounded row), and a payload
#: that hits it says so in ``truncated`` instead of quietly ending.
MAX_RECORDED_REFUSALS = 200

#: Readiness answers that mean "the planner still owes something about the facts
#: or the authority", as opposed to "a method has not been chosen yet".
_WAIT_REASONS: frozenset[ReadinessReason] = frozenset(
    {
        ReadinessReason.WAITING_EVIDENCE,
        ReadinessReason.OBSERVER_UNAVAILABLE,
        ReadinessReason.VALIDITY_RECHECK_PENDING,
        ReadinessReason.WAITING_APPROVAL,
    }
)


def is_hierarchical(mission: Mission) -> bool:
    """Whether one Mission runs under the new semantics (§18.5 rule 1)."""

    return semantics_of(mission) == HIERARCHICAL_SEMANTICS


class WriteConflictPending(ContractError):
    """A step would start from a file the plan lets several versions of reach it — unordered
    producers wrote it differently, or ordered/relayed versions arrive with no proof of which is
    the latest, or two port documents land on one path; it waits for the Planner's answer to the
    write-conflict repair request (阶段 D；2026-10-09 第 4 条)."""

    def __init__(self, conflicts: Sequence[Mapping[str, Any]]) -> None:
        self.conflicts = tuple(dict(item) for item in conflicts)
        super().__init__(
            "WRITE_CONFLICT: "
            + "; ".join(
                f"{item['path']} written by {' and '.join(str(step) for step in item['steps'])}"
                + (f" (both reach {item['consumer']})" if item.get("consumer") else "")
                for item in self.conflicts
            )
        )


class PlanIntegrityError(GraphIntegrityError):
    """Corruption that is *not* a cycle: the plan's meaning is incomplete (§18.5).

    A :class:`~..graph.projection_validation.GraphIntegrityError` so that every
    caller which already stops the scope on a damaged projection stops on this too
    (§24.1 decision 11).  Its message is its own, because the base class's
    "N node(s) could not be ordered; cycle: unknown" would describe the wrong
    defect: nothing here is unordered — a *meaning* is absent, and telling an
    operator to look for a cycle would send them to the wrong place.
    """

    def __init__(self, mission_id: str, code: str, subjects: Sequence[str], message: str) -> None:
        self.mission_id = str(mission_id)
        # 只收登记过的一轮故障码（错误码表 RoundFaultCode）：没登记的当场报错
        self.code = RoundFaultCode(code)
        self.subjects = tuple(sorted(str(item) for item in subjects))
        self._message = str(message)
        super().__init__(self.subjects, ())

    def __str__(self) -> str:
        return self._message

    def diagnose(self) -> str:
        """A description a person can act on.  Never an order a machine can run."""

        return self._message


def missing_bindings(mission_id: str, task_ids: Sequence[str]) -> PlanIntegrityError:
    """§18.5: a Task in the plan with no ``TaskSemanticBindingV1`` is corruption."""

    named = sorted(str(item) for item in task_ids)
    return PlanIntegrityError(
        mission_id,
        "semantic_binding_missing",
        [f"task:{item}" for item in named],
        f"graph integrity failure in mission {mission_id}: {len(named)} task(s) in the plan "
        f"carry no semantic binding [{', '.join(named) or 'none recorded'}]. In the "
        "hierarchical mode that is corruption and not a legacy fallback (§18.5); the plan "
        "cannot be executed until a binding exists for every member.",
    )


def root_not_identified(mission_id: str, task_ids: Sequence[str]) -> PlanIntegrityError:
    """No plan revision, and not exactly one root goal to seed one from."""

    named = sorted(str(item) for item in task_ids)
    return PlanIntegrityError(
        mission_id,
        "root_not_identified",
        [f"task:{item}" for item in named],
        f"graph integrity failure in mission {mission_id}: a Mission with no plan revision "
        f"seeds its network from exactly one root goal, and this one has {len(named)} "
        f"semantic binding(s) [{', '.join(named) or 'none'}]. Choosing one of several would "
        "be this module inventing a root; none at all is the missing-binding corruption.",
    )


# --------------------------------------------------------------------------------------
# compound phases (TG §7)
# --------------------------------------------------------------------------------------


class CompoundPhase(StrEnum):
    """TG §7's typed phase of a compound task.

    The coarse ``TaskStatus`` stays as display; this is the transition function
    ``next_compound_task`` the annex asks for.  It exists so a compound can be
    planned and accepted *without* a Worker Attempt: none of these transitions
    needs one, and creating a fake one to satisfy the old state machine is the
    exact bug TG §7 forbids.
    """

    PLANNING_READY = "planning_ready"
    REFINING = "refining"
    WAITING_CHILDREN = "waiting_children"
    COMPOSITION_REVIEW = "composition_review"
    RESOLUTION_COMMITTED = "resolution_committed"
    EVIDENCE_OR_AUTHORITY_WAIT = "evidence_or_authority_wait"


#: TG §7's display mapping.  Read by an operator and by the API; never by a gate.
COMPOUND_DISPLAY_STATUS: Mapping[CompoundPhase, TaskStatus] = {
    CompoundPhase.PLANNING_READY: TaskStatus.READY,
    CompoundPhase.REFINING: TaskStatus.ACTIVE,
    CompoundPhase.WAITING_CHILDREN: TaskStatus.ACTIVE,
    CompoundPhase.COMPOSITION_REVIEW: TaskStatus.VERIFYING,
    CompoundPhase.RESOLUTION_COMMITTED: TaskStatus.COMPLETED,
    CompoundPhase.EVIDENCE_OR_AUTHORITY_WAIT: TaskStatus.BLOCKED,
}


def next_compound_phase(
    occurrence: OccurrenceSpec,
    network: TaskNetworkSnapshot,
    report: ReadinessReport,
    *,
    child_outcomes: Mapping[OccurrenceId, OccurrenceOutcome],
    resolved: bool,
) -> CompoundPhase:
    """The phase of one compound occurrence, from typed inputs only (TG §7).

    Deliberately total and deliberately ignorant of ``TaskStatus``: the inputs are
    the occurrence's membership, the adopted method instance, the readiness verdict
    and the children's outcomes.  A caller that passes a legacy status string in
    gets the same answer, which is the property the suite pins down.
    """

    if not isinstance(occurrence, OccurrenceSpec):
        raise ContractError("next_compound_phase expects an OccurrenceSpec")
    if occurrence.form is not TaskForm.COMPOUND:
        raise ContractError(
            f"{occurrence.occurrence_id!s} is {occurrence.form!s}; only a compound occurrence "
            "has a typed phase (a primitive is driven by its Attempts, TG §7)"
        )
    if resolved:
        # An adopted GoalResolution is the end of the compound, whatever else is
        # still open: TG §7 says a COMPLETED compound does not go back.
        return CompoundPhase.RESOLUTION_COMMITTED
    if report.reason in _WAIT_REASONS:
        return CompoundPhase.EVIDENCE_OR_AUTHORITY_WAIT
    adopted = network.adopted_instance_for(occurrence.occurrence_id)
    if adopted is None:
        return CompoundPhase.PLANNING_READY
    children = network.adopted_children(occurrence.occurrence_id)
    known = {spec.occurrence_id for spec in network.occurrences}
    if any(binding.occurrence_id not in known for binding in children):
        # A slot the adopted method opened whose occurrence is not in the plan yet:
        # the refinement is committed but not fully expanded.
        return CompoundPhase.REFINING
    gating = [
        binding.occurrence_id for binding in children if binding.requiredness in GATING_REQUIREDNESS
    ]
    if any(
        child_outcomes.get(child, OccurrenceOutcome.UNKNOWN) is not OccurrenceOutcome.ACCEPTED
        for child in gating
    ):
        return CompoundPhase.WAITING_CHILDREN
    # TG §6.2 / §24.1 decision 2: the composition review waits for the *children*,
    # not for the parent, which is what makes it terminate at all.
    return CompoundPhase.COMPOSITION_REVIEW


# --------------------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanRefusal:
    """One refused delivery of a plan commit, kept so the round can explain itself."""

    attempt: int
    reason: str
    detail: str
    recompilable: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "reason": self.reason,
            "detail": self.detail,
            "recompilable": self.recompilable,
        }


@dataclass(frozen=True, slots=True)
class PlanRoundOutcome:
    """What one Planner reply achieved: a receipt, or a named sequence of refusals."""

    proposal_id: str
    receipt: PlanCommitReceipt | None = None
    refusals: tuple[PlanRefusal, ...] = ()

    @property
    def committed(self) -> bool:
        return self.receipt is not None

    @property
    def attempts(self) -> int:
        return len(self.refusals) + (1 if self.committed else 0)

    @property
    def last_reason(self) -> str | None:
        return self.refusals[-1].reason if self.refusals else None


@dataclass(frozen=True, slots=True)
class DispatchInterception:
    """A compound that the old READY entry would have dispatched (§18.5 rule 4)."""

    task_id: str
    occurrence_id: str | None
    reason: str
    explanation: str


@dataclass(frozen=True, slots=True)
class RootResolutionInputs:
    """The read facts one Mission-root ``GoalResolution`` command is built from.

    ``reason`` non-empty means the facts are *not* complete and names which one is
    missing.  A missing review anchor is reported rather than fabricated: the root
    resolution is formed out of the success formula plus the final acceptance, so a
    system that writes its own review package has decided the answer it was meant to
    check (§6.3, §8.1, AER §6.1).
    """

    reason: str = ""
    detail: str = ""
    occurrence_id: str = ""
    task_id: str = ""
    obligation_id: str = ""
    contract_revision: int = 1
    method_instance_id: str | None = None
    requirements: RequirementsRevision | None = None
    package: ReviewPackage | None = None
    record: ReviewRecord | None = None
    contributions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: ``primitive`` / ``compound`` form of the root occurrence (read, never assumed).
    root_form: str = ""

    @property
    def complete(self) -> bool:
        return not self.reason


@dataclass(frozen=True, slots=True)
class RootResolutionOutcome:
    """Whether the Mission's root resolution was formed this cycle, and why not."""

    committed: bool
    reason: str = ""
    detail: str = ""
    resolution_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "committed": self.committed,
            "reason": self.reason,
            "detail": self.detail,
            "resolution_id": self.resolution_id,
        }


@dataclass(frozen=True, slots=True)
class DispatchRefusal:
    """Why one occurrence is not competing for a Worker this cycle.

    The reason is a :class:`~..graph.eligibility.ReadinessReason` and the detail codes
    are the gate's own, unmerged: "the producer has not finished" (``WAITING_DATA``),
    "we could not ask" (``OBSERVER_UNAVAILABLE``) and "this compound still needs a
    method" (``NEEDS_REFINEMENT``) call for three different repairs, and the scheduler
    is the wrong place to collapse them into "not ready".
    """

    task_id: str
    occurrence_id: str
    reason: ReadinessReason
    detail_codes: tuple[str, ...] = ()
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "occurrence_id": self.occurrence_id,
            "reason": str(self.reason),
            "detail_codes": list(self.detail_codes),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class DispatchAdmissions:
    """What :func:`~..scheduling.allocator.allocate_v2` is handed for one Mission.

    Three maps and nothing else: the semantic binding of every Task row (its absence
    is corruption, not a legacy fallback), the admissions
    :func:`~..graph.eligibility.admit_for_dispatch` built for the occurrences that
    passed every gate, and a named refusal for each one that did not.  Holding the
    refusals beside the admissions is the point — a Mission that is not moving has to
    be able to say why without anybody re-deriving it.
    """

    plan_revision: int
    bindings: Mapping[str, TaskSemanticBindingV1] = field(default_factory=dict)
    readiness: Mapping[str, EligiblePrimitiveTask] = field(default_factory=dict)
    refusals: tuple[DispatchRefusal, ...] = ()

    def admission_for(self, task_id: str) -> EligiblePrimitiveTask | None:
        return self.readiness.get(task_id)

    def refusal_for(self, task_id: str) -> DispatchRefusal | None:
        for item in self.refusals:
            if item.task_id == task_id:
                return item
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "plan_revision": int(self.plan_revision),
            "admitted": sorted(self.readiness),
            "refusals": [item.to_json() for item in self.refusals],
        }


@dataclass(frozen=True, slots=True)
class NetworkView:
    """One read of a Mission's plan: the snapshot plus what readiness said about it."""

    network: TaskNetworkSnapshot
    plan: ActivePlanView
    views: Mapping[OccurrenceId, TaskView]
    reports: Mapping[OccurrenceId, ReadinessReport]
    outcomes: Mapping[OccurrenceId, OccurrenceOutcome]
    resolved: frozenset[OccurrenceId] = frozenset()
    #: The acceptance projection and the two witness key spaces this read judged the
    #: plan against (review F8).  They used to be computed inside ``read()`` and
    #: dropped on the floor, so ``admissions()`` read them a *second* time and the
    #: readiness report it reported came from one snapshot while the manifest it
    #: hashed came from another.  Carrying them makes the docstring's "one read" a
    #: fact about the code rather than a claim about it.
    accepted: AcceptedOutputsIndex | None = None
    witnesses: Mapping[str, ValidityWitness] = field(default_factory=dict)
    #: consumer task id → condition digest → the START-precondition licence this read
    #: judged against (P2.3c part 2c; the sibling of ``licences`` on the DATA lane).
    starts: Mapping[str, Mapping[str, ValidityWitness]] = field(default_factory=dict)
    licences: Mapping[str, Mapping[str, ValidityWitness]] = field(default_factory=dict)
    #: The input resolution each report was judged against (NEXT-TG-1.0 §5.2).  An
    #: admission takes *this* resolution; it never resolves the inputs a second time
    #: and never stands an empty manifest in for a missing one.
    resolutions: Mapping[OccurrenceId, ResolutionResult] = field(default_factory=dict)

    @property
    def planning_frontier(self) -> PlanningFrontier:
        return PlanningFrontier.compute(self.plan, self.reports)

    @property
    def execution_frontier(self) -> ExecutionFrontier:
        return ExecutionFrontier.compute(self.plan, self.reports)

    def report_for(self, occurrence: OccurrenceId) -> ReadinessReport:
        return self.reports[occurrence]


def _typed(kind: TypedRefKind, identifier: str) -> TypedRef:
    """A typed reference to something this library already holds by id."""

    return TypedRef(
        kind=kind, id=str(identifier), revision=1, content_hash=content_hash_of(str(identifier))
    )


class PlanningWorld(Protocol):
    """The declarations one deployment brings to a compilation.

    A narrow structural type on purpose: this module needs a task-type catalog, a
    schema catalog, a method registry, a predicate registry and the two evidence
    reads — and nothing else.  Anything wider would make the assembly layer depend
    on how a deployment happens to be configured.
    """

    @property
    def catalog(self) -> Any: ...

    @property
    def schemas(self) -> Any: ...

    @property
    def registry(self) -> Any: ...

    @property
    def predicates(self) -> Any: ...

    def snapshot(self) -> Any: ...

    def capabilities(self) -> Any: ...


def _root_criteria(
    criteria: Sequence[Any],
    record: Any,
    *,
    effective_grades: Mapping[str, str] | None = None,
) -> tuple[ResolutionCriterion, ...]:
    """The root resolution's criteria, restated from the review record (review F4).

    The *set* of criteria is the root's content projection (Assurance §7.2: the effect
    criteria are judged by their own outcome reviews and converge in the closeout) —
    that is what the root review judged — and each verdict is the record's own.  ``UNKNOWN`` where the reviewer said
    nothing: it is the enum's word for "not judged", and it is the only honest thing a
    trigger that decides nothing can write.

    ``effective_grades`` (assured lane, handoff item 7) are the current re-decision
    of the bound review manifest that the prepared UseCertificate carries; the
    public ``CriterionOutcome`` projection of an assured record shows a SEMANTIC
    PASS as UNKNOWN, and the resolution restates the manifest, not the projection.
    """

    reviewed = {str(item.criterion_id): item.verdict for item in record.criteria}
    if effective_grades is not None:
        reviewed = {
            **reviewed,
            **{str(name): CriterionVerdict(value) for name, value in effective_grades.items()},
        }
    return tuple(
        ResolutionCriterion(
            criterion_id=item.criterion_id,
            verdict=reviewed.get(str(item.criterion_id), CriterionVerdict.UNKNOWN),
            evidence_refs=tuple(
                EvidenceRef.from_json(ref.to_json())
                for outcome in record.criteria
                if outcome.criterion_id == item.criterion_id
                for ref in outcome.evidence_refs
                if str(ref.kind) in {str(kind) for kind in EvidenceRefKind}
            ),
        )
        for item in criteria
    )


def _same_conclusion(held: ValidityWitness, offered: ValidityWitness) -> bool:
    """Whether two licences over one key say the same thing (review P2-1).

    Only the *verdict* axes, deliberately: the id already pins consumer, purpose,
    scope, epoch, support revision and subject, so what is left to differ is what the
    licence concluded.  ``as_of_ms`` is not on the list — re-reading the same world a
    second later is the same conclusion, and treating it as a contradiction would make
    every honest re-issue an anomaly.
    """

    return (
        held.truth is offered.truth
        and held.decision is offered.decision
        and held.freshness is offered.freshness
        and held.availability is offered.availability
    )


def append_hierarchical_event(
    store: Store,
    event_type: str,
    mission_id: str,
    *,
    key: str,
    payload: Mapping[str, Any],
    task_id: str | None = None,
) -> Event:
    """Append one of this module's event types, keyed for idempotency.

    A module function and not only a method because :data:`ASSEMBLY_MISSING` has to be
    recordable by a caller that has *no* assembly — that is what the event says.
    """

    idempotency_key = f"{event_type}:{key}"
    return store.append_event(
        Event(
            id=ids.event_id(idempotency_key),
            type=event_type,
            trace_id=ids.trace_id(mission_id),
            mission_id=mission_id,
            task_id=task_id,
            attempt_id=None,
            actor_type="system",
            actor_id="orchestrator",
            payload=dict(payload),
            idempotency_key=idempotency_key,
            created_at=store.now,
        )
    )


def record_assembly_missing(store: Store, mission: Mission, *, at: str) -> Event:
    """Refuse-and-record: this Mission runs hierarchically and the gates are not installed.

    Review finding F1.  ``commit_plan_revision`` materialises occurrence rows through
    the Commit Service while every dispatch gate — ``allocate_v2``'s admissions, the
    TG §8.3 re-check, the root ``GoalResolution`` trigger — lives on this assembly and
    is reached only through ``Orchestrator._wiring.hierarchical``.  Nothing couples the two,
    so a deployment that writes hierarchical plans without calling
    ``install_hierarchical`` used to hand those rows to the legacy ``allocate()``:
    dispatch on the ``TaskStatus.READY`` string, a DATA consumer running before its
    producer was accepted, and "every live Task is COMPLETED" standing in for a root
    resolution.  That is the silent half-mode §18.5 rule 1 forbids — half the Mission
    committed under the new rules and half of it scheduled under the old ones.

    The answer is the same one ``require_planning_world`` already gives on the planning
    side: refuse, and say so.  The Mission stays where it is, visibly not moving, with
    one event naming the deployment defect — which is a Mission an operator can fix,
    rather than one that quietly ran under rules nobody chose for it.
    """

    return append_hierarchical_event(
        store,
        ASSEMBLY_MISSING,
        mission.id,
        key=mission.id,
        payload={
            "semantics": semantics_of(mission),
            "at": at,
            "detail": (
                "this Mission runs under hierarchical semantics and no HierarchicalDispatch "
                "is installed on this Orchestrator; the readiness gate, the TG §8.3 dispatch "
                "re-check and the root GoalResolution trigger all live on that assembly, so "
                "nothing is dispatched and nothing is judged (§18.5 rule 1: a Mission does "
                "not run half in each mode).  Call Orchestrator.install_hierarchical()."
            ),
        },
    )


def shared_goal_index(
    network: TaskNetworkSnapshot,
    *,
    catalog: Any,
    exclude_occurrence_ids: Sequence[OccurrenceId] = (),
) -> tuple[SharedGoalEntry, ...]:
    """The occurrences of this network a later slot may name instead of re-doing.

    G2.  Every occurrence that has a semantic binding is listed; nothing here decides
    that two goals *are* one — the Planner names the step it means (TaskGraph 补全第三
    批) and :func:`~..planning.htn.grounding.named_share_refusal` checks the order.  An
    occurrence whose task type this deployment cannot resolve is skipped rather than
    listed under a guess.

    P2.3n: ``exclude_occurrence_ids`` are occurrences that will leave with a
    ``retire_method`` in the same proposal — offering them as share targets would
    ground the replacement against slots the merge then removes.
    """

    skipped = {OccurrenceId(str(item)) for item in exclude_occurrence_ids}
    by_signature = {
        (str(spec.goal_signature.signature_id), int(spec.goal_signature.version)): spec
        for spec in catalog.task_types()
    }
    entries: list[SharedGoalEntry] = []
    for occurrence in network.occurrences:
        if occurrence.occurrence_id in skipped:
            continue
        try:
            binding = network.binding_for_occurrence(occurrence.occurrence_id)
        except (KeyError, ContractError):
            continue
        spec = by_signature.get(
            (str(binding.goal_signature.signature_id), int(binding.goal_signature.version))
        )
        if spec is None:
            continue
        entries.append(
            SharedGoalEntry(
                occurrence_id=occurrence.occurrence_id,
                task_id=binding.task_id,
                obligation_id=binding.obligation_id,
                signature=SharingSignature.of(
                    spec,
                    authority_scope=binding.semantic_scope,
                    semantic_scope=binding.semantic_scope,
                ),
            )
        )
    return tuple(entries)


def criterion_statement(statement: str) -> str:
    """A criterion's text as the Planner reads it when it writes a method.

    2026-09-29 第十三局：写做法的模型不知道发布由系统做，给写文件的步骤都写了"在发布目录中
    给出落点路径"、又自设了发布步骤，审阅员照这句把写模块那步判不通过。
    ``file:``/``action:`` 要求的原文后面附一句谁负责。
    """
    if statement.startswith("action:"):
        return (
            statement + "（操作要求：内容全部通过后由系统执行，不属于任何步骤；不要为它单独设步骤，"
            "也不要在任何步骤的 evidence_requirement 里要求发布结果、发布目录或落点路径）"
        )
    if statement.startswith("file:"):
        return (
            statement + "（在任务工作区写出这个文件即满足；发布由系统完成，evidence_requirement "
            "不要写发布目录或落点路径）"
        )
    return statement


def _is_continuation(semantic: Any) -> bool:
    """Every input port is also an output port (e.g. ``desktop.continue-delivery``)."""
    if semantic is None or not semantic.input_ports:
        return False
    outputs = {(port.port_key, port.schema_ref) for port in semantic.output_ports}
    return all((port.port_key, port.schema_ref) in outputs for port in semantic.input_ports)
