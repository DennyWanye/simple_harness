"""Durable, payload-free receipts for Context page-in reference issue/consume (G1).

Lives in operation-audit.db beside the terminal-run pages. A receipt records
which reference (hashed) was issued for which request scope, and which SDK
effect consumed or was denied it. Reference content, sources and raw ids never
appear: only hashes, closed outcome codes, the SDK run/effect identities used by
the coverage checker join, and timestamps. Recording failure never changes the
tool result.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from deskpet.operation_audit.store import digest

log = logging.getLogger(__name__)
PHASES = frozenset({"issued", "consumed", "denied"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_page_in_receipts (
 receipt_id TEXT PRIMARY KEY, phase TEXT NOT NULL, reference_ref_hash TEXT NOT NULL,
 kind TEXT NOT NULL, source_ref_hash TEXT, source_hash TEXT,
 session_ref_hash TEXT, request_ref_hash TEXT, scope_ref_hash TEXT,
 sdk_run_id TEXT, effect_id TEXT, outcome TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS context_page_in_receipts_effect ON context_page_in_receipts(effect_id);
CREATE INDEX IF NOT EXISTS context_page_in_receipts_reference ON context_page_in_receipts(reference_ref_hash,phase);
"""


def reference_ref_hash(reference_id: str) -> str:
    return digest(["host.page_in.reference.v1", str(reference_id)])


def _scope_hash(name: str, value: object) -> str | None:
    return digest([f"host.page_in.{name}.v1", str(value)]) if value not in (None, "") else None


class ContextPageInReceiptLedger:
    def __init__(self, path: Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.last_code: str | None = None
        self._ready = False

    def _write(self, row: tuple) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.5)
        try:
            if not self._ready:
                # The sidecar file already exists (AuditStore owns it); creating
                # the table once keeps the per-issue write off the parse path.
                db.executescript(_SCHEMA)
            with db:
                db.execute(
                    "INSERT INTO context_page_in_receipts(receipt_id,phase,reference_ref_hash,kind,"
                    "source_ref_hash,source_hash,session_ref_hash,request_ref_hash,scope_ref_hash,"
                    "sdk_run_id,effect_id,outcome,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
            self._ready = True
        finally:
            db.close()

    def _row(self, *, phase, reference_id, kind, source, source_hash, session_id, request_id,
             scope_id, sdk_run_id, effect_id, outcome) -> tuple:
        if phase not in PHASES:
            raise ValueError("page_in_receipt_phase_invalid")
        return (
            uuid.uuid4().hex, phase, reference_ref_hash(reference_id), str(kind),
            _scope_hash("source", source), source_hash or None,
            _scope_hash("session", session_id), _scope_hash("request", request_id), _scope_hash("scope", scope_id),
            str(sdk_run_id) if sdk_run_id else None, str(effect_id) if effect_id else None,
            str(outcome), float(self.clock()),
        )

    def record_sync(self, **fields) -> None:
        """Issue-time receipt from the synchronous ``put`` path; never raises."""
        try:
            self._write(self._row(**fields))
        except Exception:  # noqa: BLE001 - audit sidecar must not block context preparation
            self.last_code = "page_in_receipt_unavailable"
            log.warning(self.last_code)

    async def record(self, **fields) -> None:
        """Consume-time receipt from the tool handler; never raises."""
        try:
            row = self._row(**fields)
            await asyncio.wait_for(asyncio.to_thread(self._write, row), timeout=0.5)
        except Exception:  # noqa: BLE001 - a receipt failure cannot alter the tool result
            self.last_code = "page_in_receipt_unavailable"
            log.warning(self.last_code)


__all__ = ["ContextPageInReceiptLedger", "PHASES", "reference_ref_hash"]
