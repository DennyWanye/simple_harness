# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The readiness gate, compound phases, dispatch interception and commit commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ...artifacts.input_bindings import ManifestNotFrozen
from ...contracts import ContractError, Event
from ...contracts.htn import (
    GraphStructureBudget,
    OccurrenceId,
    RunningWorkPolicy,
    TaskForm,
    TaskSemanticBindingV1,
)
from ...contracts.semantic_base import content_hash_of
from ...graph.eligibility import (
    EligiblePrimitiveTask,
    NotEligible,
    OccurrenceOutcome,
    ReadinessReason,
    admit_for_dispatch,
    legacy_ready_is_not_eligibility,
)
from ...graph.projection_validation import GraphIntegrityError
from ...graph.task_network import TaskNetworkSnapshot
from ...planning.htn.compiler import RefinementCompilation
from ...storage.store import StoreError
from ..completion_status import read_occurrence_completion
from ..plan_commits import CommitPlanCommand, PlanPrincipal
from ..taskgraph_policy import read_installed_graph_policy
from .support import (
    COMPOUND_DISPLAY_STATUS,
    COMPOUND_PHASE_CHANGED,
    DISPATCH_INTERCEPTED,
    DISPATCH_WITHHELD,
    PLAN_INTEGRITY_FAILED,
    CompoundPhase,
    DispatchAdmissions,
    DispatchInterception,
    DispatchRefusal,
    NetworkView,
    missing_bindings,
    next_compound_phase,
)

if TYPE_CHECKING:
    from ...contracts.htn import PlanProposal
from .core import HierarchicalDispatchCore


class DispatchAdmissionMixin(HierarchicalDispatchCore):
    """The readiness gate, compound phases, dispatch interception and commit commands."""

    __slots__ = ()

    if TYPE_CHECKING:

        def network(self, mission_id: str) -> TaskNetworkSnapshot: ...
        def read(
            self, mission_id: str, *, now_ms: int | None = None, tolerate_integrity: bool = False
        ) -> NetworkView: ...

    # ------------------------------------------------------- the admission transaction
    def admissions(self, mission_id: str, *, now_ms: int | None = None) -> DispatchAdmissions:
        """Run the readiness gate over the whole plan and admit what passed (TG §8.3).

        This is the input :func:`~..scheduling.allocator.allocate_v2` takes, and it is
        the answer to P2.3b's blocker (b): before P2.3c part 2 the event handler only
        asked the *form* gate before creating an Attempt, so a DATA consumer whose
        producer had not been accepted was dispatched with no inputs and ran anyway.
        Now every occurrence goes through ``evaluate_readiness`` first and only a
        ``READY_CANDIDATE`` reaches ``admit_for_dispatch``.

        Three properties worth stating, because each is a way this could have been
        written wrongly:

        * **One read.**  ``self.read()`` fetches the acceptance projection, the
          witnesses and the duty accounts once and every occurrence is judged against
          that same snapshot, so the plan cannot move between two occurrences'
          verdicts — and ``admit_for_dispatch``'s ``_same_origin`` check would refuse
          the admission if it had.
        * **A refusal is never an admission with a flag.**  An occurrence that is not
          ready simply has no :class:`EligiblePrimitiveTask`; the allocator takes only
          admissions, so there is no field a caller could misread as a permission.
        * **``NotEligible`` at this point is fail-closed, not a retry.**  A report that
          said ``READY_CANDIDATE`` and an admission that refuses it means the report
          and the view disagree — a race or a caller bug — and the honest answer is to
          withhold this occurrence with the refusal text, not to admit it anyway.
        """

        view = self.read(mission_id, now_ms=now_ms)
        moment = int(self.store.now * 1000) if now_ms is None else int(now_ms)
        network = view.network
        # Review F8: taken from the view, not read again.  The acceptance projection and
        # the DATA lane's licences (keyed by acceptance id per consumer — see ``read``
        # for why the precondition map cannot answer that question) are what the
        # readiness reports on this view were computed against, and hashing a manifest
        # built from a *second* read would mean the report and the admission describe
        # two different moments.
        accepted = view.accepted
        licences = view.licences
        bindings: dict[str, TaskSemanticBindingV1] = {}
        readiness: dict[str, EligiblePrimitiveTask] = {}
        refusals: list[DispatchRefusal] = []
        for spec in network.occurrences:
            task_id = str(spec.task_id)
            task_view = view.views[spec.occurrence_id]
            if task_view.binding is not None:
                bindings[task_id] = task_view.binding
            task = self.store.get_task(task_id)
            if task is not None and task.accepted_result_id:
                # 阶段 E：这一步已有通过的结果。结果在现行要求下算数 → 等完成，不再派执行者。
                # 是按旧版要求通过的、现在不算数 → 不派执行者：系统请审阅员按新要求重审同一份结果；
                # 重审没过 → 如实报出来，换掉这一步或换做法由规划器定。
                counts = self._accepted_result_counts(mission_id, str(spec.occurrence_id))
                # 按需导入：循环依赖（hierarchical_dispatch ↔ carried_review）
                from ..carried_review import (
                    CARRIED_RESULT_NOT_KEPT,
                    CARRIED_RESULT_REJECTED,
                    CARRIED_REVIEW_PENDING,
                    kept,
                    rejected,
                )

                latest = self.semantics().latest_requirements_revision(mission_id)
                refused = (
                    not counts
                    and latest is not None
                    and rejected(self.store, task.accepted_result_id, int(latest.revision))
                )
                changed = (
                    not counts
                    and not refused
                    and latest is not None
                    and not kept(self.store, task.accepted_result_id, int(latest.revision))
                )
                refusals.append(
                    DispatchRefusal(
                        task_id=task_id,
                        occurrence_id=str(spec.occurrence_id),
                        reason=ReadinessReason.NOT_SELECTED,
                        detail_codes=("PREPARATION_ALREADY_ACCEPTED",)
                        if counts
                        else (CARRIED_RESULT_REJECTED,)
                        if refused
                        else (CARRIED_RESULT_NOT_KEPT,)
                        if changed
                        else (CARRIED_REVIEW_PENDING,),
                        detail="accepted preparation is waiting for completion, not another Worker"
                        if counts
                        else (
                            "this step's result was accepted under an earlier revision of the requirements and "
                            "its review under the current ones ended without an acceptance; it is not re-run "
                            "automatically — replace the step (PROPOSE_SUCCESSOR) or its method"
                        )
                        if refused
                        else (
                            "this step's result was accepted under an earlier revision of the requirements and "
                            "the step itself has changed since (its inputs or contract), so the old result "
                            "cannot be kept — replace the step (PROPOSE_SUCCESSOR) or its method"
                        )
                        if changed
                        else (
                            "this step's result was accepted under an earlier revision of the requirements; "
                            "the reviewer is judging the same result under the current ones (after its "
                            "inputs count) — no Worker runs"
                        ),
                    )
                )
                continue
            report = view.reports[spec.occurrence_id]
            if not report.ready:
                refusals.append(
                    DispatchRefusal(
                        task_id=task_id,
                        occurrence_id=str(spec.occurrence_id),
                        reason=report.reason,
                        detail_codes=report.detail_codes,
                        detail="; ".join(detail.message for detail in report.details),
                    )
                )
                continue
            # NEXT-TG-1.0 §5.2: the resolution the report was judged against — not a
            # second resolution, and never an empty manifest standing in for none.
            result = view.resolutions.get(spec.occurrence_id)
            manifest = None if result is None else result.manifest
            if manifest is None:
                refusals.append(
                    DispatchRefusal(
                        task_id=task_id,
                        occurrence_id=str(spec.occurrence_id),
                        reason=ReadinessReason.STALE_BINDING,
                        detail_codes=("input_resolution_absent",),
                        detail="the read that judged this occurrence ready carries no input manifest",
                    )
                )
                continue
            try:
                readiness[task_id] = admit_for_dispatch(
                    report, task_view, view.plan, manifest, now_ms=moment
                )
            except (NotEligible, ManifestNotFrozen) as error:
                refusals.append(
                    DispatchRefusal(
                        task_id=task_id,
                        occurrence_id=str(spec.occurrence_id),
                        reason=ReadinessReason.STALE_BINDING,
                        detail_codes=("admission_refused",),
                        detail=str(error),
                    )
                )
        return DispatchAdmissions(
            plan_revision=int(network.plan_revision),
            bindings=bindings,
            readiness=readiness,
            refusals=tuple(refusals),
        )

    def _accepted_result_counts(self, mission_id: str, occurrence_id: str) -> bool:
        """Whether this step's completion rests on an acceptance right now — the completion
        reading's answer (the same one the Planner package's ``counts_under_current`` gives)."""

        try:
            return bool(
                read_occurrence_completion(
                    self.store, mission_id, occurrence_id
                ).preparation_acceptance_ids
            )
        except (ContractError, StoreError):
            return (
                True  # unreadable completion is reported by its own path, not as "old requirements"
            )

    def record_withheld(self, mission_id: str, admissions: DispatchAdmissions) -> tuple[Event, ...]:
        """Record every structured refusal once per (occurrence, revision, reason).

        The idempotency key carries the plan revision *and* the reason, and
        ``Store.append_event`` returns the stored row for a key it already holds — so
        a Mission that waits ten cycles for a producer leaves one event rather than
        ten, and a Mission whose reason *changes* leaves the new one beside it.  That
        is the difference between a log an operator can read and a log that drowns the
        one line that mattered.
        """

        return tuple(
            self._append(
                DISPATCH_WITHHELD,
                mission_id,
                key=(
                    f"{mission_id}:{refusal.task_id}:{admissions.plan_revision}:{refusal.reason!s}"
                ),
                task_id=refusal.task_id,
                payload={"plan_revision": int(admissions.plan_revision), **refusal.to_json()},
            )
            for refusal in admissions.refusals
        )

    # -------------------------------------------------------------- the dispatch gate
    def intercept_worker_dispatch(
        self, mission_id: str, task_id: str
    ) -> DispatchInterception | None:
        """Refuse a compound before the old READY entry can dispatch it.

        The gate is ``form`` from the semantic binding (§18.5 rule 4), asked of
        :func:`legacy_ready_is_not_eligibility` so the allocator wiring in P2.3c and
        this one cannot disagree about the answer.  A primitive returns None here —
        which is not a permission, only "this gate has nothing to say".
        """

        binding = self.semantics().task_semantics_of(mission_id, task_id)
        if binding is None:
            raise missing_bindings(mission_id, [task_id])
        task = self.store.get_task(task_id)
        verdict = legacy_ready_is_not_eligibility(
            "" if task is None else str(task.status), form=binding.form
        )
        if verdict.gate_reason is None:
            return None
        occurrence = self._occurrence_of(mission_id, task_id)
        interception = DispatchInterception(
            task_id=task_id,
            occurrence_id=None if occurrence is None else str(occurrence),
            reason=str(verdict.gate_reason),
            explanation=verdict.explanation,
        )
        self._append(
            DISPATCH_INTERCEPTED,
            mission_id,
            key=f"{mission_id}:{task_id}:{interception.reason}",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "occurrence_id": interception.occurrence_id,
                "form": str(binding.form),
                "reason": interception.reason,
                "legacy_status": "" if task is None else str(task.status),
                "explanation": interception.explanation,
            },
        )
        return interception

    def record_integrity_failure(self, mission_id: str, error: GraphIntegrityError) -> Event:
        """Record that one Mission's plan is damaged, with the diagnosis (TG §14.3).

        The durable record is the point: the caller is about to stop *this* Mission
        and leave every other Mission in the run alone, so what went wrong has to be
        readable afterwards without re-deriving it from a traceback that no longer
        exists.  Deliberately no topological order in the payload — a damaged
        projection's healthy prefix is a partial order, not a plan.
        """

        code = str(error.code)
        return self._append(
            PLAN_INTEGRITY_FAILED,
            mission_id,
            key=f"{mission_id}:{code}:{content_hash_of(sorted(error.remaining))[:16]}",
            payload={
                "code": str(code),
                "subjects": sorted(str(item) for item in error.remaining),
                "cycle": [str(item) for item in error.cycle],
                "diagnose": error.diagnose(),
            },
        )

    def _occurrence_of(self, mission_id: str, task_id: str) -> OccurrenceId | None:
        try:
            network = self.network(mission_id)
        except GraphIntegrityError:
            return None
        for spec in network.occurrences:
            if str(spec.task_id) == task_id:
                return spec.occurrence_id
        return None

    # ----------------------------------------------------------- compound phase driver
    def advance_compound_phases(self, mission_id: str) -> dict[OccurrenceId, CompoundPhase]:
        """Run the typed reducer over every compound and record what changed.

        No Attempt is created and no legacy Task row is written: the phase is a
        *projection* of typed state, so the only durable trace is the event.
        """

        view = self.read(mission_id)
        phases: dict[OccurrenceId, CompoundPhase] = {}
        for spec in view.network.occurrences:
            if spec.form is not TaskForm.COMPOUND:
                continue
            children = {
                binding.occurrence_id: view.outcomes.get(
                    binding.occurrence_id, OccurrenceOutcome.UNKNOWN
                )
                for binding in view.network.adopted_children(spec.occurrence_id)
            }
            phase = next_compound_phase(
                spec,
                view.network,
                view.reports[spec.occurrence_id],
                child_outcomes=children,
                resolved=spec.occurrence_id in view.resolved,
            )
            phases[spec.occurrence_id] = phase
            self._append(
                COMPOUND_PHASE_CHANGED,
                mission_id,
                key=f"{mission_id}:{spec.occurrence_id!s}:{int(view.network.plan_revision)}:{phase!s}",
                task_id=str(spec.task_id),
                payload={
                    "occurrence_id": str(spec.occurrence_id),
                    "task_id": str(spec.task_id),
                    "plan_revision": int(view.network.plan_revision),
                    "phase": str(phase),
                    "display_status": str(COMPOUND_DISPLAY_STATUS[phase]),
                    "readiness_reason": str(view.reports[spec.occurrence_id].reason),
                },
            )
        return phases

    def _lease_blocks_cancel(self, attempt: Any, owner: str | None) -> bool:
        """A live lease held by somebody else must not be stolen (P2.3s)."""

        expires = getattr(attempt, "lease_expires_at", None)
        holder = str(getattr(attempt, "lease_owner", "") or "")
        if expires is None or holder == "":
            return False
        try:
            if float(expires) <= float(self.store.now):
                return False
        except (TypeError, ValueError):
            return False
        if owner is None:
            return False
        return holder != str(owner)

    def build_command(
        self,
        mission_id: str,
        proposal: PlanProposal,
        compilation: RefinementCompilation,
        *,
        principal: PlanPrincipal,
        command_id: str,
        source: Mapping[str, Any] | None = None,
    ) -> CommitPlanCommand:
        """The command the Commit service checks.  Authority is bound here, not read.

        P2.3j: a delta that retires an instance replaces that instance's slots, and
        the commit's running-work check refuses ``retain_if_bindings_unchanged`` for
        a replacement (it cannot decide the case).  The policy the command carries is
        therefore the *system's* reading of what the delta does — ``request_stop_then
        _reconcile`` when something is retired — and not the proposal's own claim,
        which for a plain refinement keeps the command byte-for-byte as before.

        Verification P1-2: the policy is a label, not an action; the TaskGraph commit
        revokes the retired work's execution rights in its own transaction.
        """

        policy: dict[str, Any] = {}

        policy["structure_budget"] = GraphStructureBudget.from_json(
            read_installed_graph_policy(self.store, mission_id).to_json()["graph_structure_budget"]
        )
        if compilation.delta.retired_instance_ids or compilation.superseded_occurrences:
            policy["running_work_policy"] = RunningWorkPolicy.REQUEST_STOP_THEN_RECONCILE
        if compilation.superseded_occurrences:
            policy["superseded_occurrences"] = compilation.superseded_occurrences
        return CommitPlanCommand(
            command_id=command_id,
            mission_id=mission_id,
            delta=compilation.delta,
            network=compilation.network,
            task_bindings=compilation.task_bindings,
            **policy,
            issued_by=principal.principal_id,
            scope_id=principal.scope_id,
            budget_requirement=compilation.budget_requirement,
            seed_methods=tuple(
                getattr(self.require_planning_world(), "seed_methods", ())
            ),  # see seed_method_keys
            source={
                **dict(source or {}),
                "proposal_id": proposal.proposal_id,
                "rationale": proposal.rationale,
            },
        )
