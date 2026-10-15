# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Hierarchical, TaskGraph and Assurance deployment installation and read APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ...api.assurance import AssuranceApi, AssuranceReadError
from ...api.taskgraph import TaskGraphReadApi, _fail
from ...assurance.codec import AssuranceError
from ...contracts import TERMINAL_MISSION, ContractError, Mission, MissionStatus, MissionStopReason
from ...contracts.error_table import CodedFault
from ...contracts.htn import TaskForm
from ...contracts.models import sha256_hex
from ...contracts.planning_decisions import RepairReplaceMethodDecision, UnsupportedPlanningPackage
from ...graph.projection_validation import GraphIntegrityError
from ...storage import htn_store
from ...storage.store import Store, StoreError
from ...storage.taskgraph_binding import NotBoundError, require_bound
from ..assurance_final_writer import is_assured
from ..commit_service import CommitRejected, CommitService
from ..hierarchical_dispatch import (
    CompoundPhase,
    HierarchicalDispatch,
    is_hierarchical,
    next_compound_phase,
    record_assembly_missing,
)
from ..method_library import record_attribution
from ..planning_backend_runtime import frozen_deployment_conflict
from ..planning_protocol_binding import current_planning_protocol
from ..root_review import RootReviewStatus
from ..taskgraph_assembly import install_taskgraph
from ..taskgraph_execution_sources import _document
from ..taskgraph_notifications import TaskGraphNotifications
from .core import OrchestratorCore
from .support import logger


class DeploymentMixin(OrchestratorCore):
    """Hierarchical, TaskGraph and Assurance deployment installation and read APIs."""

    __slots__ = ()

    @property
    def store(self) -> Store:
        assert self._store is not None
        return self._store

    def _require_assurance_execution_root(self) -> None:
        if self._wiring.assurance_root_gate is not None:
            self._wiring.assurance_root_gate.require_execution()
        if self._wiring.assurance_management_only:
            raise AssuranceError("ROOT_RUNTIME_NOT_STARTED")

    @property
    def commit(self) -> CommitService:
        assert self._commit is not None
        return self._commit

    @property
    def hierarchical(self) -> HierarchicalDispatch | None:
        """The hierarchical assembly, or None when this deployment has not installed one."""

        return self._wiring.hierarchical

    def install_hierarchical(self) -> HierarchicalDispatch:
        """Install the hierarchical assembly (P2.3b).  Without it nothing is planned,
        dispatched or judged: a Mission is recorded as waiting for the assembly.  Each
        Mission's planning world is bound per Mission (``_dispatch_for``)."""

        self._wiring.hierarchical = HierarchicalDispatch(self.store, self.commit)
        return self._wiring.hierarchical

    def set_between_cycles(self, duty: Callable[[], Any] | None, *, every_seconds: float) -> None:
        """Let the embedding host run its own duties while :meth:`run` is still going.

        NEXT-TG-1.0 2A.1g (real run 2026-09-27): the Host issues planning grants,
        check-policy approvals and TaskGraph enables only after ``run()`` returns, and
        ``run()`` does not return while any turn anywhere is in flight. One Mission's
        long model turn held every other Mission's grant for eleven minutes, and the
        Mission waiting for it could never start. The duty runs between cycles, never
        inside one, at most once per ``every_seconds``; its failure is logged and does
        not end the run.
        """

        self._between.between_cycles = duty
        self._between.between_every = max(0.0, float(every_seconds))
        self._between.between_last = 0.0

    async def _run_between_cycles(self) -> bool:
        if self._between.between_cycles is None:
            return False
        now = asyncio.get_running_loop().time()
        if now - self._between.between_last < self._between.between_every:
            return False
        self._between.between_last = now
        try:
            outcome = self._between.between_cycles()
            if asyncio.iscoroutine(outcome):
                outcome = await outcome
        except Exception as error:  # noqa: BLE001 - a host duty never ends the run
            logger.warning("orchestrator.between_cycles_failed error=%s", error)
            return False
        return bool(outcome)

    def install_hierarchical_deployment(self, world_factory: Any, *, start_gate: Any) -> None:
        """Install a deployment-owned, Mission-isolated world resolver.

        Each dispatcher has its own registry and evidence scope. Async turns never
        mutate a shared world's mission_id. Rebuilds reconstruct from durable data.
        """
        self._wiring.planning_world_factory = world_factory
        self._wiring.planning_start_gate = start_gate
        self._wiring.mission_dispatches.clear()
        self.install_hierarchical()

    def _blame_replaced_method(self, mission_id: str, decision: Any, decision_id: str) -> None:
        """换做法提交成功、规划器写明"换下的做法本身有错"（阶段 C3）：换下的做法若是照全库先例
        写的，记一条归因。同一事务；程序只记规划器明确写出的，怎么数在 method_library 里。"""

        payload = decision.payload
        if not isinstance(payload, RepairReplaceMethodDecision) or not payload.method_at_fault:
            return

        replaced = htn_store.HtnStore(self.store).get_method_instance(
            mission_id, str(payload.rejected_method_instance.id)
        )
        record_attribution(
            self.store,
            mission_id=mission_id,
            method_ref=replaced.method_ref,
            source_ref=decision_id,
            source_kind="PLANNER",
            reason=payload.method_at_fault,
        )

    def _dispatch_for(self, mission_id: str) -> HierarchicalDispatch | None:
        if self._wiring.planning_world_factory is None:
            return self._wiring.hierarchical
        mission = self.store.get_mission(mission_id)
        if mission is None:
            return None
        if mission_id not in self._wiring.mission_dispatches:
            world = self._wiring.planning_world_factory(mission)
            if world.mission_id != mission_id:
                raise ContractError("planning world belongs to another Mission")
            self._wiring.mission_dispatches[mission_id] = HierarchicalDispatch(
                self.store,
                self.commit,
                planning=world,
            )
        dispatch = self._wiring.mission_dispatches[mission_id]
        if self._wiring.taskgraph_dispatch_setup is not None:
            self._wiring.taskgraph_dispatch_setup(dispatch)
        return dispatch

    def _new_mode(self, mission: Mission) -> HierarchicalDispatch | None:
        """The assembly for this Mission, or None when this deployment installed none."""

        if self._wiring.hierarchical is None:
            return None
        return self._dispatch_for(mission.id)

    def install_taskgraph_notifications(
        self, *, history: Any, convergence: Any, validate_current: Any
    ) -> Any:

        if self._wiring.taskgraph_notifications is not None:
            raise ValueError("TaskGraph notifications are already installed")
        if self._wiring.hierarchical is None:
            raise ValueError("TaskGraph notifications require the hierarchical assembly")
        self._wiring.taskgraph_notifications = TaskGraphNotifications(
            self, history=history, convergence=convergence, validate_current=validate_current
        )
        return self._wiring.taskgraph_notifications

    def install_taskgraph(self, ports: Any) -> Any:
        """Fixed deployment setup; never called with model or Host request JSON."""

        return install_taskgraph(self, ports)

    def install_taskgraph_read_api(self, api: Any, *, tenant_id: str, principal: Any) -> None:

        if not isinstance(api, TaskGraphReadApi) or not api.matches_binding(
            self.commit, tenant_id, principal
        ):
            raise ValueError("TaskGraph read API must bind this Store and authenticated caller")
        if any(
            item.matches_binding(self.commit, tenant_id, principal)
            for item in self._wiring.taskgraph_read_apis
        ):
            raise ValueError("TaskGraph read API is already installed for this caller")
        self._wiring.taskgraph_read_apis.append(api)

    def taskgraph_read_api(self, *, tenant_id: str, principal: Any) -> Any:
        for api in self._wiring.taskgraph_read_apis:
            if api.matches_binding(self.commit, tenant_id, principal):
                return api

        _fail(
            "SOURCE_UNAVAILABLE",
            "TaskGraph source assembly is not installed",
            retry="OPERATOR_REPAIR",
        )

    def install_assurance_read_api(self, api: Any, *, tenant_id: str, principal: Any) -> None:
        """Fixed-caller Assurance read verbs (S25); bound by the deployment assembly only."""

        if not isinstance(api, AssuranceApi) or not api.matches_binding(
            self.commit, tenant_id, principal
        ):
            raise ValueError("Assurance read API must bind this Store and authenticated caller")
        if any(
            item.matches_binding(self.commit, tenant_id, principal)
            for item in self._wiring.assurance_read_apis
        ):
            raise ValueError("Assurance read API is already installed for this caller")
        self._wiring.assurance_read_apis.append(api)

    def assurance_read_api(self, *, tenant_id: str, principal: Any) -> Any:
        for api in self._wiring.assurance_read_apis:
            if api.matches_binding(self.commit, tenant_id, principal):
                return api

        raise AssuranceReadError(
            "PROFILE_UNBOUND", "Assurance read assembly is not installed for this caller"
        )

    def taskgraph_operator_api(self, *, tenant_id: str, principal: Any) -> Any:
        """Authenticated internal operator access, never part of model tools."""
        operator = self._wiring.taskgraph_operator
        if operator is None or not operator.matches_binding(self.commit, tenant_id, principal):
            raise StoreError("TASKGRAPH_OPERATOR_ASSEMBLY_UNAVAILABLE")
        return operator

    def taskgraph_policy_api(self, *, tenant_id: str, principal: Any) -> Any:
        """Explicit kernel activation through fixed authenticated Host setup."""
        policy = self._wiring.taskgraph_policy
        if policy is None or not policy.matches_binding(self.commit, tenant_id, principal):
            raise StoreError("TASKGRAPH_POLICY_ASSEMBLY_UNAVAILABLE")
        return policy

    def taskgraph_recheck_mission(self, mission_id: str) -> dict[str, Any]:
        """The notification re-enters the original typed reducer, without dispatching."""
        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise StoreError("TASKGRAPH_MISSION_UNAVAILABLE")
        if mission.status in TERMINAL_MISSION:
            return {"status": "MISSION_TERMINAL", "mission_status": str(mission.status)}
        dispatch = self._new_mode(mission)
        if dispatch is None:
            raise StoreError("TASKGRAPH_HIERARCHICAL_ASSEMBLY_REQUIRED")
        if not self.store.connection.in_transaction:
            raise StoreError("TASKGRAPH_RECHECK_REQUIRES_TRANSACTION")
        # Re-enter the actual witness producers before reducing readiness. Reading
        # the cached witnesses alone does not discharge a revision/evidence wakeup.
        # These producers interpret the existing evidence snapshot; they perform
        # no provider calls and never promote a PLAN witness into START authority.
        dispatch._world()
        network = dispatch.network(mission_id)
        moment = int(self.store.now * 1000)
        inputs = dispatch.issue_input_witnesses(mission_id, network, now_ms=moment)
        starts = dispatch.issue_start_witnesses(mission_id, network, now_ms=moment)
        phases = dispatch.advance_compound_phases(mission_id)
        view = dispatch.read(mission_id, now_ms=moment)

        # ReadinessReport is a typed dataclass, not a JSON-contract object. Keep
        # its complete read set and detail identities in the durable observation.
        # This receipt proves a fresh reducer read; it does not clear arbitrary
        # validity_dirty entries or grant an evidence/support revalidation.
        reports = [_document(view.reports[key]) for key in sorted(view.reports)]
        return {
            "revision": int(view.network.plan_revision),
            "readiness_hash": sha256_hex(reports),
            "input_witness_ids": sorted(item.witness_id for item in inputs),
            "start_witness_ids": sorted(item.witness_id for item in starts),
            "pending_validity": [
                _document(item)
                for state in ("PENDING", "RECHECKING")
                for item in dispatch.semantics().list_dirty(mission_id, state=state)
            ],
            "evaluated_occurrence_ids": [str(key) for key in sorted(view.reports)],
            "phases": {str(key): str(value) for key, value in sorted(phases.items())},
        }

    async def taskgraph_request_composition(
        self, mission_id: str, occurrence_id: Any
    ) -> dict[str, Any]:

        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise StoreError("TASKGRAPH_MISSION_UNAVAILABLE")
        if mission.status in TERMINAL_MISSION:
            return {"status": "MISSION_TERMINAL", "mission_status": str(mission.status)}
        dispatch = self._new_mode(mission)
        if dispatch is None:
            raise StoreError("TASKGRAPH_HIERARCHICAL_ASSEMBLY_REQUIRED")
        with self.store.read_view():
            view = dispatch.read(mission_id)
            specs = [
                item for item in view.network.occurrences if item.occurrence_id == occurrence_id
            ]
            if not specs:
                return {"status": "SUPERSEDED", "revision": int(view.network.plan_revision)}
            spec = specs[0]
            if spec.form is not TaskForm.COMPOUND:
                raise StoreError("TASKGRAPH_COMPOSITION_SUBJECT_INVALID")
            phase = next_compound_phase(
                spec,
                view.network,
                view.reports[occurrence_id],
                child_outcomes={
                    child.occurrence_id: view.outcomes.get(child.occurrence_id)
                    for child in view.network.adopted_children(occurrence_id)
                },
                resolved=occurrence_id in view.resolved,
            )
        if phase is not CompoundPhase.COMPOSITION_REVIEW:
            return {
                "status": "NOT_READY",
                "phase": str(phase),
                "revision": int(view.network.plan_revision),
            }
        if occurrence_id in view.network.root_occurrence_ids:
            await self._advance_root_review(mission, dispatch)
            state = self._root_review(mission, dispatch).state(mission_id)
            if state.status is RootReviewStatus.UNREADABLE_PLAN:
                raise StoreError("TASKGRAPH_COMPOSITION_SOURCE_UNAVAILABLE")
            return {
                "status": str(state.status),
                "package_id": None if state.package is None else str(state.package.package_id),
            }
        receipt = self._composition_assembly(mission, dispatch).resolve_one(
            mission_id, occurrence_id
        )
        return {
            "status": "NOT_READY" if receipt is None else "RESOLVED",
            "resolution_id": None if receipt is None else receipt.resolution_id,
        }

    def _assembly_missing(self, mission: Mission, *, at: str) -> bool:
        """Fail-closed: with no assembly installed a Mission is not scheduled at all.

        Review finding F1: :data:`~.hierarchical_dispatch.ASSEMBLY_MISSING` is recorded
        once for the Mission and its callers do nothing rather than fall back.  There
        is no auto-assembly to prefer over it: ``build_planning_world`` needs the
        deployment's own facts (which domains, which worktree, which layers are
        deployed, which observers) and an Orchestrator that guessed them would be
        inventing the declarations the plan is admitted against.
        """

        if self._wiring.hierarchical is not None:
            return False
        record_assembly_missing(self.store, mission, at=at)
        self._note(
            f"mission {mission.id} runs under hierarchical semantics and this deployment "
            f"installed no assembly; nothing is dispatched or judged at {at} "
            "(§18.5 rule 1 — call install_hierarchical())"
        )
        return True

    def _refuse_unsupported_contract(self, mission: Mission) -> bool:
        """Stop a Mission built under a contract this build dropped.

        2026-10-01 / 10-02: one orchestration mode, one planning protocol, one package
        version, no old-data compatibility.  A flat-mode Mission left in the library, a
        hierarchical one with no protocol binding (created under the removed
        proposal-text protocol) or with a binding to another package is ended here, by
        name, before anything is planned, dispatched or judged for it — never served on
        a fallback path and never allowed to take the loop down.
        """

        if mission.id in self._rounds.contract_checked:
            return False
        if not is_hierarchical(mission):
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_orchestration_semantics",
                detail={"error": "the flat orchestration mode was removed on 2026-10-02"},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: flat orchestration mode removed → stopped")
            return True
        try:
            # 删旧平面模式第三刀第 4 步: a domain snapshot frozen under another profile
            # schema is not read through a compatibility path.
            self.commit.domain_for(mission.id)
        except CommitRejected as error:
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_domain_profile",
                detail={"error": str(error)[:300]},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: {error} → stopped")
            return True

        try:
            require_bound(self.store, mission.id)
            bound = True
        except NotBoundError:
            bound = False
        if not bound or not is_assured(self.store, mission.id):
            # 2026-10-03 (A′ release review): a Mission of a development library created
            # before every Mission was TaskGraph-bound and assured at creation is ended
            # here, by name, never served nor allowed to stop the loop for the others.
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_unbound_mission",
                detail={"error": "created before every Mission was TaskGraph-bound and assured"},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: not TaskGraph-bound/assured at creation → stopped")
            return True

        try:
            current_planning_protocol(self.store, mission.id)
            # The same goes for the planning deployment the Mission froze at its first
            # round: an identity this process does not produce is not replanned.
            conflict = frozen_deployment_conflict(self, mission.id)
            if conflict is not None:
                raise UnsupportedPlanningPackage(conflict)
        except UnsupportedPlanningPackage as error:
            if mission.status is MissionStatus.CREATED:
                self.commit.begin_planning(mission.id)
            self._stop_planning_round(
                mission.id,
                reason="unsupported_planning_package",
                detail={"error": str(error)[:300]},
                stop_reason=MissionStopReason.PLANNING_FAILED,
            )
            self._note(f"mission {mission.id}: {error} → stopped")
            return True
        self._rounds.contract_checked.add(mission.id)
        return False

    async def _plan_integrity_stop(self, mission: Mission, error: Exception) -> None:
        """Stop *this* Mission for a damaged plan and leave the run alone (§24.1 dec. 11).

        ``GraphIntegrityError`` is a ``RuntimeError``, and ``_cycle`` only forgives
        ``StoreBusy`` / ``CommitRejected`` / ``IllegalTransition`` — so an unguarded
        one would end ``run()`` and take every *other* Mission in this process down
        with it.  One Mission's corruption is one Mission's stop: the diagnosis is
        recorded, the Mission fails with its open work cascaded, and the loop carries
        on with the rest.
        """

        if isinstance(error, GraphIntegrityError):
            if self._wiring.hierarchical is not None:
                dispatch = self._dispatch_for(mission.id)
                if dispatch is not None:
                    dispatch.record_integrity_failure(mission.id, error)
            detail = {
                "code": str(error.code),
                "subjects": sorted(str(item) for item in error.remaining),
                "cycle": [str(item) for item in error.cycle],
                "diagnose": error.diagnose()[:600],
            }
        else:  # a stored record that fails its own integrity check (history, inputs, sources)
            detail = {
                "code": str(error.code) if isinstance(error, CodedFault) else type(error).__name__,
                "subjects": [],
                "cycle": [],
                "diagnose": str(error)[:600],
            }
        current = self.store.get_mission(mission.id)
        status = mission.status if current is None else current.status
        if status is MissionStatus.CREATED:
            # Damaged before its first planning round: stopped the same way, by name
            # (TaskGraph 补全第二批; the contract gate does the same for an unbound one).
            self.commit.begin_planning(mission.id)
            status = MissionStatus.PLANNING
        if status is MissionStatus.PLANNING:
            self._commit_fail_planning(mission.id, reason="plan_integrity", detail=detail)
        elif status is MissionStatus.ACTIVE:
            self._commit_fail_mission(
                mission.id, stop_reason=MissionStopReason.PLANNING_FAILED, detail=detail
            )
        else:  # already terminal, or not yet planning: the record is the whole answer
            self._note(f"mission {mission.id}: plan integrity failure while {status!s}")
            return
        await self._release_mission(mission.id)
        self._note(f"mission {mission.id} stopped: plan integrity ({detail['code']})")
