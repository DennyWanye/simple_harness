"""Receipt-supplied OA1 public reader. No grant minting, private SQL or live fallback."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import replace
from pathlib import Path

from deskpet.operation_audit.memory_attempts import owner_ref
from deskpet.operation_audit.store import canonical, digest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_audit_reads (
 read_ref TEXT PRIMARY KEY, query_hash TEXT NOT NULL, generation INTEGER NOT NULL,
 status TEXT NOT NULL, snapshot_hash TEXT, next_cursor TEXT, page_count INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until REAL, code TEXT
);
CREATE TABLE IF NOT EXISTS memory_audit_read_attempts (
 attempt_ref TEXT PRIMARY KEY, read_ref TEXT NOT NULL, generation INTEGER NOT NULL,
 page_index INTEGER NOT NULL, started_at REAL NOT NULL, settled_at REAL, outcome TEXT,
 superseded_by TEXT, access_event_hash TEXT
);
CREATE TABLE IF NOT EXISTS memory_audit_inputs (
 read_ref TEXT NOT NULL, page_index INTEGER NOT NULL, snapshot_hash TEXT NOT NULL,
 page_json TEXT NOT NULL, page_hash TEXT NOT NULL, host_hash TEXT NOT NULL,
 PRIMARY KEY(read_ref,page_index)
);
"""


def validated(page):
    import simple_harness_memory as m

    if (
        type(page) is not m.OperationAuditPage
        or page.all_operations_recorded is not False
    ):
        raise ValueError("memory_audit_page_invalid")
    # Rebuild frozen public DTOs rather than trusting dataclass identity alone.
    for item in page.items:
        if (
            type(item) is not m.OperationAuditItemV1
            or replace(item).item_hash != item.item_hash
        ):
            raise ValueError("memory_audit_item_invalid")
    rebuilt = replace(page)
    if rebuilt.page_hash != page.page_hash:
        raise ValueError("memory_audit_page_hash_invalid")
    data = page.to_json()
    if len(canonical(data).encode()) > 2 * 1024 * 1024:
        raise ValueError("memory_audit_page_bytes_invalid")
    return data, page.page_hash, page.access_event_hash


class MemoryPublicAuditReader:
    def __init__(self, path, *, clock=time.time, fault=None):
        self.path = Path(path)
        self.clock = clock
        self.fault = fault or (lambda _: None)

    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.executescript(_SCHEMA)
        return db

    def _verify_chain(self, db, row):
        pages = db.execute(
            "SELECT * FROM memory_audit_inputs WHERE read_ref=? ORDER BY page_index",
            (row["read_ref"],),
        )
        count, previous = 0, None
        principal_hash, coverage, expectations = None, None, None
        for page in pages:
            data = json.loads(page["page_json"])
            expected_hash = digest(
                {"domain": "memory.operation.audit.page.v1", "payload": data}
            )
            if (
                page["page_index"] != count
                or page["snapshot_hash"] != row["snapshot_hash"]
                or data["snapshot_hash"] != row["snapshot_hash"]
                or digest(data) != page["host_hash"]
                or expected_hash != page["page_hash"]
                or data["all_operations_recorded"] is not False
                or (count and previous is None)
            ):
                raise ValueError("memory_audit_saved_chain_invalid")
            if count == 0:
                principal_hash, coverage, expectations = (
                    data["principal_ref_hash"],
                    data["coverage"],
                    data["expectation_results"],
                )
            if (
                data["principal_ref_hash"] != principal_hash
                or data["coverage"] != coverage
                or data["expectation_results"] != expectations
            ):
                raise ValueError("memory_audit_saved_header_invalid")
            previous = data["next_cursor"]
            if data["enumeration_complete"] != (previous is None):
                raise ValueError("memory_audit_saved_cursor_invalid")
            count += 1
        if count != row["page_count"] or (
            count
            and (None if previous is None else previous["token"]) != row["next_cursor"]
        ):
            raise ValueError("memory_audit_saved_frontier_invalid")

    async def advance(
        self,
        manager,
        *,
        requester,
        target_principal,
        access_receipt,
        read_ref,
        limit=100,
        expected=(),
        max_pages=4,
    ):
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(max_pages) is not int
            or not 1 <= max_pages <= 16
        ):
            raise ValueError("memory_audit_bounds_invalid")
        if type(read_ref) is not str or not 1 <= len(read_ref) <= 256:
            raise ValueError("memory_audit_read_ref_invalid")
        read_ref = "memory-read:" + digest(["host.memory.audit.read.v1", read_ref])
        if access_receipt is None:
            return {"status": "authority_unavailable", "all_operations_recorded": False}
        if not callable(getattr(manager, "read_operation_audit", None)):
            return {
                "status": "capability_unavailable",
                "all_operations_recorded": False,
            }
        query = digest(
            [
                owner_ref(requester),
                owner_ref(target_principal),
                access_receipt.receipt_hash,
                limit,
                [v.to_json() for v in expected],
            ]
        )
        for _ in range(max_pages):
            attempt = uuid.uuid4().hex

            def claim(attempt=attempt):
                db = self._db()
                try:
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "INSERT OR IGNORE INTO memory_audit_reads(read_ref,query_hash,generation,status) VALUES(?,?,1,'pending')",
                            (read_ref, query),
                        )
                        row = dict(
                            db.execute(
                                "SELECT * FROM memory_audit_reads WHERE read_ref=?",
                                (read_ref,),
                            ).fetchone()
                        )
                        if row["query_hash"] != query:
                            raise ValueError("memory_audit_query_binding_invalid")
                        # Even a previously completed read must not endorse a
                        # subsequently damaged stored snapshot.
                        try:
                            self._verify_chain(db, row)
                        except (ValueError, KeyError, TypeError):
                            db.execute(
                                "UPDATE memory_audit_reads SET status='unavailable',code='saved_chain_invalid' WHERE read_ref=?",
                                (read_ref,),
                            )
                            row["status"] = "unavailable"
                            return row, None
                        if row["status"] in {"enumerated", "unavailable"}:
                            return row, None
                        if (
                            row["lease_until"] is not None
                            and row["lease_until"] > self.clock()
                        ):
                            return row, None
                        if row["lease_owner"] is not None:
                            db.execute(
                                "UPDATE memory_audit_read_attempts SET outcome='unknown',superseded_by=? WHERE attempt_ref=? AND settled_at IS NULL",
                                (attempt, row["lease_owner"]),
                            )
                            if not row["page_count"]:
                                row["generation"] += 1
                        elif row["status"] == "unsaved_read_unknown":
                            # A read may have executed and consumed grant budget.
                            # Keep that unknown attempt; an explicit next advance
                            # may open a distinct generation only with ZERO pages.
                            if row["page_count"] or row["snapshot_hash"] is not None:
                                raise ValueError("memory_audit_unsaved_state_invalid")
                            db.execute(
                                "UPDATE memory_audit_read_attempts SET superseded_by=? WHERE read_ref=? AND generation=? AND outcome='unknown' AND superseded_by IS NULL",
                                (attempt, read_ref, row["generation"]),
                            )
                            row["generation"] += 1
                        db.execute(
                            "UPDATE memory_audit_reads SET generation=?,lease_owner=?,lease_until=?,status='reading' WHERE read_ref=?",
                            (row["generation"], attempt, self.clock() + 30, read_ref),
                        )
                        db.execute(
                            "INSERT INTO memory_audit_read_attempts(attempt_ref,read_ref,generation,page_index,started_at) VALUES(?,?,?,?,?)",
                            (
                                attempt,
                                read_ref,
                                row["generation"],
                                row["page_count"],
                                self.clock(),
                            ),
                        )
                        return row, attempt
                finally:
                    db.close()

            row, claimed = await asyncio.to_thread(claim)
            if claimed is None:
                return {"status": row["status"], "all_operations_recorded": False}
            try:
                import simple_harness_memory as m

                cursor = (
                    m.OperationAuditCursor(row["next_cursor"])
                    if row["next_cursor"] is not None
                    else None
                )
                page = await manager.read_operation_audit(
                    requester=requester,
                    target_principal=target_principal,
                    access_receipt=access_receipt,
                    limit=limit,
                    cursor=cursor,
                    expected=expected,
                )
                data, page_hash, access_hash = validated(page)
                if len(page.items) > limit:
                    raise ValueError("memory_audit_page_limit_invalid")
                if (
                    row["snapshot_hash"] is not None
                    and page.snapshot_hash != row["snapshot_hash"]
                ):
                    raise ValueError("memory_audit_snapshot_changed")
                self.fault("memory_reader.before_save")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never reopen a selected snapshot on failure
                failure_status = (
                    "unavailable" if row["page_count"] else "unsaved_read_unknown"
                )

                def fail(attempt=attempt, failure_status=failure_status):
                    db = self._db()
                    try:
                        with db:
                            db.execute(
                                "UPDATE memory_audit_reads SET status=?,code='snapshot_read_unknown',lease_owner=NULL,lease_until=NULL WHERE read_ref=? AND lease_owner=?",
                                (failure_status, read_ref, attempt),
                            )
                            db.execute(
                                "UPDATE memory_audit_read_attempts SET outcome='unknown',settled_at=? WHERE attempt_ref=?",
                                (self.clock(), attempt),
                            )
                    finally:
                        db.close()

                await asyncio.to_thread(fail)
                return {"status": failure_status, "all_operations_recorded": False}

            def save(
                attempt=attempt,
                row=row,
                page=page,
                data=data,
                page_hash=page_hash,
                access_hash=access_hash,
            ):
                db = self._db()
                try:
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        current = dict(
                            db.execute(
                                "SELECT * FROM memory_audit_reads WHERE read_ref=?",
                                (read_ref,),
                            ).fetchone()
                        )
                        if (
                            current["lease_owner"] != attempt
                            or current["lease_until"] <= self.clock()
                        ):
                            raise ValueError("memory_audit_reader_fenced")
                        self._verify_chain(db, current)
                        db.execute(
                            "INSERT INTO memory_audit_inputs VALUES(?,?,?,?,?,?)",
                            (
                                read_ref,
                                row["page_count"],
                                page.snapshot_hash,
                                canonical(data),
                                page_hash,
                                digest(data),
                            ),
                        )
                        next_cursor = (
                            page.next_cursor.token if page.next_cursor else None
                        )
                        db.execute(
                            "UPDATE memory_audit_reads SET snapshot_hash=?,next_cursor=?,page_count=page_count+1 WHERE read_ref=?",
                            (page.snapshot_hash, next_cursor, read_ref),
                        )
                        updated = dict(
                            db.execute(
                                "SELECT * FROM memory_audit_reads WHERE read_ref=?",
                                (read_ref,),
                            ).fetchone()
                        )
                        # Decisive nonlast-page corruption control: entire saved chain
                        # is checked in this transaction before any enumeration claim.
                        self._verify_chain(db, updated)
                        db.execute(
                            "UPDATE memory_audit_reads SET status=?,lease_owner=NULL,lease_until=NULL WHERE read_ref=?",
                            (
                                "enumerated" if next_cursor is None else "pending",
                                read_ref,
                            ),
                        )
                        db.execute(
                            "UPDATE memory_audit_read_attempts SET settled_at=?,outcome='saved',access_event_hash=? WHERE attempt_ref=?",
                            (self.clock(), access_hash, attempt),
                        )
                finally:
                    db.close()

            await asyncio.to_thread(save)
            if page.enumeration_complete:
                return {"status": "enumerated", "all_operations_recorded": False}
        return {"status": "pending", "all_operations_recorded": False}
