"""Continuations, admission and ReAct boundary persistence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    TERMINAL_ADMISSION_PHASES,
    TERMINAL_RUN_STATUSES,
    ActorContext,
    AdmissionBoundary,
    AdmissionLaunchClaim,
    AdmissionPhase,
    AdmissionResolution,
    AdmissionSpec,
    CreateRunResult,
    DecisionConflict,
    DecisionNotFound,
    DecisionOpen,
    DecisionSignal,
    DeliverySpec,
    IdempotencyConflict,
    OutcomeStatus,
    PersistenceLevel,
    PersistenceRequired,
    RecoveryLease,
    RunCreate,
    RunEventCandidate,
    RunNotFound,
    RunRef,
    RunStartSnapshotRecord,
    RunStatus,
    TerminalConflict,
    VersionConflict,
    canonical_json,
    fingerprint_json,
    thaw_json,
)
from deskpet.types.task_grants import TaskGrant

from .core import ExecutionUowCore
from .support import ContinuationRecord, RuntimeActivationError


class ContinuationAdmissionMixin(ExecutionUowCore):
    """Continuations, admission and ReAct boundary persistence."""

    __slots__ = ()

    @staticmethod
    def _row_to_continuation(row: Mapping[str, Any]) -> ContinuationRecord:
        payload = json.loads(str(row["pending_prepared_call_json"]))
        if not isinstance(payload, dict):
            raise RuntimeActivationError(
                "continuation_payload_corrupt",
                "persisted continuation payload must be a JSON object",
            )
        return ContinuationRecord(
            run_id=str(row["run_id"]),
            payload=payload,
            version=int(row["continuation_version"]),
            pending_decision_id=(
                str(row["pending_decision_id"]) if row["pending_decision_id"] is not None else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    async def _load_continuation_tx(
        self,
        db: aiosqlite.Connection,
        run_id: str,
    ) -> ContinuationRecord | None:
        row = await (
            await db.execute("SELECT * FROM execution_continuations WHERE run_id=?", (run_id,))
        ).fetchone()
        return None if row is None else self._row_to_continuation(row)

    async def _continuation_run_tx(self, db: aiosqlite.Connection, run_id: str) -> aiosqlite.Row:
        row = await self._recovery_run_tx(db, run_id)
        if str(row["persistence_level"]) != PersistenceLevel.DURABLE.value:
            raise PersistenceRequired(
                "continuation_requires_durable_run",
                "continuations require a durable execution run",
            )
        return row

    async def _recovery_run_tx(self, db: aiosqlite.Connection, run_id: str) -> aiosqlite.Row:
        """Load an existing run only when its durable owner is currently active."""

        row = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
        self._assert_run_owner(row, await self._required_recovery_owner_tx(db))
        return row

    async def _insert_continuation_decision_tx(
        self,
        db: aiosqlite.Connection,
        decision: DecisionOpen,
        *,
        run: Mapping[str, Any],
        now: float,
    ) -> None:
        if decision.run_id != str(run["run_id"]):
            raise DecisionConflict(
                "decision_run_mismatch",
                "continuation and pending decision must belong to the same run",
            )
        existing_row = await (
            await db.execute(
                """SELECT * FROM execution_decisions
                WHERE decision_id=? OR (run_id=? AND nonce=?)
                ORDER BY CASE WHEN decision_id=? THEN 0 ELSE 1 END LIMIT 1""",
                (
                    decision.decision_id,
                    decision.run_id,
                    decision.nonce,
                    decision.decision_id,
                ),
            )
        ).fetchone()
        if existing_row is not None:
            self._assert_decision_intent(self._row_to_decision(existing_row), decision)
            return
        if (
            str(run["status"]) in {status.value for status in TERMINAL_RUN_STATUSES}
            or str(run["status"]) == RunStatus.CANCEL_REQUESTED.value
        ):
            raise DecisionConflict(
                "run_not_signalable",
                "cancelled or terminal runs cannot open decisions",
            )
        if decision.expires_at is not None and decision.expires_at <= now:
            raise DecisionConflict("decision_expired", "cannot open an already expired decision")
        if decision.capability_hash is not None and decision.capability_hash != str(
            run["capability_hash"]
        ):
            raise DecisionConflict(
                "decision_binding_mismatch",
                "decision capability does not match the durable run context",
            )
        await db.execute(
            """INSERT INTO execution_decisions(
            decision_id,schema_version,run_id,nonce,kind,status,
            prompt_schema_version,prompt_json,response_schema_version,response_json,
            domain_kind,domain_id,call_id,effect_id,tool_name,args_hash,
            capability_hash,scope_hash,decision_version,expires_at,created_at,resolved_at
            ) VALUES(?,?,?,?,?,'open',?,?,NULL,NULL,?,?,?,?,?,?,?,?,0,?,?,NULL)""",
            (
                decision.decision_id,
                decision.schema_version,
                decision.run_id,
                decision.nonce,
                decision.kind.value,
                decision.prompt_schema_version,
                canonical_json(thaw_json(decision.prompt)),
                decision.domain_kind,
                decision.domain_id,
                decision.call_id,
                decision.effect_id,
                decision.tool_name,
                decision.args_hash,
                decision.capability_hash,
                decision.scope_hash,
                decision.expires_at,
                now,
            ),
        )

    @staticmethod
    def _continuation_payload_json(payload: Mapping[str, Any]) -> str:
        normalized = thaw_json(payload)
        if not isinstance(normalized, dict):
            raise ValueError("continuation payload must be a JSON object")
        return canonical_json(normalized)

    @staticmethod
    def _validate_continuation_version(expected_version: int) -> None:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be non-negative")

    async def _save_continuation_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        expected_version: int,
        payload_json: str,
        decision: DecisionOpen | None,
        now: float,
    ) -> tuple[ContinuationRecord, bool]:
        run_id = str(run["run_id"])
        existing = await (
            await db.execute("SELECT * FROM execution_continuations WHERE run_id=?", (run_id,))
        ).fetchone()
        decision_id = decision.decision_id if decision is not None else None
        if existing is None:
            if expected_version != 0:
                raise VersionConflict(
                    "stale_continuation_version",
                    "new continuation must start from expected version zero",
                )
        elif int(existing["continuation_version"]) == expected_version + 1:
            replay = self._row_to_continuation(existing)
            if (
                canonical_json(thaw_json(replay.payload)) == payload_json
                and replay.pending_decision_id == decision_id
            ):
                if decision is not None:
                    await self._insert_continuation_decision_tx(db, decision, run=run, now=now)
                return replay, True
            raise VersionConflict(
                "continuation_replay_conflict",
                "continuation retry supplied different payload or decision intent",
            )
        elif int(existing["continuation_version"]) != expected_version:
            raise VersionConflict(
                "stale_continuation_version",
                "continuation changed before the save CAS",
            )
        if decision is not None:
            await self._insert_continuation_decision_tx(db, decision, run=run, now=now)
        self._fault("continuation_after_decision")
        next_version = expected_version + 1
        if existing is None:
            await db.execute(
                """INSERT INTO execution_continuations(
                run_id,schema_version,command_schema_version,canonical_messages_json,
                session_projection_cursor,prepared_context_ref,tool_set_snapshot_ref,
                pending_prepared_call_json,pending_decision_id,iteration,
                provider_state_json,continuation_version,created_at,updated_at
                ) VALUES(?,1,1,'[]',0,NULL,NULL,?,?,0,'{}',?,?,?)""",
                (run_id, payload_json, decision_id, next_version, now, now),
            )
        else:
            cursor = await db.execute(
                """UPDATE execution_continuations
                SET pending_prepared_call_json=?,pending_decision_id=?,
                    continuation_version=?,updated_at=?
                WHERE run_id=? AND continuation_version=?""",
                (
                    payload_json,
                    decision_id,
                    next_version,
                    now,
                    run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_continuation_version",
                    "continuation changed before the save CAS",
                )
        row = await (
            await db.execute("SELECT * FROM execution_continuations WHERE run_id=?", (run_id,))
        ).fetchone()
        assert row is not None
        return self._row_to_continuation(row), False

    @staticmethod
    def _admission(record: ContinuationRecord) -> AdmissionBoundary:
        value = record.payload.get("_admission")
        if not isinstance(value, Mapping):
            raise DecisionNotFound("admission_not_found", "continuation has no admission boundary")
        boundary = AdmissionBoundary.from_dict(value)
        if boundary.run_id != record.run_id or boundary.boundary_version != record.version:
            raise RuntimeActivationError(
                "admission_boundary_corrupt", "admission and continuation fences differ"
            )
        return boundary

    async def _admission_tx(self, db: aiosqlite.Connection, run_id: str):
        record = await self._load_continuation_tx(db, run_id)
        if record is None:
            raise DecisionNotFound("admission_not_found", "run has no admission continuation")
        return record, self._admission(record)

    @staticmethod
    def _with_admission(payload: Mapping[str, Any], boundary: AdmissionBoundary) -> dict[str, Any]:
        return {**payload, "_admission": boundary.to_dict()}

    @staticmethod
    def _advance_admission(
        boundary: AdmissionBoundary,
        phase: AdmissionPhase,
        version: int,
        *,
        consumed: bool | None = None,
        resolution_fingerprint: str | None = None,
    ) -> AdmissionBoundary:
        terminal = phase in TERMINAL_ADMISSION_PHASES if consumed is None else consumed
        value = boundary.to_dict()
        value.update(phase=phase.value, boundary_version=version, consumed=terminal)
        if resolution_fingerprint is not None:
            value["resolution_fingerprint"] = resolution_fingerprint
        return AdmissionBoundary.from_dict(value)

    @staticmethod
    def _resolved_admission_event(
        boundary: AdmissionBoundary, phase: AdmissionPhase | None = None
    ) -> RunEventCandidate:
        phase = phase or boundary.phase
        terminal = phase in {
            AdmissionPhase.REJECTED,
            AdmissionPhase.CANCELLED,
            AdmissionPhase.EXPIRED,
        }
        return RunEventCandidate(
            event_key="run:final" if terminal else "admission:accepted",
            kind="run.final" if terminal else "admission.accepted",
            status=OutcomeStatus.CANCELLED if terminal else OutcomeStatus.ACCEPTED,
            driver_kind=boundary.driver_kind,
            correlation={
                "decision_id": boundary.decision_id,
                "launch_operation_id": boundary.launch_operation_id,
            },
            payload={"admission_phase": phase.value, "consumed": terminal},
        )

    async def start_admission(
        self,
        spec: RunCreate,
        admission: AdmissionSpec,
        start_snapshot: AdmissionBoundary,
        waiting_event: RunEventCandidate,
        *,
        run_start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        deliveries: Sequence[DeliverySpec] = (),
    ) -> AdmissionBoundary:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired("admission_must_be_durable", "admission runs are durable")
        expected = (
            spec.run_id,
            spec.driver_kind,
            spec.profile_key,
            admission,
            AdmissionPhase.PENDING,
            1,
            False,
        )
        actual = (
            start_snapshot.run_id,
            start_snapshot.driver_kind,
            start_snapshot.profile_key,
            start_snapshot.admission,
            start_snapshot.phase,
            start_snapshot.boundary_version,
            start_snapshot.consumed,
        )
        if actual != expected or waiting_event.status is not OutcomeStatus.WAITING:
            raise DecisionConflict(
                "invalid_admission_boundary", "admission start facts do not align"
            )
        decision = DecisionOpen(
            start_snapshot.decision_id,
            spec.run_id,
            start_snapshot.nonce,
            admission.kind,
            admission.prompt_schema_version,
            thaw_json(admission.prompt),
            admission.expires_at,
        )
        association = (
            RunEventCandidate.from_dict(start_snapshot.association_event)
            if start_snapshot.association_event is not None
            else None
        )
        async with self._write_transaction() as db:
            run, created = await self._insert_run_tx(db, spec, version=0)
            if run_start_snapshot is not None:
                if run_start_snapshot.run_id != spec.run_id:
                    raise IdempotencyConflict(
                        "run_start_scope_conflict",
                        "admission Run start snapshot belongs to another Run",
                    )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=spec,
                    start_snapshot=run_start_snapshot,
                    run_created=created,
                    start_commit_extensions=start_commit_extensions,
                )
            elif start_commit_extensions:
                raise IdempotencyConflict(
                    "start_extension_without_snapshot",
                    "admission start extensions require a Run start snapshot",
                )
            prior = await self._load_continuation_tx(db, spec.run_id)
            if prior is not None:
                current = self._admission(prior)
                if (
                    self._advance_admission(current, AdmissionPhase.PENDING, 1, consumed=False)
                    != start_snapshot
                ):
                    raise IdempotencyConflict(
                        "admission_intent_conflict", "admission replay changed immutable facts"
                    )
                if association is not None:
                    await self._append_event_tx(
                        db,
                        run,
                        expected_version=int(run["version"]),
                        event=association,
                        deliveries=(),
                    )
                await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=waiting_event,
                    deliveries=deliveries,
                )
                return current
            if not created:
                raise IdempotencyConflict("admission_run_conflict", "existing run has no admission")
            self._fault("batch_boundary_after_promotion")
            await self._save_continuation_tx(
                db,
                run=run,
                expected_version=0,
                payload_json=self._continuation_payload_json(
                    self._with_admission({}, start_snapshot)
                ),
                decision=decision,
                now=float(self._clock()),
            )
            self._fault("batch_boundary_after_continuation")
            await db.execute(
                "UPDATE execution_runs SET status='waiting',updated_at=? WHERE run_id=?",
                (self._clock(), spec.run_id),
            )
            run = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,))
            ).fetchone()
            assert run is not None
            if association is not None:
                _, run, _ = await self._append_event_tx(
                    db, run, expected_version=int(run["version"]), event=association, deliveries=()
                )
            await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=waiting_event,
                deliveries=deliveries,
            )
            self._fault("batch_boundary_after_waiting_event")
            self._fault("batch_boundary_before_commit")
            return start_snapshot

    async def resolve_admission(
        self,
        ref: RunRef,
        actor: ActorContext,
        signal: DecisionSignal,
        *,
        expected_boundary_version: int,
        terminal_deliveries: Sequence[DeliverySpec] = (),
        admission_task_grant: TaskGrant | None = None,
    ) -> AdmissionResolution:
        async with self._write_transaction() as db:
            run = await self._continuation_run_tx(db, ref.run_id)
            run_record = self._authorize_run_row(
                run,
                expected_session_id=ref.expected_session_id,
                actor=actor,
            )
            record, boundary = await self._admission_tx(db, ref.run_id)
            resolution_fingerprint = fingerprint_json(signal.to_dict())
            decision_row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?", (boundary.decision_id,)
                )
            ).fetchone()
            assert decision_row is not None
            decision = self._row_to_decision(decision_row)
            cancel_accepted = (
                boundary.phase is AdmissionPhase.ACCEPTED_START_PENDING
                and not signal.allow
                and signal.response.get("resolution") == "cancelled"
            )
            if cancel_accepted:
                self._assert_signal_binding(decision, signal)
            if boundary.phase is not AdmissionPhase.PENDING and not cancel_accepted:
                same = (
                    signal.run_id == ref.run_id
                    and boundary.resolution_fingerprint == resolution_fingerprint
                )
                if not same:
                    raise DecisionConflict(
                        "admission_already_resolved", "another resolution already won"
                    )
                event, _, _ = await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=self._resolved_admission_event(
                        boundary,
                        AdmissionPhase.ACCEPTED_START_PENDING
                        if signal.allow and boundary.phase is not AdmissionPhase.EXPIRED
                        else None,
                    ),
                    deliveries=terminal_deliveries
                    if boundary.phase
                    in {AdmissionPhase.REJECTED, AdmissionPhase.CANCELLED, AdmissionPhase.EXPIRED}
                    else (),
                )
                return AdmissionResolution(boundary, decision, event, True)
            if record.version != expected_boundary_version:
                raise VersionConflict("stale_admission_version", "admission changed before resolve")
            expired = False
            if not cancel_accepted:
                decision, _, expired = await self._resolve_decision_tx(
                    db, signal, actor, now=float(self._clock())
                )
            now = float(self._clock())
            grant_fields = (
                "task_grant_id",
                "task_grant_version",
                "authorization_source",
                "policy_generation",
            )
            response_has_grant = any(name in signal.response for name in grant_fields)
            if admission_task_grant is None and response_has_grant:
                raise DecisionConflict(
                    "unexpected_admission_task_grant",
                    "admission response cannot assert TaskGrant provenance",
                )
            if admission_task_grant is not None:
                if not signal.allow or expired:
                    raise DecisionConflict(
                        "unexpected_admission_task_grant",
                        "TaskGrant is only valid for an accepted admission",
                    )
                grant = admission_task_grant
                expected_response = {
                    "task_grant_id": grant.task_grant_id,
                    "task_grant_version": grant.version,
                    "authorization_source": grant.source,
                    "policy_generation": grant.policy_generation,
                }
                if any(
                    signal.response.get(name) != value for name, value in expected_response.items()
                ):
                    raise DecisionConflict(
                        "admission_task_grant_binding_mismatch",
                        "TaskGrant differs from the durable admission response",
                    )
                if (
                    grant.root_run_id != run_record.context.root_run_id
                    or actor.root_run_id not in {None, run_record.context.root_run_id}
                    or grant.principal_id != actor.principal_id
                    or grant.source != "user"
                ):
                    raise DecisionConflict(
                        "admission_task_grant_principal_mismatch",
                        "TaskGrant principal, root, or source is invalid",
                    )
                policy_row = await (
                    await db.execute(
                        """SELECT mode,generation,updated_at
                        FROM authorization_policy_state
                        WHERE singleton_id=1"""
                    )
                ).fetchone()
                if (
                    policy_row is None
                    or str(policy_row["mode"]) != "manual"
                    or int(policy_row["generation"]) != grant.policy_generation
                    or (grant.expires_at is not None and now >= grant.expires_at)
                ):
                    raise DecisionConflict(
                        "admission_authorization_policy_changed",
                        "authorization policy changed before admission commit",
                    )
                grant_json = canonical_json(grant.to_dict())
                grant_row = await (
                    await db.execute(
                        "SELECT * FROM task_grants WHERE task_grant_id=?",
                        (grant.task_grant_id,),
                    )
                ).fetchone()
                if grant_row is None:
                    await db.execute(
                        """INSERT INTO task_grants(
                        task_grant_id,root_run_id,source,policy_generation,
                        version,grant_fingerprint,grant_json,status,created_at,
                        revoked_at
                        ) VALUES(?,?,?,?,?,?,?,'active',?,NULL)""",
                        (
                            grant.task_grant_id,
                            grant.root_run_id,
                            grant.source,
                            grant.policy_generation,
                            grant.version,
                            grant.fingerprint,
                            grant_json,
                            now,
                        ),
                    )
                elif (
                    str(grant_row["status"]) != "active"
                    or str(grant_row["grant_fingerprint"]) != grant.fingerprint
                    or str(grant_row["grant_json"]) != grant_json
                ):
                    raise DecisionConflict(
                        "admission_task_grant_conflict",
                        "TaskGrant identity belongs to another grant",
                    )
                self._fault("decision_resolve_after_grant")
                boundary_payload = boundary.to_dict()
                boundary_payload["request_payload"] = {
                    **dict(boundary.request_payload),
                    **expected_response,
                }
                boundary = AdmissionBoundary.from_dict(boundary_payload)
            phase = (
                AdmissionPhase.EXPIRED
                if expired
                else AdmissionPhase.ACCEPTED_START_PENDING
                if signal.allow
                else AdmissionPhase.CANCELLED
                if signal.response.get("resolution") == "cancelled"
                else AdmissionPhase.REJECTED
            )
            updated = self._advance_admission(
                boundary, phase, record.version + 1, resolution_fingerprint=resolution_fingerprint
            )
            continuation, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=record.version,
                payload_json=self._continuation_payload_json(
                    self._with_admission(record.payload, updated)
                ),
                decision=None,
                now=float(self._clock()),
            )
            self._fault("decision_resolve_after_boundary")
            event, changed, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=self._resolved_admission_event(updated),
                deliveries=terminal_deliveries
                if updated.phase
                in {AdmissionPhase.REJECTED, AdmissionPhase.CANCELLED, AdmissionPhase.EXPIRED}
                else (),
            )
            if updated.consumed:
                cursor = await db.execute(
                    """UPDATE execution_runs SET status='cancelled',terminal_event_id=?,
                    version=version+1,ended_at=?,updated_at=? WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                    (event.event_id, now, now, ref.run_id, int(changed["version"])),
                )
                if cursor.rowcount != 1:
                    raise TerminalConflict(
                        "terminal_conflict", "admission terminalization lost its CAS"
                    )
            else:
                await db.execute(
                    "UPDATE execution_runs SET status='queued',updated_at=? WHERE run_id=?",
                    (now, ref.run_id),
                )
            self._fault("decision_resolve_before_commit")
            return AdmissionResolution(self._admission(continuation), decision, event)

    async def claim_admission_launch(
        self,
        recovery_lease: RecoveryLease,
        *,
        expected_boundary_version: int,
    ) -> AdmissionLaunchClaim:
        async with self._write_transaction() as db:
            await self._assert_recovery_fence_tx(db, recovery_lease, run_id=recovery_lease.run_id)
            run = await self._continuation_run_tx(db, recovery_lease.run_id)
            if RunStatus(str(run["status"])) is not RunStatus.QUEUED:
                raise TerminalConflict(
                    "admission_launch_cancelled", "run is no longer queued for launch"
                )
            record, boundary = await self._admission_tx(db, recovery_lease.run_id)
            if (
                boundary.phase is AdmissionPhase.LAUNCH_CLAIMED
                and record.version == expected_boundary_version + 1
            ):
                return AdmissionLaunchClaim(boundary, recovery_lease, True)
            if (
                boundary.phase is not AdmissionPhase.ACCEPTED_START_PENDING
                or record.version != expected_boundary_version
            ):
                raise VersionConflict(
                    "stale_admission_version", "admission is not launch-claimable"
                )
            updated = self._advance_admission(
                boundary, AdmissionPhase.LAUNCH_CLAIMED, record.version + 1
            )
            saved, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=record.version,
                payload_json=self._continuation_payload_json(
                    self._with_admission(record.payload, updated)
                ),
                decision=None,
                now=float(self._clock()),
            )
            self._fault("effect_claim_after_grant")
            self._fault("effect_claim_after_effect")
            self._fault("effect_claim_before_commit")
            return AdmissionLaunchClaim(self._admission(saved), recovery_lease)

    async def persist_react_boundary(
        self,
        run: str | RunCreate,
        expected_continuation_version: int,
        payload: Mapping[str, Any],
        decision: DecisionOpen | None = None,
        *,
        expected_run_version: int | None = None,
        waiting_event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        admission_launch: AdmissionLaunchClaim | None = None,
        skill_scope_activation_values: Mapping[str, Any] | None = None,
    ) -> ContinuationRecord | tuple[CreateRunResult, ContinuationRecord]:
        """Persist either an existing or newly promoted ReAct boundary."""

        self._validate_continuation_version(expected_continuation_version)
        payload_json = self._continuation_payload_json(payload)
        async with self._write_transaction() as db:
            if isinstance(run, RunCreate):
                if admission_launch is not None:
                    raise DecisionConflict(
                        "admission_run_conflict", "admission launch requires its precreated run"
                    )
                if run.persistence_level is not PersistenceLevel.DURABLE:
                    raise PersistenceRequired(
                        "promotion_target_not_durable",
                        "batch-boundary promotion target must be durable",
                    )
                if expected_run_version is None or expected_run_version < 0:
                    raise ValueError("expected_run_version must be non-negative")
                if waiting_event is None and deliveries:
                    raise IdempotencyConflict(
                        "delivery_without_event",
                        "batch-boundary deliveries require a waiting event",
                    )
                if waiting_event is not None and waiting_event.status is not OutcomeStatus.WAITING:
                    raise IdempotencyConflict(
                        "invalid_waiting_event",
                        "batch-boundary event must carry waiting status",
                    )
                row, created = await self._insert_run_tx(db, run, version=expected_run_version + 1)
                if str(row["persistence_level"]) != PersistenceLevel.DURABLE.value:
                    raise PersistenceRequired(
                        "promotion_not_durable", "persisted promotion is not durable"
                    )
                self._fault("batch_boundary_after_promotion")
                now = float(self._clock())
                record, replayed = await self._save_continuation_tx(
                    db,
                    run=row,
                    expected_version=expected_continuation_version,
                    payload_json=payload_json,
                    decision=decision,
                    now=now,
                )
                if not created and not replayed and int(row["version"]) != expected_run_version:
                    raise VersionConflict(
                        "stale_run_version",
                        f"expected run version {expected_run_version}, found {row['version']}",
                    )
                self._fault("batch_boundary_after_continuation")
                if decision is not None or waiting_event is not None:
                    cursor = await db.execute(
                        """UPDATE execution_runs SET status='waiting',updated_at=?
                        WHERE run_id=? AND terminal_event_id IS NULL
                        AND status IN ('created','queued','running','waiting')""",
                        (now, run.run_id),
                    )
                    if cursor.rowcount != 1:
                        raise DecisionConflict(
                            "run_not_signalable",
                            "cancelled or terminal runs cannot persist a waiting boundary",
                        )
                if waiting_event is not None:
                    _, row, _ = await self._append_event_tx(
                        db,
                        row,
                        expected_version=int(row["version"]),
                        event=waiting_event,
                        deliveries=deliveries,
                    )
                else:
                    row = await (
                        await db.execute(
                            "SELECT * FROM execution_runs WHERE run_id=?", (run.run_id,)
                        )
                    ).fetchone()
                    assert row is not None
                self._fault("batch_boundary_after_waiting_event")
                result = CreateRunResult(self._row_to_record(row), created)
                self._fault("batch_boundary_before_commit")
                await db.commit()
                return result, record
            run_id = run
            if skill_scope_activation_values is not None:
                expected_activation_version = expected_continuation_version + 1
                if (
                    skill_scope_activation_values.get("continuation_version")
                    != expected_activation_version
                    or str(skill_scope_activation_values.get("run_id") or "") != run_id
                ):
                    raise IdempotencyConflict(
                        "skill_scope_activation_conflict",
                        "scope receipt does not name this continuation",
                    )
            if recovery_lease is not None:
                await self._assert_recovery_fence_tx(db, recovery_lease, run_id=run_id)
            run_row = await self._continuation_run_tx(db, run_id)
            existing = await self._load_continuation_tx(db, run_id)
            if existing is None and admission_launch is not None:
                raise DecisionNotFound("admission_not_found", "launch claim has no continuation")
            if existing is not None:
                try:
                    boundary = self._admission(existing)
                except DecisionNotFound:
                    boundary = None
                if boundary is not None and not boundary.consumed and admission_launch is None:
                    raise DecisionConflict(
                        "admission_launch_required",
                        "unconsumed admission requires its typed launch claim",
                    )
                if admission_launch is not None:
                    if boundary != admission_launch.boundary:
                        raise VersionConflict(
                            "stale_admission_version", "launch claim differs from continuation"
                        )
                    await self._assert_recovery_fence_tx(
                        db, admission_launch.recovery_lease, run_id=run_id
                    )
                if boundary is not None:
                    phase = (
                        AdmissionPhase.LAUNCHED if admission_launch is not None else boundary.phase
                    )
                    boundary = self._advance_admission(
                        boundary, phase, expected_continuation_version + 1
                    )
                    payload_json = self._continuation_payload_json(
                        self._with_admission(payload, boundary)
                    )
            now = float(self._clock())
            if skill_scope_activation_values is not None:
                await self.bind(db).insert_or_verify_skill_scope_activation(
                    skill_scope_activation_values,
                    created_at=now,
                )
            record, _ = await self._save_continuation_tx(
                db,
                run=run_row,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=decision,
                now=now,
            )
            if skill_scope_activation_values is not None:
                self._fault("skill_scope_activation_before_commit")
            self._fault("continuation_before_commit")
            await db.commit()
            return record

    async def load_continuation(self, run_id: str) -> ContinuationRecord | None:
        async with self._read_connection() as db:
            return await self._load_continuation_tx(db, run_id)
