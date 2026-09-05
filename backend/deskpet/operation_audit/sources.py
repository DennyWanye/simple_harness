"""Read only Host-owned terminal/admission facts, never SDK private tables."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from deskpet.operation_audit.store import (
    RULE_VERSION,
    AuditStore,
    AuditStoreError,
    TerminalSource,
    digest,
)

# All selected authority fields contribute; changed/corrected input can be retried.
_SOURCE_FIELDS = (
    "terminal_receipt_id",
    "host_run_id",
    "sdk_run_id",
    "terminal_state",
    "generation",
    "sdk_event_id",
    "sdk_event_hash",
    "receipt_hash",
    "receipt_json",
    "subject",
    "admission_json",
    "admission_receipt_hash",
    "admission_receipt_id",
    "head_sdk_run",
    "head_subject",
    "head_generation",
    "head_state",
)


class TerminalSources:
    def __init__(self, state_path: Path, audit_path: Path, *, subject: str) -> None:
        self.state_path, self.audit_path, self.subject = (
            Path(state_path),
            Path(audit_path),
            subject,
        )

    @staticmethod
    def _source(row: sqlite3.Row) -> TerminalSource:
        terminal, admission = (
            json.loads(row["receipt_json"]),
            json.loads(row["admission_json"]),
        )
        if not isinstance(terminal, dict) or not isinstance(admission, dict):
            raise AuditStoreError("terminal_source_shape_invalid")
        if (
            digest(terminal) != row["receipt_hash"]
            or digest(admission) != row["admission_receipt_hash"]
        ):
            raise AuditStoreError("terminal_source_hash_invalid")
        pairs = {
            "terminal_receipt_id": "terminal_receipt_id",
            "host_run_id": "host_run_id",
            "sdk_run_id": "sdk_run_id",
            "terminal_state": "terminal_state",
            "generation": "generation",
            "sdk_event_id": "sdk_event_id",
            "sdk_event_hash": "sdk_event_hash",
        }
        if any(terminal.get(key) != row[column] for key, column in pairs.items()):
            raise AuditStoreError("terminal_source_binding_invalid")
        if (
            admission.get("host_run_id") != row["host_run_id"]
            or admission.get("subject") != row["subject"]
            or admission.get("admission_receipt_id") != row["admission_receipt_id"]
            or row["head_sdk_run"] != row["sdk_run_id"]
            or row["head_subject"] != row["subject"]
            or row["head_generation"] != row["generation"]
            or row["head_state"] != row["terminal_state"]
        ):
            raise AuditStoreError("terminal_source_owner_invalid")
        return TerminalSource(
            row["host_run_id"],
            row["sdk_run_id"],
            digest(["host.audit.owner.v1", row["subject"]]),
            row["terminal_receipt_id"],
            row["receipt_hash"],
            row["terminal_state"],
            row["generation"],
            row["admission_receipt_hash"],
        )

    async def read(
        self, *, limit: int = 32, host_run_id: str | None = None
    ) -> tuple[TerminalSource, ...]:
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("audit source limit invalid")

        def read() -> tuple[tuple[TerminalSource, ...], tuple[str, ...]]:
            db = sqlite3.connect(
                self.state_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2
            )
            db.row_factory = sqlite3.Row
            try:
                db.execute(
                    "ATTACH DATABASE ? AS audit",
                    (self.audit_path.resolve().as_uri() + "?mode=ro",),
                )
                db.create_function(
                    "host_source_key",
                    len(_SOURCE_FIELDS),
                    lambda *values: digest(values),
                    deterministic=True,
                )
                key_sql = "host_source_key(" + ",".join(_SOURCE_FIELDS) + ")"
                selector = (
                    "AND host_run_id=?"
                    if host_run_id is not None
                    else (
                        "AND NOT EXISTS (SELECT 1 FROM audit.audit_jobs a WHERE "
                        "a.terminal_ref=selected.terminal_receipt_id AND a.terminal_hash=selected.receipt_hash AND a.rule_version=?) "
                        "AND NOT EXISTS (SELECT 1 FROM audit.audit_source_rejections x WHERE "
                        "x.source_key=" + key_sql + ")"
                    )
                )
                rows = db.execute(
                    "WITH selected AS (SELECT t.*,r.subject,r.admission_json,r.admission_receipt_hash,"
                    "r.admission_receipt_id,h.sdk_run_id AS head_sdk_run,h.subject AS head_subject,"
                    "h.generation AS head_generation,h.current_state AS head_state "
                    "FROM foreground_terminal_receipts t JOIN foreground_runs r ON r.host_run_id=t.host_run_id "
                    "LEFT JOIN foreground_run_heads h ON h.host_run_id=t.host_run_id WHERE r.subject=?) "
                    "SELECT selected.*,"
                    + key_sql
                    + " AS source_key FROM selected WHERE 1=1 "
                    + selector
                    + " ORDER BY terminal_receipt_id LIMIT ?",
                    (
                        self.subject,
                        host_run_id if host_run_id is not None else RULE_VERSION,
                        limit,
                    ),
                ).fetchall()
                valid, rejected = [], []
                for row in rows:
                    try:
                        valid.append(self._source(row))
                    except (AuditStoreError, ValueError, TypeError):
                        if host_run_id is not None:
                            raise AuditStoreError("terminal_source_invalid") from None
                        rejected.append(row["source_key"])
                return tuple(valid), tuple(rejected)
            finally:
                db.close()

        valid, rejected = await asyncio.to_thread(read)
        await AuditStore(self.audit_path).reject_sources(
            rejected, owner_ref=digest(["host.audit.owner.v1", self.subject])
        )
        return valid

    async def verify(self, job: dict) -> bool:
        rows = await self.read(limit=1, host_run_id=job["host_run_id"])
        return bool(
            rows
            and rows[0].job_id == job["job_id"]
            and rows[0].sdk_run_id == job["sdk_run_id"]
            and rows[0].owner_ref == job["owner_ref"]
            and rows[0].admission_hash == job["admission_hash"]
        )
