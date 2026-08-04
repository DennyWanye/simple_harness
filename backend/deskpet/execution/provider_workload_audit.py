"""Durable privacy-safe audit authority for every provider workload class."""

from __future__ import annotations

import time
import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import aiosqlite


class ProviderWorkloadAuditStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = Path(db_path)
        self._clock = clock

    async def record(self, event: Mapping[str, Any]) -> None:
        now = float(self._clock())
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO provider_workload_audit(
                    stable_call_id, workload_class, callsite_id, purpose,
                    provider_id, provider_incarnation_id, model, config_revision,
                    session_id, root_run_id, detached, owner_policy, status,
                    duration_ms, error_class, breaker_transition, injection_ref,
                    injection_correlation_hash, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(stable_call_id) DO UPDATE SET
                    status=excluded.status,
                    duration_ms=excluded.duration_ms,
                    error_class=excluded.error_class,
                    breaker_transition=excluded.breaker_transition,
                    injection_ref=excluded.injection_ref,
                    injection_correlation_hash=excluded.injection_correlation_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    str(event["stable_call_id"]),
                    str(event["workload_class"]),
                    str(event["callsite_id"]),
                    str(event["purpose"]),
                    str(event["provider_id"]),
                    str(event["provider_incarnation_id"]),
                    str(event["model"]),
                    int(event["config_revision"]),
                    event.get("session_id"),
                    event.get("root_run_id"),
                    int(bool(event.get("detached"))),
                    str(event["owner_policy"]),
                    str(event["status"]),
                    max(0, int(event.get("duration_ms") or 0)),
                    event.get("error_class"),
                    event.get("breaker_transition"),
                    event.get("injection_ref"),
                    (
                        hashlib.sha256(
                            str(event["injection_bound_correlation_id"]).encode(
                                "utf-8"
                            )
                        ).hexdigest()
                        if event.get("injection_bound_correlation_id")
                        else None
                    ),
                    now,
                    now,
                ),
            )
            await db.commit()

    async def record_maintenance_error(self, exc: BaseException) -> None:
        """Keep retention failure observable without blocking provider calls."""

        now = float(self._clock())
        stable_id = f"audit-retention:{int(now // 21600)}"
        event = {
            "stable_call_id": stable_id,
            "workload_class": "system-maintenance",
            "callsite_id": "provider.audit_retention",
            "purpose": "auxiliary_unknown",
            "provider_id": "local-maintenance",
            "provider_incarnation_id": "local-maintenance",
            "model": "none",
            "config_revision": 1,
            "session_id": None,
            "root_run_id": None,
            "detached": True,
            "owner_policy": "detached-maintenance",
            "status": "failed",
            "duration_ms": 0,
            "error_class": type(exc).__name__,
            "breaker_transition": None,
            "injection_ref": None,
            "injection_bound_correlation_id": None,
        }
        try:
            await self.record(event)
        except Exception:
            # A busy/corrupt audit DB cannot recursively audit itself.
            return

    async def enforce_retention(
        self,
        *,
        max_age_days: int = 30,
        max_rows: int = 100_000,
        batch_size: int = 1_000,
    ) -> int:
        if max_age_days < 1 or max_rows < 1 or not 1 <= batch_size <= 1_000:
            raise ValueError("invalid provider workload audit retention policy")
        cutoff = float(self._clock()) - max_age_days * 86400.0
        async with aiosqlite.connect(self._db_path, timeout=0.05) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM provider_workload_audit"
            )
            total = int((await cursor.fetchone() or (0,))[0])
            overflow = max(0, total - max_rows)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM provider_workload_audit WHERE created_at <= ?",
                (cutoff,),
            )
            expired = int((await cursor.fetchone() or (0,))[0])
            limit = min(batch_size, max(overflow, expired))
            if limit <= 0:
                return 0
            cursor = await db.execute(
                """
                SELECT stable_call_id
                FROM provider_workload_audit
                ORDER BY created_at ASC, stable_call_id ASC
                LIMIT ?
                """,
                (limit,),
            )
            candidates = [str(row[0]) for row in await cursor.fetchall()]
            if not candidates:
                return 0
            placeholders = ",".join("?" for _ in candidates)
            await db.execute(
                f"DELETE FROM provider_workload_audit WHERE stable_call_id IN ({placeholders})",
                candidates,
            )
            await db.commit()
            return len(candidates)


async def provider_workload_audit_retention_loop(
    store: ProviderWorkloadAuditStore,
    *,
    initial_delay_s: float = 600.0,
    interval_s: float = 21600.0,
) -> None:
    """Non-blocking maintenance loop: startup+10m, then every six hours."""

    import asyncio

    await asyncio.sleep(initial_delay_s)
    while True:
        try:
            await store.enforce_retention()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await store.record_maintenance_error(exc)
        await asyncio.sleep(interval_s)


__all__ = [
    "ProviderWorkloadAuditStore",
    "provider_workload_audit_retention_loop",
]
