"""Durable node projection and trace instrumentation for workflow runs."""

from __future__ import annotations

import hashlib
from typing import Any

from ..contracts import NodeExecutionIdentity
from ..store.run_store import WorkflowRunStore
from .models import SpanKind, SpanStatus
from .store import TraceStore


class WorkflowExecutionObserver:
    def __init__(self, store: WorkflowRunStore, traces: TraceStore, *, trace_id: str) -> None:
        self.store = store
        self.traces = traces
        self.trace_id = trace_id
        self._executions: dict[tuple[str, int], str] = {}

    @staticmethod
    def _span_id(execution_id: str, attempt: int) -> str:
        return hashlib.sha256(f"{execution_id}:{attempt}".encode("utf-8")).hexdigest()

    async def node_started(self, identity: NodeExecutionIdentity) -> str:
        execution_id = await self.store.record_node_start(
            run_id=identity.run_id,
            node_id=identity.node_id,
            checkpoint_id=identity.checkpoint_id,
            task_id=identity.task_id,
            attempt=identity.attempt,
        )
        self._executions[(identity.task_id, identity.attempt)] = execution_id
        span = await self.traces.start_span(
            trace_id=self.trace_id,
            span_id=self._span_id(execution_id, identity.attempt),
            run_id=identity.run_id,
            workflow_name=identity.workflow_name,
            workflow_version=identity.workflow_version,
            node_id=identity.node_id,
            lifecycle_stage="node",
            name=identity.node_id,
            kind=SpanKind.NODE,
            attributes={
                "attempt": identity.attempt,
                "task_id": identity.task_id,
                "engine_kind": "deskpet-native",
                "snapshot_version": 1,
            },
        )
        return span.span_id

    async def node_finished(
        self,
        identity: NodeExecutionIdentity,
        status: str,
        *,
        error: Any = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        execution_id = self._executions.get((identity.task_id, identity.attempt))
        if execution_id is None:
            return
        await self.store.record_node_finish(
            execution_id,
            identity.attempt,
            status,
            error_ref=type(error).__name__ if error is not None else None,
        )
        span_status = {
            "succeeded_pending": SpanStatus.OK,
            "waiting": SpanStatus.BLOCKED,
            "cancelled": SpanStatus.CANCELLED,
        }.get(status, SpanStatus.ERROR)
        await self.traces.finish_span(
            self._span_id(execution_id, identity.attempt),
            span_status,
            error=error,
            attributes=attributes,
        )


__all__ = ["WorkflowExecutionObserver"]
