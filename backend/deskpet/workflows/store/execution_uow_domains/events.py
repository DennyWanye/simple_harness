"""Run fences and the run event log."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    TERMINAL_RUN_STATUSES,
    EventNotFound,
    ExecutionRunFenceRecord,
    IdempotencyConflict,
    RunIdentityConflict,
    RunNotFound,
    TerminalConflict,
    VersionConflict,
    canonical_json,
    stable_event_id,
    thaw_json,
)
from deskpet.execution.delivery_contracts import (
    DeliverySpec,
    RunEvent,
    RunEventCandidate,
)

from .core import ExecutionUowCore


class RunEventMixin(ExecutionUowCore):
    """Run fences and the run event log."""

    __slots__ = ()

    @staticmethod
    def _row_to_run_fence(row: Mapping[str, Any]) -> ExecutionRunFenceRecord:
        return ExecutionRunFenceRecord(
            run_id=str(row["run_id"]),
            fence_kind=str(row["fence_kind"]),
            fence_version=int(row["fence_version"]),
            status=str(row["status"]),
            reason=str(row["reason"]),
            source_ref=str(row["source_ref"]),
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    async def write_run_fence(self, record: ExecutionRunFenceRecord) -> ExecutionRunFenceRecord:
        async with self._write_transaction() as db:
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_run_fences
                    WHERE run_id=? AND fence_kind=? AND fence_version=?""",
                    (record.run_id, record.fence_kind, record.fence_version),
                )
            ).fetchone()
            if existing is not None:
                stored = self._row_to_run_fence(existing)
                if stored != record:
                    raise IdempotencyConflict(
                        "run_fence_conflict",
                        "run fence identity already names different facts",
                    )
                return stored
            latest = await (
                await db.execute(
                    """SELECT fence_version FROM execution_run_fences
                    WHERE run_id=? AND fence_kind=?
                    ORDER BY fence_version DESC LIMIT 1""",
                    (record.run_id, record.fence_kind),
                )
            ).fetchone()
            expected_version = 0 if latest is None else int(latest[0]) + 1
            if record.fence_version != expected_version:
                raise VersionConflict(
                    "run_fence_version_not_monotonic",
                    "run fence version must advance by exactly one",
                )
            await db.execute(
                """INSERT INTO execution_run_fences(
                run_id,fence_kind,fence_version,schema_version,status,reason,
                source_ref,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    record.run_id,
                    record.fence_kind,
                    record.fence_version,
                    record.schema_version,
                    record.status.value,
                    record.reason,
                    record.source_ref,
                    record.created_at,
                ),
            )
            return record

    async def read_run_fence(
        self,
        run_id: str,
        fence_kind: str,
        fence_version: int | None = None,
    ) -> ExecutionRunFenceRecord | None:
        async with self._read_connection() as db:
            if fence_version is None:
                row = await (
                    await db.execute(
                        """SELECT * FROM execution_run_fences
                        WHERE run_id=? AND fence_kind=?
                        ORDER BY fence_version DESC LIMIT 1""",
                        (run_id, fence_kind),
                    )
                ).fetchone()
            else:
                row = await (
                    await db.execute(
                        """SELECT * FROM execution_run_fences
                        WHERE run_id=? AND fence_kind=? AND fence_version=?""",
                        (run_id, fence_kind, fence_version),
                    )
                ).fetchone()
            return None if row is None else self._row_to_run_fence(row)

    @staticmethod
    def _event_json(candidate: RunEventCandidate) -> dict[str, str | None]:
        return {
            "correlation_json": canonical_json(thaw_json(candidate.correlation)),
            "payload_json": canonical_json(thaw_json(candidate.payload)),
            "error_json": (
                canonical_json(thaw_json(candidate.error)) if candidate.error is not None else None
            ),
            "artifact_refs_json": canonical_json(list(candidate.artifact_refs)),
        }

    def _assert_event_matches(
        self,
        row: Mapping[str, Any],
        run: Mapping[str, Any],
        candidate: RunEventCandidate,
    ) -> None:
        expected_id = stable_event_id(str(run["run_id"]), candidate.event_key)
        payload = self._event_json(candidate)
        expected: dict[str, object] = {
            "event_id": expected_id,
            "kind": candidate.kind,
            "status": candidate.status.value,
            "driver_kind": candidate.driver_kind,
            **payload,
        }
        for field_name, value in expected.items():
            if row[field_name] != value:
                raise IdempotencyConflict(
                    "event_intent_conflict",
                    f"event_key already names different {field_name}",
                )

    async def _append_event_tx(
        self,
        db: aiosqlite.Connection,
        run: aiosqlite.Row,
        *,
        expected_version: int,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec],
    ) -> tuple[RunEvent, aiosqlite.Row, bool]:
        if event.driver_kind != str(run["driver_kind"]):
            raise RunIdentityConflict(
                "driver_event_conflict", "event driver differs from the run owner"
            )
        existing = await (
            await db.execute(
                "SELECT * FROM execution_events WHERE run_id=? AND event_key=?",
                (run["run_id"], event.event_key),
            )
        ).fetchone()
        if existing is not None:
            self._assert_event_matches(existing, run, event)
            await self._assert_delivery_set_tx(db, str(existing["event_id"]), deliveries)
            hydrated = dict(existing)
            hydrated["root_run_id"] = run["root_run_id"]
            hydrated["session_id"] = run["session_id"]
            return self._row_to_event(hydrated), run, True
        if str(run["status"]) in {status.value for status in TERMINAL_RUN_STATUSES}:
            raise TerminalConflict(
                "run_already_terminal", "a terminal run cannot append more events"
            )
        if int(run["version"]) != expected_version:
            raise VersionConflict(
                "stale_run_version",
                f"expected run version {expected_version}, found {run['version']}",
            )
        now = float(self._clock())
        durable_seq = int(run["durable_seq"]) + 1
        event_id = stable_event_id(str(run["run_id"]), event.event_key)
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
                run["run_id"],
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
        await self._insert_deliveries_tx(
            db,
            run_id=str(run["run_id"]),
            event_id=event_id,
            deliveries=deliveries,
            now=now,
        )
        cursor = await db.execute(
            """UPDATE execution_runs SET durable_seq=?,version=version+1,updated_at=?
            WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
            (durable_seq, now, run["run_id"], expected_version),
        )
        if cursor.rowcount != 1:
            raise VersionConflict("stale_run_version", "run changed before event commit")
        updated = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run["run_id"],))
        ).fetchone()
        stored = await (
            await db.execute("SELECT * FROM execution_events WHERE event_id=?", (event_id,))
        ).fetchone()
        assert updated is not None and stored is not None
        hydrated = dict(stored)
        hydrated["root_run_id"] = updated["root_run_id"]
        hydrated["session_id"] = updated["session_id"]
        return self._row_to_event(hydrated), updated, False

    async def get_event(self, event_id: str) -> RunEvent:
        event_key = str(event_id).strip()
        if not event_key:
            raise EventNotFound("event_not_found", "event_id must be non-empty")
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT execution_events.*,execution_runs.root_run_id,
                    execution_runs.session_id FROM execution_events
                    JOIN execution_runs USING(run_id)
                    WHERE execution_events.event_id=?""",
                    (event_key,),
                )
            ).fetchone()
            if row is None:
                raise EventNotFound(
                    "event_not_found", f"execution event does not exist: {event_key}"
                )
            return self._row_to_event(row)

    async def read_terminal_extension_receipts(
        self, run_id: str, event_id: str
    ) -> tuple[Mapping[str, str], ...]:
        """Read immutable host-extension receipts in their committed order."""

        run_key = str(run_id).strip()
        event_key = str(event_id).strip()
        if not run_key or not event_key:
            raise ValueError("run_id and event_id must be non-empty")
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT kind,ref,content_hash
                    FROM execution_terminal_extension_receipts
                    WHERE run_id=? AND event_id=?
                    ORDER BY receipt_order""",
                    (run_key, event_key),
                )
            ).fetchall()
            return tuple(
                {
                    "kind": str(row["kind"]),
                    "ref": str(row["ref"]),
                    "content_hash": str(row["content_hash"]),
                }
                for row in rows
            )

    async def list_events(
        self,
        run_id: str,
        *,
        after_durable_seq: int = 0,
    ) -> tuple[RunEvent, ...]:
        if (
            not isinstance(after_durable_seq, int)
            or isinstance(after_durable_seq, bool)
            or after_durable_seq < 0
        ):
            raise ValueError("after_durable_seq must be a non-negative integer")
        async with self._read_connection() as db:
            run = await (
                await db.execute(
                    "SELECT root_run_id,session_id FROM execution_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None:
                raise RunNotFound("run_not_found", f"execution run does not exist: {run_id}")
            rows = await (
                await db.execute(
                    """SELECT execution_events.*,? AS root_run_id,? AS session_id
                    FROM execution_events WHERE run_id=? AND durable_seq>?
                    ORDER BY durable_seq,event_id""",
                    (run["root_run_id"], run["session_id"], run_id, after_durable_seq),
                )
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)
