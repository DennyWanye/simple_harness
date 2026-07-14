# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Derived Session coverage tree storage for Context OS.

SessionDB messages remain authoritative.  Segment rows contain only hashes,
boundaries, token estimates, and bounded summaries; raw message bodies are
always read back from SessionDB.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import aiosqlite

from deskpet.memory.context_snapshot_store import canonical_json


class ContextSegmentError(RuntimeError):
    pass


class SegmentConflictError(ContextSegmentError):
    pass


class SegmentStaleError(ContextSegmentError):
    pass


@dataclass(frozen=True, slots=True)
class CausalMessageGroup:
    messages: tuple[Mapping[str, Any], ...]
    message_ids: tuple[int, ...]
    valid: bool = True
    error: str = ""


@dataclass(frozen=True, slots=True)
class ContextSegment:
    segment_id: str
    session_id: str
    level: int
    kind: str
    first_message_id: int
    last_message_id: int
    message_count: int
    source_hash: str
    child_segment_ids: tuple[str, ...]
    summary_text: str
    token_estimates: Mapping[str, int]
    provider_id: str | None
    model_id: str | None
    revision: int
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CoverageCommitProof:
    message_ids: tuple[int, ...]
    source_hash: str
    valid: bool
    child_source_hashes: tuple[str, ...]


_SUMMARY_MARKERS = (
    "[压缩摘要 / compressed summary]",
    "[session history summary]",
)


def is_eligible_session_message(message: Mapping[str, Any]) -> bool:
    if bool(message.get("is_summary", False)) or bool(message.get("deleted", False)):
        return False
    role = str(message.get("role", ""))
    if role not in {"user", "assistant", "tool"}:
        return False
    content = str(message.get("content", "") or "").strip().lower()
    return not any(content.startswith(marker) for marker in _SUMMARY_MARKERS)


def eligible_session_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [message for message in messages if is_eligible_session_message(message)]


def _tool_call_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    raw = message.get("tool_calls") or ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return ()
    ids: list[str] = []
    if isinstance(raw, Sequence):
        for item in raw:
            if isinstance(item, Mapping) and item.get("id"):
                ids.append(str(item["id"]))
    return tuple(ids)


def group_causal_messages(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[CausalMessageGroup], tuple[str, ...]]:
    groups: list[CausalMessageGroup] = []
    errors: list[str] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        message_id = int(message.get("id", message.get("message_id", 0)) or 0)
        role = str(message.get("role", ""))
        call_ids = _tool_call_ids(message) if role == "assistant" else ()
        if call_ids:
            group_messages: list[Mapping[str, Any]] = [message]
            seen: set[str] = set()
            cursor = index + 1
            while cursor < len(messages) and str(messages[cursor].get("role", "")) == "tool":
                tool_message = messages[cursor]
                tool_call_id = str(tool_message.get("tool_call_id", "") or "")
                if tool_call_id not in call_ids or tool_call_id in seen:
                    break
                seen.add(tool_call_id)
                group_messages.append(tool_message)
                cursor += 1
            missing = [call_id for call_id in call_ids if call_id not in seen]
            valid = not missing
            error = "" if valid else f"missing tool results: {','.join(missing)}"
            if error:
                errors.append(f"message:{message_id}:{error}")
            groups.append(
                CausalMessageGroup(
                    messages=tuple(group_messages),
                    message_ids=tuple(
                        int(item.get("id", item.get("message_id", 0)) or 0)
                        for item in group_messages
                    ),
                    valid=valid,
                    error=error,
                )
            )
            index = cursor
            continue
        if role == "tool":
            error = "orphan tool result"
            errors.append(f"message:{message_id}:{error}")
            groups.append(
                CausalMessageGroup((message,), (message_id,), valid=False, error=error)
            )
        else:
            groups.append(CausalMessageGroup((message,), (message_id,)))
        index += 1
    return groups, tuple(errors)


def canonical_message_hash(messages: Sequence[Mapping[str, Any]]) -> str:
    payload = []
    for message in messages:
        payload.append(
            {
                "id": int(message.get("id", message.get("message_id", 0)) or 0),
                "role": str(message.get("role", "")),
                "content": message.get("content", ""),
                "reasoning_content": message.get("reasoning_content"),
                "tool_calls": message.get("tool_calls"),
                "tool_call_id": message.get("tool_call_id"),
            }
        )
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def conservative_token_estimate(messages: Sequence[Mapping[str, Any]]) -> int:
    return max(1, (len(canonical_json(list(messages))) + 3) // 4)


def _segment_id(
    session_id: str,
    level: int,
    kind: str,
    first_message_id: int,
    last_message_id: int,
    source_hash: str,
) -> str:
    raw = f"{session_id}|{level}|{kind}|{first_message_id}|{last_message_id}|{source_hash}"
    return "ctxseg_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class ContextSegmentStore:
    def __init__(self, db_path: str | Path, *, enabled: bool = True) -> None:
        self._db_path = Path(db_path)
        self._enabled = bool(enabled)
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def _connect(self, *, write: bool = False):
        if write:
            await self._write_lock.acquire()
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA busy_timeout=5000")
                yield db
        finally:
            if write:
                self._write_lock.release()

    async def get(self, segment_id: str) -> ContextSegment | None:
        if not self._enabled:
            return None
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_context_segments WHERE segment_id=?",
                (segment_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return self._decode(row) if row is not None else None

    async def list_segments(
        self,
        session_id: str,
        *,
        kind: str | None = None,
        status: str | None = "valid",
    ) -> list[ContextSegment]:
        if not self._enabled:
            return []
        clauses = ["session_id=?"]
        params: list[Any] = [session_id]
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_context_segments WHERE "
                + " AND ".join(clauses)
                + " ORDER BY first_message_id, last_message_id, level",
                tuple(params),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._decode(row) for row in rows]

    async def replace_raw_index(
        self,
        session_id: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_messages: int = 32,
        tokenizer_id: str = "deskpet-conservative-v1",
        token_estimator: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
        isolate_message_ids: Sequence[int] = (),
    ) -> list[ContextSegment]:
        """Reconcile level-0 leaves without persisting raw message bodies."""

        if not self._enabled:
            return []
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        eligible = eligible_session_messages(messages)
        if any(
            str(message.get("session_id", session_id) or session_id) != session_id
            for message in eligible
        ):
            raise ContextSegmentError("raw index input crosses session boundary")
        eligible_ids = [
            int(message.get("id", message.get("message_id", 0)) or 0)
            for message in eligible
        ]
        if any(message_id <= 0 for message_id in eligible_ids) or any(
            left >= right for left, right in zip(eligible_ids, eligible_ids[1:])
        ):
            raise ContextSegmentError("raw index message ids must be positive and increasing")
        groups, errors = group_causal_messages(eligible)
        if errors:
            raise ContextSegmentError("; ".join(errors))
        # A committed summary freezes its right edge.  Without this boundary,
        # the open raw tail would grow across the summarized range on the next
        # message (for example 65..80 -> 65..84), making future uncovered-tail
        # compaction impossible and incorrectly invalidating the summary.
        summary_boundaries = {
            int(segment.last_message_id)
            for segment in await self.list_segments(session_id, kind="summary")
        }
        estimate = token_estimator or conservative_token_estimate
        isolated = {int(item) for item in isolate_message_ids}
        chunks: list[list[CausalMessageGroup]] = []
        current: list[CausalMessageGroup] = []
        current_count = 0
        for group in groups:
            size = len(group.messages)
            isolate_group = any(message_id in isolated for message_id in group.message_ids)
            if isolate_group and current:
                chunks.append(current)
                current = []
                current_count = 0
            if current and current_count + size > max_messages:
                chunks.append(current)
                current = []
                current_count = 0
            current.append(group)
            current_count += size
            if isolate_group or group.message_ids[-1] in summary_boundaries:
                chunks.append(current)
                current = []
                current_count = 0
        if current:
            chunks.append(current)

        desired: list[ContextSegment] = []
        now = str(time.time())
        for chunk in chunks:
            raw_messages = [message for group in chunk for message in group.messages]
            ids = [int(message.get("id", message.get("message_id", 0)) or 0) for message in raw_messages]
            source_hash = canonical_message_hash(raw_messages)
            segment_id = _segment_id(session_id, 0, "raw_index", ids[0], ids[-1], source_hash)
            desired.append(
                ContextSegment(
                    segment_id=segment_id,
                    session_id=session_id,
                    level=0,
                    kind="raw_index",
                    first_message_id=ids[0],
                    last_message_id=ids[-1],
                    message_count=len(ids),
                    source_hash=source_hash,
                    child_segment_ids=(),
                    summary_text="",
                    token_estimates={tokenizer_id: int(estimate(raw_messages))},
                    provider_id=None,
                    model_id=None,
                    revision=1,
                    status="valid",
                    created_at=now,
                    updated_at=now,
                )
            )

        desired_ids = {segment.segment_id for segment in desired}
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT * FROM session_context_segments "
                    "WHERE session_id=? AND status='valid'",
                    (session_id,),
                )
                existing_rows = await cursor.fetchall()
                await cursor.close()
                stale_ranges: list[tuple[int, int]] = []
                for row in existing_rows:
                    if row["kind"] == "raw_index" and row["segment_id"] not in desired_ids:
                        stale_ranges.append((int(row["first_message_id"]), int(row["last_message_id"])))
                        await db.execute(
                            "UPDATE session_context_segments "
                            "SET status='stale', revision=revision+1, updated_at=? "
                            "WHERE segment_id=? AND status='valid'",
                            (now, row["segment_id"]),
                        )
                eligible_by_id = {
                    int(message.get("id", message.get("message_id", 0)) or 0): message
                    for message in eligible
                }
                # Appending after a summary range is not source drift.  Only
                # stale a derived segment when the exact covered SessionDB
                # slice has changed or disappeared.
                for row in existing_rows:
                    if row["kind"] == "raw_index" or not any(
                        not (
                            int(row["last_message_id"]) < first_id
                            or int(row["first_message_id"]) > last_id
                        )
                        for first_id, last_id in stale_ranges
                    ):
                        continue
                    covered = [
                        eligible_by_id[message_id]
                        for message_id in range(
                            int(row["first_message_id"]),
                            int(row["last_message_id"]) + 1,
                        )
                        if message_id in eligible_by_id
                    ]
                    if (
                        len(covered) == int(row["message_count"])
                        and canonical_message_hash(covered) == str(row["source_hash"])
                    ):
                        continue
                    await db.execute(
                        "UPDATE session_context_segments "
                        "SET status='stale', revision=revision+1, updated_at=? "
                        "WHERE segment_id=? AND status='valid'",
                        (now, row["segment_id"]),
                    )
                for segment in desired:
                    await db.execute(
                        "UPDATE session_context_segments "
                        "SET status='valid', revision=revision+1, "
                        "token_estimates_json=?, updated_at=? "
                        "WHERE segment_id=? AND kind='raw_index' AND status!='valid'",
                        (
                            canonical_json(dict(segment.token_estimates)),
                            now,
                            segment.segment_id,
                        ),
                    )
                    await db.execute(
                        "INSERT OR IGNORE INTO session_context_segments("
                        "segment_id,session_id,level,kind,first_message_id,last_message_id,"
                        "message_count,source_hash,child_segment_ids_json,summary_text,"
                        "token_estimates_json,provider_id,model_id,revision,status,created_at,updated_at"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        self._values(segment),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return desired

    async def commit_summary(
        self,
        session_id: str,
        child_segment_ids: Sequence[str],
        *,
        summary_text: str,
        proof: CoverageCommitProof,
        provider_id: str,
        model_id: str,
        token_estimates: Mapping[str, int],
    ) -> ContextSegment:
        if not self._enabled:
            raise ContextSegmentError("Context OS segment store is disabled")
        if not child_segment_ids or not summary_text.strip() or not proof.valid:
            raise ContextSegmentError("summary requires children, text, and valid coverage proof")
        stored: ContextSegment | None = None
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in child_segment_ids)
                cursor = await db.execute(
                    "SELECT * FROM session_context_segments WHERE segment_id IN ("
                    + placeholders
                    + ")",
                    tuple(child_segment_ids),
                )
                rows = await cursor.fetchall()
                await cursor.close()
                if len(rows) != len(child_segment_ids):
                    raise ContextSegmentError("summary child is missing")
                children = sorted((self._decode(row) for row in rows), key=lambda item: item.first_message_id)
                if any(child.session_id != session_id or child.status != "valid" for child in children):
                    raise SegmentStaleError("summary child is stale or belongs to another session")
                if len({child.level for child in children}) != 1:
                    raise ContextSegmentError("summary children must share one level")
                for left, right in zip(children, children[1:]):
                    if left.last_message_id >= right.first_message_id:
                        raise ContextSegmentError("summary children overlap")
                ids = tuple(int(item) for item in proof.message_ids)
                if (
                    not ids
                    or any(left >= right for left, right in zip(ids, ids[1:]))
                    or len(ids) != sum(child.message_count for child in children)
                    or ids[0] != children[0].first_message_id
                    or ids[-1] != children[-1].last_message_id
                    or not proof.source_hash
                ):
                    raise ContextSegmentError("summary coverage proof does not match children")
                if proof.child_source_hashes != tuple(
                    child.source_hash for child in children
                ):
                    raise SegmentStaleError("summary child source hash changed")
                offset = 0
                for child in children:
                    child_ids = ids[offset : offset + child.message_count]
                    if (
                        not child_ids
                        or child_ids[0] != child.first_message_id
                        or child_ids[-1] != child.last_message_id
                    ):
                        raise ContextSegmentError("summary children have a coverage gap")
                    offset += child.message_count
                level = children[0].level + 1
                source_hash = proof.source_hash
                segment_id = _segment_id(
                    session_id,
                    level,
                    "summary",
                    ids[0],
                    ids[-1],
                    source_hash,
                )
                now = str(time.time())
                segment = ContextSegment(
                    segment_id=segment_id,
                    session_id=session_id,
                    level=level,
                    kind="summary",
                    first_message_id=ids[0],
                    last_message_id=ids[-1],
                    message_count=len(ids),
                    source_hash=source_hash,
                    child_segment_ids=tuple(child.segment_id for child in children),
                    summary_text=summary_text.strip(),
                    token_estimates={str(key): int(value) for key, value in token_estimates.items()},
                    provider_id=provider_id,
                    model_id=model_id,
                    revision=1,
                    status="valid",
                    created_at=now,
                    updated_at=now,
                )
                cursor = await db.execute(
                    "SELECT * FROM session_context_segments WHERE segment_id=?",
                    (segment_id,),
                )
                existing_row = await cursor.fetchone()
                await cursor.close()
                if existing_row is None:
                    await db.execute(
                        "INSERT INTO session_context_segments("
                        "segment_id,session_id,level,kind,first_message_id,last_message_id,"
                        "message_count,source_hash,child_segment_ids_json,summary_text,"
                        "token_estimates_json,provider_id,model_id,revision,status,created_at,updated_at"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        self._values(segment),
                    )
                else:
                    existing = self._decode(existing_row)
                    if (
                        existing.summary_text != segment.summary_text
                        or existing.child_segment_ids != segment.child_segment_ids
                        or existing.provider_id != segment.provider_id
                        or existing.model_id != segment.model_id
                    ):
                        raise SegmentConflictError(
                            "summary identity already has different content"
                        )
                    if existing.status != "valid":
                        await db.execute(
                            "UPDATE session_context_segments "
                            "SET status='valid', revision=revision+1, "
                            "token_estimates_json=?, updated_at=? WHERE segment_id=?",
                            (
                                canonical_json(dict(segment.token_estimates)),
                                now,
                                segment_id,
                            ),
                        )
                cursor = await db.execute(
                    "SELECT * FROM session_context_segments WHERE segment_id=?",
                    (segment_id,),
                )
                stored_row = await cursor.fetchone()
                await cursor.close()
                if stored_row is None:
                    raise ContextSegmentError("summary commit was not observable")
                stored = self._decode(stored_row)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        if stored is None:
            raise ContextSegmentError("summary commit was not observable")
        return stored

    async def mark_stale(
        self,
        segment_id: str,
        *,
        expected_revision: int,
    ) -> ContextSegment:
        if not self._enabled:
            raise ContextSegmentError("Context OS segment store is disabled")
        now = str(time.time())
        stored: ContextSegment | None = None
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "UPDATE session_context_segments "
                "SET status='stale', revision=revision+1, updated_at=? "
                "WHERE segment_id=? AND revision=?",
                (now, segment_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise SegmentConflictError(
                    f"segment revision conflict: {segment_id}/{expected_revision}"
                )
            cursor = await db.execute(
                "SELECT * FROM session_context_segments WHERE segment_id=?",
                (segment_id,),
            )
            stored_row = await cursor.fetchone()
            await cursor.close()
            stored = self._decode(stored_row) if stored_row is not None else None
            await db.commit()
        assert stored is not None
        return stored

    async def delete_session(self, session_id: str) -> int:
        if not self._enabled:
            return 0
        async with self._connect(write=True) as db:
            cursor = await db.execute(
                "DELETE FROM session_context_segments WHERE session_id=?",
                (session_id,),
            )
            await db.commit()
            return int(cursor.rowcount)

    @staticmethod
    def _decode(row: aiosqlite.Row) -> ContextSegment:
        children = json.loads(str(row["child_segment_ids_json"]))
        estimates = json.loads(str(row["token_estimates_json"]))
        if not isinstance(children, list) or not isinstance(estimates, dict):
            raise ContextSegmentError("corrupt segment JSON")
        return ContextSegment(
            segment_id=str(row["segment_id"]),
            session_id=str(row["session_id"]),
            level=int(row["level"]),
            kind=str(row["kind"]),
            first_message_id=int(row["first_message_id"]),
            last_message_id=int(row["last_message_id"]),
            message_count=int(row["message_count"]),
            source_hash=str(row["source_hash"]),
            child_segment_ids=tuple(str(item) for item in children),
            summary_text=str(row["summary_text"]),
            token_estimates={str(key): int(value) for key, value in estimates.items()},
            provider_id=str(row["provider_id"]) if row["provider_id"] is not None else None,
            model_id=str(row["model_id"]) if row["model_id"] is not None else None,
            revision=int(row["revision"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _values(segment: ContextSegment) -> tuple[Any, ...]:
        return (
            segment.segment_id,
            segment.session_id,
            segment.level,
            segment.kind,
            segment.first_message_id,
            segment.last_message_id,
            segment.message_count,
            segment.source_hash,
            canonical_json(list(segment.child_segment_ids)),
            segment.summary_text,
            canonical_json(dict(segment.token_estimates)),
            segment.provider_id,
            segment.model_id,
            segment.revision,
            segment.status,
            segment.created_at,
            segment.updated_at,
        )


def canonical_message_ids_hash(message_ids: Sequence[int]) -> str:
    return hashlib.sha256(canonical_json([int(item) for item in message_ids]).encode("utf-8")).hexdigest()


__all__ = [
    "CausalMessageGroup",
    "ContextSegment",
    "ContextSegmentError",
    "ContextSegmentStore",
    "CoverageCommitProof",
    "SegmentConflictError",
    "SegmentStaleError",
    "canonical_message_hash",
    "canonical_message_ids_hash",
    "conservative_token_estimate",
    "eligible_session_messages",
    "group_causal_messages",
    "is_eligible_session_message",
]
