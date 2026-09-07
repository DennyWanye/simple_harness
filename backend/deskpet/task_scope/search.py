# SPDX-License-Identifier: BUSL-1.1

"""Permission-first FTS5 locator and exact canonical TaskScope open."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from deskpet.memory.writer_fence import human_memory_connection

from deskpet.memory.recovery_work_items import is_human_memory_work_item_parked_tx
from deskpet.task_scope.projection_sources import load_projection_source_tx
from deskpet.task_scope.projections import (
    CheckpointDriftReport,
    TaskScopeProjectionStore,
)
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier
from deskpet.task_scope.store import TaskScopeNotFound, _uuid

MAX_QUERY_BYTES = 64 * 1024
MAX_CANDIDATES = 100
MAX_ALLOWED_SCOPES = 1000
MAX_SNIPPET_BYTES = 1024
MAX_RESUME_PACKAGE_BYTES = 24 * 1024


class TaskScopeSearchError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    task_scope_id: str
    source_id: str
    source_hash: str
    title: str
    goal: str
    project: str
    status: str
    snippet: str
    rank: float


@dataclass(frozen=True, slots=True)
class SearchResult:
    candidates: tuple[SearchCandidate, ...]
    next_cursor: str | None
    receipt_hash: str


@dataclass(frozen=True, slots=True)
class ExactOpenResult:
    task_scope_id: str
    source_id: str
    source_hash: str
    resume_package: dict[str, object]
    resume_package_hash: str
    receipt_hash: str
    drift_report: CheckpointDriftReport | None


class TaskScopeSearchStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._projections = TaskScopeProjectionStore(db_path)

    async def rebuild_scope(self, task_scope_id: str) -> str:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await self._require_fts_tx(db)
                source = await load_projection_source_tx(db, task_scope_id=task_scope_id)
                outbox_cursor = await db.execute(
                    "SELECT outbox_id FROM task_scope_search_outbox WHERE task_scope_id=? "
                    "AND canonical_revision=?",
                    (task_scope_id, source.canonical_revision),
                )
                outbox = await outbox_cursor.fetchone()
                await outbox_cursor.close()
                if outbox is None:
                    raise TaskScopeSearchError("task_scope_search_outbox_missing")
                if await is_human_memory_work_item_parked_tx(
                    db,
                    worker_kind="search",
                    source_table="task_scope_search_outbox",
                    primary_key="outbox_id",
                    item_pk=str(outbox["outbox_id"]),
                ):
                    raise TaskScopeSearchError("human_memory_search_work_item_parked")
                state_cursor = await db.execute(
                    "SELECT s.subject,s.title,r.state_json FROM task_scopes s "
                    "JOIN task_scope_canonical_revisions r ON r.task_scope_id=s.task_scope_id "
                    "WHERE s.task_scope_id=? AND r.revision=?",
                    (task_scope_id, source.canonical_revision),
                )
                row = await state_cursor.fetchone()
                await state_cursor.close()
                if row is None:
                    raise TaskScopeNotFound(TaskScopeNotFound.code)
                state = json.loads(str(row["state_json"]))
                latest_checkpoint_cursor = await db.execute(
                    "SELECT checkpoint_json FROM task_scope_checkpoints WHERE task_scope_id=? "
                    "ORDER BY created_at DESC,checkpoint_id DESC LIMIT 1",
                    (task_scope_id,),
                )
                checkpoint = await latest_checkpoint_cursor.fetchone()
                await latest_checkpoint_cursor.close()
                checkpoint_data = (
                    {} if checkpoint is None else json.loads(str(checkpoint["checkpoint_json"]))
                )
                metadata = checkpoint_data.get("metadata", {})
                project = str(metadata.get("project", metadata.get("repo", "")))
                title = str(row["title"])
                goal = "" if state.get("goal") is None else str(state["goal"])
                status = str(state.get("status", "unknown"))
                document_text = "\n".join(
                    [
                        title,
                        goal,
                        project,
                        status,
                        " ".join(
                            str(item.get("value", ""))
                            for item in state.get("operations", [])
                            if isinstance(item, dict)
                        ),
                    ]
                )
                document = {
                    "schema_version": 1,
                    "task_scope_id": task_scope_id,
                    "subject": str(row["subject"]),
                    "source_id": source.source_id,
                    "source_hash": source.source_hash,
                    "source_sequence": source.source_sequence,
                    "title": title,
                    "goal": goal,
                    "project": project,
                    "status": status,
                    "document_text": document_text,
                }
                document_hash = canonical_hash(document)
                receipt = {
                    "schema_version": 1,
                    "source_id": source.source_id,
                    "source_hash": source.source_hash,
                    "document_hash": document_hash,
                }
                receipt_hash = canonical_hash(receipt)
                document_id = _uuid(f"task-scope-search-document:{source.source_hash}")
                await db.execute(
                    "INSERT OR IGNORE INTO task_scope_search_documents("
                    "document_id,task_scope_id,subject,source_id,source_hash,source_sequence,"
                    "title,goal,project,status,updated_at,document_text,document_hash,"
                    "receipt_hash,receipt_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        task_scope_id,
                        row["subject"],
                        source.source_id,
                        source.source_hash,
                        source.source_sequence,
                        title,
                        goal,
                        project,
                        status,
                        float(source.source_sequence),
                        document_text,
                        document_hash,
                        receipt_hash,
                        canonical_json(receipt),
                        time.time(),
                    ),
                )
                await db.execute(
                    "DELETE FROM task_scope_search_fts WHERE document_id=?", (document_id,)
                )
                await db.execute(
                    "INSERT INTO task_scope_search_fts("
                    "document_id,task_scope_id,subject,title,goal,project,status,content) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (document_id, task_scope_id, row["subject"], title, goal, project, status, document_text),
                )
                await db.execute(
                    "INSERT INTO task_scope_search_heads("
                    "task_scope_id,document_id,source_id,source_hash,updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(task_scope_id) DO UPDATE SET document_id=excluded.document_id,"
                    "source_id=excluded.source_id,source_hash=excluded.source_hash,updated_at=excluded.updated_at",
                    (task_scope_id, document_id, source.source_id, source.source_hash, time.time()),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return document_hash

    async def rebuild_index(self) -> str:
        async with self._connection() as db:
            await self._require_fts_tx(db)
            cursor = await db.execute(
                "SELECT task_scope_id FROM task_scope_projection_source_heads ORDER BY task_scope_id"
            )
            scope_ids = [str(row["task_scope_id"]) for row in await cursor.fetchall()]
            await cursor.close()
        for scope_id in scope_ids:
            await self.rebuild_scope(scope_id)
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "SELECT d.document_id,d.document_hash,d.source_hash FROM task_scope_search_heads h "
                    "JOIN task_scope_search_documents d ON d.document_id=h.document_id "
                    "ORDER BY d.task_scope_id"
                )
                rows = await cursor.fetchall()
                await cursor.close()
                source_set_hash = canonical_hash([row["source_hash"] for row in rows])
                index_root_hash = canonical_hash([row["document_hash"] for row in rows])
                rebuild_id = _uuid(f"task-scope-search-rebuild:{source_set_hash}:{index_root_hash}")
                receipt = {
                    "schema_version": 1,
                    "rebuild_id": rebuild_id,
                    "source_set_hash": source_set_hash,
                    "index_root_hash": index_root_hash,
                    "document_count": len(rows),
                }
                receipt_hash = canonical_hash(receipt)
                await db.execute(
                    "INSERT OR IGNORE INTO task_scope_search_rebuild_receipts("
                    "receipt_id,rebuild_id,source_set_hash,index_root_hash,document_count,"
                    "receipt_hash,receipt_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        _uuid(f"task-scope-search-rebuild-receipt:{receipt_hash}"),
                        rebuild_id,
                        source_set_hash,
                        index_root_hash,
                        len(rows),
                        receipt_hash,
                        canonical_json(receipt),
                        time.time(),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt_hash

    async def search(
        self,
        *,
        subject: str,
        allowed_scope_ids: tuple[str, ...] | list[str],
        query: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> SearchResult:
        identifier(subject, "subject", 512)
        allowed = tuple(sorted(set(allowed_scope_ids)))
        if len(allowed) > MAX_ALLOWED_SCOPES:
            raise TaskScopeSearchError("human_memory_search_allowset_too_large")
        for scope_id in allowed:
            identifier(scope_id, "task_scope_id", 512)
        if not isinstance(query, str) or not query.strip():
            raise TaskScopeSearchError("human_memory_search_query_invalid")
        if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
            raise TaskScopeSearchError("human_memory_search_query_too_large")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CANDIDATES:
            raise TaskScopeSearchError("human_memory_search_limit_invalid")
        request_base = {
            "schema_version": 1,
            "subject": subject,
            "allowed_scope_ids": list(allowed),
            "query": query,
            "limit": limit,
        }
        request_hash = canonical_hash(request_base)
        async with self._connection() as db:
            await self._require_fts_tx(db)
            if allowed:
                allow_placeholders = ",".join("?" for _ in allowed)
                authorized_cursor = await db.execute(
                    "SELECT task_scope_id FROM task_scopes WHERE subject=? "
                    f"AND task_scope_id IN ({allow_placeholders}) ORDER BY task_scope_id",
                    (subject, *allowed),
                )
                authorized_scopes = tuple(
                    str(row["task_scope_id"]) for row in await authorized_cursor.fetchall()
                )
                await authorized_cursor.close()
            else:
                authorized_scopes = ()
        if not authorized_scopes:
            _decode_cursor(cursor, request_hash, canonical_hash([]))
            return await self._record_search_receipt(subject, request_hash, (), None)
        for scope_id in authorized_scopes:
            await self.rebuild_scope(scope_id)
        fts_query = _fts_query(query)
        async with self._connection() as db:
            await self._require_fts_tx(db)
            document_cursor = await db.execute(
                "SELECT d.document_id,d.source_hash FROM task_scope_search_heads h "
                "JOIN task_scope_search_documents d ON d.document_id=h.document_id "
                f"WHERE d.subject=? AND d.task_scope_id IN ({','.join('?' for _ in authorized_scopes)}) "
                "ORDER BY d.document_id",
                (subject, *authorized_scopes),
            )
            document_rows = await document_cursor.fetchall()
            authorized_documents = tuple(str(row["document_id"]) for row in document_rows)
            source_set_hash = canonical_hash(
                [
                    {"document_id": row["document_id"], "source_hash": row["source_hash"]}
                    for row in document_rows
                ]
            )
            await document_cursor.close()
            if not authorized_documents:
                _decode_cursor(cursor, request_hash, source_set_hash)
                return await self._record_search_receipt(subject, request_hash, (), None)
            offset = _decode_cursor(cursor, request_hash, source_set_hash)
            placeholders = ",".join("?" for _ in authorized_documents)
            # Phase one above produces only authorized immutable document ids.
            # FTS MATCH and ranking are phase two and cannot introduce another id.
            sql = (
                "WITH ranked AS ("
                " SELECT d.*,bm25(task_scope_search_fts) AS score,"
                " snippet(task_scope_search_fts,7,'[',']','…',16) AS hit "
                " FROM task_scope_search_documents d JOIN task_scope_search_fts "
                " ON task_scope_search_fts.document_id=d.document_id "
                f" WHERE d.document_id IN ({placeholders}) AND task_scope_search_fts MATCH ?"
                ") SELECT * FROM ranked ORDER BY score ASC,source_sequence DESC,task_scope_id ASC "
                "LIMIT ? OFFSET ?"
            )
            query_cursor = await db.execute(
                sql, (*authorized_documents, fts_query, limit + 1, offset)
            )
            rows = await query_cursor.fetchall()
            await query_cursor.close()
        has_more = len(rows) > limit
        rows = rows[:limit]
        candidates = tuple(
            SearchCandidate(
                task_scope_id=str(row["task_scope_id"]),
                source_id=str(row["source_id"]),
                source_hash=str(row["source_hash"]),
                title=_bounded(str(row["title"]), 2048),
                goal=_bounded(str(row["goal"]), 4096),
                project=_bounded(str(row["project"]), 2048),
                status=_bounded(str(row["status"]), 256),
                snippet=_bounded(str(row["hit"]), MAX_SNIPPET_BYTES),
                rank=float(row["score"]),
            )
            for row in rows
        )
        next_cursor = (
            _encode_cursor(request_hash, source_set_hash, offset + limit)
            if has_more
            else None
        )
        return await self._record_search_receipt(subject, request_hash, candidates, next_cursor)

    async def open_exact(
        self,
        *,
        subject: str,
        allowed_scope_ids: tuple[str, ...] | list[str],
        task_scope_id: str,
        expected_source_hash: str | None = None,
        live_probe: Mapping[str, object] | None = None,
        source_id: str | None = None,
        materialized_only: bool = False,
    ) -> ExactOpenResult:
        identifier(subject, "subject", 512)
        identifier(task_scope_id, "task_scope_id", 512)
        allowed = set(allowed_scope_ids)
        if task_scope_id not in allowed:
            raise TaskScopeSearchError("human_memory_permission_denied")
        async with self._connection() as db:
            scope_cursor = await db.execute(
                "SELECT subject FROM task_scopes WHERE task_scope_id=?", (task_scope_id,)
            )
            scope = await scope_cursor.fetchone()
            await scope_cursor.close()
            if scope is None:
                raise TaskScopeNotFound(TaskScopeNotFound.code)
            if scope["subject"] != subject:
                raise TaskScopeSearchError("human_memory_permission_denied")
            source = await load_projection_source_tx(
                db,
                source_id=source_id,
                task_scope_id=None if source_id is not None else task_scope_id,
            )
            if source.task_scope_id != task_scope_id:
                raise TaskScopeSearchError("human_memory_permission_denied")
            if expected_source_hash is not None and expected_source_hash != source.source_hash:
                raise TaskScopeSearchError("task_scope_source_stale")
        if materialized_only:
            views = {
                kind: await self._projections.read_materialized_view(
                    kind, source_id=source.source_id
                )
                for kind in ("README", "PLAN", "STATUS", "RESUME", "EVIDENCE")
            }
        else:
            views = await self._projections.materialize(source_id=source.source_id)
        drift_report = (
            None
            if live_probe is None
            else await self._projections.verify_checkpoint(
                source_id=source.source_id, live_probe=live_probe
            )
        )
        read_views: dict[str, dict[str, object]] = {
            kind: {
                "content": _bounded(views[kind].content, 4096),
                "content_sha256": views[kind].content_sha256,
                "root_block_id": views[kind].root_block_id,
                "block_count": views[kind].block_count,
                "receipt_hash": views[kind].receipt_hash,
            }
            for kind in ("README", "PLAN", "STATUS", "RESUME", "EVIDENCE")
        }
        package: dict[str, object] = {
            "schema_version": 1,
            "task_scope_id": task_scope_id,
            "source_id": source.source_id,
            "source_hash": source.source_hash,
            "canonical_revision": source.canonical_revision,
            "event_watermark": source.event_watermark,
            "binding_set_revision": source.binding_set_revision,
            "binding_receipt_hash": source.binding_receipt_hash,
            "checkpoint_sequence": source.checkpoint_sequence,
            "checkpoint_set_root": source.checkpoint_set_root,
            "read_views": read_views,
        }
        encoded = canonical_json(package).encode("utf-8")
        if len(encoded) > MAX_RESUME_PACKAGE_BYTES:
            for item in read_views.values():
                item["content"] = _bounded(str(item["content"]), 1024)
            encoded = canonical_json(package).encode("utf-8")
        if len(encoded) > MAX_RESUME_PACKAGE_BYTES:
            raise TaskScopeSearchError("task_scope_resume_package_too_large")
        package_hash = hashlib.sha256(encoded).hexdigest()
        request = {
            "schema_version": 1,
            "subject": subject,
            "task_scope_id": task_scope_id,
            "expected_source_hash": expected_source_hash,
            "source_id": source_id,
            "materialized_only": materialized_only,
        }
        request_hash = canonical_hash(request)
        receipt_hash = await self._record_access_receipt(
            "open", subject, request_hash, package_hash
        )
        return ExactOpenResult(
            task_scope_id,
            source.source_id,
            source.source_hash,
            package,
            package_hash,
            receipt_hash,
            drift_report,
        )

    async def _record_search_receipt(
        self,
        subject: str,
        request_hash: str,
        candidates: tuple[SearchCandidate, ...],
        next_cursor: str | None,
    ) -> SearchResult:
        result_payload = {
            "candidates": [
                {
                    "task_scope_id": item.task_scope_id,
                    "source_id": item.source_id,
                    "source_hash": item.source_hash,
                    "rank": item.rank,
                }
                for item in candidates
            ],
            "next_cursor": next_cursor,
        }
        result_hash = canonical_hash(result_payload)
        receipt_hash = await self._record_access_receipt(
            "search", subject, request_hash, result_hash
        )
        return SearchResult(candidates, next_cursor, receipt_hash)

    async def _record_access_receipt(
        self, operation: str, subject: str, request_hash: str, result_hash: str
    ) -> str:
        receipt = {
            "schema_version": 1,
            "operation": operation,
            "subject": subject,
            "request_hash": request_hash,
            "result_hash": result_hash,
        }
        receipt_hash = canonical_hash(receipt)
        async with self._connection() as db:
            await db.execute(
                "INSERT OR IGNORE INTO task_scope_search_access_receipts("
                "receipt_id,operation,subject,request_hash,result_hash,receipt_hash,"
                "receipt_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    _uuid(f"task-scope-search-access:{receipt_hash}"),
                    operation,
                    subject,
                    request_hash,
                    result_hash,
                    receipt_hash,
                    canonical_json(receipt),
                    time.time(),
                ),
            )
            await db.commit()
        return receipt_hash

    @staticmethod
    async def _require_fts_tx(db: aiosqlite.Connection) -> None:
        cursor = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='task_scope_search_fts'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise TaskScopeSearchError("human_memory_search_unavailable")
        try:
            probe = await db.execute(
                "SELECT count(*) FROM task_scope_search_fts WHERE task_scope_search_fts MATCH ?",
                ('"__fts5_probe__"',),
            )
            await probe.fetchone()
            await probe.close()
        except Exception as exc:
            raise TaskScopeSearchError("human_memory_search_unavailable") from exc

    @asynccontextmanager
    async def _connection(self):
        async with human_memory_connection(self._db_path) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=5000")
            yield connection


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w\-]+", query, flags=re.UNICODE)
    if not tokens:
        raise TaskScopeSearchError("human_memory_search_query_invalid")
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:64])


def _bounded(value: str, limit: int) -> str:
    data = value.encode("utf-8")
    if len(data) <= limit:
        return value
    data = data[: max(0, limit - 3)]
    while True:
        try:
            return data.decode("utf-8") + "…"
        except UnicodeDecodeError:
            data = data[:-1]


def _encode_cursor(request_hash: str, source_set_hash: str, offset: int) -> str:
    payload = canonical_json(
        {
            "schema_version": 1,
            "request_hash": request_hash,
            "source_set_hash": source_set_hash,
            "offset": offset,
        }
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, request_hash: str, source_set_hash: str) -> int:
    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if payload != {
            "schema_version": 1,
            "request_hash": request_hash,
            "source_set_hash": source_set_hash,
            "offset": payload["offset"],
        }:
            raise ValueError
        offset = payload["offset"]
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except Exception as exc:
        raise TaskScopeSearchError("human_memory_search_cursor_invalid") from exc
