"""Durable reader attempts, immutable page inputs and replay-idempotent findings."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RULE_VERSION = "terminal-run-v1"


def canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class AuditStoreError(RuntimeError):
    """Safe closed failures, no SQL/credentials in exported status."""


@dataclass(frozen=True)
class TerminalSource:
    host_run_id: str
    sdk_run_id: str
    owner_ref: str
    terminal_ref: str
    terminal_hash: str
    terminal_state: str
    generation: int
    admission_hash: str

    @property
    def job_id(self) -> str:
        return digest(
            [
                "host.terminal.audit.v1",
                self.terminal_ref,
                self.terminal_hash,
                RULE_VERSION,
            ]
        )


@dataclass(frozen=True)
class ReadClaim:
    job: dict[str, Any]
    attempt_id: str
    worker: str
    generation: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_jobs (
 job_id TEXT PRIMARY KEY, host_run_id TEXT NOT NULL, sdk_run_id TEXT NOT NULL,
 owner_ref TEXT NOT NULL, terminal_ref TEXT NOT NULL, terminal_hash TEXT NOT NULL,
 terminal_state TEXT NOT NULL, source_generation INTEGER NOT NULL, admission_hash TEXT NOT NULL,
 rule_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 snapshot_hash TEXT, next_cursor TEXT, next_page INTEGER NOT NULL DEFAULT 0,
 total_pages INTEGER, total_operations INTEGER, processed_operations INTEGER NOT NULL DEFAULT 0,
 header_json TEXT, lease_owner TEXT, lease_until REAL, generation INTEGER NOT NULL DEFAULT 0,
 snapshot_generation INTEGER NOT NULL DEFAULT 1, last_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(terminal_ref,terminal_hash,rule_version)
);
CREATE TABLE IF NOT EXISTS audit_attempts (
 attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES audit_jobs(job_id),
 ordinal INTEGER NOT NULL, kind TEXT NOT NULL, request_ref_hash TEXT NOT NULL,
 started_at REAL NOT NULL, settled_at REAL, outcome TEXT, code TEXT, superseded_by TEXT, snapshot_generation INTEGER NOT NULL,
 UNIQUE(job_id,ordinal)
);
CREATE TABLE IF NOT EXISTS audit_pages (
 job_id TEXT NOT NULL REFERENCES audit_jobs(job_id), page_index INTEGER NOT NULL,
 snapshot_hash TEXT NOT NULL, page_hash TEXT NOT NULL, input_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(job_id,page_index)
);
CREATE TABLE IF NOT EXISTS audit_findings (
 finding_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES audit_jobs(job_id),
 operation_id TEXT NOT NULL, source_hash TEXT NOT NULL, rule_id TEXT NOT NULL,
 rule_version TEXT NOT NULL, owner_component TEXT NOT NULL, owner_ref TEXT NOT NULL,
 payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_finding_sources (
 finding_id TEXT NOT NULL REFERENCES audit_findings(finding_id),
 job_id TEXT NOT NULL REFERENCES audit_jobs(job_id),
 source_operation_id TEXT NOT NULL, source_hash TEXT NOT NULL, page_index INTEGER NOT NULL,
 PRIMARY KEY(finding_id,source_operation_id,source_hash)
);
CREATE TABLE IF NOT EXISTS audit_source_rejections (
 source_key TEXT PRIMARY KEY, owner_ref TEXT NOT NULL, code TEXT NOT NULL,
 observed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_pending ON audit_jobs(status,lease_until,created_at);
"""


class AuditStore:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.clock = clock
        self.fault = fault or (lambda _: None)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    async def initialize(self) -> None:
        def initialize() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(_SCHEMA)

        await asyncio.to_thread(initialize)

    async def admit(self, source: TerminalSource) -> str:
        def admit() -> str:
            now = self.clock()
            with self.connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO audit_jobs(job_id,host_run_id,sdk_run_id,owner_ref,"
                    "terminal_ref,terminal_hash,terminal_state,source_generation,admission_hash,"
                    "rule_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        source.job_id,
                        source.host_run_id,
                        source.sdk_run_id,
                        source.owner_ref,
                        source.terminal_ref,
                        source.terminal_hash,
                        source.terminal_state,
                        source.generation,
                        source.admission_hash,
                        RULE_VERSION,
                        now,
                        now,
                    ),
                )
                row = db.execute(
                    "SELECT * FROM audit_jobs WHERE job_id=?", (source.job_id,)
                ).fetchone()
                expected = (
                    source.host_run_id,
                    source.sdk_run_id,
                    source.owner_ref,
                    source.admission_hash,
                    source.terminal_state,
                    source.generation,
                )
                actual = tuple(
                    row[k]
                    for k in (
                        "host_run_id",
                        "sdk_run_id",
                        "owner_ref",
                        "admission_hash",
                        "terminal_state",
                        "source_generation",
                    )
                )
                if actual != expected:
                    raise AuditStoreError("source_binding_conflict")
            return source.job_id

        return await asyncio.to_thread(admit)

    async def reject_sources(self, keys: tuple[str, ...], *, owner_ref: str) -> None:
        def record() -> None:
            with self.connect() as db:
                db.executemany(
                    "INSERT OR IGNORE INTO audit_source_rejections VALUES (?,?,?,?)",
                    [
                        (key, owner_ref, "source_binding_invalid", self.clock())
                        for key in keys
                    ],
                )

        if keys:
            await asyncio.to_thread(record)

    async def claim(
        self, worker: str, *, lease_seconds: float = 60
    ) -> ReadClaim | None:
        def claim() -> ReadClaim | None:
            now = self.clock()
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM audit_jobs WHERE rule_version=? AND "
                    "(status='pending' OR (status='reading' AND lease_until<=?)) "
                    "ORDER BY updated_at,job_id LIMIT 1",
                    (RULE_VERSION, now),
                ).fetchone()
                if row is None:
                    return None
                job = dict(row)
                if not self._resume_valid(db, job):
                    db.execute(
                        "UPDATE audit_jobs SET status='unavailable',last_code='journal_cursor_invalid',"
                        "lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=?",
                        (now, job["job_id"]),
                    )
                    db.execute(
                        "UPDATE audit_attempts SET settled_at=?,outcome='unknown',code='journal_cursor_invalid' "
                        "WHERE job_id=? AND settled_at IS NULL",
                        (now, job["job_id"]),
                    )
                    return None
                unresolved = db.execute(
                    "SELECT attempt_id FROM audit_attempts WHERE job_id=? "
                    "AND settled_at IS NULL",
                    (job["job_id"],),
                ).fetchall()
                if unresolved:
                    db.execute(
                        "UPDATE audit_attempts SET settled_at=?,outcome='unknown',code='lease_expired' "
                        "WHERE job_id=? AND settled_at IS NULL",
                        (now, job["job_id"]),
                    )
                    if job["snapshot_hash"] is None:
                        # Read-only open can be repeated in an explicit new generation
                        # before any snapshot/page was selected. Preserve its unknown.
                        job["snapshot_generation"] += 1
                        db.execute(
                            "UPDATE audit_jobs SET snapshot_generation=? WHERE job_id=?",
                            (job["snapshot_generation"], job["job_id"]),
                        )
                        db.execute(
                            "UPDATE audit_attempts SET code='abandoned_unsaved_read' "
                            "WHERE job_id=? AND settled_at=? AND code='lease_expired'",
                            (job["job_id"], now),
                        )
                generation = job["generation"] + 1
                db.execute(
                    "UPDATE audit_jobs SET status='reading',generation=?,lease_owner=?,"
                    "lease_until=?,updated_at=? WHERE job_id=?",
                    (generation, worker, now + lease_seconds, now, job["job_id"]),
                )
                attempt = uuid.uuid4().hex
                ordinal = db.execute(
                    "SELECT COALESCE(MAX(ordinal),0)+1 FROM audit_attempts WHERE job_id=?",
                    (job["job_id"],),
                ).fetchone()[0]
                kind = "open" if job["snapshot_hash"] is None else "page"
                db.execute(
                    "INSERT INTO audit_attempts(attempt_id,job_id,ordinal,kind,request_ref_hash,"
                    "started_at,snapshot_generation) VALUES (?,?,?,?,?,?,?)",
                    (
                        attempt,
                        job["job_id"],
                        ordinal,
                        kind,
                        digest(
                            [
                                job["sdk_run_id"],
                                job["snapshot_hash"],
                                job["next_cursor"],
                            ]
                        ),
                        now,
                        job["snapshot_generation"],
                    ),
                )
                for old in unresolved:
                    db.execute(
                        "UPDATE audit_attempts SET superseded_by=? WHERE attempt_id=?",
                        (attempt, old["attempt_id"]),
                    )
                return ReadClaim(job, attempt, worker, generation)

        result = await asyncio.to_thread(claim)
        if result is not None:
            self.fault("audit.after_started_commit")
        return result

    @staticmethod
    def _resume_valid(db: sqlite3.Connection, job: dict[str, Any]) -> bool:
        count = db.execute(
            "SELECT count(*) FROM audit_pages WHERE job_id=?", (job["job_id"],)
        ).fetchone()[0]
        if job["snapshot_hash"] is None:
            return (
                count == job["next_page"] == 0
                and job["header_json"] is None
                and job["next_cursor"] is None
            )
        if count != job["next_page"] or count < 1:
            return False
        last = db.execute(
            "SELECT * FROM audit_pages WHERE job_id=? AND page_index=?",
            (job["job_id"], count - 1),
        ).fetchone()
        if last is None:
            return False
        try:
            page = json.loads(last["payload_json"])
            header = {
                key: page[key]
                for key in (
                    "run_id",
                    "snapshot_hash",
                    "page_size",
                    "total_operations",
                    "total_pages",
                    "metadata",
                )
            }
            return (
                digest(page) == last["input_hash"]
                and page["snapshot_hash"]
                == last["snapshot_hash"]
                == job["snapshot_hash"]
                and page["page_hash"] == last["page_hash"]
                and page["next_cursor"] == job["next_cursor"]
                and canonical(header) == job["header_json"]
                and page["page_index"] == count - 1
            )
        except (ValueError, TypeError, KeyError):
            return False

    def _fence(self, db: sqlite3.Connection, claim: ReadClaim) -> None:
        row = db.execute(
            "SELECT status,lease_owner,generation,lease_until FROM audit_jobs WHERE job_id=?",
            (claim.job["job_id"],),
        ).fetchone()
        if (
            row is None
            or (row["status"], row["lease_owner"], row["generation"])
            != ("reading", claim.worker, claim.generation)
            or row["lease_until"] <= self.clock()
        ):
            raise AuditStoreError("audit_lease_lost")

    async def unavailable(self, claim: ReadClaim, code: str) -> None:
        # Codes are selected by the consumer, never exception text.
        if code not in {
            "capability_unavailable",
            "snapshot_unavailable",
            "reader_failed",
            "page_invalid",
            "source_binding_invalid",
        }:
            raise ValueError("invalid audit degradation code")

        def settle() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._fence(db, claim)
                now = self.clock()
                db.execute(
                    "UPDATE audit_attempts SET settled_at=?,outcome='unavailable',code=? "
                    "WHERE attempt_id=?",
                    (now, code, claim.attempt_id),
                )
                db.execute(
                    "UPDATE audit_jobs SET status='unavailable',last_code=?,lease_owner=NULL,"
                    "lease_until=NULL,updated_at=? WHERE job_id=?",
                    (code, now, claim.job["job_id"]),
                )

        await asyncio.to_thread(settle)

    async def commit_page(
        self, claim: ReadClaim, page: dict[str, Any], findings: list[dict[str, Any]]
    ) -> None:
        def commit() -> None:
            payload = canonical(page)
            header = canonical(
                {
                    k: page[k]
                    for k in (
                        "run_id",
                        "snapshot_hash",
                        "page_size",
                        "total_operations",
                        "total_pages",
                        "metadata",
                    )
                }
            )
            now = self.clock()
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._fence(db, claim)
                current = db.execute(
                    "SELECT * FROM audit_jobs WHERE job_id=?", (claim.job["job_id"],)
                ).fetchone()
                if page["page_index"] != current["next_page"] or (
                    current["header_json"] is not None
                    and current["header_json"] != header
                ):
                    raise AuditStoreError("audit_page_binding_conflict")
                db.execute(
                    "INSERT INTO audit_pages VALUES (?,?,?,?,?,?)",
                    (
                        claim.job["job_id"],
                        page["page_index"],
                        page["snapshot_hash"],
                        page["page_hash"],
                        digest(page),
                        payload,
                    ),
                )
                for finding in findings:
                    finding_id = digest(
                        [
                            claim.job["job_id"],
                            RULE_VERSION,
                            finding["rule_id"],
                            finding["operation_id"],
                            claim.job["owner_ref"],
                        ]
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO audit_findings VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            finding_id,
                            claim.job["job_id"],
                            finding["operation_id"],
                            finding["source_hash"],
                            finding["rule_id"],
                            RULE_VERSION,
                            finding["owner_component"],
                            claim.job["owner_ref"],
                            canonical(finding),
                        ),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO audit_finding_sources VALUES (?,?,?,?,?)",
                        (
                            finding_id,
                            claim.job["job_id"],
                            finding["source_operation_id"],
                            finding["source_hash"],
                            page["page_index"],
                        ),
                    )
                status = "enumerated" if page["next_cursor"] is None else "pending"
                db.execute(
                    "UPDATE audit_jobs SET status=?,snapshot_hash=?,next_cursor=?,next_page=?,"
                    "total_pages=?,total_operations=?,processed_operations=processed_operations+?,"
                    "header_json=?,lease_owner=NULL,lease_until=NULL,last_code=NULL,updated_at=? WHERE job_id=?",
                    (
                        status,
                        page["snapshot_hash"],
                        page["next_cursor"],
                        page["page_index"] + 1,
                        page["total_pages"],
                        page["total_operations"],
                        len(page["operations"]),
                        header,
                        now,
                        claim.job["job_id"],
                    ),
                )
                db.execute(
                    "UPDATE audit_attempts SET settled_at=?,outcome='returned' WHERE attempt_id=?",
                    (now, claim.attempt_id),
                )
                self.fault("audit.page.before_commit")
            self.fault("audit.page.after_commit")

        await asyncio.to_thread(commit)

    async def inspect(self, job_id: str) -> dict[str, Any]:
        """Trusted in-process read; not a remotely authorized audit endpoint."""

        def read() -> dict[str, Any]:
            with self.connect() as db:
                row = db.execute(
                    "SELECT * FROM audit_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                return {
                    "job": None if row is None else dict(row),
                    **{
                        name: [
                            dict(v)
                            for v in db.execute(
                                f"SELECT * FROM audit_{name} WHERE job_id=?", (job_id,)
                            )
                        ]
                        for name in ("attempts", "pages", "findings", "finding_sources")
                    },
                }

        return await asyncio.to_thread(read)

    async def coverage(self) -> dict[str, Any]:
        """Trusted local diagnostic; job counts are never physical call counts."""

        def read() -> dict[str, Any]:
            with self.connect() as db:
                return {
                    "producer_scope": "foreground_terminal_only",
                    "jobs_by_status": dict(
                        db.execute(
                            "SELECT status,count(*) FROM audit_jobs GROUP BY status"
                        ).fetchall()
                    ),
                    "source_rejections": db.execute(
                        "SELECT count(*) FROM audit_source_rejections"
                    ).fetchone()[0],
                    "operation_count_unit": "public_projection_dto",
                    "usage_and_cost_aggregation": "not_produced",
                    "historical_coverage": "see_each_snapshot_metadata",
                }

        return await asyncio.to_thread(read)
