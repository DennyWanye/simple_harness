# SPDX-License-Identifier: BUSL-1.1

"""Durable S1 ExecutionEvidence ingress and terminal watermark seam."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from deskpet.task_scope.protocol import validate_execution_evidence, validate_refs
from deskpet.task_scope.store import (
    CanonicalTaskScopeStore,
    TaskScopeConflict,
    TaskScopeNotFound,
    _uuid,
)


class TerminalWatermarkPending(RuntimeError):
    code = "task_scope_terminal_watermark_pending"


@dataclass(frozen=True, slots=True)
class ExecutionIngestReceipt:
    receipt_id: str
    task_scope_id: str
    run_id: str
    source_sequence: int
    source_event_id: str
    evidence_hash: str
    event_id: str
    durable_source_sequence: int
    terminal_source_sequence: int | None


@dataclass(frozen=True, slots=True)
class TerminalGateReceipt:
    gate_receipt_id: str
    task_scope_id: str
    run_id: str
    terminal_source_sequence: int
    durable_source_sequence: int


class ExecutionEvidenceIngress:
    """Imports public S1 facts exactly once and advances contiguous watermarks."""

    def __init__(self, db_path: str | Path) -> None:
        self._store = CanonicalTaskScopeStore(db_path)

    async def ingest(
        self,
        *,
        task_scope_id: str,
        source_sequence: int,
        evidence: object,
    ) -> ExecutionIngestReceipt:
        if isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence < 1:
            raise ValueError("source_sequence_invalid")
        raw, evidence_hash = validate_execution_evidence(evidence)
        source_event_id = str(raw["event_id"])
        run_id = str(raw["run_id"])
        refs = validate_refs(raw["evidence_refs"])
        await self._store.initialize()
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._store._fetchone(
                    db,
                    "SELECT * FROM task_scope_execution_ingest_receipts WHERE source_event_id=?",
                    (source_event_id,),
                )
                if existing is not None:
                    if (
                        existing["evidence_hash"] != evidence_hash
                        or existing["task_scope_id"] != task_scope_id
                        or existing["run_id"] != run_id
                        or int(existing["source_sequence"]) != source_sequence
                    ):
                        raise TaskScopeConflict("execution_source_event_hash_conflict")
                    watermark = await self._watermark_tx(db, run_id)
                    await db.commit()
                    return self._receipt(existing, watermark)
                scope = await self._store._fetchone(
                    db, "SELECT subject FROM task_scopes WHERE task_scope_id=?", (task_scope_id,)
                )
                if scope is None:
                    raise TaskScopeNotFound(TaskScopeNotFound.code)
                if scope["subject"] != raw["subject"]:
                    raise ValueError("execution_subject_binding_mismatch")
                collision = await self._store._fetchone(
                    db,
                    "SELECT source_event_id,evidence_hash FROM task_scope_execution_ingest_receipts WHERE run_id=? AND source_sequence=?",
                    (run_id, source_sequence),
                )
                if collision is not None:
                    raise TaskScopeConflict("execution_source_sequence_conflict")
                existing_watermark = await self._store._fetchone(
                    db,
                    "SELECT terminal_source_sequence FROM task_scope_run_watermarks WHERE run_id=?",
                    (run_id,),
                )
                if (
                    existing_watermark is not None
                    and existing_watermark["terminal_source_sequence"] is not None
                    and source_sequence > int(existing_watermark["terminal_source_sequence"])
                ):
                    raise TaskScopeConflict("execution_after_terminal_rejected")
                if raw["kind"] == "run_terminal":
                    later = await self._store._fetchone(
                        db,
                        "SELECT 1 FROM task_scope_execution_ingest_receipts "
                        "WHERE run_id=? AND source_sequence>? LIMIT 1",
                        (run_id, source_sequence),
                    )
                    if later is not None:
                        raise TaskScopeConflict("execution_terminal_not_last")
                await self._store._verify_refs_tx(db, refs)
                event = await self._store._append_event_tx(
                    db,
                    task_scope_id=task_scope_id,
                    event_kind=f"harness.{raw['kind']}",
                    source_kind="harness",
                    source_event_id=f"execution:{source_event_id}",
                    payload=raw,
                    payload_hash=evidence_hash,
                    occurred_at=float(raw["occurred_at"]),
                    reason_code=None,
                )
                now = time.time()
                await self._store._link_refs_tx(db, task_scope_id, event.event_id, refs, now)
                receipt_id = _uuid(f"task-scope-execution-ingest:{source_event_id}")
                await db.execute(
                    "INSERT INTO task_scope_execution_ingest_receipts(receipt_id,task_scope_id,run_id,source_sequence,source_event_id,evidence_hash,event_id,evidence_kind,committed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (receipt_id, task_scope_id, run_id, source_sequence, source_event_id, evidence_hash, event.event_id, raw["kind"], now),
                )
                watermark = await self._advance_watermark_tx(
                    db,
                    task_scope_id=task_scope_id,
                    run_id=run_id,
                    terminal_sequence=source_sequence if raw["kind"] == "run_terminal" else None,
                    now=now,
                )
                await db.execute(
                    "UPDATE task_scope_heads SET event_watermark=?,updated_at=? "
                    "WHERE task_scope_id=? AND event_watermark<?",
                    (event.event_sequence, now, task_scope_id, event.event_sequence),
                )
                row = await self._store._fetchone(
                    db, "SELECT * FROM task_scope_execution_ingest_receipts WHERE receipt_id=?", (receipt_id,)
                )
                assert row is not None
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return self._receipt(row, watermark)

    async def authorize_terminal(self, run_id: str) -> TerminalGateReceipt:
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                watermark = await self._watermark_tx(db, run_id)
                terminal = watermark["terminal_source_sequence"]
                durable = int(watermark["durable_source_sequence"])
                if terminal is None or durable < int(terminal):
                    raise TerminalWatermarkPending(TerminalWatermarkPending.code)
                existing = await self._store._fetchone(
                    db, "SELECT * FROM task_scope_terminal_gate_receipts WHERE run_id=?", (run_id,)
                )
                if existing is None:
                    gate_id = _uuid(f"task-scope-terminal-gate:{run_id}")
                    await db.execute(
                        "INSERT INTO task_scope_terminal_gate_receipts(gate_receipt_id,task_scope_id,run_id,terminal_source_sequence,durable_source_sequence,created_at) VALUES (?,?,?,?,?,?)",
                        (gate_id, watermark["task_scope_id"], run_id, terminal, durable, time.time()),
                    )
                    existing = await self._store._fetchone(
                        db, "SELECT * FROM task_scope_terminal_gate_receipts WHERE run_id=?", (run_id,)
                    )
                assert existing is not None
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return TerminalGateReceipt(str(existing["gate_receipt_id"]), str(existing["task_scope_id"]), run_id, int(existing["terminal_source_sequence"]), int(existing["durable_source_sequence"]))

    async def _advance_watermark_tx(self, db, *, task_scope_id: str, run_id: str, terminal_sequence: int | None, now: float):
        row = await self._store._fetchone(db, "SELECT * FROM task_scope_run_watermarks WHERE run_id=?", (run_id,))
        if row is None:
            await db.execute(
                "INSERT INTO task_scope_run_watermarks(run_id,task_scope_id,durable_source_sequence,terminal_source_sequence,updated_at) VALUES (?,?,0,?,?)",
                (run_id, task_scope_id, terminal_sequence, now),
            )
            durable = 0
            current_terminal = terminal_sequence
        else:
            if row["task_scope_id"] != task_scope_id:
                raise TaskScopeConflict("execution_run_scope_conflict")
            durable = int(row["durable_source_sequence"])
            current_terminal = row["terminal_source_sequence"]
            if terminal_sequence is not None and current_terminal not in (None, terminal_sequence):
                raise TaskScopeConflict("execution_terminal_sequence_conflict")
            current_terminal = terminal_sequence if terminal_sequence is not None else current_terminal
        while True:
            next_row = await self._store._fetchone(
                db,
                "SELECT 1 FROM task_scope_execution_ingest_receipts WHERE run_id=? AND source_sequence=?",
                (run_id, durable + 1),
            )
            if next_row is None:
                break
            durable += 1
        await db.execute(
            "UPDATE task_scope_run_watermarks SET durable_source_sequence=?,terminal_source_sequence=?,updated_at=? WHERE run_id=?",
            (durable, current_terminal, now, run_id),
        )
        return await self._watermark_tx(db, run_id)

    async def _watermark_tx(self, db, run_id: str):
        row = await self._store._fetchone(db, "SELECT * FROM task_scope_run_watermarks WHERE run_id=?", (run_id,))
        if row is None:
            raise TerminalWatermarkPending(TerminalWatermarkPending.code)
        return row

    @staticmethod
    def _receipt(row, watermark) -> ExecutionIngestReceipt:
        terminal = watermark["terminal_source_sequence"]
        return ExecutionIngestReceipt(
            str(row["receipt_id"]), str(row["task_scope_id"]), str(row["run_id"]),
            int(row["source_sequence"]), str(row["source_event_id"]), str(row["evidence_hash"]),
            str(row["event_id"]), int(watermark["durable_source_sequence"]),
            None if terminal is None else int(terminal),
        )
