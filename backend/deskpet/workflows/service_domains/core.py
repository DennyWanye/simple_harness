"""Construction, runtime activation, locks and start records."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deskpet.execution.ports import ExecutionUnitOfWork

from ..contracts import TERMINAL_RUN_STATUSES, JsonValue
from ..evaluation.store import EvaluationStore
from ..execution_ports import WorkflowExecutionPorts
from ..human import HumanDecision, HumanDecisionStore
from ..outbox import WorkflowOutbox
from ..runner import WorkflowRunner
from ..runtime_adapters import RuntimeIdentity, WorkflowRuntimeAdapterRegistry
from ..store.execution_uow import SqliteExecutionUnitOfWork
from ..store.research_repository import ResearchWorkflowRepository
from ..store.run_store import WorkflowRunStore
from ..terminal_projection import VersionedActionMatrix
from ..trace.store import TraceStore
from .support import START_SNAPSHOT_KEY, WorkflowServiceError, WorkflowStartIdentity, _json_value


class WorkflowServiceCore:
    """Construction, runtime activation, locks and start records."""

    if TYPE_CHECKING:

        @staticmethod
        def _decision_dict(decision: HumanDecision | object) -> dict[str, Any]: ...
        def _definition_projection(
            self, run: Mapping[str, Any]
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...
        async def _load_research_snapshot_manifest(
            self, *, manifest_ref: str, expected_hash: str
        ) -> dict[str, JsonValue]: ...
        async def _node_projection(self, run_id: str) -> list[dict[str, Any]]: ...
        async def _parent_report_ref(self, run_id: str) -> str | None: ...
        async def _require_run(self, run_id: str) -> dict[str, Any]: ...
        @staticmethod
        def _run_summary(row: Mapping[str, Any]) -> dict[str, Any]: ...
        async def cancel_precreated(self, run_id: str, reason: str = ...) -> dict[str, Any]: ...
        async def checkpoint_history(
            self, run_id: str, *, limit: int | None = ...
        ) -> dict[str, Any]: ...
        async def deliver_event_once(self, event_id: str) -> dict[str, Any]: ...
        async def execution_owner(self, run_id: str) -> tuple[str, int] | None: ...
        async def list_deliveries(
            self,
            *,
            run_id: str | None = ...,
            status: str | None = ...,
            cursor: str | None = ...,
            limit: int = ...,
        ) -> dict[str, Any]: ...
        async def require_legacy_owner(self, run_id: str, operation: str) -> None: ...
        async def trace_tree(
            self, *, run_id: str | None = ..., trace_id: str | None = ...
        ) -> dict[str, Any]: ...

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
        session_delivery_state_reader: Callable[[str], Awaitable[Mapping[str, Any]]] | None = None,
        research_repository: object | None = None,
        research_snapshot_loader: Callable[[str], Awaitable[bytes | Mapping[str, JsonValue]]]
        | None = None,
        action_matrix: VersionedActionMatrix | None = None,
        runtime_adapters: WorkflowRuntimeAdapterRegistry | None = None,
        execution_ports: WorkflowExecutionPorts | None = None,
        owns_execution_uow: bool = False,
        runtime_activation_required: bool = False,
        runtime_activation_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
    ) -> None:
        selected = run_store if run_store is not None else store
        if selected is None and runner is not None:
            selected = getattr(runner, "store", None)
        if selected is None:
            raise ValueError("WorkflowService requires a workflow run store")
        self.run_store = (
            selected if isinstance(selected, WorkflowRunStore) else WorkflowRunStore(selected)
        )
        self.runner = runner
        self.human_store = human_store or decision_store or HumanDecisionStore(self.run_store.path)
        self.trace_store = trace_store or TraceStore(self.run_store.path)
        self.evaluation_store = evaluation_store or EvaluationStore(self.run_store.path)
        self.outbox = outbox or WorkflowOutbox(self.run_store)
        self._forker = forker
        self._delivery_handlers = dict(delivery_handlers or {})
        self._session_delivery_state_reader = session_delivery_state_reader
        self.research_repository = research_repository or ResearchWorkflowRepository(
            self.run_store.path
        )
        self._research_snapshot_loader = research_snapshot_loader
        self.action_matrix = action_matrix or VersionedActionMatrix()
        self.runtime_adapters = runtime_adapters or WorkflowRuntimeAdapterRegistry()
        self.execution_ports = execution_ports
        self._owner_uow = (
            execution_ports.unit_of_work
            if execution_ports
            else SqliteExecutionUnitOfWork(self.run_store.path)
        )
        self._owns_execution_uow = execution_ports is None or owns_execution_uow
        self._close_lock = asyncio.Lock()
        self._closed = False
        if execution_ports is not None and runner is not None:
            configure_execution = getattr(runner, "configure_execution_ports", None)
            if not callable(configure_execution):
                raise ValueError("workflow service requires an execution-aware runner")
            configure_execution(execution_ports)
        self._runtime_activation_required = bool(runtime_activation_required)
        self._runtime_activation_hooks = tuple(runtime_activation_hooks)
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._delivery_locks: dict[str, asyncio.Lock] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._execution_delivery_wakeup: Callable[[], None] | None = None

    @property
    def delivery_handlers(self) -> Mapping[str, Callable[..., Any | Awaitable[Any]]]:
        """Return a copy of the physical delivery registry for Kernel wiring."""

        return dict(self._delivery_handlers)

    @property
    def deep_research_versions(self):
        """Typed view retained as a compatibility name without a second map."""

        return self.runtime_adapters.deep_research

    @property
    def execution_uow(self) -> ExecutionUnitOfWork:
        return self._owner_uow

    async def close(self) -> None:
        """Composition shutdown, after external admission/borrowers stop.

        Join launcher work first; runner drains its other admitted calls before
        releasing its independent UoW. Never close a supplied borrowed UoW.
        If cancellation/error interrupts draining, keep storage available for
        the remaining work and allow the owner to retry shutdown.
        """
        async with self._close_lock:
            if self._closed:
                return
            launcher = getattr(self, "launcher", None)
            if launcher is not None:
                await launcher.shutdown()
            if self.runner is not None:
                await self.runner.close()
            if self._owns_execution_uow:
                await self._owner_uow.close()
            self._closed = True

    def ensure_runtime_active(self) -> None:
        if self._runtime_activation_required:
            self.runtime_adapters.require_active()

    async def activate_runtime(
        self,
        *,
        required_runtime_identities: Sequence[RuntimeIdentity] = (),
    ) -> None:
        """Seal registered adapters, validate durable recovery, then activate.

        Production bootstrap calls this only after Main has registered every
        DeepResearch, Code, and PPT runtime closure.  Existing direct service
        tests are not forced through the product bootstrap gate.
        """

        persisted: set[RuntimeIdentity] = set()
        terminal = {str(status.value) for status in TERMINAL_RUN_STATUSES}
        for row in await self.run_store.list_runs(limit=10_000):
            if str(row.get("status") or "") in terminal:
                continue
            persisted.add(
                (str(row.get("workflow_name") or ""), str(row.get("workflow_version") or ""))
            )
        required = tuple(sorted(set(required_runtime_identities) | persisted))
        self.runtime_adapters.seal(required)
        self.runtime_adapters.activate()
        for hook in self._runtime_activation_hooks:
            await hook()

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
