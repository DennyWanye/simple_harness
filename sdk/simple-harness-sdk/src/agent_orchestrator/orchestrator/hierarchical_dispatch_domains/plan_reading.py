# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Reading the plan: networks, plan views, occurrences and readiness reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...artifacts.input_bindings import AcceptedOutputsIndex, ResolutionResult, TargetRules
from ...artifacts.taskgraph_inputs import encode_target_rules
from ...contracts import TERMINAL_MISSION, ContractError
from ...contracts.evidence_state import ValidityWitness
from ...contracts.htn import (
    MissionRef,
    ObligationId,
    OccurrenceId,
    OccurrenceSpec,
    PlanRevision,
    TaskForm,
    TaskSemanticBindingV1,
)
from ...contracts.obligations import ObligationAccountView
from ...graph.eligibility import (
    ActivePlanView,
    EvidenceView,
    OccurrenceOutcome,
    ReadinessReport,
    TaskView,
    evaluate_readiness,
)
from ...graph.network_codec import decode
from ...graph.projection_validation import GraphIntegrityError, require_topological_order
from ...graph.task_network import TaskNetworkSnapshot
from ...planning.htn.compiler import RootNetwork
from ...runtime.planning_operations import SourceUnavailable
from ...storage.obligation_store import ObligationStore
from ...storage.taskgraph_binding import require_bound
from ..completion_status import read_occurrence_completion
from ..method_plan_reviews import seed_keys
from ..taskgraph_epochs import current_scope_epochs
from ..taskgraph_outcomes import read_taskgraph_outcomes
from .core import HierarchicalDispatchCore
from .support import NetworkView, PlanIntegrityError, missing_bindings, root_not_identified


class PlanReadingMixin(HierarchicalDispatchCore):
    """Reading the plan: networks, plan views, occurrences and readiness reads."""

    __slots__ = ()

    if TYPE_CHECKING:

        def accepted_outputs(
            self,
            mission_id: str,
            network: TaskNetworkSnapshot,
            *,
            outcomes: Mapping[OccurrenceId, OccurrenceOutcome] | None = None,
        ) -> AcceptedOutputsIndex: ...
        def input_witness_index(self, mission_id: str) -> dict[str, dict[str, ValidityWitness]]: ...
        def resolved_inputs(
            self,
            mission_id: str,
            network: TaskNetworkSnapshot,
            spec: OccurrenceSpec,
            *,
            accepted: AcceptedOutputsIndex | None = None,
            witnesses: Mapping[str, ValidityWitness] | None = None,
            now_ms: int | None = None,
        ) -> ResolutionResult: ...
        def start_witness_index(self, mission_id: str) -> dict[str, dict[str, ValidityWitness]]: ...
        def witnesses(
            self, mission_id: str, network: TaskNetworkSnapshot
        ) -> dict[str, ValidityWitness]: ...

    def target_rules_for(self, task_id: str) -> TargetRules:
        """The original workspace destination policy, resolved for one Task."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ContractError("workspace target requires a Task identity")
        return self.target_rules or TargetRules(namespace=f"workspace:{task_id}")

    def target_rules_policy(self) -> dict[str, Any]:
        """Freeze the installed rule or its original per-Task namespace strategy."""

        if self.target_rules is not None:
            return {"kind": "FIXED", "rules": encode_target_rules(self.target_rules)}
        return {
            "kind": "TASK_WORKSPACE_V1",
            "namespace_prefix": "workspace:",
            "port_prefixes": {},
            "preserve_source_namespace": False,
            "case_insensitive": False,
        }

    def network(self, mission_id: str) -> TaskNetworkSnapshot:
        """The active plan revision, read back as a typed network — or corruption.

        This is the *execution* read, so a task in the plan with no
        ``TaskSemanticBindingV1`` raises :class:`PlanIntegrityError`.  §18.5: in
        this mode a missing binding is corruption, and treating it as "then use the
        legacy rules" is how a Mission silently runs half in each mode.

        The reading side that only has to *explain* a Mission goes through
        :meth:`read` with ``tolerate_integrity=True``, which carries the same error
        on the :class:`ActivePlanView` instead of raising: every verdict in the
        scope then reads ``GRAPH_INTEGRITY`` and nothing is dispatched (§24.1
        decision 11).  Two behaviours, one read, and the caller says which it is.
        """

        snapshot, integrity = self._read_network(mission_id)
        if integrity is not None:
            raise integrity
        return snapshot

    def _read_network(
        self, mission_id: str
    ) -> tuple[TaskNetworkSnapshot, PlanIntegrityError | None]:
        """The plan as it can be read, plus what was wrong with it.

        An occurrence whose task has no meaning cannot be *in* a
        :class:`TaskNetworkSnapshot` — construction checks that every endpoint names
        something the snapshot contains — so it is left out and reported.  Leaving
        it out is not tolerating it: the returned error makes every occurrence in the
        scope refuse, which is a stronger answer than one task failing.
        """

        semantics = self.semantics()
        active = semantics.active_plan_revision(mission_id)
        if active is None:
            # Before the first refinement there is no plan revision yet, and the
            # network is the Mission's own root goal.  Building that seed by hand in
            # every caller is how two callers end up disagreeing about whether the
            # root is its own occurrence, which is why the compiler owns the shape.
            return self._read_seed_network(mission_id)
        revision = int(active.revision)

        require_bound(self.store, mission_id)
        if self._taskgraph_history is None:
            raise SourceUnavailable("taskgraph_history_reader_unavailable")
        frozen = decode(
            self._taskgraph_history.read_revision(mission_id, revision).record.document.to_json()
        ).snapshot
        current_bindings = []
        for original in frozen.task_bindings:
            current = semantics.task_semantics_of(mission_id, str(original.task_id))
            if current is None:
                raise missing_bindings(mission_id, [str(original.task_id)])
            current_bindings.append(current)
        # Roots, memberships, adoptions and declared endpoints come from the
        # verified full record. Current control is a separate binding overlay;
        # never infer new roots or drop dangling edges while reading a graph.
        return replace(frozen, task_bindings=tuple(current_bindings)), None

    def seed_network(self, mission_id: str) -> TaskNetworkSnapshot:
        """The one-occurrence starting network of a Mission with no plan revision.

        Exactly one semantic binding is expected: the root goal the Mission was
        opened for.  Zero is the missing-binding corruption §18.5 names; more than
        one means someone wrote task meanings without a plan to hold them, and
        picking one of them would be this module inventing a root.  Both are
        :class:`PlanIntegrityError`, not a legacy fallback.
        """

        snapshot, integrity = self._read_seed_network(mission_id)
        if integrity is not None:
            raise integrity
        return snapshot

    def _read_seed_network(
        self, mission_id: str
    ) -> tuple[TaskNetworkSnapshot, PlanIntegrityError | None]:
        """The seed, or an empty network plus the reason there is no seed.

        The empty snapshot is what lets the explaining caller answer at all: with
        the error carried beside it, every query over it reports corruption instead
        of raising out of a loop that is also driving other Missions.
        """

        bindings: dict[str, TaskSemanticBindingV1] = {}
        for binding in self.semantics().list_task_semantics(mission_id):
            bindings[str(binding.task_id)] = binding  # ordered by revision: newest wins
        if len(bindings) != 1:
            empty = TaskNetworkSnapshot(
                mission_id=MissionRef(mission_id),
                plan_revision=PlanRevision(0),
                occurrences=(),
                task_bindings=(),
            )
            return empty, root_not_identified(mission_id, sorted(bindings))
        root = next(iter(bindings.values()))
        return RootNetwork(mission_id=MissionRef(mission_id), root_task=root).snapshot(), None

    def plan_view(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot | None = None,
        *,
        integrity: GraphIntegrityError | None = None,
    ) -> ActivePlanView:
        """The frozen read one readiness judgement is made against.

        Both kinds of corruption are *carried* here rather than raised: an
        unorderable projection (``require_topological_order``) and an incomplete
        plan meaning (``integrity``, from :meth:`_read_network`).  §24.1 decision 11
        wants a damaged plan to stop every dispatch in the scope, which is exactly
        what ``ActivePlanView.integrity_error`` does — while the caller that actually
        materialises files gets the exception from :mod:`..artifacts.versioning`.
        """

        mission = self.mission(mission_id)
        if network is None:
            network, carried = self._read_network(mission_id)
            integrity = integrity or carried
        resolved = network
        if integrity is None:
            try:
                require_topological_order(resolved.execution_projection())
            except GraphIntegrityError as error:
                integrity = error
        return ActivePlanView(
            snapshot=resolved,
            # "Admits work" is the *selection* gate's question — is this Mission still
            # one whose plan may be acted on — so the answer is "it has not stopped",
            # not "it is already dispatching".  A Mission that is still being planned
            # admits planning work; a stopped one admits none.  Whether a primitive may
            # actually be dispatched is the dispatch transaction's decision (TG §8.1
            # layer 3), which this view deliberately does not make.
            mission_admits_work=mission.status not in TERMINAL_MISSION,
            # TG decision 9: an active share needs a live demand, and the duty's
            # account is where that is recorded.  Read, never invented: a duty with
            # no account is "no admitted demand", which is the selection gate's
            # NOT_SELECTED and not an implicit permission.
            obligation_accounts=self.obligation_accounts(mission_id, resolved),
            # The epoch barrier the START-precondition gate compares against (§11.5 /
            # I19).  Left empty — as it was — every recorded witness answered
            # ``witness_epoch_unknown``: the view could not confirm the epoch it was
            # taken at, so the one lane that reads ``scope_epochs`` could never open,
            # whatever the deployment had observed.
            scope_epochs=self.scope_epochs(mission_id),
            integrity_error=integrity,
            **self.requirements_revisions(mission_id),
        )

    def requirements_revisions(self, mission_id: str) -> dict[str, int]:
        """Which requirements revision the active plan was made for, and which is current
        (阶段 E).  Before any plan is committed there is nothing to compare."""
        semantics = self.semantics()
        active = semantics.active_plan_revision(mission_id)
        latest = semantics.latest_requirements_revision(mission_id)
        if active is None or latest is None:
            return {}
        return {
            "planned_requirements_revision": int(active.read_set.requirements_revision),
            "current_requirements_revision": int(latest.revision),
        }

    def requirements_changed(self, mission_id: str) -> bool:
        """The user amended the requirements and no plan for the new revision is committed."""
        found = self.requirements_revisions(mission_id)
        return (
            bool(found)
            and bool(found["planned_requirements_revision"])
            and (found["planned_requirements_revision"] != found["current_requirements_revision"])
        )

    def scope_epochs(self, mission_id: str) -> dict[str, int]:
        """The live validity epoch of every scope this Mission's witnesses name.

        ``mission`` is always in it: it is the scope a witness takes by default and
        the one the manager epoch belongs to, and a barrier that is missing reads as
        "unknown", which refuses rather than allows.
        """

        # A newly bumped scope can have no witness yet. Its barrier still belongs in
        # the complete current read token and input policy.

        require_bound(self.store, mission_id)
        return current_scope_epochs(self.store, mission_id)

    def obligation_accounts(
        self, mission_id: str, network: TaskNetworkSnapshot
    ) -> dict[ObligationId, ObligationAccountView]:
        duties = ObligationStore(self.store)
        accounts: dict[ObligationId, ObligationAccountView] = {}
        for spec in network.occurrences:
            if spec.obligation_id in accounts:
                continue
            if duties.exists(mission_id, spec.obligation_id):
                accounts[spec.obligation_id] = duties.account(mission_id, spec.obligation_id)
        return accounts

    def read(
        self, mission_id: str, *, now_ms: int | None = None, tolerate_integrity: bool = False
    ) -> NetworkView:
        """One complete read: snapshot, views, readiness reports, outcomes.

        This is the single place the new mode answers "what is in the plan", so
        ``list_tasks``, the ready set, the concurrency count, the terminal test and
        the root review all end up reading the same thing (TG §7).

        ``tolerate_integrity`` picks which of the two answers decision 11 asks for.
        The default is the execution answer: a plan whose meaning is incomplete
        raises, because acting on the readable part of a damaged plan is exactly the
        silent half-mode §18.5 forbids.  ``True`` is the explaining answer: the same
        error is carried on the view, every occurrence reports ``GRAPH_INTEGRITY``,
        both frontiers come back empty, and an operator can still see the Mission.

        Everything the readiness gates read is fetched **once** here and passed down:
        the acceptance projection, the witnesses and the duty accounts are the same
        for every occurrence in one read, and re-deriving them per occurrence turned
        one read of a plan into a quadratic number of store round-trips.  They are also
        *carried* on the returned view (review F8), so a caller that has to build the
        same manifest again — ``admissions()`` — judges against this snapshot rather
        than taking a second one.
        """

        network, integrity = self._read_network(mission_id)
        if integrity is not None and not tolerate_integrity:
            raise integrity
        plan = self.plan_view(mission_id, network, integrity=integrity)
        outcomes = self.occurrence_outcomes(mission_id, network)
        if not callable(self._taskgraph_settlement_reader):
            raise SourceUnavailable("taskgraph_settlement_reader_unavailable")
        settlements = self._taskgraph_settlement_reader(mission_id, network, outcomes)
        resolved = self.resolved_occurrences(mission_id, network)
        witnesses = self.witnesses(mission_id, network)
        accepted = self.accepted_outputs(mission_id, network, outcomes=outcomes)
        # Two different questions, two different key spaces: ``witnesses()`` answers
        # "which condition was this witness taken for" (the START-precondition lane)
        # and the input index answers "which acceptance did this consumer get a
        # licence over" (the DATA lane, I19).  Feeding the precondition map to the
        # resolver meant no key ever matched and every DATA consumer refused with
        # ``WITNESS_MISSING`` whatever the deployment had accepted.
        licences = self.input_witness_index(mission_id)
        # The START-precondition lane is keyed by *consumer*, not by condition: two
        # occurrences under one method inherit the same digest, and §11.5 says one
        # consumer's permission is not another's.  The mission-wide map from the
        # frozen ``witness_ref`` stays underneath it, so a deployment that recorded
        # the link that way is still read.
        starts = self.start_witness_index(mission_id)
        moment = int(self.store.now * 1000) if now_ms is None else int(now_ms)
        views: dict[OccurrenceId, TaskView] = {}
        reports: dict[OccurrenceId, ReadinessReport] = {}
        resolutions: dict[OccurrenceId, ResolutionResult] = {}
        for spec in network.occurrences:
            view = self.task_view(mission_id, network, spec)
            views[spec.occurrence_id] = view
            resolution = self.resolved_inputs(
                mission_id,
                network,
                spec,
                accepted=accepted,
                witnesses=licences.get(str(spec.task_id), {}),
                now_ms=moment,
            )
            resolutions[spec.occurrence_id] = resolution
            reports[spec.occurrence_id] = evaluate_readiness(
                view,
                plan,
                outcomes,
                EvidenceView(witnesses={**witnesses, **starts.get(str(spec.task_id), {})}),
                resolution,
                now_ms=moment,
                settlements=settlements,
            )
        return NetworkView(
            network=network,
            plan=plan,
            views=views,
            reports=reports,
            outcomes=outcomes,
            resolved=resolved,
            accepted=accepted,
            witnesses=witnesses,
            licences=licences,
            starts=starts,
            resolutions=resolutions,
        )

    def task_view(
        self, mission_id: str, network: TaskNetworkSnapshot, spec: OccurrenceSpec
    ) -> TaskView:
        """The typed read of one occurrence; the legacy status is diagnostics only."""

        task = self.store.get_task(str(spec.task_id))
        try:
            binding: TaskSemanticBindingV1 | None = network.binding_for_occurrence(
                spec.occurrence_id
            )
        except KeyError:  # pragma: no cover - network() refuses this first
            binding = None
        return TaskView(
            occurrence_id=spec.occurrence_id,
            task_id=spec.task_id,
            binding=binding,
            legacy_status="" if task is None else str(task.status),
        )

    def occurrence_outcomes(
        self, mission_id: str, network: TaskNetworkSnapshot
    ) -> dict[OccurrenceId, OccurrenceOutcome]:
        """What the world says about each occurrence, for the ORDER release test.

        A reading, not a status copy: an accepted :class:`Acceptance` is what makes
        an occurrence ``ACCEPTED``, and an occurrence with no Acceptance is
        ``RUNNING`` or ``UNKNOWN`` however its legacy Task row happens to read.
        """

        require_bound(self.store, mission_id)
        return read_taskgraph_outcomes(self.store, mission_id, network)

    def resolved_occurrences(
        self, mission_id: str, network: TaskNetworkSnapshot
    ) -> frozenset[OccurrenceId]:
        """Occurrences whose duty has an *adopted* GoalResolution (§7.2)."""

        if self.semantics().active_plan_revision(mission_id) is None:
            # No plan is committed yet, so nothing has a completion scope or a resolution.
            self.seed_network(mission_id)  # verify the original seed
            return frozenset()

        return frozenset(
            spec.occurrence_id
            for spec in network.occurrences
            if spec.form is TaskForm.COMPOUND
            and read_occurrence_completion(self.store, mission_id, str(spec.occurrence_id)).complete
        )

    # ------------------------------------------------------------------ the three reads
    def occurrences(self, mission_id: str) -> tuple[TaskView, ...]:
        """The new mode's ``list_tasks``: typed views in projection order."""

        view = self.read(mission_id)
        projection = view.network.execution_projection()
        ordered = [
            occurrence
            for occurrence in view.views
            if occurrence in projection.projected_occurrences
        ]
        unprojected = [
            occurrence
            for occurrence in view.views
            if occurrence not in projection.projected_occurrences
        ]
        order = [*sorted(ordered, key=str), *sorted(unprojected, key=str)]
        return tuple(view.views[occurrence] for occurrence in order)

    def seed_method_keys(self) -> set[tuple[str, int, str]]:
        """The deployment's own seed methods, as the adoption rule keys them (the plan-commit gate
        and the planner's candidate list read this one set)."""

        return seed_keys(getattr(self.require_planning_world(), "seed_methods", ()))

    def ready_occurrences(self, mission_id: str) -> tuple[OccurrenceId, ...]:
        """The ExecutionFrontier — not a ``TaskStatus.READY`` scan (TG §8.1)."""

        return self.read(mission_id).execution_frontier.occurrences

    def running_occurrences(self, mission_id: str) -> tuple[OccurrenceId, ...]:
        """The concurrency count: adopted primitive occurrences with a live Attempt.

        The *set* comes from the projection, so a compound is never counted as
        running work and a retired branch is never counted at all; the liveness
        comes from the Attempt rows, which is where it actually lives.
        """

        view = self.read(mission_id)
        projected = view.network.execution_projection().projected_occurrences
        live: list[OccurrenceId] = []
        for spec in view.network.occurrences:
            if spec.occurrence_id not in projected or spec.form is not TaskForm.PRIMITIVE:
                continue
            attempts = self.store.list_attempts(str(spec.task_id))
            if any(str(attempt.status) == "RUNNING" for attempt in attempts):
                live.append(spec.occurrence_id)
        return tuple(sorted(live, key=str))
