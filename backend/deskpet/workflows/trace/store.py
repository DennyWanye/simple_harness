from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from ..store.schema import initialize_workflow_db
from .context import SpanContext, current_span
from .models import SpanStatus, TraceRun, TraceSpan
from deskpet.security.redaction import TraceRedactor


class TraceStore:
    def __init__(self, path: str | Path, *, redactor: TraceRedactor | None = None, clock=time.time) -> None:
        self.path = Path(path)
        self.redactor = redactor or TraceRedactor()
        self._clock = clock

    async def _connect(self) -> aiosqlite.Connection:
        await initialize_workflow_db(self.path)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA synchronous=FULL")
        return db

    async def start_run(
        self,
        *,
        session_id: str,
        kind: str,
        trace_id: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        turn_id: str | None = None,
        workflow_name: str | None = None,
        workflow_version: str | None = None,
    ) -> TraceRun:
        trace = TraceRun(
            trace_id=trace_id or uuid.uuid4().hex,
            run_id=run_id,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            kind=kind,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            status=SpanStatus.RUNNING,
            started_at=self._clock(),
        )
        db = await self._connect()
        try:
            await db.execute(
                """INSERT OR IGNORE INTO trace_runs(trace_id,run_id,session_id,request_id,turn_id,kind,
                workflow_name,workflow_version,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    trace.trace_id, trace.run_id, trace.session_id, trace.request_id, trace.turn_id,
                    trace.kind, trace.workflow_name, trace.workflow_version, trace.status, trace.started_at,
                ),
            )
            await db.commit()
            return trace
        finally:
            await db.close()

    async def finish_run(self, trace_id: str, status: str, *, error: Any = None) -> None:
        ended = self._clock()
        db = await self._connect()
        try:
            row = await (await db.execute("SELECT started_at FROM trace_runs WHERE trace_id=?", (trace_id,))).fetchone()
            if row is None:
                raise KeyError(trace_id)
            await db.execute(
                "UPDATE trace_runs SET status=?,ended_at=?,duration_ms=?,error_json=? WHERE trace_id=?",
                (
                    status,
                    ended,
                    max(0.0, (ended - float(row["started_at"])) * 1000.0),
                    json.dumps(self.redactor.redact(error), ensure_ascii=False) if error is not None else None,
                    trace_id,
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def start_span(
        self,
        *,
        trace_id: str,
        name: str,
        kind: str,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        run_id: str | None = None,
        workflow_name: str | None = None,
        workflow_version: str | None = None,
        node_id: str | None = None,
        lifecycle_stage: str | None = None,
        attributes: dict[str, Any] | None = None,
        input_ref: str | None = None,
        privacy_class: str = "internal",
    ) -> TraceSpan:
        inherited = current_span()
        if inherited and inherited.trace_id == trace_id:
            parent_span_id = parent_span_id or inherited.span_id
            run_id = run_id or inherited.run_id
        span = TraceSpan(
            span_id=span_id or uuid.uuid4().hex,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            run_id=run_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            lifecycle_stage=lifecycle_stage,
            kind=kind,
            status=SpanStatus.RUNNING,
            name=name,
            attributes=self.redactor.redact(attributes or {}),
            input_ref=input_ref,
            privacy_class=privacy_class,
            started_at=self._clock(),
        )
        db = await self._connect()
        try:
            await db.execute(
                """INSERT OR IGNORE INTO trace_spans(span_id,trace_id,parent_span_id,run_id,workflow_name,
                workflow_version,node_id,lifecycle_stage,kind,status,name,attributes_json,input_ref,
                privacy_class,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    span.span_id, span.trace_id, span.parent_span_id, span.run_id, span.workflow_name,
                    span.workflow_version, span.node_id, span.lifecycle_stage, span.kind, span.status,
                    span.name, json.dumps(span.attributes, ensure_ascii=False, sort_keys=True),
                    span.input_ref, span.privacy_class, span.started_at,
                ),
            )
            await db.commit()
            return span
        finally:
            await db.close()

    async def finish_span(
        self,
        span_id: str,
        status: str,
        *,
        output_ref: str | None = None,
        error: Any = None,
        attributes: dict[str, Any] | None = None,
    ) -> bool:
        ended = self._clock()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute(
                "SELECT started_at,attributes_json,status FROM trace_spans WHERE span_id=?",
                (span_id,),
            )).fetchone()
            if row is None:
                raise KeyError(span_id)
            if str(row["status"]) != SpanStatus.RUNNING:
                await db.rollback()
                return False
            merged_attributes = json.loads(str(row["attributes_json"]) or "{}")
            if attributes:
                merged_attributes.update(self.redactor.redact(attributes))
            cursor = await db.execute(
                """UPDATE trace_spans SET status=?,ended_at=?,duration_ms=?,output_ref=?,error_json=?,attributes_json=?
                WHERE span_id=? AND status=?""",
                (
                    status, ended, max(0.0, (ended - float(row["started_at"])) * 1000.0), output_ref,
                    json.dumps(self.redactor.redact(error), ensure_ascii=False) if error is not None else None,
                    json.dumps(merged_attributes, ensure_ascii=False, sort_keys=True),
                    span_id, SpanStatus.RUNNING,
                ),
            )
            updated = int(cursor.rowcount or 0) > 0
            await cursor.close()
            await db.commit()
            return updated
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def reconcile_native_node_spans(self, run_id: str) -> int:
        """Close node spans whose durable task result survived a crash."""

        ended = self._clock()
        db = await self._connect()
        try:
            cursor = await db.execute(
                """UPDATE trace_spans SET status=?,ended_at=?,
                duration_ms=MAX(0,(?-started_at)*1000)
                WHERE run_id=? AND kind='node' AND status=?
                  AND EXISTS (
                    SELECT 1 FROM workflow_nodes node
                    WHERE node.run_id=trace_spans.run_id
                      AND node.node_id=trace_spans.node_id
                      AND node.task_id=json_extract(trace_spans.attributes_json,'$.task_id')
                      AND node.latest_status IN ('succeeded_pending','succeeded')
                  )""",
                (SpanStatus.OK, ended, ended, run_id, SpanStatus.RUNNING),
            )
            await db.commit()
            return int(cursor.rowcount)
        finally:
            await db.close()

    async def tree(self, trace_id: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            run = await (await db.execute("SELECT * FROM trace_runs WHERE trace_id=?", (trace_id,))).fetchone()
            if run is None:
                return None
            spans = await (
                await db.execute("SELECT * FROM trace_spans WHERE trace_id=? ORDER BY started_at,span_id", (trace_id,))
            ).fetchall()
            return {"run": dict(run), "spans": [dict(span) for span in spans]}
        finally:
            await db.close()

    async def span(self, span_id: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute("SELECT * FROM trace_spans WHERE span_id=?", (span_id,))
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            await db.close()

    @staticmethod
    def context(span: TraceSpan) -> SpanContext:
        return SpanContext(span.trace_id, span.span_id, span.run_id)
