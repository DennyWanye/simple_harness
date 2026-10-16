"""Delivery claims, completion, settlement and release."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    DeliveryClaimConflict,
    DeliveryNotFound,
    DeliveryStatus,
    IdempotencyConflict,
    stable_delivery_id,
)
from deskpet.execution.delivery_contracts import DeliveryRecord, DeliverySpec, RunEvent

from .core import ExecutionUowCore


class DeliveryMixin(ExecutionUowCore):
    """Delivery claims, completion, settlement and release."""

    __slots__ = ()

    @staticmethod
    def _delivery_identity(delivery: DeliverySpec) -> tuple[str, str, str, str]:
        return (
            delivery.sink_kind,
            delivery.sink_instance,
            delivery.target_id,
            delivery.policy.value,
        )

    async def _assert_delivery_set_tx(
        self,
        db: aiosqlite.Connection,
        event_id: str,
        deliveries: Sequence[DeliverySpec],
    ) -> None:
        rows = await (
            await db.execute(
                """SELECT sink_kind,sink_instance,target_id,policy
                FROM execution_deliveries WHERE event_id=?""",
                (event_id,),
            )
        ).fetchall()
        stored = {
            (
                str(row["sink_kind"]),
                str(row["sink_instance"]),
                str(row["target_id"]),
                str(row["policy"]),
            )
            for row in rows
        }
        requested = {self._delivery_identity(item) for item in deliveries}
        if stored != requested:
            raise IdempotencyConflict(
                "delivery_intent_conflict",
                "event replay supplied a different delivery set",
            )

    async def _insert_deliveries_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        event_id: str,
        deliveries: Sequence[DeliverySpec],
        now: float,
        delivery_fence: Mapping[str, Any] | None = None,
        release_receipt: Mapping[str, Any] | None = None,
    ) -> None:
        identities = [self._delivery_identity(item) for item in deliveries]
        if len(set(identities)) != len(identities):
            raise IdempotencyConflict(
                "duplicate_delivery", "one event cannot contain duplicate sink intents"
            )
        for delivery in deliveries:
            fence_values: tuple[object | None, ...] = (None,) * 8
            if delivery_fence is not None:
                required = {
                    "delivery_fence_epoch",
                    "owner_id",
                    "owner_generation",
                    "dependency_hash",
                    "snapshot_ref",
                    "snapshot_hash",
                }
                if set(delivery_fence) != required:
                    raise IdempotencyConflict(
                        "invalid_delivery_fence",
                        "terminal delivery fence fields differ",
                    )
                for hash_name in ("dependency_hash", "snapshot_hash"):
                    digest = str(delivery_fence[hash_name])
                    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                        raise IdempotencyConflict(
                            "invalid_delivery_fence",
                            f"{hash_name} must be lowercase SHA-256",
                        )
                receipt_ref = None
                receipt_hash = None
                if release_receipt is not None:
                    receipt_ref = str(release_receipt.get("ref") or "")
                    receipt_hash = str(release_receipt.get("content_hash") or "")
                    if not receipt_ref or len(receipt_hash) != 64:
                        raise IdempotencyConflict(
                            "invalid_release_receipt",
                            "release receipt ref/hash are required",
                        )
                fence_values = (
                    int(delivery_fence["delivery_fence_epoch"]),
                    str(delivery_fence["owner_id"]),
                    int(delivery_fence["owner_generation"]),
                    str(delivery_fence["dependency_hash"]),
                    str(delivery_fence["snapshot_ref"]),
                    str(delivery_fence["snapshot_hash"]),
                    receipt_ref,
                    receipt_hash,
                )
            await db.execute(
                """INSERT INTO execution_deliveries(
                delivery_id,schema_version,event_id,run_id,sink_kind,sink_instance,
                target_id,policy,status,created_at,updated_at,
                delivery_fence_epoch,delivery_owner_id,
                delivery_owner_generation,delivery_dependency_hash,
                delivery_snapshot_ref,delivery_snapshot_hash,
                release_receipt_ref,release_receipt_hash
                ) VALUES(?,1,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?)""",
                (
                    stable_delivery_id(event_id, delivery),
                    event_id,
                    run_id,
                    delivery.sink_kind,
                    delivery.sink_instance,
                    delivery.target_id,
                    delivery.policy.value,
                    now,
                    now,
                    *fence_values,
                ),
            )

    async def list_session_terminal_events(
        self,
        session_id: str,
        target_id: str,
        *,
        session_epoch: int,
        limit: int | None = None,
    ) -> tuple[RunEvent, ...]:
        """Read every current-epoch root terminal visible at one DB snapshot.

        ``None`` is intentionally unbounded: product consistency gates must
        not silently omit old failures merely because more than one repair
        page accumulated while the dispatcher was unavailable.
        """

        session_key = str(session_id or "").strip()
        target_key = str(target_id or "").strip()
        if not session_key or not target_key:
            return ()
        bounded = None if limit is None else max(1, min(int(limit), 64))
        limit_sql = "" if bounded is None else " LIMIT ?"
        params: list[object] = [
            session_key,
            target_key,
            int(session_epoch),
        ]
        if bounded is not None:
            params.append(bounded)
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    f"""SELECT e.*,r.root_run_id,r.session_id
                    FROM execution_events AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.session_id=? AND r.run_id=r.root_run_id
                      AND e.kind='run.final'
                      AND e.status IN ('succeeded','failed','cancelled')
                      AND (
                        EXISTS (
                          SELECT 1 FROM execution_deliveries AS d
                          WHERE d.event_id=e.event_id AND d.run_id=e.run_id
                            AND d.sink_kind='session_terminal'
                            AND d.sink_instance='session-transcript-v1'
                            AND d.target_id=? AND d.status!='discarded'
                        )
                        OR (
                          r.auth_epoch=?
                          AND NOT EXISTS (
                            SELECT 1 FROM execution_deliveries AS d0
                            WHERE d0.event_id=e.event_id
                              AND d0.run_id=e.run_id
                              AND d0.sink_kind='session_terminal'
                          )
                        )
                      )
                    ORDER BY e.created_at,e.event_id{limit_sql}""",
                    tuple(params),
                )
            ).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    async def claim_delivery_tx(
        self,
        connection: aiosqlite.Connection,
        *,
        owner_generation: int,
        sink_keys: Sequence[tuple[str, str]] = (),
        claim_ttl_seconds: float = 30.0,
        now: float,
    ) -> DeliveryRecord | None:
        """Claim one execution delivery inside the caller-owned transaction."""

        if (
            isinstance(owner_generation, bool)
            or not isinstance(owner_generation, int)
            or owner_generation < 1
        ):
            raise ValueError("owner_generation must be a positive kernel generation")
        ttl = float(claim_ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("claim_ttl_seconds must be finite and positive")
        normalized_keys = tuple(
            (str(kind).strip(), str(instance).strip()) for kind, instance in sink_keys
        )
        if any(not kind or not instance for kind, instance in normalized_keys):
            raise ValueError("sink keys require non-empty kind and instance")
        if len(dict.fromkeys(normalized_keys)) != len(normalized_keys):
            raise ValueError("sink keys must be unique")

        now = float(now)
        if not math.isfinite(now):
            raise ValueError("delivery claim time must be finite")
        eligible = """(
            status='pending'
            OR (status='failed' AND COALESCE(next_attempt_at,0)<=?)
            OR (status='delivering' AND next_attempt_at IS NOT NULL
                AND next_attempt_at<=? AND policy!='best_effort')
        )"""
        select_eligible = """(
            d.status='pending'
            OR (d.status='failed' AND COALESCE(d.next_attempt_at,0)<=?)
            OR (d.status='delivering' AND d.next_attempt_at IS NOT NULL
                AND d.next_attempt_at<=? AND d.policy!='best_effort')
        )"""
        sink_clause = ""
        sink_params: list[object] = []
        if normalized_keys:
            sink_clause = (
                " AND ("
                + " OR ".join("(d.sink_kind=? AND d.sink_instance=?)" for _ in normalized_keys)
                + ")"
            )
            for kind, instance in normalized_keys:
                sink_params.extend((kind, instance))

        # BEST_EFFORT is at-most-one physical attempt.  If its worker
        # vanished after claim, persistently discard the expired claim;
        # replaying TTS after restart is worse than dropping the cue.
        await connection.execute(
            """UPDATE execution_deliveries SET status='discarded',
            delivery_version=delivery_version+1,next_attempt_at=NULL,
            last_error='best-effort claim expired',updated_at=?
            WHERE status='delivering' AND policy='best_effort'
            AND next_attempt_at IS NOT NULL AND next_attempt_at<=?
            AND EXISTS(SELECT 1 FROM execution_runs r
                JOIN execution_runtime_state s ON s.singleton_id=1
                WHERE r.run_id=execution_deliveries.run_id
                AND r.owner_kind='kernel' AND r.owner_generation=?
                AND s.generation=? AND s.phase IN ('activated','open'))""",
            (now, now, owner_generation, owner_generation),
        )
        row = await (
            await connection.execute(
                f"""SELECT d.* FROM execution_deliveries d
                JOIN execution_runs r ON r.run_id=d.run_id
                JOIN execution_runtime_state s ON s.singleton_id=1
                WHERE r.owner_kind='kernel' AND r.owner_generation=?
                AND s.generation=? AND s.phase IN ('activated','open')
                AND {select_eligible}{sink_clause}
                ORDER BY CASE d.policy
                    WHEN 'durable_required' THEN 0
                    WHEN 'retry_while_bound' THEN 1 ELSE 2 END,
                    d.created_at,d.delivery_id LIMIT 1""",
                (owner_generation, owner_generation, now, now, *sink_params),
            )
        ).fetchone()
        if row is None:
            return None
        lease_expires_at = now + ttl
        cursor = await connection.execute(
            f"""UPDATE execution_deliveries SET status='delivering',
            attempts=attempts+1,delivery_version=delivery_version+1,
            next_attempt_at=?,updated_at=?
            WHERE delivery_id=? AND delivery_version=? AND {eligible}""",
            (
                lease_expires_at,
                now,
                row["delivery_id"],
                row["delivery_version"],
                now,
                now,
            ),
        )
        if cursor.rowcount != 1:
            raise DeliveryClaimConflict(
                "delivery_claim_conflict", "delivery changed before claim CAS"
            )
        claimed = await (
            await connection.execute(
                "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                (row["delivery_id"],),
            )
        ).fetchone()
        assert claimed is not None
        return self._row_to_delivery(claimed)

    async def _assert_delivery_owner_tx(
        self, connection: aiosqlite.Connection, delivery_id: str, owner_generation: int
    ) -> None:
        row = await (
            await connection.execute(
                """SELECT r.owner_kind,r.owner_generation,s.generation,s.phase
            FROM execution_deliveries d JOIN execution_runs r ON r.run_id=d.run_id
            JOIN execution_runtime_state s ON s.singleton_id=1 WHERE d.delivery_id=?""",
                (delivery_id,),
            )
        ).fetchone()
        if row is None:
            raise DeliveryNotFound(
                "delivery_not_found", f"execution delivery does not exist: {delivery_id}"
            )
        if (
            str(row["owner_kind"]) != "kernel"
            or int(row["owner_generation"]) != owner_generation
            or int(row["generation"]) != owner_generation
            or str(row["phase"]) not in {"activated", "open"}
        ):
            raise DeliveryClaimConflict(
                "delivery_owner_fence", "delivery owner generation is stale or inactive"
            )

    async def claim_delivery(
        self,
        *,
        owner_generation: int,
        sink_keys: Sequence[tuple[str, str]] = (),
        claim_ttl_seconds: float = 30.0,
    ) -> DeliveryRecord | None:
        now = float(self._clock())
        async with self._write_transaction() as db:
            claimed = await self.claim_delivery_tx(
                db,
                owner_generation=owner_generation,
                sink_keys=sink_keys,
                claim_ttl_seconds=claim_ttl_seconds,
                now=now,
            )
            await db.commit()
            return claimed

    async def complete_delivery_tx(
        self,
        connection: aiosqlite.Connection,
        delivery_id: str,
        *,
        expected_version: int,
        owner_generation: int,
        now: float,
    ) -> DeliveryRecord:
        """Complete one claimed execution delivery in the caller transaction."""

        await self._assert_delivery_owner_tx(connection, delivery_id, owner_generation)
        now = float(now)
        if not math.isfinite(now):
            raise ValueError("delivery completion time must be finite")
        cursor = await connection.execute(
            """UPDATE execution_deliveries SET status='delivered',
            delivery_version=delivery_version+1,next_attempt_at=NULL,last_error=NULL,
            updated_at=?,delivered_at=? WHERE delivery_id=?
            AND delivery_version=? AND status='delivering'
            """,
            (now, now, delivery_id, expected_version),
        )
        if cursor.rowcount != 1:
            existing = await (
                await connection.execute(
                    "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if existing is None:
                raise DeliveryNotFound(
                    "delivery_not_found",
                    f"execution delivery does not exist: {delivery_id}",
                )
            if (
                str(existing["status"]) == DeliveryStatus.DELIVERED.value
                and int(existing["delivery_version"]) == expected_version + 1
            ):
                return self._row_to_delivery(existing)
            raise DeliveryClaimConflict(
                "delivery_claim_conflict", "delivery completion lost its claim fence"
            )
        row = await (
            await connection.execute(
                "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                (delivery_id,),
            )
        ).fetchone()
        assert row is not None
        return self._row_to_delivery(row)

    async def settle_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        owner_generation: int,
        error: str | None = None,
        retry_at: float | None = None,
        discard: bool = False,
    ) -> DeliveryRecord:
        now = float(self._clock())
        async with self._write_transaction() as db:
            if error is None:
                completed = await self.complete_delivery_tx(
                    db,
                    delivery_id,
                    expected_version=expected_version,
                    owner_generation=owner_generation,
                    now=now,
                )
            else:
                completed = await self.release_delivery_tx(
                    db,
                    delivery_id,
                    expected_version=expected_version,
                    owner_generation=owner_generation,
                    error=error,
                    retry_at=retry_at,
                    discard=discard,
                    now=now,
                )
            await db.commit()
            return completed

    async def release_delivery_tx(
        self,
        connection: aiosqlite.Connection,
        delivery_id: str,
        *,
        expected_version: int,
        owner_generation: int,
        error: str,
        retry_at: float | None,
        discard: bool,
        now: float,
    ) -> DeliveryRecord:
        """Release one claimed execution delivery in the caller transaction."""

        await self._assert_delivery_owner_tx(connection, delivery_id, owner_generation)
        message = str(error).strip()
        if not message:
            raise ValueError("delivery error must be non-empty")
        if not discard and retry_at is None:
            raise ValueError("retryable delivery release requires retry_at")
        if retry_at is not None and not math.isfinite(float(retry_at)):
            raise ValueError("retry_at must be finite")
        status = DeliveryStatus.DISCARDED if discard else DeliveryStatus.FAILED
        now = float(now)
        if not math.isfinite(now):
            raise ValueError("delivery release time must be finite")
        cursor = await connection.execute(
            """UPDATE execution_deliveries SET status=?,
            delivery_version=delivery_version+1,next_attempt_at=?,last_error=?,
            updated_at=?,delivered_at=NULL WHERE delivery_id=?
            AND delivery_version=? AND status='delivering'
            """,
            (
                status.value,
                None if discard else float(retry_at),
                message,
                now,
                delivery_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            existing = await (
                await connection.execute(
                    "SELECT delivery_id FROM execution_deliveries WHERE delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if existing is None:
                raise DeliveryNotFound(
                    "delivery_not_found",
                    f"execution delivery does not exist: {delivery_id}",
                )
            raise DeliveryClaimConflict(
                "delivery_claim_conflict", "delivery release lost its claim fence"
            )
        row = await (
            await connection.execute(
                "SELECT * FROM execution_deliveries WHERE delivery_id=?",
                (delivery_id,),
            )
        ).fetchone()
        assert row is not None
        return self._row_to_delivery(row)
