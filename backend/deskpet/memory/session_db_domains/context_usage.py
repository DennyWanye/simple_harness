# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Context usage samples and binding state."""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from deskpet.agent.context_usage import (
    ContextUsageSampleConflict,
    ContextUsageStateConflict,
    ContextUsageStateV2,
    binding_only_state,
    normalize_context_usage_sample,
    reduce_context_usage_state,
)

from .core import SessionDBCore


class ContextUsageMixin(SessionDBCore):
    """Context usage samples and binding state."""

    __slots__ = ()

    async def record_context_usage_sample(
        self,
        sample: dict[str, Any],
    ) -> str:
        """Append one immutable fact and advance ContextUsageStateV2.

        The durable source identity is idempotent.  Reusing it with a
        different canonical payload is corruption and never updates history.
        """

        if not self._initialized:
            await self.initialize()
        normalized = normalize_context_usage_sample(sample)

        async def _do() -> None:
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        cursor = await db.execute(
                            """INSERT INTO session_context_usage_history(
                            sample_id,session_id,source_event_id,payload_hash,
                            run_id,request_id,attempt_id,event_type,
                            tokens_before,tokens_after,prompt_tokens,
                            completion_tokens,cached_tokens,context_window,
                            effective_ceiling,estimate_method,provider_id,
                            model_id,binding_epoch,based_on_sample_id,
                            metadata_json,completed_at,created_at
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(session_id,source_event_id) DO NOTHING""",
                            (
                                normalized["sample_id"],
                                normalized["session_id"],
                                normalized["source_event_id"],
                                normalized["payload_hash"],
                                normalized["run_id"],
                                normalized["request_id"],
                                normalized["attempt_id"],
                                normalized["event_type"],
                                normalized["tokens_before"],
                                normalized["tokens_after"],
                                normalized["prompt_tokens"],
                                normalized["completion_tokens"],
                                normalized["cached_tokens"],
                                normalized["context_window"],
                                normalized["effective_ceiling"],
                                normalized["estimate_method"],
                                normalized["provider_id"],
                                normalized["model_id"],
                                normalized["binding_epoch"],
                                normalized["based_on_sample_id"],
                                json.dumps(
                                    normalized["metadata"],
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    default=str,
                                ),
                                normalized["completed_at"],
                                normalized["created_at"],
                            ),
                        )
                        inserted = int(cursor.rowcount or 0) == 1
                        await cursor.close()
                        existing_cursor = await db.execute(
                            """SELECT *
                               FROM session_context_usage_history
                               WHERE session_id=? AND source_event_id=?""",
                            (
                                normalized["session_id"],
                                normalized["source_event_id"],
                            ),
                        )
                        existing = await existing_cursor.fetchone()
                        await existing_cursor.close()
                        if existing is None:
                            raise RuntimeError("context usage history insert disappeared")
                        if str(existing["payload_hash"]) != normalized["payload_hash"]:
                            raise ContextUsageSampleConflict(ContextUsageSampleConflict.code)
                        normalized["sample_id"] = str(existing["sample_id"])
                        durable_fact = dict(normalized)
                        for key in (
                            "sample_id",
                            "source_event_id",
                            "event_type",
                            "tokens_before",
                            "tokens_after",
                            "prompt_tokens",
                            "completion_tokens",
                            "cached_tokens",
                            "context_window",
                            "effective_ceiling",
                            "estimate_method",
                            "provider_id",
                            "model_id",
                            "binding_epoch",
                            "based_on_sample_id",
                            "completed_at",
                            "created_at",
                        ):
                            durable_fact[key] = existing[key]

                        state = await self._read_context_usage_state_tx(
                            db, normalized["session_id"]
                        )
                        had_state = state is not None
                        if state is None:
                            binding = await self._read_context_usage_binding_tx(
                                db, normalized["session_id"]
                            )
                            state = binding_only_state(
                                session_id=normalized["session_id"],
                                state_version=0,
                                binding_epoch=int(binding["binding_epoch"]),
                                provider_id=binding["provider_id"],
                                model_id=binding["preferred_model"],
                                availability=str(binding["availability"]),
                                updated_at=float(normalized["created_at"]),
                            )
                        reduced = reduce_context_usage_state(state, durable_fact)
                        if not had_state and reduced == state:
                            reduced = ContextUsageStateV2(**{**state.__dict__, "state_version": 1})
                        if inserted or reduced != state:
                            await self._write_context_usage_state_tx(
                                db, state if had_state else None, reduced
                            )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

        await self._with_retry(_do)
        return str(normalized["sample_id"])

    async def list_context_usage_history(
        self,
        session_id: str,
        *,
        limit: int = 256,
    ) -> list[dict[str, Any]]:
        """Return oldest-to-newest durable Context history for one session."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid:
            return []
        bounded = max(1, min(int(limit), 2_000))
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM (
                SELECT * FROM session_context_usage_history
                WHERE session_id=?
                ORDER BY created_at DESC,sample_id DESC LIMIT ?
                ) ORDER BY created_at,sample_id""",
                (sid, bounded),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._context_usage_sample_from_row(row) for row in rows]

    @staticmethod
    def _context_usage_sample_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        event_type = str(row["event_type"])
        return {
            "sample_id": str(row["sample_id"]),
            "session_id": str(row["session_id"]),
            "source_event_id": str(row["source_event_id"]),
            "payload_hash": str(row["payload_hash"]),
            "run_id": row["run_id"],
            "request_id": row["request_id"],
            "attempt_id": row["attempt_id"],
            "event_type": event_type,
            "source": ("compacted" if event_type == "compaction" else "measured"),
            "tokens_before": row["tokens_before"],
            "tokens_after": int(row["tokens_after"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "cached_tokens": int(row["cached_tokens"]),
            "context_window": int(row["context_window"]),
            "effective_ceiling": int(row["effective_ceiling"]),
            "estimate_method": str(row["estimate_method"]),
            "provider_id": row["provider_id"],
            "model_id": row["model_id"],
            "binding_epoch": int(row["binding_epoch"]),
            "based_on_sample_id": row["based_on_sample_id"],
            "metadata": metadata,
            "completed_at": float(row["completed_at"]),
            "created_at": float(row["created_at"]),
        }

    async def get_context_usage_sample(
        self, session_id: str, sample_id: str
    ) -> dict[str, Any] | None:
        """Read one exact immutable Context sample without history limits."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        exact_sample_id = str(sample_id or "").strip()
        if not sid or not exact_sample_id:
            return None
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM session_context_usage_history
                   WHERE session_id=? AND sample_id=?""",
                (sid, exact_sample_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return self._context_usage_sample_from_row(row) if row is not None else None

    @staticmethod
    def _context_usage_state_from_row(row: aiosqlite.Row) -> ContextUsageStateV2:
        return ContextUsageStateV2(
            session_id=str(row["session_id"]),
            source=str(row["source"]),
            sample_id=row["sample_id"],
            state_version=int(row["state_version"]),
            binding_epoch=int(row["binding_epoch"]),
            availability=str(row["availability"]),
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            context_window=int(row["context_window"]),
            tokens=int(row["tokens"]),
            effective_ceiling=int(row["effective_ceiling"]),
            completion_tokens=int(row["completion_tokens"]),
            cached_tokens=int(row["cached_tokens"]),
            based_on_sample_id=row["based_on_sample_id"],
            source_event_id=row["source_event_id"],
            source_completed_at=float(row["source_completed_at"]),
            has_measurement=bool(row["has_measurement"]),
            legacy_incomplete=bool(row["legacy_incomplete"]),
            updated_at=float(row["updated_at"]),
        )

    async def _read_context_usage_state_tx(
        self, db: aiosqlite.Connection, session_id: str
    ) -> ContextUsageStateV2 | None:
        cursor = await db.execute(
            "SELECT * FROM session_context_usage_state_v2 WHERE session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return self._context_usage_state_from_row(row) if row is not None else None

    async def _read_context_usage_binding_tx(
        self, db: aiosqlite.Connection, session_id: str
    ) -> dict[str, Any]:
        columns_cursor = await db.execute("PRAGMA table_info(code_session_provider)")
        columns = {str(row[1]) for row in await columns_cursor.fetchall()}
        await columns_cursor.close()
        has_epoch = "binding_epoch" in columns
        cursor = await db.execute(
            "SELECT provider_id,preferred_model"
            + (",binding_epoch" if has_epoch else "")
            + " FROM code_session_provider WHERE base_session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return {
                "provider_id": None,
                "preferred_model": None,
                "binding_epoch": 0,
                "availability": "unknown",
            }
        return {
            "provider_id": row[0],
            "preferred_model": row[1],
            "binding_epoch": int(row[2]) if has_epoch else 0,
            "availability": "available",
        }

    async def _write_context_usage_state_tx(
        self,
        db: aiosqlite.Connection,
        previous: ContextUsageStateV2 | None,
        state: ContextUsageStateV2,
    ) -> None:
        record = state.to_record()
        columns = (
            "session_id,source,sample_id,state_version,binding_epoch,availability,"
            "provider_id,model_id,context_window,tokens,effective_ceiling,"
            "completion_tokens,cached_tokens,based_on_sample_id,source_event_id,"
            "source_completed_at,has_measurement,legacy_incomplete,updated_at"
        )
        values = tuple(record[name] for name in columns.split(","))
        if previous is None or previous.state_version == 0:
            cursor = await db.execute(
                f"INSERT INTO session_context_usage_state_v2({columns}) "
                f"VALUES ({','.join('?' for _ in values)}) "
                "ON CONFLICT(session_id) DO NOTHING",
                values,
            )
        else:
            assignments = ",".join(
                f"{name}=?" for name in columns.split(",") if name != "session_id"
            )
            cursor = await db.execute(
                f"UPDATE session_context_usage_state_v2 SET {assignments} "
                "WHERE session_id=? AND state_version=?",
                tuple(values[1:]) + (state.session_id, previous.state_version),
            )
        if int(cursor.rowcount or 0) != 1:
            await cursor.close()
            raise ContextUsageStateConflict(ContextUsageStateConflict.code)
        await cursor.close()

    async def set_context_usage_binding_state(
        self,
        session_id: str,
        *,
        binding_epoch: int,
        provider_id: str | None,
        model_id: str | None,
        availability: str = "available",
        updated_at: float | None = None,
    ) -> dict[str, Any]:
        """Advance public authority after Task 1 commits a binding mutation."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        result: ContextUsageStateV2 | None = None

        async def _do() -> None:
            nonlocal result
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        previous = await self._read_context_usage_state_tx(db, sid)
                        result = await self.advance_context_usage_binding_state_tx(
                            db,
                            session_id=sid,
                            binding_epoch=binding_epoch,
                            provider_id=provider_id,
                            model_id=model_id,
                            availability=availability,
                            updated_at=updated_at,
                            previous=previous,
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        raise

        await self._with_retry(_do)
        assert result is not None
        return result.to_public_payload()

    async def advance_context_usage_binding_state_tx(
        self,
        db: aiosqlite.Connection,
        *,
        session_id: str,
        binding_epoch: int,
        provider_id: str | None,
        model_id: str | None,
        availability: str = "available",
        updated_at: float | None = None,
        previous: ContextUsageStateV2 | None = None,
    ) -> ContextUsageStateV2:
        """Write binding-only authority inside the caller's binding txn.

        Task 1's set/clear/inherit paths call this helper before committing
        their ``code_session_provider`` mutation, so the new epoch and public
        Context state are observed atomically.
        """

        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        db.row_factory = aiosqlite.Row
        current = previous
        if current is None:
            current = await self._read_context_usage_state_tx(db, sid)
        state = binding_only_state(
            session_id=sid,
            state_version=(current.state_version + 1 if current else 1),
            binding_epoch=max(0, int(binding_epoch)),
            provider_id=provider_id,
            model_id=model_id,
            availability=availability,
            updated_at=float(updated_at or time.time()),
        )
        await self._write_context_usage_state_tx(db, current, state)
        return state

    async def get_context_usage_state(self, session_id: str) -> dict[str, Any]:
        """Read or atomically rebuild the V2 materialized authority."""

        if not self._initialized:
            await self.initialize()
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        rebuilt: ContextUsageStateV2 | None = None

        async def _do() -> None:
            nonlocal rebuilt
            async with self._write_lock:
                async with aiosqlite.connect(self._db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("PRAGMA busy_timeout=5000")
                    await db.execute("BEGIN IMMEDIATE")
                    try:
                        current = await self._read_context_usage_state_tx(db, sid)
                        if current is not None:
                            rebuilt = current
                            await db.commit()
                            return
                        binding = await self._read_context_usage_binding_tx(db, sid)
                        current = binding_only_state(
                            session_id=sid,
                            state_version=0,
                            binding_epoch=int(binding["binding_epoch"]),
                            provider_id=binding["provider_id"],
                            model_id=binding["preferred_model"],
                            availability=str(binding["availability"]),
                            updated_at=time.time(),
                        )
                        legacy_incomplete = False
                        after_created = -1.0
                        after_sample = ""
                        while True:
                            cursor = await db.execute(
                                """SELECT * FROM session_context_usage_history
                                   WHERE session_id=? AND
                                     (created_at>? OR (created_at=? AND sample_id>?))
                                   ORDER BY created_at,sample_id LIMIT 256""",
                                (sid, after_created, after_created, after_sample),
                            )
                            rows = await cursor.fetchall()
                            await cursor.close()
                            if not rows:
                                break
                            for row in rows:
                                try:
                                    metadata = json.loads(row["metadata_json"] or "{}")
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    metadata = {}
                                fact = {
                                    key: row[key]
                                    for key in (
                                        "sample_id",
                                        "source_event_id",
                                        "event_type",
                                        "tokens_after",
                                        "completion_tokens",
                                        "cached_tokens",
                                        "context_window",
                                        "effective_ceiling",
                                        "provider_id",
                                        "model_id",
                                        "binding_epoch",
                                        "based_on_sample_id",
                                        "completed_at",
                                    )
                                }
                                fact["model_id"] = fact["model_id"] or metadata.get("model")
                                if (
                                    fact["event_type"] == "compaction"
                                    and not fact["based_on_sample_id"]
                                ):
                                    legacy_incomplete = True
                                    continue
                                current = reduce_context_usage_state(current, fact)
                            after_created = float(rows[-1]["created_at"])
                            after_sample = str(rows[-1]["sample_id"])
                        if legacy_incomplete:
                            current = ContextUsageStateV2(
                                **{
                                    **current.__dict__,
                                    "legacy_incomplete": True,
                                }
                            )
                        if current.state_version == 0:
                            current = ContextUsageStateV2(
                                **{**current.__dict__, "state_version": 1}
                            )
                        await self._write_context_usage_state_tx(db, None, current)
                        await db.commit()
                        rebuilt = current
                    except Exception:
                        await db.rollback()
                        raise

        await self._with_retry(_do)
        assert rebuilt is not None
        payload = rebuilt.to_public_payload()
        if rebuilt.sample_id:
            sample = await self.get_context_usage_sample(sid, rebuilt.sample_id)
            metadata = sample.get("metadata") if isinstance(sample, dict) else None
            if isinstance(metadata, dict) and isinstance(
                metadata.get("cache_tokens_available"), bool
            ):
                payload["cache_tokens_available"] = metadata["cache_tokens_available"]
        return payload
