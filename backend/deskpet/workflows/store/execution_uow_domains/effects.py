"""Effect handoff, dispatch marks, reconciliation and settlement."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    IdempotencyConflict,
    RecoveryLease,
    RunNotFound,
    StaleRecoveryLease,
    VersionConflict,
    canonical_json,
    thaw_json,
)
from deskpet.execution.delivery_contracts import DeliverySpec, RunEvent, RunEventCandidate
from deskpet.security.tool_public_projection import (
    ToolPresentationPolicyV1,
    ToolPublicProjectorV1,
    legacy_unknown_tool_policy,
)

from .core import ExecutionUowCore
from .support import ContinuationRecord


@dataclass(frozen=True, slots=True)
class ExecutionEffectSettlement:
    effect_id: str
    status: str
    effect_version: int
    continuation: ContinuationRecord
    event: RunEvent


@dataclass(frozen=True, slots=True)
class ExecutionEffectHandoff:
    effect_id: str
    attempt_no: int
    status: str
    handoff_state: str
    completion_disposition: str
    effect_version: int


class EffectHandoffMixin(ExecutionUowCore):
    """Effect handoff, dispatch marks, reconciliation and settlement."""

    __slots__ = ()

    @staticmethod
    def _effect_handoff_record(
        effect: Mapping[str, Any], attempt_no: int
    ) -> ExecutionEffectHandoff:
        return ExecutionEffectHandoff(
            effect_id=str(effect["effect_id"]),
            attempt_no=attempt_no,
            status=str(effect["status"]),
            handoff_state=str(effect["handoff_state"]),
            completion_disposition=str(effect["completion_disposition"]),
            effect_version=int(effect["effect_version"]),
        )

    async def _effect_attempt_tx(
        self,
        db: aiosqlite.Connection,
        effect_id: str,
        attempt_no: int,
    ) -> tuple[aiosqlite.Row, aiosqlite.Row]:
        effect = await (
            await db.execute("SELECT * FROM execution_effects WHERE effect_id=?", (effect_id,))
        ).fetchone()
        attempt = await (
            await db.execute(
                """SELECT * FROM execution_effect_attempts
                WHERE effect_id=? AND attempt_no=?""",
                (effect_id, attempt_no),
            )
        ).fetchone()
        if effect is None or attempt is None:
            raise RunNotFound("effect_not_found", "execution effect attempt does not exist")
        return effect, attempt

    async def read_effect_handoff(self, effect_id: str) -> ExecutionEffectHandoff | None:
        async with self._read_connection() as db:
            effect = await (
                await db.execute(
                    "SELECT execution_effects.*, ("
                    "SELECT MAX(attempt_no) FROM execution_effect_attempts "
                    "WHERE execution_effect_attempts.effect_id="
                    "execution_effects.effect_id"
                    ") AS latest_attempt_no FROM execution_effects "
                    "WHERE effect_id=?",
                    (effect_id,),
                )
            ).fetchone()
            if effect is None:
                return None
            attempt_no = int(effect["latest_attempt_no"] or 0)
            if attempt_no < 1:
                return None
            return self._effect_handoff_record(effect, attempt_no)

    @staticmethod
    def _receipt_pair(ref: str, digest: str) -> None:
        if not ref.strip():
            raise ValueError("effect handoff receipt ref is required")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("effect handoff receipt hash must be lowercase SHA-256")

    async def mark_effect_dispatch_not_started(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(receipt_ref, receipt_hash)
        async with self._write_transaction() as db:
            effect, attempt = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["status"]) == "cancelled":
                expected = (
                    "not_started",
                    "confirmed_not_started",
                    receipt_ref,
                    receipt_hash,
                )
                actual = (
                    str(effect["handoff_state"]),
                    str(effect["completion_disposition"]),
                    str(effect["handoff_ack_ref"] or ""),
                    str(effect["handoff_ack_hash"] or ""),
                )
                if actual != expected:
                    raise IdempotencyConflict(
                        "effect_not_started_conflict",
                        "effect has another not-started receipt",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "running"
                or str(effect["handoff_state"]) != "unresolved"
                or int(effect["effect_version"]) != expected_effect_version
                or str(attempt["status"]) != "running"
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot be marked not-started from its current state",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts
                SET status='cancelled',handoff_state='not_started',
                    completion_disposition='confirmed_not_started',
                    handoff_ack_ref=?,handoff_ack_hash=?,handoff_ack_at=?,
                    updated_at=?,ended_at=?
                WHERE effect_id=? AND attempt_no=? AND status='running'
                  AND handoff_state='unresolved'""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    attempt_no,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET status='cancelled',handoff_state='not_started',
                    completion_disposition='confirmed_not_started',
                    handoff_ack_ref=?,handoff_ack_hash=?,handoff_ack_at=?,
                    effect_version=effect_version+1,updated_at=?,ended_at=?
                WHERE effect_id=? AND status='running'
                  AND handoff_state='unresolved' AND effect_version=?""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict("stale_effect_handoff", "effect not-started CAS lost")
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def mark_effect_dispatch_started(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        ack_ref: str,
        ack_hash: str,
        *,
        ack_at: float | None = None,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(ack_ref, ack_hash)
        async with self._write_transaction() as db:
            effect, attempt = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["handoff_state"]) == "started":
                if (
                    str(effect["handoff_ack_ref"] or ""),
                    str(effect["handoff_ack_hash"] or ""),
                ) != (ack_ref, ack_hash):
                    raise IdempotencyConflict(
                        "effect_started_conflict",
                        "effect has another dispatch-start ack",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "running"
                or str(effect["handoff_state"]) != "unresolved"
                or int(effect["effect_version"]) != expected_effect_version
                or str(attempt["handoff_state"]) != "unresolved"
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot be marked started from its current state",
                )
            now = float(self._clock()) if ack_at is None else float(ack_at)
            await db.execute(
                """UPDATE execution_effect_attempts
                SET handoff_state='started',handoff_ack_ref=?,
                    handoff_ack_hash=?,handoff_ack_at=?,updated_at=?
                WHERE effect_id=? AND attempt_no=? AND status='running'
                  AND handoff_state='unresolved'""",
                (ack_ref, ack_hash, now, now, effect_id, attempt_no),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET handoff_state='started',handoff_ack_ref=?,
                    handoff_ack_hash=?,handoff_ack_at=?,
                    effect_version=effect_version+1,updated_at=?
                WHERE effect_id=? AND status='running'
                  AND handoff_state='unresolved' AND effect_version=?""",
                (
                    ack_ref,
                    ack_hash,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict("stale_effect_handoff", "effect dispatch-start CAS lost")
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def mark_effect_inflight_may_complete(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(receipt_ref, receipt_hash)
        async with self._write_transaction() as db:
            effect, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["status"]) == "unknown":
                if (
                    str(effect["handoff_unknown_receipt_ref"] or ""),
                    str(effect["handoff_unknown_receipt_hash"] or ""),
                ) != (receipt_ref, receipt_hash):
                    raise IdempotencyConflict(
                        "effect_unknown_conflict",
                        "effect has another unknown-start receipt",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "running"
                or str(effect["handoff_state"]) not in {"unresolved", "started"}
                or int(effect["effect_version"]) != expected_effect_version
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot become may-complete from its current state",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts
                SET status='unknown',handoff_state='started_may_complete',
                    completion_disposition='inflight_effect_may_complete',
                    handoff_unknown_receipt_ref=?,
                    handoff_unknown_receipt_hash=?,handoff_unknown_at=?,
                    updated_at=?,ended_at=?
                WHERE effect_id=? AND attempt_no=? AND status='running'""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    attempt_no,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET status='unknown',handoff_state='started_may_complete',
                    completion_disposition='inflight_effect_may_complete',
                    handoff_unknown_receipt_ref=?,
                    handoff_unknown_receipt_hash=?,handoff_unknown_at=?,
                    effect_version=effect_version+1,updated_at=?,ended_at=?
                WHERE effect_id=? AND status='running'
                  AND effect_version=?""",
                (
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict("stale_effect_handoff", "effect may-complete CAS lost")
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def suppress_late_effect_completion(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        late_outcome_hash: str,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair("late-outcome", late_outcome_hash)
        async with self._write_transaction() as db:
            effect, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["late_outcome_hash"] or "") == late_outcome_hash:
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "unknown"
                or str(effect["handoff_state"]) != "started_may_complete"
                or int(effect["effect_version"]) != expected_effect_version
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "only an unknown may-complete effect can suppress late completion",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts SET late_outcome_hash=?,
                    updated_at=? WHERE effect_id=? AND attempt_no=?
                    AND status='unknown'""",
                (late_outcome_hash, now, effect_id, attempt_no),
            )
            await db.execute(
                """UPDATE execution_effects SET late_outcome_hash=?,
                    effect_version=effect_version+1,updated_at=?
                WHERE effect_id=? AND effect_version=? AND status='unknown'""",
                (
                    late_outcome_hash,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def reconcile_effect_handoff(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
        *,
        completed_suppressed: bool,
    ) -> ExecutionEffectHandoff:
        self._receipt_pair(receipt_ref, receipt_hash)
        disposition = (
            "reconciled_completed_suppressed"
            if completed_suppressed
            else "reconciled_not_completed"
        )
        async with self._write_transaction() as db:
            effect, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            if str(effect["status"]) == "late_reconciled":
                if (
                    str(effect["reconcile_receipt_ref"] or ""),
                    str(effect["reconcile_receipt_hash"] or ""),
                    str(effect["completion_disposition"]),
                ) != (receipt_ref, receipt_hash, disposition):
                    raise IdempotencyConflict(
                        "effect_reconcile_conflict",
                        "effect has another reconciliation receipt",
                    )
                return self._effect_handoff_record(effect, attempt_no)
            if (
                str(effect["status"]) != "unknown"
                or str(effect["handoff_state"]) != "started_may_complete"
                or int(effect["effect_version"]) != expected_effect_version
            ):
                raise VersionConflict(
                    "stale_effect_handoff",
                    "effect cannot reconcile from its current state",
                )
            now = float(self._clock())
            await db.execute(
                """UPDATE execution_effect_attempts
                SET status='late_reconciled',handoff_state='reconciled',
                    completion_disposition=?,reconcile_receipt_ref=?,
                    reconcile_receipt_hash=?,updated_at=?,ended_at=?
                WHERE effect_id=? AND attempt_no=? AND status='unknown'""",
                (
                    disposition,
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    effect_id,
                    attempt_no,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_effects
                SET status='late_reconciled',handoff_state='reconciled',
                    completion_disposition=?,reconcile_receipt_ref=?,
                    reconcile_receipt_hash=?,
                    effect_version=effect_version+1,updated_at=?,ended_at=?
                WHERE effect_id=? AND status='unknown' AND effect_version=?""",
                (
                    disposition,
                    receipt_ref,
                    receipt_hash,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict("stale_effect_handoff", "effect reconciliation CAS lost")
            updated, _ = await self._effect_attempt_tx(db, effect_id, attempt_no)
            return self._effect_handoff_record(updated, attempt_no)

    async def _mark_effect_unknown_tx(
        self,
        db: aiosqlite.Connection,
        effect_id: str,
        *,
        expected_effect_version: int,
        attempt_no: int,
        worker_owner: str,
        worker_epoch: int,
        outcome_json: str,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        effect = await (
            await db.execute("SELECT * FROM execution_effects WHERE effect_id=?", (effect_id,))
        ).fetchone()
        if effect is None:
            raise RunNotFound("effect_not_found", "execution effect does not exist")
        await self._assert_optional_recovery_fence_tx(db, recovery_lease, str(effect["run_id"]))
        if str(effect["status"]) == "unknown":
            if (
                int(effect["effect_version"]) == expected_effect_version + 1
                and str(effect["outcome_json"] or "") == outcome_json
            ):
                return
            raise IdempotencyConflict(
                "effect_unknown_conflict", "effect has another unknown intent"
            )
        if (
            str(effect["status"]) != "running"
            or int(effect["effect_version"]) != expected_effect_version
        ):
            raise VersionConflict("stale_effect_version", "effect changed before unknown marking")
        now = float(self._clock())
        attempt = await db.execute(
            """UPDATE execution_effect_attempts SET status='unknown',
            handoff_state='started_may_complete',
            completion_disposition='inflight_effect_may_complete',
            outcome_json=?,updated_at=?,ended_at=?
            WHERE effect_id=? AND attempt_no=?
            AND status='running' AND worker_owner=? AND worker_epoch=?""",
            (outcome_json, now, now, effect_id, attempt_no, worker_owner, worker_epoch),
        )
        if attempt.rowcount != 1:
            raise VersionConflict("stale_effect_attempt", "effect attempt owner or epoch changed")
        updated = await db.execute(
            """UPDATE execution_effects SET status='unknown',
            handoff_state='started_may_complete',
            completion_disposition='inflight_effect_may_complete',
            outcome_json=?,effect_version=effect_version+1,
            updated_at=?,ended_at=?
            WHERE effect_id=? AND effect_version=? AND status='running'""",
            (outcome_json, now, now, effect_id, expected_effect_version),
        )
        if updated.rowcount != 1:
            raise VersionConflict("stale_effect_version", "effect changed before unknown marking")
        self._fault("effect_unknown_before_commit")

    async def settle_effect(
        self,
        effect_id: str,
        *,
        expected_effect_version: int,
        attempt_no: int,
        worker_owner: str,
        worker_epoch: int,
        status: str = "unknown",
        outcome: Mapping[str, Any],
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
        node_execution_id: str | None = None,
        checkpoint_ns: str | None = None,
        checkpoint_id: str | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        reconciliation: bool = False,
        evidence_verified: bool = False,
        transaction_hook: Callable[
            [aiosqlite.Connection, ExecutionEffectSettlement],
            Any,
        ]
        | None = None,
    ) -> ExecutionEffectSettlement | None:
        """Fence an unknown effect or settle it with the next durable boundary."""

        advances_boundary = continuation_payload is not None or event is not None
        allowed = {"succeeded", "failed", "accepted", "unknown", "cancelled"}
        if status not in allowed or attempt_no < 1 or worker_epoch < 1 or not worker_owner:
            raise ValueError("invalid effect settlement fence or status")
        outcome_json = canonical_json(thaw_json(outcome))
        if not advances_boundary:
            async with self._write_transaction() as db:
                await self._mark_effect_unknown_tx(
                    db,
                    effect_id,
                    expected_effect_version=expected_effect_version,
                    attempt_no=attempt_no,
                    worker_owner=worker_owner,
                    worker_epoch=worker_epoch,
                    outcome_json=outcome_json,
                    recovery_lease=recovery_lease,
                )
                effect = await (
                    await db.execute(
                        "SELECT * FROM execution_effects WHERE effect_id=?",
                        (effect_id,),
                    )
                ).fetchone()
                assert effect is not None
                await self._materialize_tool_public_projection_tx(
                    db,
                    effect=effect,
                    status="unknown",
                    outcome=thaw_json(outcome),
                    created_at=float(self._clock()),
                )
                await db.commit()
                return None
        if (
            expected_continuation_version is None
            or continuation_payload is None
            or event is None
            or node_execution_id is None
            or checkpoint_ns is None
            or checkpoint_id is None
        ):
            raise ValueError("effect boundary requires continuation, event and checkpoint identity")
        if reconciliation:
            if recovery_lease is None:
                raise StaleRecoveryLease("effect reconciliation requires a recovery lease")
            state = str(outcome.get("state") or "")
            status = (
                "accepted"
                if status == "accepted"
                else "failed"
                if state == "failure"
                else "succeeded"
                if state == "success" and evidence_verified
                else "unknown"
            )
        self._validate_continuation_version(expected_continuation_version)
        artifacts_json = canonical_json([str(item) for item in artifact_refs])
        payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            effect = await (
                await db.execute("SELECT * FROM execution_effects WHERE effect_id=?", (effect_id,))
            ).fetchone()
            if effect is None:
                raise RunNotFound("effect_not_found", "execution effect does not exist")
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, str(effect["run_id"]))
            source_status = str(effect["status"])
            if source_status != "running" and not (reconciliation and source_status == "unknown"):
                replay_event = await (
                    await db.execute(
                        """SELECT event.*,run.root_run_id,run.session_id
                        FROM execution_events AS event JOIN execution_runs AS run
                        ON run.run_id=event.run_id
                        WHERE event.run_id=? AND event.event_key=?""",
                        (effect["run_id"], event.event_key),
                    )
                ).fetchone()
                replay_continuation = await (
                    await db.execute(
                        "SELECT * FROM execution_continuations WHERE run_id=?",
                        (effect["run_id"],),
                    )
                ).fetchone()
                if (
                    int(effect["effect_version"]) == expected_effect_version + 1
                    and str(effect["status"]) == status
                    and str(effect["outcome_json"] or "") == outcome_json
                    and str(effect["receipt_ref"] or "") == str(receipt_ref or "")
                    and str(effect["artifact_refs_json"]) == artifacts_json
                    and replay_event is not None
                    and replay_continuation is not None
                    and int(replay_continuation["continuation_version"])
                    == expected_continuation_version + 1
                    and str(replay_continuation["pending_prepared_call_json"]) == payload_json
                ):
                    await db.commit()
                    return ExecutionEffectSettlement(
                        effect_id,
                        status,
                        expected_effect_version + 1,
                        self._row_to_continuation(replay_continuation),
                        self._row_to_event(replay_event),
                    )
                raise IdempotencyConflict("effect_already_settled", "effect is no longer running")
            if int(effect["effect_version"]) != expected_effect_version:
                raise VersionConflict(
                    "stale_effect_version", "effect changed before settlement CAS"
                )
            now = float(self._clock())
            target_handoff_state = "started_may_complete" if status == "unknown" else "reconciled"
            target_disposition = "inflight_effect_may_complete" if status == "unknown" else "normal"
            if reconciliation:
                # A recovery lease replaces the dead worker's owner fence, but
                # it must still terminalize that worker's attempt in the same
                # transaction as the canonical effect.  Leaving the attempt
                # at ``running`` after the effect becomes ``unknown`` makes
                # diagnostics report a ghost executor and weakens restart
                # invariants even though the physical call is not replayed.
                cursor = await db.execute(
                    """UPDATE execution_effect_attempts SET status=?,
                    handoff_state=?,completion_disposition=?,outcome_json=?,
                    updated_at=?,ended_at=? WHERE effect_id=? AND attempt_no=?
                    AND status IN ('running','unknown')""",
                    (
                        status,
                        target_handoff_state,
                        target_disposition,
                        outcome_json,
                        now,
                        now,
                        effect_id,
                        attempt_no,
                    ),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "stale_effect_attempt",
                        "effect attempt changed before reconciliation",
                    )
            else:
                cursor = await db.execute(
                    """UPDATE execution_effect_attempts SET status=?,
                    handoff_state=?,completion_disposition=?,outcome_json=?,
                    updated_at=?,ended_at=? WHERE effect_id=? AND attempt_no=?
                    AND status='running' AND worker_owner=? AND worker_epoch=?""",
                    (
                        status,
                        target_handoff_state,
                        target_disposition,
                        outcome_json,
                        now,
                        now,
                        effect_id,
                        attempt_no,
                        worker_owner,
                        worker_epoch,
                    ),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "stale_effect_attempt", "effect attempt owner or epoch changed"
                    )
            self._fault("effect_settle_after_attempt")
            cursor = await db.execute(
                """UPDATE execution_effects SET status=?,handoff_state=?,
                completion_disposition=?,outcome_json=?,receipt_ref=?,
                artifact_refs_json=?,effect_version=effect_version+1,
                updated_at=?,ended_at=?
                WHERE effect_id=? AND effect_version=? AND status=?""",
                (
                    status,
                    target_handoff_state,
                    target_disposition,
                    outcome_json,
                    receipt_ref,
                    artifacts_json,
                    now,
                    now,
                    effect_id,
                    expected_effect_version,
                    source_status,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_effect_version", "effect changed before settlement CAS"
                )
            await self._materialize_tool_public_projection_tx(
                db,
                effect=effect,
                status=status,
                outcome=thaw_json(outcome),
                created_at=now,
            )
            self._fault("effect_settle_after_effect")
            run_id = str(effect["run_id"])
            await db.execute(
                """INSERT INTO execution_effect_links(
                run_id,node_execution_id,effect_id,checkpoint_ns,checkpoint_id,created_at
                ) VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (
                    run_id,
                    node_execution_id,
                    effect_id,
                    checkpoint_ns,
                    checkpoint_id,
                    now,
                ),
            )
            self._fault("effect_settle_after_link")
            run = await self._continuation_run_tx(db, run_id)
            continuation, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=int(expected_continuation_version),
                payload_json=payload_json,
                decision=None,
                now=now,
            )
            self._fault("effect_settle_after_boundary")
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=event,
                deliveries=deliveries,
            )
            settlement = ExecutionEffectSettlement(
                effect_id,
                status,
                expected_effect_version + 1,
                continuation,
                stored_event,
            )
            if transaction_hook is not None:
                hook_result = transaction_hook(db, settlement)
                if inspect.isawaitable(hook_result):
                    await hook_result
            self._fault("effect_settle_before_commit")
            await db.commit()
            return settlement

    async def _materialize_tool_public_projection_tx(
        self,
        db: aiosqlite.Connection,
        *,
        effect: Mapping[str, Any],
        status: str,
        outcome: Mapping[str, Any],
        created_at: float,
    ) -> None:
        """Write the fail-closed public effect view in the effect transaction.

        ``unknown`` is a durable recovery state, not an immutable final result.  A
        reconciler may later advance that same effect to a verified terminal
        state.  Keep the effect identity immutable, but allow the public view to
        follow that one legal CAS transition in the same transaction.
        """

        run = await (
            await db.execute(
                "SELECT root_run_id FROM execution_runs WHERE run_id=?",
                (effect["run_id"],),
            )
        ).fetchone()
        if run is None:
            raise RunNotFound("run_not_found", "effect owner Run does not exist")
        root_run_id = str(run["root_run_id"])
        tool_name = str(effect["tool_name"])
        policy_row = await (
            await db.execute(
                "SELECT policy_json FROM execution_run_tool_presentation_specs "
                "WHERE root_run_id=? AND tool_name=?",
                (root_run_id, tool_name),
            )
        ).fetchone()
        policy = None
        if policy_row is not None:
            try:
                raw_policy = json.loads(str(policy_row["policy_json"]))
                if not isinstance(raw_policy, dict):
                    raise TypeError("policy payload must be an object")
                raw_policy["safe_arg_paths"] = tuple(raw_policy.get("safe_arg_paths", ()))
                raw_policy["safe_result_paths"] = tuple(raw_policy.get("safe_result_paths", ()))
                policy = ToolPresentationPolicyV1(**raw_policy)
            except (TypeError, ValueError, json.JSONDecodeError):
                policy = None
        try:
            projection = (
                ToolPublicProjectorV1()
                .project(
                    policy or legacy_unknown_tool_policy(tool_name),
                    arguments=json.loads(str(effect["prepared_json"] or "{}")),
                    result=outcome,
                    status=status,
                )
                .to_dict()
            )
        except Exception:  # noqa: BLE001 - default deny is the safety boundary
            projection = (
                ToolPublicProjectorV1()
                .project(
                    legacy_unknown_tool_policy(tool_name),
                    arguments={},
                    result={},
                    status=status,
                )
                .to_dict()
            )
            projection["projection_error"] = "default_deny"
        projection_json = canonical_json(projection)
        projection_hash = hashlib.sha256(projection_json.encode("utf-8")).hexdigest()
        await db.execute(
            "INSERT INTO execution_tool_public_projections("
            "effect_id,root_run_id,run_id,tool_name,schema_version,"
            "projection_json,projection_hash,status,created_at) "
            "VALUES(?,?,?,?,1,?,?,?,?) ON CONFLICT(effect_id) DO NOTHING",
            (
                str(effect["effect_id"]),
                root_run_id,
                str(effect["run_id"]),
                tool_name,
                projection_json,
                projection_hash,
                status,
                created_at,
            ),
        )
        stored = await (
            await db.execute(
                "SELECT root_run_id,run_id,tool_name,projection_json,"
                "projection_hash,status FROM execution_tool_public_projections "
                "WHERE effect_id=?",
                (str(effect["effect_id"]),),
            )
        ).fetchone()
        assert stored is not None
        immutable_identity = (
            root_run_id,
            str(effect["run_id"]),
            tool_name,
        )
        stored_identity = tuple(str(stored[index]) for index in range(3))
        if stored_identity != immutable_identity:
            raise IdempotencyConflict(
                "tool_public_projection_conflict",
                "effect replay supplied a different public projection identity",
            )
        expected = (
            *immutable_identity,
            projection_json,
            projection_hash,
            status,
        )
        actual = tuple(str(stored[index]) for index in range(6))
        if actual == expected:
            return

        source_status = str(effect["status"])
        stored_status = str(stored["status"])
        legal_reconciliation = (
            source_status == "unknown"
            and stored_status == source_status
            and status in {"succeeded", "failed", "accepted", "cancelled"}
        )
        if not legal_reconciliation:
            raise IdempotencyConflict(
                "tool_public_projection_conflict",
                "effect replay supplied a different public projection",
            )
        cursor = await db.execute(
            "UPDATE execution_tool_public_projections SET "
            "projection_json=?,projection_hash=?,status=? "
            "WHERE effect_id=? AND root_run_id=? AND run_id=? AND tool_name=? "
            "AND status=? AND projection_hash=?",
            (
                projection_json,
                projection_hash,
                status,
                str(effect["effect_id"]),
                *immutable_identity,
                stored_status,
                str(stored["projection_hash"]),
            ),
        )
        if cursor.rowcount != 1:
            raise IdempotencyConflict(
                "tool_public_projection_conflict",
                "effect public projection changed before reconciliation",
            )
