"""Run outcome commit and blocked roots."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    TERMINAL_RUN_STATUSES,
    AdmissionLaunchUnknownFence,
    AdmissionPhase,
    DeliverySpec,
    FinalizeRunResult,
    IdempotencyConflict,
    OutcomeStatus,
    PersistenceLevel,
    PersistenceRequired,
    RecoveryLease,
    RunCreate,
    RunEventCandidate,
    RunIdentityConflict,
    RunNotFound,
    RunRecord,
    RunStartSnapshotRecord,
    RunStatus,
    TerminalConflict,
    VersionConflict,
    stable_event_id,
)

from .core import ExecutionUowCore


class RunOutcomeMixin(ExecutionUowCore):
    """Run outcome commit and blocked roots."""

    __slots__ = ()

    @staticmethod
    def _terminal_outcome(status: RunStatus) -> OutcomeStatus:
        return {
            RunStatus.COMPLETED: OutcomeStatus.SUCCEEDED,
            RunStatus.FAILED: OutcomeStatus.FAILED,
            RunStatus.CANCELLED: OutcomeStatus.CANCELLED,
        }[status]

    async def _consume_admission_failure_tx(
        self,
        db: aiosqlite.Connection,
        fence: AdmissionLaunchUnknownFence,
    ) -> None:
        run = await self._continuation_run_tx(db, fence.run_id)
        record, boundary = await self._admission_tx(db, fence.run_id)
        identity = (boundary.decision_id, boundary.launch_operation_id)
        if (
            boundary.phase is AdmissionPhase.LAUNCH_UNKNOWN
            and record.version == fence.expected_boundary_version + 1
        ):
            if identity == (fence.decision_id, fence.launch_operation_id):
                return
        if (
            boundary.phase not in {AdmissionPhase.LAUNCH_CLAIMED, AdmissionPhase.LAUNCHED}
            or record.version != fence.expected_boundary_version
            or identity != (fence.decision_id, fence.launch_operation_id)
        ):
            raise VersionConflict(
                "stale_admission_version", "launch-unknown fence differs from continuation"
            )
        updated = self._advance_admission(
            boundary, AdmissionPhase.LAUNCH_UNKNOWN, record.version + 1
        )
        await self._save_continuation_tx(
            db,
            run=run,
            expected_version=record.version,
            payload_json=self._continuation_payload_json(
                self._with_admission(record.payload, updated)
            ),
            decision=None,
            now=float(self._clock()),
        )

    async def commit_run_outcome(
        self,
        run_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus | None = None,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
        parent_signal_operation_id: str | None = None,
        parent_signal_value: Any = None,
        recovery_lease: RecoveryLease | None = None,
        cancel_reason: str | None = None,
        admission_failure: AdmissionLaunchUnknownFence | None = None,
        terminal_commit_extensions: Sequence[Any] = (),
        delivery_fence: Mapping[str, Any] | None = None,
        release_receipt_kind: str | None = None,
    ) -> FinalizeRunResult | RunRecord:
        if terminal_status is None:
            reason = str(cancel_reason or "").strip()
            if not reason or event.status != OutcomeStatus.CANCEL_REQUESTED:
                raise TerminalConflict(
                    "invalid_cancel_request",
                    "cancel requires a reason and cancel_requested event",
                )
            async with self._write_transaction() as db:
                run = await (
                    await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
                ).fetchone()
                if run is None:
                    raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
                if str(run["status"]) == RunStatus.CANCEL_REQUESTED.value:
                    existing = await (
                        await db.execute(
                            "SELECT * FROM execution_events WHERE run_id=? AND event_key=?",
                            (run_id, event.event_key),
                        )
                    ).fetchone()
                    if existing is None or str(run["cancel_reason"] or "") != reason:
                        raise IdempotencyConflict(
                            "cancel_intent_conflict",
                            "another cancel intent already owns the run",
                        )
                    self._assert_event_matches(existing, run, event)
                    await self._assert_delivery_set_tx(db, str(existing["event_id"]), deliveries)
                    await db.commit()
                    return self._row_to_record(run)
                _, updated, idempotent = await self._append_event_tx(
                    db,
                    run,
                    expected_version=expected_version,
                    event=event,
                    deliveries=deliveries,
                )
                if idempotent:
                    if (
                        str(updated["status"]) != RunStatus.CANCEL_REQUESTED.value
                        or str(updated["cancel_reason"] or "") != reason
                    ):
                        raise IdempotencyConflict(
                            "cancel_intent_conflict",
                            "cancel event exists without the same coarse cancel intent",
                        )
                else:
                    cursor = await db.execute(
                        """UPDATE execution_runs SET status='cancel_requested',cancel_reason=?,
                        updated_at=? WHERE run_id=? AND version=?
                        AND terminal_event_id IS NULL""",
                        (reason, self._clock(), run_id, int(updated["version"])),
                    )
                    if cursor.rowcount != 1:
                        raise VersionConflict(
                            "stale_run_version", "run changed before cancel commit"
                        )
                    await db.execute(
                        """UPDATE workflow_runs SET status='cancel_requested',cancel_reason=?,
                        lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
                        lease_epoch=lease_epoch+1,run_version=run_version+1,updated_at=?
                        WHERE run_id=? AND status NOT IN ('completed','failed','cancelled')""",
                        (reason, self._clock(), run_id),
                    )
                self._fault("cancel_before_commit")
                result = await (
                    await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
                ).fetchone()
                assert result is not None
                await db.commit()
                return self._row_to_record(result)
        terminal_status = RunStatus(terminal_status)
        if terminal_status not in TERMINAL_RUN_STATUSES:
            raise TerminalConflict(
                "non_terminal_finalize", "finalize requires a terminal RunStatus"
            )
        if event.status != self._terminal_outcome(terminal_status):
            raise TerminalConflict(
                "terminal_outcome_mismatch", "terminal event outcome differs from run status"
            )
        if admission_failure is not None and (
            terminal_status is not RunStatus.FAILED
            or event.kind != "run.final"
            or event.payload.get("error_code") != "launch_outcome_unknown"
            or event.payload.get("retry_safe") is not False
            or event.payload.get("launch_operation_id") != admission_failure.launch_operation_id
            or admission_failure.run_id != run_id
        ):
            raise TerminalConflict(
                "invalid_launch_unknown_outcome", "invalid launch-unknown final payload"
            )
        async with self._write_transaction() as db:
            run = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
            if run["parent_run_id"] is not None and any(
                item.sink_kind in {"goal_projection", "session_projection"} for item in deliveries
            ):
                raise RunIdentityConflict(
                    "child_terminal_projection", "child runs cannot project the root terminal"
                )
            child_command = await (
                await db.execute(
                    "SELECT operation_id FROM execution_child_commands WHERE child_run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if child_command is not None:
                operation_id = str(child_command["operation_id"])
                if parent_signal_operation_id != operation_id:
                    raise RunIdentityConflict(
                        "child_terminal_requires_parent_signal",
                        "child runs must use atomic terminal-to-parent signal finalization",
                    )
            if recovery_lease is not None:
                await self._assert_recovery_fence_tx(db, recovery_lease, run_id=run_id)
            if event.driver_kind != str(run["driver_kind"]):
                raise RunIdentityConflict(
                    "driver_event_conflict", "terminal event driver differs from run owner"
                )
            event_id = stable_event_id(run_id, event.event_key)
            if str(run["status"]) in {status.value for status in TERMINAL_RUN_STATUSES}:
                existing = await (
                    await db.execute("SELECT * FROM execution_events WHERE event_id=?", (event_id,))
                ).fetchone()
                if (
                    str(run["status"]) != terminal_status.value
                    or str(run["terminal_event_id"] or "") != event_id
                    or existing is None
                ):
                    raise TerminalConflict(
                        "terminal_conflict", "another terminal intent already won"
                    )
                try:
                    self._assert_event_matches(existing, run, event)
                except IdempotencyConflict as exc:
                    # A terminal event key is deliberately stable per run.  If
                    # two terminal candidates race, the second candidate must
                    # observe the already-settled winner instead of looking
                    # like a retry that changed its payload.
                    raise TerminalConflict(
                        "terminal_conflict", "another terminal intent already won"
                    ) from exc
                await self._assert_delivery_set_tx(db, event_id, deliveries)
                transaction = self.bind(db)
                for extension in terminal_commit_extensions:
                    verify = getattr(extension, "verify_terminal_replay", None)
                    if not callable(verify):
                        raise TerminalConflict(
                            "terminal_extension_replay_unverifiable",
                            "terminal extension cannot verify an idempotent replay",
                        )
                    verified = verify(
                        transaction,
                        record=self._row_to_record(run),
                        terminal_event=event,
                    )
                    if inspect.isawaitable(verified):
                        await verified
                if admission_failure is not None:
                    await self._consume_admission_failure_tx(db, admission_failure)
                hydrated = dict(existing)
                hydrated["root_run_id"] = run["root_run_id"]
                hydrated["session_id"] = run["session_id"]
                if parent_signal_operation_id is not None:
                    await self._enqueue_child_terminal_signal_tx(
                        db,
                        parent_signal_operation_id,
                        terminal_status=terminal_status.value,
                        value=parent_signal_value,
                    )
                await db.commit()
                return FinalizeRunResult(
                    record=self._row_to_record(run),
                    event=self._row_to_event(hydrated),
                    idempotent=True,
                )
            if int(run["version"]) != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {run['version']}",
                )
            now = float(self._clock())
            durable_seq = int(run["durable_seq"]) + 1
            payload = self._event_json(event)
            extension_receipts: list[Mapping[str, Any]] = []
            transaction = self.bind(db)
            for extension in terminal_commit_extensions:
                apply = getattr(extension, "apply_terminal_commit", None)
                if not callable(apply):
                    raise TypeError("terminal commit extension has no apply_terminal_commit")
                receipt = apply(
                    transaction,
                    record=self._row_to_record(run),
                    terminal_event=event,
                )
                if inspect.isawaitable(receipt):
                    receipt = await receipt
                if hasattr(receipt, "to_dict"):
                    receipt = receipt.to_dict()
                if not isinstance(receipt, Mapping) or set(receipt) != {
                    "kind",
                    "ref",
                    "content_hash",
                }:
                    raise TypeError("terminal extension must return kind/ref/content_hash")
                content_hash = str(receipt["content_hash"])
                if len(content_hash) != 64 or any(
                    ch not in "0123456789abcdef" for ch in content_hash
                ):
                    raise TypeError("terminal extension receipt hash must be lowercase SHA-256")
                extension_receipts.append(dict(receipt))
            release_receipt = None
            if release_receipt_kind is not None:
                matches = [
                    receipt
                    for receipt in extension_receipts
                    if str(receipt["kind"]) == release_receipt_kind
                ]
                if len(matches) != 1:
                    raise TerminalConflict(
                        "terminal_release_receipt_missing",
                        "terminal delivery requires exactly one release receipt",
                    )
                release_receipt = matches[0]
            if delivery_fence is not None and release_receipt_kind is None:
                raise TerminalConflict(
                    "terminal_release_receipt_missing",
                    "fenced terminal deliveries require a release receipt kind",
                )
            await db.execute(
                """INSERT INTO execution_events(
                event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
                correlation_json,payload_json,error_json,artifact_refs_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    event.schema_version,
                    event.event_key,
                    run_id,
                    durable_seq,
                    event.kind,
                    event.status.value,
                    event.driver_kind,
                    payload["correlation_json"],
                    payload["payload_json"],
                    payload["error_json"],
                    payload["artifact_refs_json"],
                    now,
                ),
            )
            for receipt_order, receipt in enumerate(extension_receipts):
                await db.execute(
                    """INSERT INTO execution_terminal_extension_receipts(
                    run_id,event_id,receipt_order,kind,ref,content_hash,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        event_id,
                        receipt_order,
                        str(receipt["kind"]),
                        str(receipt["ref"]),
                        str(receipt["content_hash"]),
                        now,
                    ),
                )
            await self._insert_deliveries_tx(
                db,
                run_id=run_id,
                event_id=event_id,
                deliveries=deliveries,
                now=now,
                delivery_fence=delivery_fence,
                release_receipt=release_receipt,
            )
            self._fault("finalize_after_outbox")
            if admission_failure is not None:
                await self._consume_admission_failure_tx(db, admission_failure)
            cursor = await db.execute(
                """UPDATE execution_runs SET status=?,terminal_event_id=?,durable_seq=?,
                version=version+1,updated_at=?,ended_at=?
                WHERE run_id=? AND version=? AND terminal_event_id IS NULL
                  AND status NOT IN ('completed','failed','cancelled')""",
                (
                    terminal_status.value,
                    event_id,
                    durable_seq,
                    now,
                    now,
                    run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise TerminalConflict("terminal_conflict", "another terminal intent already won")
            workflow = await (
                await db.execute("SELECT status FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if workflow is not None and str(workflow["status"]) not in {
                "completed",
                "failed",
                "cancelled",
            }:
                await db.execute(
                    """UPDATE workflow_runs SET status=?,lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,run_version=run_version+1,
                    updated_at=?,ended_at=COALESCE(ended_at,?) WHERE run_id=?""",
                    (terminal_status.value, now, now, run_id),
                )
            if parent_signal_operation_id is not None:
                self._fault("child_finalize_after_terminal")
                await self._enqueue_child_terminal_signal_tx(
                    db,
                    parent_signal_operation_id,
                    terminal_status=terminal_status.value,
                    value=parent_signal_value,
                )
                self._fault("child_finalize_after_parent_signal")
                self._fault("child_terminal_before_commit")
            self._fault("finalize_before_commit")
            updated = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            stored = await (
                await db.execute("SELECT * FROM execution_events WHERE event_id=?", (event_id,))
            ).fetchone()
            assert updated is not None and stored is not None
            await db.commit()
            hydrated = dict(stored)
            hydrated["root_run_id"] = updated["root_run_id"]
            hydrated["session_id"] = updated["session_id"]
            return FinalizeRunResult(
                record=self._row_to_record(updated),
                event=self._row_to_event(hydrated),
                idempotent=False,
            )

    async def start_blocked_root(
        self,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        *,
        event: RunEventCandidate,
        block_reporter: Any,
        start_commit_extensions: Sequence[Any] = (),
        deliveries: Sequence[DeliverySpec] = (),
    ) -> FinalizeRunResult:
        """Atomically create and fail a preflight-blocked durable Root.

        Provider/workspace/capability preflight can fail before the ordinary
        driver starts.  Persisting the minimum Root, immutable start evidence,
        failed final and structured block sidecar in one transaction makes that
        outcome visible without inventing a provider invocation.
        """

        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "durable_create_required",
                "a blocked preflight Root requires durable persistence",
            )
        if spec.context.parent_run_id is not None or spec.context.root_run_id != spec.run_id:
            raise RunIdentityConflict(
                "blocked_preflight_requires_root",
                "blocked preflight may only create a Root",
            )
        if start_snapshot.run_id != spec.run_id:
            raise IdempotencyConflict(
                "run_start_scope_conflict",
                "start snapshot belongs to another Run",
            )
        if event.status is not OutcomeStatus.FAILED or not event.is_terminal:
            raise TerminalConflict(
                "blocked_preflight_requires_failed_final",
                "blocked preflight requires a failed final event",
            )
        if event.driver_kind != spec.driver_kind:
            raise RunIdentityConflict(
                "driver_event_conflict",
                "terminal event driver differs from run owner",
            )
        async with self._write_transaction() as db:
            run, created = await self._insert_run_tx(db, spec, version=0)
            await self._ensure_start_snapshot_tx(
                db,
                spec=spec,
                start_snapshot=start_snapshot,
                run_created=created,
                start_commit_extensions=start_commit_extensions,
            )
            event_id = stable_event_id(spec.run_id, event.event_key)
            transaction = self.bind(db)
            if not created:
                if (
                    str(run["status"]) != RunStatus.FAILED.value
                    or str(run["terminal_event_id"] or "") != event_id
                ):
                    raise IdempotencyConflict(
                        "blocked_preflight_conflict",
                        "Root identity is already owned by another outcome",
                    )
                existing = await (
                    await db.execute("SELECT * FROM execution_events WHERE event_id=?", (event_id,))
                ).fetchone()
                if existing is None:
                    raise IdempotencyConflict(
                        "blocked_preflight_event_missing",
                        "blocked preflight terminal event is missing",
                    )
                self._assert_event_matches(existing, run, event)
                await self._assert_delivery_set_tx(db, event_id, deliveries)
                verified = block_reporter.verify_terminal_replay(
                    transaction,
                    record=self._row_to_record(run),
                    terminal_event=event,
                )
                if inspect.isawaitable(verified):
                    await verified
                hydrated = dict(existing)
                hydrated["root_run_id"] = run["root_run_id"]
                hydrated["session_id"] = run["session_id"]
                await db.commit()
                return FinalizeRunResult(
                    record=self._row_to_record(run),
                    event=self._row_to_event(hydrated),
                    idempotent=True,
                )

            receipt = block_reporter.apply_terminal_commit(
                transaction,
                record=self._row_to_record(run),
                terminal_event=event,
            )
            if inspect.isawaitable(receipt):
                receipt = await receipt
            if hasattr(receipt, "to_dict"):
                receipt = receipt.to_dict()
            if not isinstance(receipt, Mapping) or set(receipt) != {
                "kind",
                "ref",
                "content_hash",
            }:
                raise TypeError("block reporter returned an invalid terminal receipt")
            content_hash = str(receipt["content_hash"])
            if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
                raise TypeError("block reporter receipt hash must be lowercase SHA-256")
            now = float(self._clock())
            durable_seq = int(run["durable_seq"]) + 1
            payload = self._event_json(event)
            await db.execute(
                """INSERT INTO execution_events(
                event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
                correlation_json,payload_json,error_json,artifact_refs_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    event.schema_version,
                    event.event_key,
                    spec.run_id,
                    durable_seq,
                    event.kind,
                    event.status.value,
                    event.driver_kind,
                    payload["correlation_json"],
                    payload["payload_json"],
                    payload["error_json"],
                    payload["artifact_refs_json"],
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO execution_terminal_extension_receipts(
                run_id,event_id,receipt_order,kind,ref,content_hash,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    spec.run_id,
                    event_id,
                    0,
                    str(receipt["kind"]),
                    str(receipt["ref"]),
                    content_hash,
                    now,
                ),
            )
            await self._insert_deliveries_tx(
                db,
                run_id=spec.run_id,
                event_id=event_id,
                deliveries=deliveries,
                now=now,
            )
            cursor = await db.execute(
                """UPDATE execution_runs SET status='failed',terminal_event_id=?,
                durable_seq=?,version=version+1,updated_at=?,ended_at=?
                WHERE run_id=? AND version=0 AND terminal_event_id IS NULL""",
                (event_id, durable_seq, now, now, spec.run_id),
            )
            if cursor.rowcount != 1:
                raise TerminalConflict("terminal_conflict", "another terminal intent already won")
            updated = await (
                await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (spec.run_id,))
            ).fetchone()
            stored = await (
                await db.execute("SELECT * FROM execution_events WHERE event_id=?", (event_id,))
            ).fetchone()
            assert updated is not None and stored is not None
            await db.commit()
            hydrated = dict(stored)
            hydrated["root_run_id"] = updated["root_run_id"]
            hydrated["session_id"] = updated["session_id"]
            return FinalizeRunResult(
                record=self._row_to_record(updated),
                event=self._row_to_event(hydrated),
                idempotent=False,
            )
