# SPDX-License-Identifier: Apache-2.0
"""Convergence coordination and fences over original Store, never an effect ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contracts.models import Event
from ..graph.convergence import ConvergenceTarget, compute_convergence_impact
from ..graph.execution_contracts import PreviewBindingV1
from ..graph.network_codec import NetworkDocumentV1
from ..graph.notification_contracts import FollowupCauseRef, FollowupKind, FollowupV1
from ..planning.htn.grounding import derive_id
from .store import Store, StoreConflict, StoreError
from .taskgraph_followups import TaskGraphFollowupStore


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvergenceJob:
    job_id: str
    mission_id: str
    decision_id: str
    request_id: str
    command_id: str
    source_revision: int
    candidate_hash: str
    impact_hash: str
    state: str
    row_version: int
    created_at: float
    updated_at: float
    targets: tuple[ConvergenceTarget, ...]


class ConvergenceAuthority(Protocol):
    """Required synchronous actual-source readers. An unavailable source must raise.

    Implementations belong to the existing H1 admission/runtime adapters. These
    methods must read the supplied Store snapshot, never perform external IO.
    """

    def validate_begin(
        self,
        store: Store,
        caller: object,
        preview: PreviewBindingV1,
        before: NetworkDocumentV1,
        candidate: NetworkDocumentV1,
    ) -> None:
        """Verify current grant, base, all sources, structure and computed impact."""
        ...

    def require_quiescence(self, store: Store, job: ConvergenceJob) -> None:
        """Verify all target attempts/intents/operations and external outcomes complete."""
        ...

    def require_reconciliation_started(self, store: Store, job: ConvergenceJob) -> None:
        """Require actual stable cancellation/reconciliation commands or foreign owner."""
        ...

    def require_safe_abandonment(
        self, store: Store, job: ConvergenceJob, caller: object, command_id: str
    ) -> None:
        """Verify operator authority, effects and explicit old-demand restoration."""
        ...


class TaskGraphConvergenceStore:
    def __init__(self, store: Store, *, authority: ConvergenceAuthority) -> None:
        self.store = store
        self.authority = authority

    def get_job(self, mission_id: str, job_id: str) -> ConvergenceJob:
        with self.store.read_view() as db:
            row = db.execute(
                "SELECT * FROM taskgraph_convergence_jobs WHERE mission_id=? AND job_id=?",
                (mission_id, job_id),
            ).fetchone()
            if row is None:
                raise StoreError("TASKGRAPH_CONVERGENCE_MISSING")
            targets = tuple(
                ConvergenceTarget(**dict(item))
                for item in db.execute(
                    "SELECT occurrence_id,task_id,expected_generation,target_kind FROM taskgraph_convergence_targets "
                    "WHERE mission_id=? AND job_id=? ORDER BY occurrence_id",
                    (mission_id, job_id),
                )
            )
            return ConvergenceJob(**dict(row), targets=targets)

    def begin_convergence(
        self,
        *,
        caller: object,
        preview: PreviewBindingV1,
        before: NetworkDocumentV1,
        candidate: NetworkDocumentV1,
        command_id: str,
        event: Event,
        now_ms: int,
    ) -> ConvergenceJob:
        """Fence, original event, targets and notification commit atomically."""
        impact = compute_convergence_impact(before, candidate)
        if (
            preview.mission_id != impact.mission_id
            or preview.base_revision != impact.source_revision
            or preview.candidate_hash != impact.candidate_hash
            or not impact.targets
        ):
            raise StoreError("TASKGRAPH_CONVERGENCE_PREVIEW_MISMATCH")
        job_id = derive_id("tg-converge", impact.mission_id, preview.decision_id)
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT * FROM taskgraph_convergence_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if old is not None:
                if (
                    old["candidate_hash"] != impact.candidate_hash
                    or old["impact_hash"] != impact.content_hash
                    or old["request_id"] != preview.request_id
                    or old["command_id"] != command_id
                ):
                    raise StoreConflict("TASKGRAPH_CONVERGENCE_IDEMPOTENCY_CONFLICT")
                # The caller still needs current read/command authority, even on replay.
                self.authority.validate_begin(self.store, caller, preview, before, candidate)
                return self.get_job(impact.mission_id, job_id)
            self.authority.validate_begin(self.store, caller, preview, before, candidate)
            # One live convergence per Mission: an older live fence ends here, replaced by
            # the new decision's (阶段 B 裁决第 5 类) — never two fences stacked.
            for stale in db.execute(
                    "SELECT job_id FROM taskgraph_convergence_jobs WHERE mission_id=? AND decision_id<>? "
                    "AND state IN ('FENCED','WAITING','READY')",
                    (impact.mission_id, preview.decision_id)).fetchall():
                self._transition(self.get_job(impact.mission_id, str(stale[0])), "ABANDONED", now_ms,
                                 reason="superseded_by_new_decision")
            active = db.execute(
                "SELECT revision FROM plan_revisions WHERE mission_id=? AND state='ACTIVE'",
                (impact.mission_id,),
            ).fetchall()
            if len(active) != 1 or active[0][0] != impact.source_revision:
                raise StoreConflict("TASKGRAPH_CONVERGENCE_BASE_STALE")
            # Exact frozen history supplies original generation; current binding cannot drift.
            for target in impact.targets:
                current = db.execute(
                    "SELECT dispatch_generation FROM task_semantics WHERE mission_id=? AND task_id=? "
                    "ORDER BY binding_revision DESC LIMIT 1",
                    (impact.mission_id, target.task_id),
                ).fetchone()
                if current is None or current[0] != target.expected_generation:
                    raise StoreConflict("TASKGRAPH_CONVERGENCE_GENERATION_STALE")
            if (
                event.mission_id != impact.mission_id
                or event.payload.get("job_id") != job_id
                or event.payload.get("candidate_hash") != impact.candidate_hash
                or event.payload.get("impact_hash") != impact.content_hash
            ):
                raise StoreError("TASKGRAPH_CONVERGENCE_EVENT_MISMATCH")
            db.execute(
                "INSERT INTO taskgraph_convergence_jobs VALUES (?,?,?,?,?,?,?,?,'FENCED',1,?,?)",
                (
                    job_id,
                    impact.mission_id,
                    preview.decision_id,
                    preview.request_id,
                    command_id,
                    impact.source_revision,
                    impact.candidate_hash,
                    impact.content_hash,
                    now_ms / 1000,
                    now_ms / 1000,
                ),
            )
            db.executemany(
                "INSERT INTO taskgraph_convergence_targets VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        job_id,
                        impact.mission_id,
                        impact.source_revision,
                        item.occurrence_id,
                        item.task_id,
                        item.expected_generation,
                        item.target_kind,
                    )
                    for item in impact.targets
                ],
            )
            persisted = self.store.append_event(event)
            persisted_body, proposed_body = persisted.to_json(), event.to_json()
            persisted_body.pop("seq")
            proposed_body.pop("seq")
            if persisted_body != proposed_body:
                raise StoreConflict("TASKGRAPH_CONVERGENCE_EVENT_CONFLICT")
            TaskGraphFollowupStore(self.store).append_followup(
                FollowupV1(
                    mission_id=impact.mission_id,
                    source_event_id=event.id,
                    kind=FollowupKind.CONVERGE,
                    subject_key=job_id,
                    source_revision=impact.source_revision,
                    cause_ref=FollowupCauseRef(
                        kind="convergence_job",
                        id=job_id,
                        revision=1,
                        content_hash=impact.content_hash,
                    ),
                ),
                now_ms=now_ms,
            )
            return self.get_job(impact.mission_id, job_id)

    def active_fences(self, mission_id: str, task_id: str) -> tuple[dict[str, object], ...]:
        with self.store.read_view() as db:
            return tuple(
                dict(row)
                for row in db.execute(
                    "SELECT t.job_id,t.occurrence_id,t.expected_generation,j.state,j.row_version "
                    "FROM taskgraph_convergence_targets t JOIN taskgraph_convergence_jobs j ON j.job_id=t.job_id "
                    "WHERE t.mission_id=? AND t.task_id=? AND j.state IN ('FENCED','WAITING','READY') "
                    "ORDER BY t.job_id,t.occurrence_id",
                    (mission_id, task_id),
                )
            )

    def require_unfenced(self, mission_id: str, task_id: str) -> None:
        if self.active_fences(mission_id, task_id):
            raise StoreConflict("TASKGRAPH_TARGET_FENCED")

    def advance_state(
        self, mission_id: str, job_id: str, *, expected_version: int, ready: bool, now_ms: int
    ) -> ConvergenceJob:
        with self.store.transaction():
            job = self._current(mission_id, job_id, expected_version)
            if job.state not in ("FENCED", "WAITING", "READY"):
                raise StoreConflict("TASKGRAPH_CONVERGENCE_TERMINAL")
            if ready:
                self.authority.require_quiescence(self.store, job)
                if job.state == "READY":
                    return job
            else:
                self.authority.require_reconciliation_started(self.store, job)
            self._transition(job, "READY" if ready else "WAITING", now_ms)
            return self.get_job(mission_id, job_id)

    def mark_applied(
        self,
        mission_id: str,
        job_id: str,
        *,
        expected_version: int,
        committed_revision: int,
        now_ms: int,
    ) -> ConvergenceJob:
        if not self.store.connection.in_transaction:
            raise StoreError("TASKGRAPH_APPLIED_REQUIRES_COMMIT_TRANSACTION")
        with self.store.transaction() as db:
            job = self._current(mission_id, job_id, expected_version)
            if job.state != "READY":
                raise StoreConflict("TASKGRAPH_CONVERGENCE_NOT_READY")
            self.authority.require_quiescence(self.store, job)
            # An actual graph record, actual APPLIED plan check and matching Commit
            # command must exist in this SAME transaction before the fence is removed.
            row = db.execute(
                "SELECT r.manifest_hash,r.command_id,c.phase FROM taskgraph_revision_records r "
                "JOIN planning_admission_checks c ON c.check_id=r.admission_check_id "
                "JOIN plan_revisions p ON p.mission_id=r.mission_id AND p.revision=r.revision "
                "WHERE r.mission_id=? AND r.revision=? AND p.state='ACTIVE'",
                (mission_id, committed_revision),
            ).fetchone()
            if (
                committed_revision != job.source_revision + 1
                or row is None
                or tuple(row) != (job.candidate_hash, job.command_id, "APPLIED")
            ):
                raise StoreError("TASKGRAPH_CONVERGENCE_COMMIT_MISSING")
            self._transition(job, "APPLIED", now_ms)
            return self.get_job(mission_id, job_id)

    def abandon(
        self,
        mission_id: str,
        job_id: str,
        *,
        expected_version: int,
        caller: object,
        command_id: str,
        now_ms: int,
    ) -> ConvergenceJob:
        with self.store.transaction():
            job = self._current(mission_id, job_id, expected_version)
            if job.state not in ("FENCED", "WAITING", "READY"):
                raise StoreConflict("TASKGRAPH_CONVERGENCE_TERMINAL")
            self.authority.require_safe_abandonment(self.store, job, caller, command_id)
            self._transition(job, "ABANDONED", now_ms)
            return self.get_job(mission_id, job_id)

    def _current(self, mission: str, identity: str, version: int) -> ConvergenceJob:
        job = self.get_job(mission, identity)
        if job.row_version != version:
            raise StoreConflict("TASKGRAPH_CONVERGENCE_CAS_CONFLICT")
        return job

    def release_for_decision(self, mission_id: str, decision_id: str, *, reason: str, now_ms: int) -> bool:
        """End the live fence of one decision that will never be applied (阶段 B 裁决第 5 类).

        A fence belongs to one decision.  When that decision is finally refused (its
        commit rejected) the fence ends with it — lifecycle order, in the caller's
        transaction, with the transition event naming why.  Pins and demands were never
        removed by the fence, so ending it only restores the old plan's eligibility;
        the original dispatch path still admits every new Attempt.  The refusal itself
        still reaches the Planner through the planning ladder, unchanged."""

        if not self.store.connection.in_transaction:
            raise StoreError("TASKGRAPH_CONVERGENCE_RELEASE_TRANSACTION_REQUIRED")
        row = self.store.connection.execute(
            "SELECT job_id FROM taskgraph_convergence_jobs WHERE mission_id=? AND decision_id=? "
            "AND state IN ('FENCED','WAITING','READY')", (mission_id, decision_id)).fetchone()
        if row is None:
            return False
        self._transition(self.get_job(mission_id, str(row[0])), "ABANDONED", now_ms, reason=reason)
        return True

    def _transition(self, job: ConvergenceJob, state: str, now_ms: int, *, reason: str | None = None) -> None:
        from ..graph.notification_contracts import _integer

        _integer(now_ms, "now_ms")
        changed = self.store.connection.execute(
            "UPDATE taskgraph_convergence_jobs SET state=?,row_version=row_version+1,updated_at=? "
            "WHERE job_id=? AND mission_id=? AND state=? AND row_version=?",
            (state, now_ms / 1000, job.job_id, job.mission_id, job.state, job.row_version),
        )
        if changed.rowcount != 1:
            raise StoreConflict("TASKGRAPH_CONVERGENCE_CAS_CONFLICT")
        if state == job.state:
            return
        if state in ("APPLIED", "ABANDONED"):
            # 阶段 B 第 2 条（2026-10-03）：推进这个作业的通知连败被挡住后，作业结束了（人放弃、
            # 决定被拒或被新决定替代）。被挡的通知仍算"在等执行图来源"，任务就永远挂着。放回待发：
            # 原投递路径读到作业已结束，照常带回执了结，不另开一条路。
            self.store.connection.execute(
                "UPDATE taskgraph_followups SET delivery_state='PENDING',row_version=row_version+1,"
                "next_attempt_ms=? WHERE mission_id=? AND kind='CONVERGE' AND subject_key=? "
                "AND delivery_state='BLOCKED'", (now_ms, job.mission_id, job.job_id))
        # §10.1 auxiliary event, written only by this transition's own Commit; the new
        # row version is the transition's identity, so a retried Commit reuses it.
        version = job.row_version + 1
        identity = derive_id("tg-convergence-advanced", job.job_id, str(version))
        self.store.append_event(Event(
            id=identity, type="TaskGraphConvergenceAdvanced", trace_id=job.command_id,
            mission_id=job.mission_id, task_id=None, attempt_id=None, actor_type="system",
            actor_id="taskgraph-convergence",
            payload={"schema_version": 1, "job_id": job.job_id, "decision_id": job.decision_id,
                     "from_state": job.state, "to_state": state, "row_version": version,
                     **({"reason": reason} if reason is not None else {})},
            idempotency_key=identity, created_at=now_ms / 1000))
