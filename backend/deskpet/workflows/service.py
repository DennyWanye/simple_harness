"""Framework-neutral application facade for durable workflows."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .contracts import JsonValue, WorkflowContext, canonical_json, validate_json_value
from .evaluation.models import (
    EvaluationOutcome,
    HumanEvaluationRecord,
    PairwiseEvaluationRecord,
)
from .evaluation.store import EvaluationStore
from .human import HumanDecision, HumanDecisionStore
from .outbox import MAX_PAGE_SIZE, WorkflowOutbox, hydrate_event
from .runner import WorkflowRunner
from .store.run_store import WorkflowRunStore
from .trace.store import TraceStore


START_SNAPSHOT_KEY = "_workflow_start"


class WorkflowServiceError(RuntimeError):
    """Stable error contract consumed by IPC without framework coupling."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        current_version: int | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.current_version = current_version
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class WorkflowStartIdentity:
    venue: str
    base_session_id: str
    code_session_id: str
    delivery_session_id: str
    base_epoch: int
    code_epoch: int
    request_id: str
    turn_id: str
    workflow_name: str
    logical_slot: str

    def to_dict(self) -> dict[str, JsonValue]:
        return asdict(self)

    @property
    def identity_key(self) -> str:
        return _hash_json(self.to_dict())


def _hash_json(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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


def _json_value(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(item) for item in value]
    return value


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


class WorkflowService:
    """Facade over current stores and replaceable runner/fork/delivery adapters."""

    def __init__(
        self,
        run_store: WorkflowRunStore | str | Path | None = None,
        runner: WorkflowRunner | object | None = None,
        human_store: HumanDecisionStore | object | None = None,
        trace_store: TraceStore | object | None = None,
        evaluation_store: EvaluationStore | object | None = None,
        outbox: WorkflowOutbox | object | None = None,
        *,
        store: WorkflowRunStore | str | Path | None = None,
        decision_store: HumanDecisionStore | object | None = None,
        forker: Callable[..., Any | Awaitable[Any]] | None = None,
        delivery_handlers: Mapping[str, Callable[..., Any | Awaitable[Any]]] | None = None,
    ) -> None:
        selected = run_store if run_store is not None else store
        if selected is None and runner is not None:
            selected = getattr(runner, "store", None)
        if selected is None:
            raise ValueError("WorkflowService requires a workflow run store")
        self.run_store = selected if isinstance(selected, WorkflowRunStore) else WorkflowRunStore(selected)
        self.runner = runner
        self.human_store = human_store or decision_store or HumanDecisionStore(self.run_store.path)
        self.trace_store = trace_store or TraceStore(self.run_store.path)
        self.evaluation_store = evaluation_store or EvaluationStore(self.run_store.path)
        self.outbox = outbox or WorkflowOutbox(self.run_store)
        self._forker = forker
        self._delivery_handlers = dict(delivery_handlers or {})
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._delivery_locks: dict[str, asyncio.Lock] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    def session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(str(session_id), asyncio.Lock())

    async def _connect(self):
        await self.run_store.initialize()
        return await self.run_store._connect()

    @staticmethod
    def _required(value: str, field: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise WorkflowServiceError("invalid_request", f"{field} must not be empty")
        return normalized

    async def _start_record(self, identity_key: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT s.run_id,c.snapshot_json FROM workflow_start_requests s
                    JOIN workflow_runs r ON r.run_id=s.run_id
                    LEFT JOIN workflow_capabilities c ON c.capability_hash=r.capability_hash
                    WHERE s.request_key=?""",
                    (identity_key,),
                )
            ).fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    @staticmethod
    def _assert_same_start(existing: Mapping[str, Any] | None, request_hash: str) -> None:
        if existing is None:
            return
        snapshot = _json_value(existing.get("snapshot_json"), {})
        metadata = snapshot.get(START_SNAPSHOT_KEY) if isinstance(snapshot, Mapping) else None
        if not isinstance(metadata, Mapping) or metadata.get("request_hash") != request_hash:
            raise WorkflowServiceError(
                "start_payload_conflict",
                "The workflow start identity already has a different request snapshot",
                details={"existing_run_id": str(existing["run_id"])},
            )

    def _manifest(self, workflow_name: str, workflow_version: str) -> object:
        registry = getattr(self.runner, "registry", None)
        if registry is None:
            raise WorkflowServiceError(
                "workflow_start_unavailable",
                "The workflow runner does not expose a version registry",
            )
        return registry.require(workflow_name, workflow_version).manifest

    @staticmethod
    def _session_refs(identity: WorkflowStartIdentity) -> tuple[tuple[str, str, int], ...]:
        refs: list[tuple[str, str, int]] = [
            ("base", identity.base_session_id, identity.base_epoch),
        ]
        if identity.code_session_id:
            refs.append(("code", identity.code_session_id, identity.code_epoch))
        delivery_epoch = (
            identity.code_epoch
            if identity.code_session_id and identity.delivery_session_id == identity.code_session_id
            else identity.base_epoch
        )
        refs.append(("delivery", identity.delivery_session_id, delivery_epoch))
        return tuple(refs)

    async def start_workflow(
        self,
        *,
        venue: str,
        base_session_id: str,
        delivery_session_id: str,
        request_id: str,
        turn_id: str,
        workflow_name: str,
        workflow_version: str,
        capability_snapshot: Mapping[str, JsonValue],
        start_payload: Mapping[str, JsonValue] | None = None,
        code_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str] | Mapping[str, str]] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_ns: str = "",
    ) -> dict[str, Any]:
        if self.runner is None or not callable(getattr(self.runner, "start", None)):
            raise WorkflowServiceError(
                "workflow_start_unavailable", "The workflow runner cannot start workflows"
            )
        if isinstance(base_epoch, bool) or base_epoch < 0 or isinstance(code_epoch, bool) or code_epoch < 0:
            raise WorkflowServiceError("invalid_request", "session epochs must be non-negative")
        identity = WorkflowStartIdentity(
            venue=self._required(venue, "venue"),
            base_session_id=self._required(base_session_id, "base_session_id"),
            code_session_id=str(code_session_id or ""),
            delivery_session_id=self._required(delivery_session_id, "delivery_session_id"),
            base_epoch=int(base_epoch),
            code_epoch=int(code_epoch),
            request_id=self._required(request_id, "request_id"),
            turn_id=self._required(turn_id, "turn_id"),
            workflow_name=self._required(workflow_name, "workflow_name"),
            logical_slot=self._required(logical_slot, "logical_slot"),
        )
        version = self._required(workflow_version, "workflow_version")
        capabilities = dict(capability_snapshot)
        args = dict(start_payload or {})
        validate_json_value(capabilities, path="$.capability_snapshot")
        validate_json_value(args, path="$.start_payload")
        if START_SNAPSHOT_KEY in capabilities:
            raise WorkflowServiceError(
                "reserved_snapshot_key",
                f"capability_snapshot may not contain {START_SNAPSHOT_KEY}",
            )
        manifest = self._manifest(identity.workflow_name, version)
        capability_hash = _hash_json(capabilities)
        args_hash = _hash_json(args)
        request_hash = _hash_json(
            {
                "workflow_version": version,
                "definition_hash": str(
                    getattr(
                        manifest,
                        "definition_hash",
                        getattr(manifest, "implementation_bundle_hash", "unversioned"),
                    )
                ),
                "args_hash": args_hash,
                "capability_hash": capability_hash,
            }
        )
        persisted_snapshot: dict[str, JsonValue] = {
            **capabilities,
            START_SNAPSHOT_KEY: {
                "identity": identity.to_dict(),
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "args_hash": args_hash,
                "capability_hash": capability_hash,
                "start_payload": args,
            },
        }
        lock = self._start_locks.setdefault(identity.identity_key, asyncio.Lock())
        async with lock:
            existing = await self._start_record(identity.identity_key)
            self._assert_same_start(existing, request_hash)
            resolved_run_id = await self.runner.start(
                session_id=identity.base_session_id,
                request_id=identity.request_id,
                turn_id=identity.turn_id,
                workflow_name=identity.workflow_name,
                workflow_version=version,
                capability_snapshot=persisted_snapshot,
                request_key=identity.identity_key,
                capability_hash=_hash_json(persisted_snapshot),
                run_id=run_id,
                trace_id=trace_id,
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
            )
            persisted = await self._start_record(identity.identity_key)
            self._assert_same_start(persisted, request_hash)
            await self.run_store.bind_session_refs(
                str(resolved_run_id),
                self._session_refs(identity),
            )
            targets = list(
                delivery_targets
                or (
                    ("session_message", identity.delivery_session_id),
                    ("websocket", identity.delivery_session_id),
                )
            )
            card: dict[str, JsonValue] = {
                "run_id": str(resolved_run_id),
                "request_id": identity.request_id,
                "turn_id": identity.turn_id,
                "workflow_name": identity.workflow_name,
                "workflow_version": version,
                "status": "running",
                "recovery_action": None,
                "error": None,
            }
            accepted = await self.outbox.ensure_event(
                run_id=str(resolved_run_id),
                event_key="run:create:accepted",
                event_type="workflow.accepted",
                payload={
                    "kind": "accepted",
                    "run_id": str(resolved_run_id),
                    "request_id": identity.request_id,
                    "turn_id": identity.turn_id,
                    "workflow_name": identity.workflow_name,
                    "workflow_version": version,
                    "status": "running",
                    "identity_key": identity.identity_key,
                    "request_hash": request_hash,
                    "card": card,
                },
                deliveries=targets,
            )
            return {
                "run_id": str(resolved_run_id),
                "created": existing is None,
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "accepted_event_id": accepted["event_id"],
                "accepted_event": accepted,
            }

    start = start_workflow

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

    async def checkpoint_history(
        self, run_id: str, *, limit: int | None = None
    ) -> dict[str, Any]:
        await self._require_run(run_id)
        method = getattr(self.runner, "get_state_history", None)
        history = [] if not callable(method) else [dict(item) for item in await method(run_id, limit=limit)]
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

    @staticmethod
    def _decision_dict(decision: HumanDecision | object) -> dict[str, Any]:
        return _plain(decision)

    async def resolve_decision(
        self,
        decision_id: str,
        *,
        nonce: str,
        response: JsonValue,
        expected_version: int,
    ) -> dict[str, Any]:
        decision = await self.human_store.resolve_decision(
            decision_id,
            nonce=nonce,
            response=response,
            expected_version=expected_version,
        )
        responses = await self.human_store.build_resume_payload(decision_id)
        launcher = getattr(self, "launcher", None)
        schedule = getattr(launcher, "schedule_resume", None)
        if callable(schedule):
            accepted = schedule(decision.run_id, responses)
            if inspect.isawaitable(accepted):
                accepted = await accepted
            return {
                **self._decision_dict(decision),
                "resume": {
                    "triggered": True,
                    "accepted": True,
                    "completion_semantics": "accepted_async",
                    "interrupt_ids": list(responses),
                    "dispatch": _plain(accepted),
                },
            }
        resumed = await self.resume_run(decision.run_id, responses, trusted=True)
        return {
            **self._decision_dict(decision),
            "resume": {
                "triggered": True,
                "interrupt_ids": list(responses),
                "result": resumed,
            },
        }

    async def resume_run(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
        context: WorkflowContext | None = None,
        trusted: bool = False,
    ) -> dict[str, Any]:
        if not trusted:
            authoritative = await self.human_store.build_run_resume_payload(run_id)
            if canonical_json(dict(responses)) != canonical_json(authoritative):
                raise WorkflowServiceError(
                    "invalid_resume_payload",
                    "Workflow resume responses must come from resolved decisions",
                )
        launcher = getattr(self, "launcher", None)
        method = getattr(launcher, "resume_run", None)
        uses_launcher = callable(method)
        if not callable(method):
            method = getattr(self.runner, "resume", None)
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_resume_unavailable", "The workflow runner cannot resume runs"
            )
        if uses_launcher:
            return _plain(await method(run_id, dict(responses)))
        return _plain(await method(run_id, dict(responses), context))

    resume = resume_run

    async def cancel_run(self, run_id: str, *, reason: str = "user") -> dict[str, Any]:
        method = getattr(self.runner, "request_cancel", None)
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_cancel_unavailable", "The workflow runner cannot cancel runs"
            )
        return _plain(await method(run_id, reason))

    cancel = cancel_run

    async def cancel_runs_for_session(
        self,
        session_id: str,
        *,
        session_kind: str | None = None,
        reason: str = "session_deleted",
    ) -> list[dict[str, Any]]:
        """Fence every nonterminal run bound to a deleted session."""

        run_ids = await self.run_store.tombstone_session_refs(
            self._required(session_id, "session_id"),
            session_kind=session_kind,
        )
        cancelled: list[dict[str, Any]] = []
        for run_id in run_ids:
            cancelled.append(await self.cancel_run(run_id, reason=reason))
        return cancelled

    async def fork_checkpoint(
        self,
        *,
        run_id: str,
        checkpoint_id: str,
        expected_version: int,
        state_patch: Mapping[str, JsonValue] | None = None,
        fork_key: str | None = None,
        confirm_dangerous_effects: bool = False,
    ) -> dict[str, Any]:
        method = self._forker
        if method is None:
            method = getattr(self.runner, "fork_checkpoint", None) or getattr(self.runner, "fork", None)
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_fork_unavailable",
                "Checkpoint fork support is not available in the current runner",
            )
        patch = dict(state_patch or {})
        validate_json_value(patch, path="$.state_patch")
        arguments = {
            "run_id": run_id,
            "checkpoint_id": checkpoint_id,
            "expected_version": expected_version,
            "state_patch": patch,
            "fork_key": fork_key,
        }
        parameters = inspect.signature(method).parameters
        accepts_confirmation = "confirm_dangerous_effects" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_confirmation:
            arguments["confirm_dangerous_effects"] = confirm_dangerous_effects
        elif confirm_dangerous_effects:
            raise WorkflowServiceError(
                "workflow_fork_confirmation_unsupported",
                "The configured fork adapter cannot enforce dangerous effect confirmation",
            )
        result = method(**arguments)
        if inspect.isawaitable(result):
            result = await result
        plain = _plain(result)
        launcher = getattr(self, "launcher", None)
        child_run_id = plain.get("run_id") if isinstance(plain, Mapping) else None
        if child_run_id and callable(getattr(launcher, "recover_pending", None)):
            await launcher.recover_pending(only_run_ids={str(child_run_id)})
        return plain

    fork = fork_checkpoint

    async def events_after_seq(
        self, run_id: str, after_seq: int, *, limit: int = MAX_PAGE_SIZE
    ) -> dict[str, Any]:
        return await self.outbox.events_after(run_id, after_seq, limit=limit)

    events_after = events_after_seq

    async def hydrate_history_event_ids(
        self, run_id: str, event_ids: Sequence[str]
    ) -> dict[str, Any]:
        if not event_ids:
            return {
                "run_id": run_id,
                "events": [],
                "missing_event_ids": [],
                "degraded": False,
                "mark_seen_after_reduce": True,
            }
        run = await self._require_run(run_id)
        placeholders = ",".join("?" for _ in event_ids)
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM workflow_events WHERE run_id=?
                    AND event_id IN ({placeholders})""",
                    (run_id, *event_ids),
                )
            ).fetchall()
        finally:
            await db.close()
        by_id = {str(row["event_id"]): dict(row) for row in rows}
        events = [hydrate_event(by_id[event_id], run=run) for event_id in event_ids if event_id in by_id]
        missing = [event_id for event_id in event_ids if event_id not in by_id]
        return {
            "run_id": run_id,
            "events": events,
            "missing_event_ids": missing,
            "degraded": bool(missing),
            "mark_seen_after_reduce": True,
        }

    async def hydrate_session_history_event_ids(
        self,
        session_id: str,
        event_ids: Sequence[str],
        *,
        current_session_epoch: int | None = None,
        session_deleted: bool = False,
    ) -> dict[str, Any]:
        """Hydrate SessionDB event references without crossing a session fence.

        Session history only stores ``workflow_event_id``.  This adapter restores
        the durable envelope in one batch, but only when the event was projected
        to the requested session and the run's persisted delivery reference still
        matches the current SessionDB epoch.
        """

        target = self._required(session_id, "session_id")
        normalized_ids = list(
            dict.fromkeys(str(event_id).strip() for event_id in event_ids if str(event_id).strip())
        )
        if len(normalized_ids) > MAX_PAGE_SIZE:
            raise WorkflowServiceError(
                "history_page_too_large",
                f"At most {MAX_PAGE_SIZE} workflow history events can be hydrated",
            )
        if not normalized_ids or session_deleted:
            return {
                "session_id": target,
                "events": [],
                "missing_event_ids": normalized_ids if session_deleted else [],
                "fenced_event_ids": normalized_ids if session_deleted else [],
                "degraded": bool(normalized_ids and session_deleted),
                "mark_seen_after_reduce": True,
            }

        placeholders = ",".join("?" for _ in normalized_ids)
        db = await self._connect()
        try:
            event_rows = await (
                await db.execute(
                    f"SELECT * FROM workflow_events WHERE event_id IN ({placeholders})",
                    tuple(normalized_ids),
                )
            ).fetchall()
            event_by_id = {str(row["event_id"]): dict(row) for row in event_rows}
            run_ids = list(dict.fromkeys(str(row["run_id"]) for row in event_rows))
            run_by_id: dict[str, dict[str, Any]] = {}
            ref_by_run: dict[str, dict[str, Any]] = {}
            if run_ids:
                run_placeholders = ",".join("?" for _ in run_ids)
                run_rows = await (
                    await db.execute(
                        f"SELECT * FROM workflow_runs WHERE run_id IN ({run_placeholders})",
                        tuple(run_ids),
                    )
                ).fetchall()
                run_by_id = {str(row["run_id"]): dict(row) for row in run_rows}
                ref_rows = await (
                    await db.execute(
                        f"""SELECT * FROM workflow_session_refs
                        WHERE session_kind='delivery' AND run_id IN ({run_placeholders})""",
                        tuple(run_ids),
                    )
                ).fetchall()
                ref_by_run = {str(row["run_id"]): dict(row) for row in ref_rows}

            delivery_rows = await (
                await db.execute(
                    f"""SELECT DISTINCT event_id FROM workflow_deliveries
                    WHERE event_id IN ({placeholders})
                    AND channel IN ('session_message','websocket') AND target_id=?""",
                    (*normalized_ids, target),
                )
            ).fetchall()
            delivered_ids = {str(row["event_id"]) for row in delivery_rows}
        finally:
            await db.close()

        events: list[dict[str, Any]] = []
        missing: list[str] = []
        fenced: list[str] = []
        for event_id in normalized_ids:
            row = event_by_id.get(event_id)
            if row is None:
                missing.append(event_id)
                continue
            run_id = str(row["run_id"])
            run = run_by_id.get(run_id)
            ref = ref_by_run.get(run_id)
            epoch_matches = (
                current_session_epoch is None
                or (ref is not None and int(ref["session_epoch"]) == int(current_session_epoch))
            )
            if (
                run is None
                or ref is None
                or str(ref["session_id"]) != target
                or ref.get("deleted_at") is not None
                or event_id not in delivered_ids
                or not epoch_matches
            ):
                fenced.append(event_id)
                continue
            events.append(hydrate_event(row, run=run))

        return {
            "session_id": target,
            "events": events,
            "missing_event_ids": missing,
            "fenced_event_ids": fenced,
            "degraded": bool(missing or fenced),
            "mark_seen_after_reduce": True,
        }

    async def list_deliveries(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await self.outbox.list_deliveries(
            run_id=run_id, status=status, cursor=cursor, limit=limit
        )

    async def retry_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        lock = self._delivery_locks.setdefault(delivery_id, asyncio.Lock())
        async with lock:
            return await self.outbox.retry_delivery(
                delivery_id, expected_version=expected_version, reason=reason
            )

    async def discard_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        lock = self._delivery_locks.setdefault(delivery_id, asyncio.Lock())
        async with lock:
            return await self.outbox.discard_delivery(
                delivery_id, expected_version=expected_version, reason=reason
            )

    async def deliver_event_once(self, event_id: str) -> dict[str, Any]:
        """Attempt unfinished channels independently; completed channels are skipped."""

        event = await self.outbox.get_event(event_id)
        if event is None:
            raise WorkflowServiceError("event_not_found", f"Workflow event not found: {event_id}")
        results: list[dict[str, Any]] = []
        for delivery in await self.outbox.list_event_deliveries(event_id):
            if delivery["status"] in {"delivered", "discarded", "delivering"}:
                results.append(delivery)
                continue
            handler = self._delivery_handlers.get(delivery["channel"])
            if handler is None:
                results.append(delivery)
                continue
            lock = self._delivery_locks.setdefault(delivery["delivery_id"], asyncio.Lock())
            async with lock:
                claimed = await self.outbox.mutate_delivery(
                    delivery["delivery_id"], action="begin", expected_version=delivery["version"]
                )
                current = claimed["delivery"]
                try:
                    outcome = handler(event, current)
                    if inspect.isawaitable(outcome):
                        await outcome
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed = await self.outbox.mutate_delivery(
                        current["delivery_id"],
                        action="failed",
                        expected_version=current["version"],
                        reason=str(exc),
                    )
                    results.append(failed["delivery"])
                else:
                    delivered = await self.outbox.mutate_delivery(
                        current["delivery_id"],
                        action="delivered",
                        expected_version=current["version"],
                    )
                    results.append(delivered["delivery"])
        return {"event_id": event_id, "deliveries": results}

    @staticmethod
    def _evaluation_dict(record: object) -> dict[str, Any]:
        value = _plain(record)
        if isinstance(value, Mapping) and isinstance(value.get("outcome"), Mapping):
            return {
                "evaluation_id": value["evaluation_id"],
                "trace_id": value["trace_id"],
                "run_id": value.get("run_id"),
                "span_id": value.get("span_id"),
                "created_at": value["created_at"],
                **dict(value["outcome"]),
            }
        return dict(value)

    async def submit_evaluation(
        self,
        *,
        trace_id: str,
        evaluator_name: str,
        evaluator_version: str,
        evaluator_type: str,
        verdict: str,
        score: float | None = None,
        labels: Sequence[str] = (),
        explanation: str | None = None,
        evidence_refs: Sequence[str] = (),
        run_id: str | None = None,
        span_id: str | None = None,
        evaluation_id: str | None = None,
        degraded: bool = False,
        left_version_key: str | None = None,
        right_version_key: str | None = None,
    ) -> dict[str, Any]:
        evaluator_type = str(evaluator_type).strip().lower()
        common = dict(
            trace_id=trace_id,
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
            score=score,
            labels=tuple(labels),
            explanation=explanation,
            evidence_refs=tuple(evidence_refs),
            run_id=run_id,
            span_id=span_id,
            evaluation_id=evaluation_id,
        )
        if evaluator_type == "human":
            record = await self.evaluation_store.record_human(
                HumanEvaluationRecord(verdict=verdict, **common)
            )
        elif evaluator_type == "pairwise":
            if not left_version_key or not right_version_key:
                raise WorkflowServiceError(
                    "invalid_evaluation",
                    "Pairwise evaluation requires both version keys",
                )
            record = await self.evaluation_store.record_pairwise(
                PairwiseEvaluationRecord(
                    winner=verdict,
                    left_version_key=left_version_key,
                    right_version_key=right_version_key,
                    **common,
                )
            )
        else:
            record = await self.evaluation_store.record_evaluation(
                trace_id=trace_id,
                run_id=run_id,
                span_id=span_id,
                evaluation_id=evaluation_id,
                outcome=EvaluationOutcome(
                    evaluator_name=evaluator_name,
                    evaluator_version=evaluator_version,
                    evaluator_type=evaluator_type,
                    verdict=verdict,
                    score=score,
                    labels=tuple(labels),
                    explanation=explanation,
                    evidence_refs=tuple(evidence_refs),
                    degraded=degraded,
                ),
            )
        return self._evaluation_dict(record)

    async def compare_experiments(
        self, left_experiment_id: str, right_experiment_id: str
    ) -> dict[str, Any]:
        return _plain(
            await self.evaluation_store.compare(left_experiment_id, right_experiment_id)
        )

    async def run_detail(self, run_id: str) -> dict[str, Any]:
        run = await self._require_run(run_id)
        declared_nodes, edges = self._definition_projection(run)
        by_id = {item["id"]: item for item in declared_nodes}
        for item in await self._node_projection(run_id):
            by_id[item["id"]] = {**by_id.get(item["id"], {}), **item}
        trace = await self.trace_tree(run_id=run_id)
        history = await self.checkpoint_history(run_id)
        evaluations = [
            self._evaluation_dict(item)
            for item in await self.evaluation_store.list_evaluations(run_id=run_id)
        ]
        decisions = [
            self._decision_dict(item)
            for item in await self.human_store.list_open_decisions(run_id=run_id)
        ]
        deliveries = await self.list_deliveries(run_id=run_id)
        return {
            "run_id": run_id,
            "run": self._run_summary(run),
            "nodes": list(by_id.values()),
            "edges": edges,
            "spans": trace["spans"],
            "checkpoints": history["checkpoints"],
            "evaluations": evaluations,
            "decisions": decisions,
            "deliveries": deliveries["items"],
        }

    get_run_detail = run_detail
    get_run = run_detail


__all__ = [
    "START_SNAPSHOT_KEY",
    "WorkflowService",
    "WorkflowServiceError",
    "WorkflowStartIdentity",
]
