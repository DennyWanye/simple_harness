# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable state.db → Memory SDK projection for non-Harness messages."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import time
from typing import Any, Callable
import uuid

import aiosqlite

from simple_harness.runtime import (
    ConversationMemoryError,
    ConversationMemoryErrorCode,
    ConversationMemoryIntent,
    ConversationMemoryRole,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProductMemoryOutboxRecord:
    source_event_id: str
    state_db_instance_id: str
    message_id: int
    user_id: str
    session_id: str
    role: str
    memory_text: str
    payload_hash: str
    status: str
    attempt: int
    claim_token: str | None
    lease_expires_at: float | None
    next_attempt_at: float

    def intent(self) -> ConversationMemoryIntent:
        intent = ConversationMemoryIntent(
            source_event_id=self.source_event_id,
            user_id=self.user_id,
            session_id=self.session_id,
            role=ConversationMemoryRole(self.role),
            memory_text=self.memory_text,
        )
        if intent.payload_hash != self.payload_hash:
            raise RuntimeError("product_memory_outbox_payload_conflict")
        return intent


def _record(row: aiosqlite.Row) -> ProductMemoryOutboxRecord:
    return ProductMemoryOutboxRecord(
        source_event_id=str(row["source_event_id"]),
        state_db_instance_id=str(row["state_db_instance_id"]),
        message_id=int(row["message_id"]),
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]),
        role=str(row["role"]),
        memory_text=str(row["memory_text"]),
        payload_hash=str(row["payload_hash"]),
        status=str(row["status"]),
        attempt=int(row["attempt"]),
        claim_token=row["claim_token"],
        lease_expires_at=row["lease_expires_at"],
        next_attempt_at=float(row["next_attempt_at"]),
    )


class ProductMemoryOutboxRepository:
    """Bounded CAS repository; message bodies never enter logs or errors."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)

    async def claim(
        self,
        *,
        owner_id: str,
        now: float,
        lease_seconds: float,
        limit: int,
    ) -> tuple[ProductMemoryOutboxRecord, ...]:
        if not owner_id or lease_seconds <= 0 or limit < 1:
            raise ValueError("invalid product outbox claim bounds")
        token_prefix = f"{owner_id}:{uuid.uuid4().hex}"
        claimed: list[ProductMemoryOutboxRecord] = []
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT source_event_id FROM product_memory_outbox
                   WHERE (status='pending' AND next_attempt_at<=?)
                      OR (status='claimed' AND lease_expires_at<=?)
                   ORDER BY created_at, source_event_id LIMIT ?""",
                (now, now, limit),
            )
            identities = [str(row[0]) for row in await cursor.fetchall()]
            await cursor.close()
            for index, source_event_id in enumerate(identities):
                token = f"{token_prefix}:{index}"
                await db.execute(
                    """UPDATE product_memory_outbox
                       SET status='claimed',attempt=attempt+1,claim_token=?,
                           lease_expires_at=?,updated_at=?
                       WHERE source_event_id=? AND
                         ((status='pending' AND next_attempt_at<=?) OR
                          (status='claimed' AND lease_expires_at<=?))""",
                    (token, now + lease_seconds, now, source_event_id, now, now),
                )
                row = await (
                    await db.execute(
                        "SELECT * FROM product_memory_outbox WHERE source_event_id=? AND claim_token=?",
                        (source_event_id, token),
                    )
                ).fetchone()
                if row is not None:
                    claimed.append(_record(row))
            await db.commit()
        return tuple(claimed)

    async def settle_applied(self, record: ProductMemoryOutboxRecord, *, now: float) -> bool:
        async with aiosqlite.connect(self._path) as db:
            cursor = await db.execute(
                """UPDATE product_memory_outbox
                   SET status='applied',claim_token=NULL,lease_expires_at=NULL,
                       applied_at=?,updated_at=?,last_error_code=NULL
                   WHERE source_event_id=? AND status='claimed' AND claim_token=?
                         AND attempt=?""",
                (now, now, record.source_event_id, record.claim_token, record.attempt),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def settle_failure(
        self,
        record: ProductMemoryOutboxRecord,
        *,
        now: float,
        error_code: str,
        permanent: bool,
        retry_at: float,
    ) -> bool:
        async with aiosqlite.connect(self._path) as db:
            cursor = await db.execute(
                """UPDATE product_memory_outbox
                   SET status=?,claim_token=NULL,lease_expires_at=NULL,
                       next_attempt_at=?,updated_at=?,last_error_code=?
                   WHERE source_event_id=? AND status='claimed' AND claim_token=?
                         AND attempt=?""",
                (
                    "dead_letter" if permanent else "pending",
                    retry_at,
                    now,
                    error_code,
                    record.source_event_id,
                    record.claim_token,
                    record.attempt,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def counts(self) -> dict[str, int]:
        async with aiosqlite.connect(self._path) as db:
            rows = await (
                await db.execute(
                    "SELECT status,count(*) FROM product_memory_outbox GROUP BY status"
                )
            ).fetchall()
        return {str(status): int(count) for status, count in rows}

    async def cleanup_applied(
        self,
        *,
        before: float,
        limit: int = 256,
    ) -> int:
        """Delete only a bounded oldest applied slice; never touch live work."""

        if limit < 1:
            raise ValueError("cleanup limit must be positive")
        async with aiosqlite.connect(self._path) as db:
            cursor = await db.execute(
                """DELETE FROM product_memory_outbox
                   WHERE source_event_id IN (
                     SELECT source_event_id FROM product_memory_outbox
                     WHERE status='applied' AND applied_at<?
                     ORDER BY applied_at,source_event_id LIMIT ?
                   )""",
                (before, limit),
            )
            await db.commit()
            return max(0, int(cursor.rowcount or 0))


class ProductMemoryDispatcher:
    """Restart-safe bounded dispatcher for the product-owned outbox."""

    def __init__(
        self,
        repository: ProductMemoryOutboxRepository,
        sink: Any,
        *,
        owner_id: str,
        batch_size: int = 16,
        lease_seconds: float = 30.0,
        max_attempts: int = 8,
        poll_seconds: float = 0.25,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._sink = sink
        self._owner_id = owner_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="product-memory-dispatcher")

    async def dispatch_once(self) -> int:
        now = self._clock()
        records = await self._repository.claim(
            owner_id=self._owner_id,
            now=now,
            lease_seconds=self._lease_seconds,
            limit=self._batch_size,
        )
        for item in records:
            try:
                result = await self._sink.apply(item.intent())
                if (
                    result.source_event_id != item.source_event_id
                    or result.payload_hash != item.payload_hash
                ):
                    raise RuntimeError("product_memory_sink_receipt_conflict")
            except ConversationMemoryError as exc:
                permanent = exc.code in {
                    ConversationMemoryErrorCode.APPLY_CONFLICT,
                    ConversationMemoryErrorCode.PERMANENT,
                }
                await self._repository.settle_failure(
                    item,
                    now=self._clock(),
                    error_code=exc.code.value,
                    permanent=permanent or item.attempt >= self._max_attempts,
                    retry_at=self._clock() + min(60.0, 0.25 * (2 ** min(item.attempt, 8))),
                )
            except Exception:
                await self._repository.settle_failure(
                    item,
                    now=self._clock(),
                    error_code="memory_transient",
                    permanent=item.attempt >= self._max_attempts,
                    retry_at=self._clock() + min(60.0, 0.25 * (2 ** min(item.attempt, 8))),
                )
            else:
                await self._repository.settle_applied(item, now=self._clock())
        return len(records)

    async def drain(self, *, max_batches: int = 8) -> int:
        """Bound shutdown/startup catch-up without turning close into a hang."""

        if max_batches < 1:
            raise ValueError("max_batches must be positive")
        total = 0
        for _ in range(max_batches):
            processed = await self.dispatch_once()
            total += processed
            if processed < self._batch_size:
                break
        return total

    async def backlog_counts(self) -> dict[str, int]:
        return await self._repository.counts()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self.dispatch_once()
            except (aiosqlite.Error, OSError):
                if self._stop.is_set():
                    return
                processed = 0
            if processed == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), self._poll_seconds)
                except TimeoutError:
                    pass

    async def close(self, *, timeout_seconds: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self.drain(), timeout=timeout_seconds)
        except (TimeoutError, aiosqlite.Error, OSError):
            pass
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout_seconds)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


__all__ = (
    "ProductMemoryDispatcher",
    "ProductMemoryOutboxRecord",
    "ProductMemoryOutboxRepository",
)
