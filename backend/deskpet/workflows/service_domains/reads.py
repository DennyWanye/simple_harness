"""Run summaries, listings, projections, trace trees and checkpoint history."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import JsonValue, canonical_json
from ..outbox import MAX_PAGE_SIZE
from .core import WorkflowServiceCore
from .support import WorkflowServiceError, _json_value


def _encode_cursor(created_at: float, item_id: str) -> str:
    raw = json.dumps([created_at, item_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[float, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        return float(value[0]), str(value[1])
    except Exception as exc:
        raise WorkflowServiceError("invalid_cursor", "Workflow cursor is invalid") from exc


def _error_text(value: Any) -> str | None:
    if value is None:
        return None
    parsed = _json_value(value, value)
    if isinstance(parsed, Mapping):
        for key in ("message", "message_ref", "reason", "code"):
            if parsed.get(key):
                return str(parsed[key])
        return canonical_json(dict(parsed))
    return str(parsed)


class RunReadMixin(WorkflowServiceCore):
    """Run summaries, listings, projections, trace trees and checkpoint history."""

    __slots__ = ()

    @staticmethod
    def _run_summary(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "run_id": str(row["run_id"]),
            "trace_id": str(row["trace_id"]),
            "thread_id": str(row["thread_id"]),
            "session_id": str(row["session_id"]),
            "request_id": row.get("request_id"),
            "turn_id": row.get("turn_id"),
            "workflow_name": str(row["workflow_name"]),
            "workflow_version": str(row["workflow_version"]),
            "status": str(row["status"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "started_at": row.get("started_at"),
            "ended_at": row.get("ended_at"),
            "active_nodes": list(_json_value(row.get("active_nodes_json"), [])),
            "recovery_action": row.get("recovery_action"),
            "error": _json_value(row.get("error_json"), None),
            "run_version": int(row.get("run_version", 0)),
            "event_seq": int(row.get("event_seq", 0)),
            "head_checkpoint_id": row.get("head_checkpoint_id"),
            "head_checkpoint_ns": row.get("head_checkpoint_ns"),
        }

    async def _require_run(self, run_id: str) -> dict[str, Any]:
        row = await self.run_store.get_run(run_id)
        if row is None:
            raise WorkflowServiceError("run_not_found", f"Workflow run not found: {run_id}")
        return row

    async def execution_owner(self, run_id: str) -> tuple[str, int] | None:
        return await self._owner_uow.get_execution_owner(str(run_id))

    async def emit_progress_event(
        self,
        *,
        run_id: str,
        event_key: str,
        payload: Mapping[str, JsonValue],
        deliveries: Sequence[tuple[str, str]],
    ) -> str:
        """Persist progress through the run's authoritative event owner."""

        owner = await self.execution_owner(run_id)
        event = await self.outbox.ensure_event(
            run_id=run_id,
            event_key=event_key,
            event_type="workflow.progress",
            payload=payload,
            deliveries=deliveries,
            execution_uow=self._owner_uow if owner is not None else None,
        )
        event_id = str(event["event_id"])
        if owner is None:
            await self.deliver_event_once(event_id)
        elif self._execution_delivery_wakeup is not None:
            self._execution_delivery_wakeup()
        return event_id

    async def require_legacy_owner(self, run_id: str, operation: str) -> None:
        owner = await self.execution_owner(run_id)
        if owner is not None:
            raise WorkflowServiceError(
                "execution_owner",
                f"legacy {operation} is forbidden for execution-owned run: {owner[0]}/{owner[1]}",
            )

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or limit < 1 or limit > MAX_PAGE_SIZE:
            raise WorkflowServiceError(
                "invalid_limit", f"limit must be between 1 and {MAX_PAGE_SIZE}"
            )
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id=?")
            params.append(session_id)
        if cursor:
            created_at, run_id = _decode_cursor(cursor)
            clauses.append("(created_at<? OR (created_at=? AND run_id<?))")
            params.extend((created_at, created_at, run_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM workflow_runs{where}
                    ORDER BY created_at DESC,run_id DESC LIMIT ?""",
                    (*params, limit + 1),
                )
            ).fetchall()
        finally:
            await db.close()
        selected = rows[:limit]
        next_cursor = None
        if len(rows) > limit and selected:
            tail = selected[-1]
            next_cursor = _encode_cursor(float(tail["created_at"]), str(tail["run_id"]))
        return {
            "runs": [self._run_summary(dict(row)) for row in selected],
            "next_cursor": next_cursor,
        }

    def _definition_projection(
        self, run: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        registry = getattr(self.runner, "registry", None)
        if registry is None:
            return [], []
        entry = registry.get(str(run["workflow_name"]), str(run["workflow_version"]))
        definition = getattr(getattr(entry, "workflow", None), "definition", None)
        if definition is None:
            return [], []
        nodes = [
            {"id": str(node.node_id), "label": str(node.node_id), "status": "pending"}
            for node in getattr(definition, "nodes", ())
        ]
        edges: list[dict[str, Any]] = []
        for edge in getattr(definition, "edges", ()):
            for source in edge.sources:
                edges.append({"source": str(source), "target": str(edge.target)})
        for edge in getattr(definition, "conditional_edges", ()):
            for label, target in sorted(dict(edge.routes).items()):
                edges.append(
                    {"source": str(edge.source), "target": str(target), "label": str(label)}
                )
        return nodes, edges

    async def _node_projection(self, run_id: str) -> list[dict[str, Any]]:
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT n.*,a.error_ref FROM workflow_nodes n
                    LEFT JOIN workflow_node_attempts a ON a.node_execution_id=n.node_execution_id
                    AND a.retry_attempt=n.latest_attempt WHERE n.run_id=?
                    ORDER BY n.updated_at,n.node_id,n.node_execution_id""",
                    (run_id,),
                )
            ).fetchall()
        finally:
            await db.close()
        return [
            {
                "id": str(row["node_id"]),
                "label": str(row["node_id"]),
                "status": str(row["latest_status"]),
                "attempt": int(row["latest_attempt"]),
                "error": row["error_ref"],
                "node_execution_id": str(row["node_execution_id"]),
            }
            for row in rows
        ]

    async def trace_tree(
        self, *, run_id: str | None = None, trace_id: str | None = None
    ) -> dict[str, Any]:
        run = await self._require_run(run_id) if run_id is not None else None
        resolved_trace_id = trace_id or (str(run["trace_id"]) if run else None)
        if not resolved_trace_id:
            raise WorkflowServiceError("invalid_request", "run_id or trace_id is required")
        raw = await self.trace_store.tree(resolved_trace_id)
        if raw is None:
            return {
                "run_id": run_id,
                "trace_id": resolved_trace_id,
                "run": None,
                "spans": [],
                "roots": [],
            }
        trace_run = dict(raw["run"])
        trace_run["error"] = _json_value(trace_run.pop("error_json", None), None)
        spans: list[dict[str, Any]] = []
        for item in raw["spans"]:
            span = dict(item)
            span["attributes"] = _json_value(span.pop("attributes_json", None), {})
            span["error"] = _error_text(span.pop("error_json", None))
            spans.append(span)
        children: dict[str | None, list[dict[str, Any]]] = {}
        for span in spans:
            children.setdefault(span.get("parent_span_id"), []).append(span)

        def branch(span: Mapping[str, Any]) -> dict[str, Any]:
            return {
                **dict(span),
                "children": [branch(item) for item in children.get(str(span["span_id"]), [])],
            }

        return {
            "run_id": trace_run.get("run_id") or run_id,
            "trace_id": resolved_trace_id,
            "run": trace_run,
            "spans": spans,
            "roots": [branch(item) for item in children.get(None, [])],
        }

    get_trace_tree = trace_tree

    async def checkpoint_history(self, run_id: str, *, limit: int | None = None) -> dict[str, Any]:
        await self._require_run(run_id)
        method = getattr(self.runner, "get_state_history", None)
        history = (
            []
            if not callable(method)
            else [dict(item) for item in await method(run_id, limit=limit)]
        )
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT checkpoint_id,created_at FROM workflow_checkpoint_owners WHERE run_id=?",
                    (run_id,),
                )
            ).fetchall()
        finally:
            await db.close()
        created = {str(row["checkpoint_id"]): float(row["created_at"]) for row in rows}
        checkpoints = []
        for item in history:
            checkpoint_id = str(item["checkpoint_id"])
            metadata = dict(item.get("metadata") or {})
            checkpoints.append(
                {
                    **item,
                    "checkpoint_id": checkpoint_id,
                    "node_id": metadata.get("deskpet_node_id") or metadata.get("node_id"),
                    "created_at": created.get(checkpoint_id, 0.0),
                    "status": str(metadata.get("status") or "checkpointed"),
                }
            )
        return {"run_id": run_id, "checkpoints": checkpoints, "count": len(checkpoints)}

    get_checkpoint_history = checkpoint_history
