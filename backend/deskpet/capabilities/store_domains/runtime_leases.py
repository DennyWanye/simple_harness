# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Runtime leases, runtime calls and draining unreferenced runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiosqlite

from .core import CapabilityStoreCore
from .support import (
    CapabilityRuntimeCallLease,
    CapabilityRuntimeLease,
    CapabilityStoreConflict,
    CapabilityStoreError,
)


def _runtime_lease_from_row(row: Mapping[str, Any]) -> CapabilityRuntimeLease:
    return CapabilityRuntimeLease(
        runtime_lease_id=str(row["lease_id"]),
        pack_id=str(row["pack_id"]),
        version=str(row["version"]),
        manifest_hash=str(row["manifest_hash"]),
        server_id=str(row["server_id"]),
        pid=None if row["pid"] is None else int(row["pid"]),
        run_id=None if row["run_id"] is None else str(row["run_id"]),
        session_generation=int(row["session_generation"]),
        state=str(row["state"]),
        heartbeat_at=float(row["heartbeat_at"]),
        created_at=float(row["created_at"]),
    )


def _runtime_call_lease_from_row(
    row: Mapping[str, Any],
) -> CapabilityRuntimeCallLease:
    return CapabilityRuntimeCallLease(
        call_lease_id=str(row["call_lease_id"]),
        runtime_lease_id=str(row["runtime_lease_id"]),
        effect_id=str(row["effect_id"]),
        session_generation=int(row["session_generation"]),
        state=str(row["state"]),
        started_at=float(row["started_at"]),
        ended_at=None if row["ended_at"] is None else float(row["ended_at"]),
    )


class RuntimeLeaseMixin(CapabilityStoreCore):
    """Runtime leases, runtime calls and draining unreferenced runtimes."""

    __slots__ = ()

    @staticmethod
    def _validate_runtime_identity(
        *,
        runtime_lease_id: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        pid: int | None,
    ) -> None:
        if not all(
            isinstance(value, str) and value for value in (runtime_lease_id, pack_id, version)
        ):
            raise CapabilityStoreError(
                "invalid_runtime_identity",
                "runtime lease, pack and version identities are required",
            )
        if len(manifest_hash) != 64 or any(
            character not in "0123456789abcdef" for character in manifest_hash
        ):
            raise CapabilityStoreError(
                "invalid_runtime_identity",
                "runtime manifest_hash must be a lowercase SHA-256 digest",
            )
        if pid is not None and (not isinstance(pid, int) or pid <= 0):
            raise CapabilityStoreError(
                "invalid_runtime_pid", "runtime pid must be a positive integer"
            )

    async def _create_runtime_lease_tx(
        self,
        db: aiosqlite.Connection,
        *,
        runtime_lease_id: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        server_id: str = "",
        pid: int | None = None,
        run_id: str | None = None,
    ) -> CapabilityRuntimeLease:
        self._validate_runtime_identity(
            runtime_lease_id=runtime_lease_id,
            pack_id=pack_id,
            version=version,
            manifest_hash=manifest_hash,
            pid=pid,
        )
        if run_id is not None and not run_id:
            raise CapabilityStoreError("invalid_runtime_identity", "run_id cannot be empty")
        existing = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        if existing is not None:
            current = _runtime_lease_from_row(existing)
            if (
                current.pack_id,
                current.version,
                current.manifest_hash,
                current.server_id,
                current.run_id,
            ) != (
                pack_id,
                version,
                manifest_hash,
                server_id,
                run_id,
            ):
                raise CapabilityStoreConflict(
                    "runtime_lease_conflict",
                    "runtime lease identity already belongs to another runtime",
                )
            return current
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_runtime_leases(
                lease_id,pack_id,version,manifest_hash,server_id,pid,run_id,
                session_generation,state,heartbeat_at,created_at
            ) VALUES(?,?,?,?,?,?,?,1,'starting',?,?)""",
            (
                runtime_lease_id,
                pack_id,
                version,
                manifest_hash,
                server_id,
                pid,
                run_id,
                now,
                now,
            ),
        )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        return _runtime_lease_from_row(row)

    async def create_runtime_lease(self, **kwargs: Any) -> CapabilityRuntimeLease:
        async with self.write_transaction() as db:
            return await self._create_runtime_lease_tx(db, **kwargs)

    async def get_runtime_lease(self, runtime_lease_id: str) -> CapabilityRuntimeLease | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                    (runtime_lease_id,),
                )
            ).fetchone()
            return None if row is None else _runtime_lease_from_row(row)

    async def _active_runtime_call_count_tx(
        self, db: aiosqlite.Connection, runtime_lease_id: str
    ) -> int:
        row = await (
            await db.execute(
                """SELECT COUNT(*) FROM capability_runtime_call_leases
                   WHERE runtime_lease_id=? AND state!='settled'""",
                (runtime_lease_id,),
            )
        ).fetchone()
        return 0 if row is None else int(row[0])

    async def _transition_runtime_lease_tx(
        self,
        db: aiosqlite.Connection,
        runtime_lease_id: str,
        *,
        expected_state: str,
        target_state: str,
        expected_generation: int | None = None,
        pid: int | None = None,
    ) -> CapabilityRuntimeLease:
        states = {"starting", "ready", "draining", "stopped", "unknown"}
        if expected_state not in states or target_state not in states:
            raise CapabilityStoreError("invalid_runtime_state", "unknown runtime lease state")
        if pid is not None and (not isinstance(pid, int) or pid <= 0):
            raise CapabilityStoreError(
                "invalid_runtime_pid", "runtime pid must be a positive integer"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("runtime_lease_not_found", runtime_lease_id)
        current = _runtime_lease_from_row(row)
        if expected_generation is not None and (current.session_generation != expected_generation):
            raise CapabilityStoreConflict("runtime_generation_conflict", "runtime session changed")
        if current.state == target_state:
            return current
        if current.state != expected_state:
            raise CapabilityStoreConflict(
                "runtime_state_conflict",
                f"runtime is {current.state}, expected {expected_state}",
            )
        allowed = {
            "starting": {"ready", "draining", "unknown", "stopped"},
            "ready": {"draining", "unknown"},
            "unknown": {"ready", "draining", "stopped"},
            "draining": {"unknown", "stopped"},
            "stopped": set(),
        }
        if target_state not in allowed[current.state]:
            raise CapabilityStoreConflict(
                "runtime_state_conflict",
                f"runtime cannot transition {current.state} to {target_state}",
            )
        if target_state == "stopped" and (
            await self._active_runtime_call_count_tx(db, runtime_lease_id)
        ):
            raise CapabilityStoreConflict(
                "runtime_calls_in_flight",
                "runtime cannot stop while call leases are active",
            )
        cursor = await db.execute(
            """UPDATE capability_runtime_leases
               SET state=?,pid=COALESCE(?,pid),heartbeat_at=?
               WHERE lease_id=? AND state=? AND session_generation=?""",
            (
                target_state,
                pid,
                self._clock(),
                runtime_lease_id,
                current.state,
                current.session_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "runtime_state_conflict", "runtime lease changed concurrently"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        return _runtime_lease_from_row(row)

    async def transition_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        async with self.write_transaction() as db:
            return await self._transition_runtime_lease_tx(db, runtime_lease_id, **kwargs)

    async def _reconnect_runtime_lease_tx(
        self,
        db: aiosqlite.Connection,
        runtime_lease_id: str,
        *,
        expected_generation: int,
        pid: int | None = None,
    ) -> CapabilityRuntimeLease:
        if pid is not None and (not isinstance(pid, int) or pid <= 0):
            raise CapabilityStoreError(
                "invalid_runtime_pid", "runtime pid must be a positive integer"
            )
        cursor = await db.execute(
            """UPDATE capability_runtime_leases
               SET session_generation=session_generation+1,state='ready',
                   pid=COALESCE(?,pid),heartbeat_at=?
               WHERE lease_id=? AND session_generation=?
                 AND state IN ('ready','unknown')""",
            (pid, self._clock(), runtime_lease_id, expected_generation),
        )
        if cursor.rowcount != 1:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                    (runtime_lease_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError("runtime_lease_not_found", runtime_lease_id)
            raise CapabilityStoreConflict(
                "runtime_generation_conflict",
                "runtime cannot reconnect from its current state or generation",
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        return _runtime_lease_from_row(row)

    async def reconnect_runtime_lease(
        self, runtime_lease_id: str, **kwargs: Any
    ) -> CapabilityRuntimeLease:
        async with self.write_transaction() as db:
            return await self._reconnect_runtime_lease_tx(db, runtime_lease_id, **kwargs)

    async def _claim_runtime_call_tx(
        self,
        db: aiosqlite.Connection,
        *,
        call_lease_id: str,
        runtime_lease_id: str,
        effect_id: str,
    ) -> CapabilityRuntimeCallLease:
        if not all(
            isinstance(value, str) and value
            for value in (call_lease_id, runtime_lease_id, effect_id)
        ):
            raise CapabilityStoreError(
                "invalid_runtime_call_identity",
                "call lease, runtime lease and effect identities are required",
            )
        existing_rows = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=? OR effect_id=?""",
                (call_lease_id, effect_id),
            )
        ).fetchall()
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_call_conflict",
                    "call lease and effect identities refer to different calls",
                )
            current = _runtime_call_lease_from_row(existing_rows[0])
            if (
                current.call_lease_id != call_lease_id
                or current.runtime_lease_id != runtime_lease_id
                or current.effect_id != effect_id
            ):
                raise CapabilityStoreConflict(
                    "runtime_call_conflict",
                    "runtime call identity already belongs to another call",
                )
            return current
        runtime_row = await (
            await db.execute(
                "SELECT * FROM capability_runtime_leases WHERE lease_id=?",
                (runtime_lease_id,),
            )
        ).fetchone()
        if runtime_row is None:
            raise CapabilityStoreError("runtime_lease_not_found", runtime_lease_id)
        runtime = _runtime_lease_from_row(runtime_row)
        if runtime.state != "ready":
            raise CapabilityStoreConflict(
                "runtime_not_accepting_calls",
                f"runtime is {runtime.state}; new calls require ready",
            )
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_runtime_call_leases(
                call_lease_id,runtime_lease_id,effect_id,session_generation,
                state,started_at,ended_at
            ) VALUES(?,?,?,?,'claimed',?,NULL)""",
            (
                call_lease_id,
                runtime_lease_id,
                effect_id,
                runtime.session_generation,
                now,
            ),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        return _runtime_call_lease_from_row(row)

    async def claim_runtime_call(self, **kwargs: Any) -> CapabilityRuntimeCallLease:
        async with self.write_transaction() as db:
            return await self._claim_runtime_call_tx(db, **kwargs)

    async def _mark_runtime_call_running_tx(
        self, db: aiosqlite.Connection, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("runtime_call_not_found", call_lease_id)
        current = _runtime_call_lease_from_row(row)
        if current.state == "running":
            return current
        if current.state != "claimed":
            raise CapabilityStoreConflict(
                "runtime_call_state_conflict",
                f"runtime call is {current.state}, expected claimed",
            )
        await db.execute(
            """UPDATE capability_runtime_call_leases SET state='running'
               WHERE call_lease_id=? AND state='claimed'""",
            (call_lease_id,),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        return _runtime_call_lease_from_row(row)

    async def mark_runtime_call_running(self, call_lease_id: str) -> CapabilityRuntimeCallLease:
        async with self.write_transaction() as db:
            return await self._mark_runtime_call_running_tx(db, call_lease_id)

    async def _settle_runtime_call_tx(
        self, db: aiosqlite.Connection, call_lease_id: str
    ) -> CapabilityRuntimeCallLease:
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("runtime_call_not_found", call_lease_id)
        current = _runtime_call_lease_from_row(row)
        if current.state == "settled":
            return current
        await db.execute(
            """UPDATE capability_runtime_call_leases
               SET state='settled',ended_at=?
               WHERE call_lease_id=? AND state!='settled'""",
            (self._clock(), call_lease_id),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_runtime_call_leases
                   WHERE call_lease_id=?""",
                (call_lease_id,),
            )
        ).fetchone()
        return _runtime_call_lease_from_row(row)

    async def settle_runtime_call(self, call_lease_id: str) -> CapabilityRuntimeCallLease:
        async with self.write_transaction() as db:
            return await self._settle_runtime_call_tx(db, call_lease_id)

    async def runtime_call_leases(
        self, runtime_lease_id: str
    ) -> tuple[CapabilityRuntimeCallLease, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_runtime_call_leases
                       WHERE runtime_lease_id=? ORDER BY started_at,call_lease_id""",
                    (runtime_lease_id,),
                )
            ).fetchall()
            return tuple(_runtime_call_lease_from_row(row) for row in rows)

    async def runtime_can_stop(self, runtime_lease_id: str) -> bool:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT state FROM capability_runtime_leases WHERE lease_id=?",
                    (runtime_lease_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError("runtime_lease_not_found", runtime_lease_id)
            return (
                str(row["state"]) == "draining"
                and await self._active_runtime_call_count_tx(db, runtime_lease_id) == 0
            )

    async def _drain_unreferenced_runtimes_tx(
        self,
        db: aiosqlite.Connection,
        *,
        pack_id: str,
        version: str,
        manifest_hash: str,
    ) -> tuple[CapabilityRuntimeLease, ...]:
        binding = await (
            await db.execute(
                """SELECT 1 FROM capability_bindings
                   WHERE pack_id=? AND active_version=? AND active_manifest_hash=?
                     AND enabled=1 LIMIT 1""",
                (pack_id, version, manifest_hash),
            )
        ).fetchone()
        snapshot = await (
            await db.execute(
                """SELECT 1 FROM capability_snapshot_leases
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                     AND released_at IS NULL LIMIT 1""",
                (pack_id, version, manifest_hash),
            )
        ).fetchone()
        if binding is None and snapshot is None:
            await db.execute(
                """UPDATE capability_runtime_leases
                   SET state='draining',heartbeat_at=?
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                     AND state IN ('starting','ready','unknown')""",
                (self._clock(), pack_id, version, manifest_hash),
            )
        rows = await (
            await db.execute(
                """SELECT * FROM capability_runtime_leases
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                   ORDER BY created_at,lease_id""",
                (pack_id, version, manifest_hash),
            )
        ).fetchall()
        return tuple(_runtime_lease_from_row(row) for row in rows)
