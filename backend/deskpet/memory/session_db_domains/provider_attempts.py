# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""SDK context snapshots, provider attempts and projection cursors."""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from deskpet.sdk_adapters.context_authority import SnapshotContractConflict
from deskpet.sdk_adapters.context_authority import canonical_json as sdk_canonical_json
from deskpet.sdk_adapters.context_authority import canonical_sha256 as sdk_canonical_sha256

from .core import SessionDBCore


class ProviderAttemptLedgerMixin(SessionDBCore):
    """SDK context snapshots, provider attempts and projection cursors."""

    __slots__ = ()

    async def put_sdk_context_public_snapshot(self, snapshot: dict[str, Any]) -> str:
        """Persist one bounded public snapshot with immutable source identity."""

        if not self._initialized:
            await self.initialize()
        required = (
            "snapshot_id",
            "session_id",
            "request_id",
            "root_run_id",
            "snapshot_fingerprint",
        )
        normalized = dict(snapshot)
        for key in required:
            normalized[key] = str(normalized.get(key) or "").strip()
            if not normalized[key]:
                raise ValueError(f"{key} is required")
        normalized["snapshot_version"] = int(normalized.get("snapshot_version", 0))
        if normalized["snapshot_version"] != 1:
            raise ValueError("unsupported public snapshot version")
        payload_json = sdk_canonical_json(normalized)
        payload_hash = sdk_canonical_sha256(normalized)
        created_at = time.time()

        async def _do() -> str:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        cursor = await db.execute(
                            """INSERT INTO sdk_context_public_snapshots(
                            snapshot_id,session_id,request_id,root_run_id,
                            snapshot_version,snapshot_fingerprint,payload_hash,
                            public_json,created_at
                            ) VALUES (?,?,?,?,?,?,?,?,?)
                            ON CONFLICT DO NOTHING""",
                            (
                                normalized["snapshot_id"],
                                normalized["session_id"],
                                normalized["request_id"],
                                normalized["root_run_id"],
                                normalized["snapshot_version"],
                                normalized["snapshot_fingerprint"],
                                payload_hash,
                                payload_json,
                                created_at,
                            ),
                        )
                        inserted = int(cursor.rowcount or 0) == 1
                        await cursor.close()
                        cursor = await db.execute(
                            """SELECT snapshot_id,payload_hash FROM
                               sdk_context_public_snapshots
                               WHERE snapshot_id=? OR (session_id=? AND request_id=?)""",
                            (
                                normalized["snapshot_id"],
                                normalized["session_id"],
                                normalized["request_id"],
                            ),
                        )
                        row = await cursor.fetchone()
                        await cursor.close()
                        if row is None or str(row["snapshot_id"]) != normalized["snapshot_id"]:
                            raise SnapshotContractConflict()
                        if str(row["payload_hash"]) != payload_hash:
                            raise SnapshotContractConflict()
                        await db.commit()
                        return "inserted" if inserted else "duplicate"
                    except Exception:
                        await db.rollback()
                        raise

        return await self._with_retry(_do)

    async def get_sdk_context_public_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT public_json FROM sdk_context_public_snapshots WHERE snapshot_id=?",
                (str(snapshot_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return None if row is None else json.loads(str(row[0]))

    async def get_latest_sdk_context_public_snapshot(
        self, session_id: str
    ) -> dict[str, Any] | None:
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """SELECT public_json FROM sdk_context_public_snapshots
                   WHERE session_id=? ORDER BY created_at DESC,snapshot_id DESC LIMIT 1""",
                (str(session_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return None if row is None else json.loads(str(row[0]))

    async def record_sdk_provider_attempt(
        self,
        attempt: dict[str, Any],
        *,
        expected_settlement_version: int | None = None,
    ) -> str:
        """Insert or CAS-advance one immutable provider settlement audit."""

        if not self._initialized:
            await self.initialize()
        normalized = dict(attempt)
        for key in (
            "invocation_id",
            "session_id",
            "root_run_id",
            "request_id",
            "snapshot_id",
            "state",
            "provider_id",
            "model_id",
        ):
            normalized[key] = str(normalized.get(key) or "").strip()
            if not normalized[key]:
                raise ValueError(f"{key} is required")
        normalized["settlement_version"] = int(normalized.get("settlement_version", 0))
        usage = normalized.get("usage")
        payload_json = sdk_canonical_json(normalized)
        payload_hash = sdk_canonical_sha256(normalized)
        updated_at = time.time()

        async def _do() -> str:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        cursor = await db.execute(
                            "SELECT settlement_version,payload_hash,payload_json FROM sdk_provider_attempt_audit WHERE invocation_id=?",
                            (normalized["invocation_id"],),
                        )
                        current = await cursor.fetchone()
                        await cursor.close()
                        if current is not None:
                            current_version = int(current["settlement_version"])
                            if (
                                current_version == normalized["settlement_version"]
                                and str(current["payload_hash"]) == payload_hash
                            ):
                                await db.rollback()
                                return "duplicate"
                            if normalized["settlement_version"] <= current_version or (
                                expected_settlement_version is not None
                                and expected_settlement_version != current_version
                            ):
                                raise SnapshotContractConflict("provider settlement CAS conflict")
                            prior = json.loads(str(current["payload_json"]))
                            immutable_fields = (
                                "invocation_id",
                                "session_id",
                                "root_run_id",
                                "request_id",
                                "snapshot_id",
                                "provider_id",
                                "model_id",
                                "binding_epoch",
                                "context_window",
                                "effective_ceiling",
                            )
                            if any(
                                prior.get(key) != normalized.get(key) for key in immutable_fields
                            ):
                                raise SnapshotContractConflict(
                                    "provider settlement identity mutation"
                                )
                        elif expected_settlement_version not in (None, 0):
                            raise SnapshotContractConflict(
                                "provider settlement was not initialized"
                            )

                        values = (
                            normalized["invocation_id"],
                            normalized["settlement_version"],
                            payload_hash,
                            normalized["session_id"],
                            normalized["root_run_id"],
                            normalized["request_id"],
                            normalized["snapshot_id"],
                            normalized["state"],
                            normalized["provider_id"],
                            normalized["model_id"],
                            int(normalized.get("binding_epoch", 0)),
                            int(normalized.get("context_window", 0)),
                            int(normalized.get("effective_ceiling", 0)),
                            int(usage is not None),
                            None if usage is None else usage.get("input_tokens"),
                            None if usage is None else usage.get("output_tokens"),
                            None if usage is None else usage.get("total_tokens"),
                            None if usage is None else usage.get("cache_tokens"),
                            None if usage is None else usage.get("reasoning_tokens"),
                            float(normalized.get("settled_at", 0.0)),
                            payload_json,
                            updated_at,
                        )
                        await db.execute(
                            """INSERT INTO sdk_provider_attempt_audit(
                            invocation_id,settlement_version,payload_hash,session_id,
                            root_run_id,request_id,snapshot_id,state,provider_id,model_id,
                            binding_epoch,context_window,effective_ceiling,usage_available,
                            input_tokens,output_tokens,total_tokens,cache_tokens,
                            reasoning_tokens,settled_at,payload_json,updated_at
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(invocation_id) DO UPDATE SET
                            settlement_version=excluded.settlement_version,
                            payload_hash=excluded.payload_hash,state=excluded.state,
                            usage_available=excluded.usage_available,
                            input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens,
                            total_tokens=excluded.total_tokens,cache_tokens=excluded.cache_tokens,
                            reasoning_tokens=excluded.reasoning_tokens,
                            settled_at=excluded.settled_at,payload_json=excluded.payload_json,
                            updated_at=excluded.updated_at""",
                            values,
                        )
                        await db.commit()
                        return "inserted" if current is None else "updated"
                    except Exception:
                        await db.rollback()
                        raise

        return await self._with_retry(_do)

    async def list_sdk_provider_attempts(
        self, session_id: str, *, limit: int = 256
    ) -> list[dict[str, Any]]:
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM sdk_provider_attempt_audit WHERE session_id=?
                   ORDER BY settled_at ASC,invocation_id ASC LIMIT ?""",
                (str(session_id), max(1, int(limit))),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = json.loads(str(row["payload_json"]))
            item["usage"] = (
                {
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "total_tokens": row["total_tokens"],
                    "cache_tokens": row["cache_tokens"],
                    "reasoning_tokens": row["reasoning_tokens"],
                }
                if int(row["usage_available"])
                else None
            )
            result.append(item)
        return result

    async def get_sdk_provider_projection_cursor(self, consumer_id: str) -> dict[str, Any] | None:
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sdk_provider_projection_cursors WHERE consumer_id=?",
                (str(consumer_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return None if row is None else dict(row)

    async def advance_sdk_provider_projection_cursor(
        self,
        consumer_id: str,
        *,
        settled_at: float,
        invocation_id: str,
        expected_version: int,
        source_sequence: int | None = None,
    ) -> dict[str, Any]:
        """CAS-advance a consumer cursor; the position is monotonic."""

        if not self._initialized:
            await self.initialize()
        consumer = str(consumer_id or "").strip()
        invocation = str(invocation_id or "").strip()
        if not consumer or not invocation:
            raise ValueError("consumer_id and invocation_id are required")
        if source_sequence is not None and (
            isinstance(source_sequence, bool) or int(source_sequence) < 1
        ):
            raise ValueError("source_sequence must be a positive integer")

        async def _do() -> dict[str, Any]:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        cursor = await db.execute(
                            "SELECT * FROM sdk_provider_projection_cursors WHERE consumer_id=?",
                            (consumer,),
                        )
                        row = await cursor.fetchone()
                        await cursor.close()
                        current_version = int(row["version"]) if row else 0
                        current_position = (
                            (float(row["settled_at"]), str(row["invocation_id"]))
                            if row
                            else (-1.0, "")
                        )
                        next_position = (float(settled_at), invocation)
                        current_sequence = int(row["source_sequence"]) if row else 0
                        next_sequence = (
                            int(source_sequence)
                            if source_sequence is not None
                            else current_sequence
                        )
                        same_position = (
                            next_sequence == current_sequence
                            if source_sequence is not None
                            else next_position == current_position
                        )
                        if same_position:
                            if next_position != current_position:
                                raise SnapshotContractConflict(
                                    "projection sequence identity conflict"
                                )
                            await db.rollback()
                            return dict(row)
                        regressed = (
                            next_sequence < current_sequence
                            if source_sequence is not None
                            else next_position < current_position
                        )
                        if current_version != int(expected_version) or regressed:
                            raise SnapshotContractConflict("projection cursor CAS conflict")
                        next_version = current_version + 1
                        updated_at = time.time()
                        await db.execute(
                            """INSERT INTO sdk_provider_projection_cursors(
                            consumer_id,settled_at,invocation_id,version,updated_at,
                            source_sequence
                            ) VALUES (?,?,?,?,?,?) ON CONFLICT(consumer_id) DO UPDATE SET
                            settled_at=excluded.settled_at,invocation_id=excluded.invocation_id,
                            version=excluded.version,updated_at=excluded.updated_at,
                            source_sequence=excluded.source_sequence""",
                            (
                                consumer,
                                float(settled_at),
                                invocation,
                                next_version,
                                updated_at,
                                next_sequence,
                            ),
                        )
                        await db.commit()
                        return {
                            "consumer_id": consumer,
                            "settled_at": float(settled_at),
                            "invocation_id": invocation,
                            "version": next_version,
                            "source_sequence": next_sequence,
                            "updated_at": updated_at,
                        }
                    except Exception:
                        await db.rollback()
                        raise

        return await self._with_retry(_do)
