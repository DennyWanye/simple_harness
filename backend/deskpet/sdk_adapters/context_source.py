# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable content-addressed non-Memory Context sources for SDK preparation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import json
from pathlib import Path
import time

import aiosqlite
from simple_harness.contracts import JsonValue, canonical_json
from simple_harness.runtime import source_snapshot_ref


class ProductContextSourceRepository:
    def __init__(self, state_db_path: str | Path, *, retention_seconds: float = 604800.0) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        self._path = Path(state_db_path)
        self._retention = float(retention_seconds)

    async def put_pending(
        self,
        *,
        root_run_id: str,
        continuation_id: str | None,
        payload: Mapping[str, JsonValue],
        now: float | None = None,
        lease_seconds: float = 3600.0,
    ) -> tuple[str, str]:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        canonical = canonical_json(dict(payload))
        ref = source_snapshot_ref(dict(payload))
        binding_id = f"context-source/v1/{root_run_id}/{continuation_id or 'root'}"
        timestamp = time.time() if now is None else float(now)
        item_count = max(1, len(payload))
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO sdk_context_sources(
                     source_snapshot_ref,payload_json,payload_hash,byte_count,item_count,
                     state,ref_count,created_at,expires_at,updated_at)
                   VALUES(?,?,?,?,?,'pending',0,?,?,?)
                   ON CONFLICT(source_snapshot_ref) DO UPDATE SET
                     expires_at=max(expires_at,excluded.expires_at),updated_at=excluded.updated_at""",
                (
                    ref,
                    canonical,
                    ref.removeprefix("sha256:"),
                    len(canonical.encode("utf-8")),
                    item_count,
                    timestamp,
                    timestamp + self._retention,
                    timestamp,
                ),
            )
            existing = await (
                await db.execute(
                    "SELECT source_snapshot_ref FROM sdk_context_source_bindings WHERE binding_id=?",
                    (binding_id,),
                )
            ).fetchone()
            if existing is not None and str(existing[0]) != ref:
                raise RuntimeError("context_source_binding_conflict")
            if existing is None:
                await db.execute(
                    """INSERT INTO sdk_context_source_bindings(
                         binding_id,source_snapshot_ref,root_run_id,continuation_id,status,
                         lease_expires_at,created_at,updated_at)
                       VALUES(?,?,?,?, 'pending',?,?,?)""",
                    (
                        binding_id,
                        ref,
                        root_run_id,
                        continuation_id,
                        timestamp + float(lease_seconds),
                        timestamp,
                        timestamp,
                    ),
                )
                await db.execute(
                    "UPDATE sdk_context_sources SET ref_count=ref_count+1 WHERE source_snapshot_ref=?",
                    (ref,),
                )
            await db.commit()
        return binding_id, ref

    async def read(self, ref: str) -> tuple[dict[str, JsonValue], int, int]:
        async with aiosqlite.connect(self._path) as db:
            row = await (
                await db.execute(
                    "SELECT payload_json,item_count,byte_count FROM sdk_context_sources WHERE source_snapshot_ref=?",
                    (ref,),
                )
            ).fetchone()
        if row is None:
            raise KeyError(ref)
        payload = json.loads(str(row[0]))
        if source_snapshot_ref(payload) != ref:
            raise RuntimeError("context_source_hash_conflict")
        return payload, int(row[1]), int(row[2])

    async def mark_claimed(self, binding_id: str, *, claim_token: str) -> None:
        """Record that the SDK durably accepted the source-reference claim."""
        token = str(claim_token).strip()
        if not token:
            raise ValueError("claim_token must not be empty")
        now = time.time()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT status,claim_token FROM sdk_context_source_bindings WHERE binding_id=?",
                    (binding_id,),
                )
            ).fetchone()
            if row is None:
                raise KeyError(binding_id)
            status, existing_token = str(row[0]), row[1]
            if status == "consumed":
                raise RuntimeError("context_source_binding_already_consumed")
            if existing_token is not None and str(existing_token) != token:
                raise RuntimeError("context_source_claim_conflict")
            await db.execute(
                """UPDATE sdk_context_source_bindings
                   SET status='claimed',claim_token=?,updated_at=?
                   WHERE binding_id=?""",
                (token, now, binding_id),
            )
            await db.commit()

    async def mark_staged(self, binding_id: str) -> None:
        now = time.time()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT source_snapshot_ref FROM sdk_context_source_bindings WHERE binding_id=?",
                    (binding_id,),
                )
            ).fetchone()
            if row is None:
                raise KeyError(binding_id)
            await db.execute(
                "UPDATE sdk_context_source_bindings SET status='staged',updated_at=? WHERE binding_id=?",
                (now, binding_id),
            )
            await db.execute(
                "UPDATE sdk_context_sources SET state='staged',updated_at=? WHERE source_snapshot_ref=? AND state='pending'",
                (now, str(row[0])),
            )
            await db.commit()

    async def consume(self, binding_id: str) -> None:
        now = time.time()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT source_snapshot_ref,status FROM sdk_context_source_bindings WHERE binding_id=?",
                    (binding_id,),
                )
            ).fetchone()
            if row is None:
                return
            ref, status = str(row[0]), str(row[1])
            if status != "consumed":
                await db.execute(
                    "UPDATE sdk_context_source_bindings SET status='consumed',updated_at=? WHERE binding_id=?",
                    (now, binding_id),
                )
                await db.execute(
                    "UPDATE sdk_context_sources SET state='consumed',ref_count=max(0,ref_count-1),updated_at=? WHERE source_snapshot_ref=?",
                    (now, ref),
                )
            await db.commit()

    async def consume_run(self, root_run_id: str) -> int:
        """Release every root/continuation binding after the SDK Run is terminal."""
        async with aiosqlite.connect(self._path) as db:
            rows = await (
                await db.execute(
                    """SELECT binding_id FROM sdk_context_source_bindings
                       WHERE root_run_id=? AND status!='consumed'""",
                    (str(root_run_id),),
                )
            ).fetchall()
        for (binding_id,) in rows:
            await self.consume(str(binding_id))
        return len(rows)

    async def cleanup(
        self,
        *,
        claim_exists: Callable[[str, str | None], bool | Awaitable[bool]],
        now: float | None = None,
        orphan_horizon_seconds: float = 3600.0,
        limit: int = 128,
    ) -> int:
        """Bounded GC; inspector failure retains the row for a later retry."""
        timestamp = time.time() if now is None else float(now)
        async with aiosqlite.connect(self._path) as db:
            rows = await (
                await db.execute(
                    """SELECT binding_id,root_run_id,continuation_id,status,updated_at
                       FROM sdk_context_source_bindings
                       WHERE status IN ('pending','staged')
                         AND updated_at<=?
                         AND (status='staged' OR lease_expires_at<=?)
                       ORDER BY updated_at LIMIT ?""",
                    (timestamp - orphan_horizon_seconds, timestamp, limit),
                )
            ).fetchall()
        removed = 0
        for binding_id, root_run_id, continuation_id, status, _updated in rows:
            if str(status) in {"pending", "staged"}:
                try:
                    active = claim_exists(str(root_run_id), continuation_id)
                    if hasattr(active, "__await__"):
                        active = await active  # type: ignore[assignment,misc]
                    if active:
                        continue
                except Exception:
                    continue
            await self.consume(str(binding_id))
            removed += 1
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """DELETE FROM sdk_context_source_bindings
                   WHERE status='consumed' AND source_snapshot_ref IN (
                     SELECT source_snapshot_ref FROM sdk_context_sources
                     WHERE ref_count=0 AND expires_at<=?)""",
                (timestamp,),
            )
            cursor = await db.execute(
                """DELETE FROM sdk_context_sources WHERE source_snapshot_ref IN (
                     SELECT source_snapshot_ref FROM sdk_context_sources
                     WHERE state IN ('staged','consumed') AND ref_count=0 AND expires_at<=?
                     ORDER BY expires_at LIMIT ?)""",
                (timestamp, limit),
            )
            await db.commit()
            removed += max(0, int(cursor.rowcount or 0))
        return removed


__all__ = ("ProductContextSourceRepository",)
