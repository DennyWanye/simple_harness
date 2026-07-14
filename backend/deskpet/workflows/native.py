# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeskPet's deterministic, framework-neutral workflow execution kernel."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, AsyncIterator, Mapping, Protocol, Sequence, runtime_checkable

from .contracts import JsonValue, NodeExecutionIdentity, StatePatch, WorkflowContext, WorkflowState, canonical_json, validate_json_value
from .control import ExecutionControl, WorkflowSuspended, bind_execution_control
from .errors import InvalidStatePatch, StateMergeConflict, WorkflowErrorCode, WorkflowNodeError
from .trace.context import SpanContext, use_span

if TYPE_CHECKING:
    from .definition import CompiledWorkflow, ConditionalEdge, WorkflowManifest


CHECKPOINT_TYPE = "deskpet-native-json-v1"
ENGINE_KIND = "deskpet-native"
SNAPSHOT_VERSION = 1


class _CommitUncertain(RuntimeError):
    pass


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeTask:
    task_id: str
    node_id: str
    invocation_key: str
    activation_id: str
    join_epoch: int = 0
    task_path: tuple[str, ...] = ()
    input: dict[str, JsonValue] = field(default_factory=dict)
    retry_attempt: int = 1
    next_attempt_at: float | None = None

    def __post_init__(self) -> None:
        if not all((self.task_id, self.node_id, self.invocation_key, self.activation_id)):
            raise InvalidStatePatch("invalid_native_task", "Native task identity is incomplete")
        if self.retry_attempt < 1 or self.join_epoch < 0:
            raise InvalidStatePatch("invalid_native_task", "Native task counters are invalid")
        validate_json_value(self.input, path="$.task.input")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "invocation_key": self.invocation_key,
            "activation_id": self.activation_id,
            "join_epoch": self.join_epoch,
            "task_path": list(self.task_path),
            "input": copy.deepcopy(self.input),
            "retry_attempt": self.retry_attempt,
            "next_attempt_at": self.next_attempt_at,
        }


@dataclass(frozen=True, slots=True)
class NativeExecutionInfo:
    thread_id: str
    run_id: str
    checkpoint_id: str
    checkpoint_ns: str
    task_id: str
    node_attempt: int
    node_first_attempt_time: float | None
    activation_id: str
    invocation_key: str


@dataclass(frozen=True, slots=True)
class NativeExecutionPolicy:
    """Run-local bounded concurrency policy for native parallel frontiers."""

    max_parallel_tasks: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.max_parallel_tasks, bool) or self.max_parallel_tasks < 1:
            raise ValueError("max_parallel_tasks must be a positive integer")


@dataclass(frozen=True, slots=True)
class NodeTaskOutcome:
    task: NativeTask
    patch: StatePatch | None
    consumed_interrupt_ids: tuple[str, ...]
    error: BaseException | None
    identity: NodeExecutionIdentity


@dataclass(frozen=True, slots=True)
class NativeSnapshotEnvelope:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: str | None
    run_id: str
    state_schema_version: int
    step: int
    state: WorkflowState
    frontier: tuple[NativeTask, ...]
    completed_activations: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    join_firings: tuple[str, ...] = ()
    node_writes: Mapping[str, dict[str, JsonValue]] = field(default_factory=dict)
    interrupt: dict[str, JsonValue] | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    engine_kind: str = ENGINE_KIND
    snapshot_version: int = SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        if self.engine_kind != ENGINE_KIND or self.snapshot_version != SNAPSHOT_VERSION:
            raise InvalidStatePatch("unsupported_native_snapshot", "Native snapshot version is unsupported")
        if self.step < 0 or self.state_schema_version < 1:
            raise InvalidStatePatch("invalid_native_snapshot", "Native snapshot counters are invalid")
        canonical_json(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "checkpoint_type": CHECKPOINT_TYPE,
            "engine_kind": self.engine_kind,
            "snapshot_version": self.snapshot_version,
            "state_schema_version": self.state_schema_version,
            "thread_id": self.thread_id,
            "checkpoint_ns": self.checkpoint_ns,
            "checkpoint_id": self.checkpoint_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "run_id": self.run_id,
            "step": self.step,
            "state": copy.deepcopy(dict(self.state)),
            "frontier": [task.to_dict() for task in self.frontier],
            "completed_activations": {key: list(value) for key, value in sorted(self.completed_activations.items())},
            "join_firings": list(self.join_firings),
            "node_writes": copy.deepcopy(dict(self.node_writes)),
            "interrupt": copy.deepcopy(self.interrupt),
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class NativeExecution:
    snapshot: NativeSnapshotEnvelope
    pending_results: Mapping[str, StatePatch] = field(default_factory=dict)
    first_attempt_times: Mapping[str, float] = field(default_factory=dict)
    route_selections: Mapping[str, Mapping[str, JsonValue]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NativeCommitResult:
    snapshot: NativeSnapshotEnvelope
    materialized_event_ids: tuple[str, ...] = ()


@runtime_checkable
class NativeCheckpointStore(Protocol):
    async def ensure_genesis(self, *, operation_id: str, snapshot: NativeSnapshotEnvelope, configurable: Mapping[str, JsonValue]) -> NativeSnapshotEnvelope: ...
    async def load_execution(self, *, run_id: str, thread_id: str, checkpoint_ns: str) -> NativeExecution: ...
    async def commit_task_result(self, *, operation_id: str, expected_head: str, task: NativeTask, execution_info: NativeExecutionInfo, patch: StatePatch, blob_refs: Sequence[str], configurable: Mapping[str, JsonValue]) -> None: ...
    async def commit_route_selection(self, *, operation_id: str, expected_head: str, source: str, selected_route: str, next_frontier_payload_hash: str, task_id: str, configurable: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]: ...
    async def commit_frontier(self, *, operation_id: str, expected_head: str, state: WorkflowState, frontier: Sequence[NativeTask], completed_activations: Mapping[str, tuple[str, ...]], join_firings: Sequence[str], consumed_interrupt_ids: Sequence[str], intents: Sequence[Mapping[str, JsonValue]], blob_refs: Sequence[str], terminal_status: str | None, terminal_error: Mapping[str, JsonValue] | None, recovery_action: str | None, configurable: Mapping[str, JsonValue]) -> NativeCommitResult: ...
    async def commit_retry(self, *, operation_id: str, expected_head: str, task: NativeTask, error: WorkflowNodeError, next_attempt_at: float, configurable: Mapping[str, JsonValue]) -> None: ...
    async def commit_interrupt(self, *, operation_id: str, expected_head: str, task: NativeTask, interrupt: Mapping[str, JsonValue], configurable: Mapping[str, JsonValue]) -> None: ...
    async def commit_failure(self, *, operation_id: str, expected_head: str, task: NativeTask, error: WorkflowNodeError, configurable: Mapping[str, JsonValue]) -> None: ...
    async def commit_engine_failure(self, *, operation_id: str, expected_head: str, frontier: Sequence[NativeTask], error: WorkflowNodeError, configurable: Mapping[str, JsonValue]) -> None: ...


class InMemoryNativeCheckpointStore:
    """Ephemeral native store for evaluation and definition-level tests."""

    def __init__(self) -> None:
        self.snapshot: NativeSnapshotEnvelope | None = None
        self.pending: dict[str, StatePatch] = {}
        self.interrupt: dict[str, JsonValue] | None = None
        self.retry_attempts: dict[str, int] = {}
        self.route_selections: dict[str, dict[str, JsonValue]] = {}
        self.materialized_intents: dict[str, dict[str, JsonValue]] = {}

    async def ensure_genesis(self, *, operation_id: str, snapshot: NativeSnapshotEnvelope, configurable: Mapping[str, JsonValue]) -> NativeSnapshotEnvelope:
        if self.snapshot is None:
            self.snapshot = snapshot
        return self.snapshot

    async def load_execution(self, *, run_id: str, thread_id: str, checkpoint_ns: str) -> NativeExecution:
        if self.snapshot is None:
            raise InvalidStatePatch("checkpoint_missing", "Ephemeral workflow has no checkpoint")
        projected = self.snapshot
        if self.retry_attempts:
            projected = replace(
                self.snapshot,
                frontier=tuple(
                    replace(
                        task,
                        retry_attempt=self.retry_attempts.get(task.task_id, task.retry_attempt),
                    )
                    for task in self.snapshot.frontier
                ),
            )
        return NativeExecution(projected, dict(self.pending), {}, copy.deepcopy(self.route_selections))

    async def commit_task_result(self, *, task: NativeTask, patch: StatePatch, **_: object) -> None:
        self.pending.setdefault(task.task_id, patch)

    async def commit_route_selection(self, *, task_id: str, source: str, selected_route: str, next_frontier_payload_hash: str, **_: object) -> Mapping[str, JsonValue]:
        selection: dict[str, JsonValue] = {
            "source": source,
            "selected_route": selected_route,
            "next_frontier_payload_hash": next_frontier_payload_hash,
        }
        existing = self.route_selections.setdefault(task_id, selection)
        if existing != selection:
            raise InvalidStatePatch("route_nondeterminism", "Route selection changed")
        return copy.deepcopy(existing)

    async def commit_frontier(self, *, operation_id: str, state: WorkflowState, frontier: Sequence[NativeTask], completed_activations: Mapping[str, tuple[str, ...]], join_firings: Sequence[str], intents: Sequence[Mapping[str, JsonValue]] = (), **_: object) -> NativeCommitResult:
        if self.snapshot is None:
            raise InvalidStatePatch("checkpoint_missing", "Ephemeral workflow has no checkpoint")
        self.snapshot = NativeSnapshotEnvelope(
            thread_id=self.snapshot.thread_id,
            checkpoint_ns=self.snapshot.checkpoint_ns,
            checkpoint_id=_hash(operation_id),
            parent_checkpoint_id=self.snapshot.checkpoint_id,
            run_id=self.snapshot.run_id,
            state_schema_version=self.snapshot.state_schema_version,
            step=self.snapshot.step + 1,
            state=copy.deepcopy(state),
            frontier=tuple(frontier),
            completed_activations=dict(completed_activations),
            join_firings=tuple(join_firings),
            metadata={"engine_kind": ENGINE_KIND},
        )
        self.pending.clear()
        self.route_selections.clear()
        self.interrupt = None
        event_ids: list[str] = []
        for intent in intents:
            event_key = str(intent.get("event_key") or intent.get("intent_id") or "")
            if not event_key:
                raise InvalidStatePatch("invalid_delivery_intent", "intent identity is required")
            copied = copy.deepcopy(dict(intent))
            existing = self.materialized_intents.setdefault(event_key, copied)
            if existing != copied:
                raise InvalidStatePatch("outbox_intent_conflict", "intent content changed")
            event_ids.append(_hash(self.snapshot.run_id, event_key))
        return NativeCommitResult(self.snapshot, tuple(event_ids))

    async def commit_retry(self, *, task: NativeTask, **_: object) -> None:
        self.retry_attempts[task.task_id] = task.retry_attempt + 1

    async def commit_interrupt(self, *, interrupt: Mapping[str, JsonValue], **_: object) -> None:
        self.interrupt = copy.deepcopy(dict(interrupt))

    async def commit_failure(self, **_: object) -> None:
        return None

    async def commit_engine_failure(self, **_: object) -> None:
        return None


async def _report(progress: object | None, identity: NodeExecutionIdentity, transition: str) -> None:
    report = getattr(progress, "report", None)
    if not callable(report):
        return
    try:
        result = report(identity, transition)
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception:
        return


class NativeWorkflowExecutable:
    def __init__(self, workflow: "CompiledWorkflow", store: NativeCheckpointStore) -> None:
        self.workflow = workflow
        self.store = store
        self.manifest: WorkflowManifest = workflow.manifest

    def _config(self, thread_id: str, run_id: str, checkpoint_ns: str, configurable: Mapping[str, JsonValue] | None) -> dict[str, JsonValue]:
        result = copy.deepcopy(dict(configurable or {}))
        validate_json_value(result, path="$.configurable")
        for key, expected in (("thread_id", thread_id), ("checkpoint_ns", checkpoint_ns), ("deskpet_run_id", run_id)):
            if key in result and result[key] != expected:
                raise InvalidStatePatch("conflicting_runtime_identity", f"Configurable {key} conflicts with execution identity")
            result[key] = expected
        return result

    def _entry_task(self, run_id: str, thread_id: str, checkpoint_ns: str) -> NativeTask:
        activation = _hash(run_id, thread_id, checkpoint_ns, "genesis")
        return NativeTask(
            task_id=_hash(run_id, thread_id, checkpoint_ns, "genesis", self.workflow.definition.entry_node),
            node_id=self.workflow.definition.entry_node,
            invocation_key=f"entry:{self.workflow.definition.entry_node}",
            activation_id=activation,
            task_path=(self.workflow.definition.entry_node,),
        )

    async def ainvoke(self, state: WorkflowState | object, context: WorkflowContext, *, thread_id: str, run_id: str, checkpoint_ns: str = "", configurable: Mapping[str, JsonValue] | None = None) -> object:
        if state is None:
            return await self._drive(
                context,
                thread_id,
                run_id,
                checkpoint_ns,
                self._config(thread_id, run_id, checkpoint_ns, configurable),
                {},
            )
        if not isinstance(state, Mapping):
            raise InvalidStatePatch("invalid_initial_state", "Native workflow initial state must be a mapping")
        initial = copy.deepcopy(dict(state))
        validate_json_value(initial)
        config = self._config(thread_id, run_id, checkpoint_ns, configurable)
        entry = self._entry_task(run_id, thread_id, checkpoint_ns)
        genesis_id = _hash(run_id, thread_id, checkpoint_ns, "genesis")
        genesis = NativeSnapshotEnvelope(
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=genesis_id,
            parent_checkpoint_id=None,
            run_id=run_id,
            state_schema_version=self.manifest.state_schema_version,
            step=0,
            state=initial,  # type: ignore[arg-type]
            frontier=(entry,),
            metadata={"engine_kind": ENGINE_KIND},
        )
        await self.store.ensure_genesis(operation_id=_hash(run_id, genesis_id, "genesis"), snapshot=genesis, configurable=config)
        return await self._drive(context, thread_id, run_id, checkpoint_ns, config, {})

    async def resume(self, responses: Mapping[str, JsonValue], context: WorkflowContext, *, thread_id: str, run_id: str, checkpoint_ns: str = "", configurable: Mapping[str, JsonValue] | None = None) -> object:
        copied = copy.deepcopy(dict(responses))
        if not copied:
            raise InvalidStatePatch("empty_resume", "A workflow resume requires an interrupt response")
        validate_json_value(copied, path="$.resume")
        return await self._drive(context, thread_id, run_id, checkpoint_ns, self._config(thread_id, run_id, checkpoint_ns, configurable), copied)

    async def astream(self, state: WorkflowState | object, context: WorkflowContext, **kwargs: object) -> AsyncIterator[object]:
        yield await self.ainvoke(state, context, **kwargs)  # type: ignore[arg-type]

    async def _drive(self, context: WorkflowContext, thread_id: str, run_id: str, checkpoint_ns: str, config: Mapping[str, JsonValue], responses: Mapping[str, JsonValue]) -> WorkflowState:
        while True:
            execution = await self.store.load_execution(run_id=run_id, thread_id=thread_id, checkpoint_ns=checkpoint_ns)
            snapshot = execution.snapshot
            if not snapshot.frontier:
                return copy.deepcopy(snapshot.state)
            if snapshot.step >= self.manifest.max_supersteps:
                error = WorkflowNodeError(code=WorkflowErrorCode.INVALID_STATE, message_ref="workflow_engine:max_supersteps")
                await self.store.commit_engine_failure(operation_id=_hash(run_id, snapshot.checkpoint_id, "max_supersteps"), expected_head=snapshot.checkpoint_id, frontier=snapshot.frontier, error=error, configurable=config)
                raise error
            patches, consumed = await self._run_frontier_tasks(
                execution, context, config, responses
            )
            try:
                ordered_writes = [(task.node_id, patches[task.task_id]) for task in sorted(snapshot.frontier, key=lambda item: item.task_id)]
                delta = self.workflow.merge_patches(ordered_writes)
                state = self.workflow.reduce_state(snapshot.state, delta)
                frontier, completed, firings = await self._next_frontier(
                    snapshot, state, context, execution.route_selections, config
                )
                frontier_operation = _hash(
                    run_id,
                    snapshot.checkpoint_id,
                    "frontier",
                    *(task.task_id for task in snapshot.frontier),
                    "next",
                    *(task.task_id for task in frontier),
                    canonical_json(state),
                )
                terminal_status, terminal_error, recovery_action = self._terminal_projection(
                    state, frontier
                )
                completion_intents = self._completion_intents(
                    snapshot, patches, context, execution.first_attempt_times
                )
                intents = (*completion_intents, *self._terminal_intents(
                    state,
                    run_id=run_id,
                    status=terminal_status,
                    error=terminal_error,
                    recovery_action=recovery_action,
                ))
                blob_refs = self._state_blob_refs(state)
                result = await self.store.commit_frontier(
                    operation_id=frontier_operation,
                    expected_head=snapshot.checkpoint_id,
                    state=state,
                    frontier=frontier,
                    completed_activations=completed,
                    join_firings=firings,
                    consumed_interrupt_ids=tuple(consumed),
                    intents=intents,
                    blob_refs=blob_refs,
                    terminal_status=terminal_status,
                    terminal_error=terminal_error,
                    recovery_action=recovery_action,
                    configurable=config,
                )
                if completion_intents:
                    notify = getattr(context.ports.get("progress"), "notify_dispatcher", None)
                    if callable(notify):
                        try:
                            notify()
                        except Exception:
                            pass
            except (InvalidStatePatch, StateMergeConflict, WorkflowNodeError) as exc:
                error = exc if isinstance(exc, WorkflowNodeError) else WorkflowNodeError(code=WorkflowErrorCode.INVALID_STATE, message_ref=f"workflow_engine:{exc.code}")
                await self.store.commit_engine_failure(operation_id=_hash(run_id, snapshot.checkpoint_id, "engine_failure"), expected_head=snapshot.checkpoint_id, frontier=snapshot.frontier, error=error, configurable=config)
                raise error
            except Exception:
                error = WorkflowNodeError(code=WorkflowErrorCode.INVALID_STATE, message_ref="workflow_engine:frontier_failure")
                await self.store.commit_engine_failure(operation_id=_hash(run_id, snapshot.checkpoint_id, "engine_failure"), expected_head=snapshot.checkpoint_id, frontier=snapshot.frontier, error=error, configurable=config)
                raise error from None
            if not result.snapshot.frontier:
                return copy.deepcopy(result.snapshot.state)

    @staticmethod
    def _terminal_projection(
        state: WorkflowState, frontier: Sequence[NativeTask]
    ) -> tuple[str | None, dict[str, JsonValue] | None, str | None]:
        if frontier:
            return None, None, None
        values = state.get("values")
        values = values if isinstance(values, Mapping) else {}
        domain_status = str(values.get("terminal_status") or "").strip().lower()
        if domain_status in {"", "success", "completed"}:
            return "completed", None, None
        if domain_status in {"cancelled", "canceled"}:
            return "cancelled", None, None
        raw_error = values.get("terminal_error")
        if domain_status == "error":
            if isinstance(raw_error, Mapping):
                error: dict[str, JsonValue] = {
                    "code": str(raw_error.get("code") or "workflow_domain_error")[:80],
                    "message": str(raw_error.get("user_message") or "Workflow could not complete.")[:500],
                }
                recovery = str(raw_error.get("recovery_action") or "Adjust the input and retry.")[:500]
            else:
                error = {"code": "workflow_domain_error", "message": "Workflow could not complete."}
                recovery = "Adjust the input and retry."
            return "failed", error, recovery
        return (
            "failed",
            {
                "code": "invalid_domain_terminal_status",
                "message": "Workflow returned an invalid terminal status.",
            },
            "Retry the workflow and inspect its trace if the problem persists.",
        )

    @staticmethod
    def _terminal_intents(
        state: WorkflowState,
        *,
        run_id: str,
        status: str | None,
        error: Mapping[str, JsonValue] | None,
        recovery_action: str | None,
    ) -> tuple[dict[str, JsonValue], ...]:
        if status is None:
            return ()
        values = state.get("values")
        values = values if isinstance(values, Mapping) else {}
        result: list[dict[str, JsonValue]] = []
        raw_intents = values.get("delivery_intents")
        if isinstance(raw_intents, list):
            for raw in raw_intents:
                if not isinstance(raw, Mapping):
                    continue
                intent_id = str(raw.get("intent_id") or "").strip()
                if not intent_id:
                    continue
                kind = str(raw.get("kind") or "progress")
                result.append(
                    {
                        "intent_id": intent_id,
                        "event_key": f"terminal:{intent_id}",
                        "event_type": f"workflow.{kind}",
                        "channel": str(raw.get("channel") or ""),
                        "payload": {**copy.deepcopy(dict(raw)), "status": status},
                    }
                )
        result.append(
            {
                "intent_id": f"{run_id}:run-terminal",
                "event_key": "run:terminal",
                "event_type": "workflow.final",
                "channel": "final",
                "payload": {
                    "kind": "final",
                    "status": status,
                    "error": copy.deepcopy(dict(error)) if error is not None else None,
                    "recovery_action": recovery_action,
                    "card": {
                        "run_id": run_id,
                        "status": status,
                        "error": copy.deepcopy(dict(error)) if error is not None else None,
                        "recovery_action": recovery_action,
                    },
                },
            }
        )
        validate_json_value(result, path="$.terminal_intents")
        return tuple(result)

    @staticmethod
    def _state_blob_refs(state: Mapping[str, JsonValue]) -> tuple[str, ...]:
        raw = state.get("blob_refs", [])
        if not isinstance(raw, list):
            raise InvalidStatePatch("invalid_blob_refs", "blob_refs must be a list")
        normalized: set[str] = set()
        for value in raw:
            if isinstance(value, Mapping):
                digest = str(value.get("sha256") or value.get("id") or "")
            else:
                digest = str(value)
            if digest:
                normalized.add(digest)
        refs = tuple(sorted(normalized))
        if any(len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value) for value in refs):
            raise InvalidStatePatch("invalid_blob_refs", "blob_refs must contain SHA-256 digests")
        return refs

    @staticmethod
    def _patch_blob_refs(patch: StatePatch) -> tuple[str, ...]:
        return NativeWorkflowExecutable._state_blob_refs(patch.to_dict()) if "blob_refs" in patch.to_dict() else ()

    def _policy(self, context: WorkflowContext) -> NativeExecutionPolicy:
        raw = context.ports.get("native_execution_policy")
        if raw is None:
            return NativeExecutionPolicy()
        if isinstance(raw, NativeExecutionPolicy):
            return raw
        if isinstance(raw, Mapping):
            return NativeExecutionPolicy(int(raw.get("max_parallel_tasks", 1)))
        raise InvalidStatePatch("invalid_native_execution_policy", "native execution policy is invalid")

    def _identity(
        self, snapshot: NativeSnapshotEnvelope, task: NativeTask, first_attempt_time: float | None
    ) -> tuple[NativeExecutionInfo, NodeExecutionIdentity]:
        info = NativeExecutionInfo(
            snapshot.thread_id, snapshot.run_id, snapshot.checkpoint_id,
            snapshot.checkpoint_ns, task.task_id, task.retry_attempt,
            first_attempt_time or time.time(), task.activation_id, task.invocation_key,
        )
        return info, NodeExecutionIdentity.from_execution_info(
            workflow_name=self.manifest.workflow_name,
            workflow_version=self.manifest.workflow_version,
            node_id=task.node_id,
            execution_info=info,
            state=snapshot.state,
        )

    def _freeze_public_progress(
        self, patch: StatePatch, *, first_attempt_time: float, finished_at: float
    ) -> StatePatch:
        if (self.manifest.workflow_name, self.manifest.workflow_version) != ("deep_research", "v2"):
            return patch
        data = patch.to_dict()
        values = data.get("values")
        if not isinstance(values, dict):
            return patch
        public = values.get("public_progress")
        if not isinstance(public, dict):
            return patch
        projection = public.get("stage_projection")
        if not isinstance(projection, dict) or not projection.get("stage_id"):
            return patch
        frozen = copy.deepcopy(projection)
        completed_ids = public.get("completed_stage_ids", [])
        if not isinstance(completed_ids, list):
            raise InvalidStatePatch("invalid_public_progress", "completed_stage_ids must be a list")
        frozen.update(
            {
                "started_at": float(first_attempt_time),
                "finished_at": float(finished_at),
                "duration_ms": max(0, round((finished_at - first_attempt_time) * 1000)),
                "completed_count": len({str(value) for value in completed_ids}),
            }
        )
        public["stage_projection"] = frozen
        values["public_progress"] = public
        data["values"] = values
        return StatePatch(data)

    async def _run_task_worker(self, snapshot: NativeSnapshotEnvelope, task: NativeTask, context: WorkflowContext, config: Mapping[str, JsonValue], responses: Mapping[str, JsonValue], first_attempt_time: float | None) -> NodeTaskOutcome:
        info, identity = self._identity(snapshot, task, first_attempt_time)
        observer = context.ports.get("observer")
        progress = context.ports.get("progress")
        span_id = await observer.node_started(identity) if observer is not None else None
        await _report(progress, identity, "started")
        control = ExecutionControl(task.task_id, responses)
        try:
            with bind_execution_control(control):
                if span_id and context.trace_id:
                    with use_span(SpanContext(context.trace_id, span_id, identity.run_id)):
                        patch = await self.workflow.run_node(task.node_id, snapshot.state, context, info)
                else:
                    patch = await self.workflow.run_node(task.node_id, snapshot.state, context, info)
            patch = self._freeze_public_progress(
                patch, first_attempt_time=float(info.node_first_attempt_time or time.time()), finished_at=time.time()
            )
            try:
                await self.store.commit_task_result(operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "task", task.task_id), expected_head=snapshot.checkpoint_id, task=task, execution_info=info, patch=patch, blob_refs=self._patch_blob_refs(patch), configurable=config)
            except Exception as exc:
                raise _CommitUncertain from exc
            if observer is not None:
                patch_values = patch.to_dict()
                values = patch_values.get("values", {})
                research_state = values.get("research_state", {}) if isinstance(values, dict) else {}
                route = research_state.get("route", {}) if isinstance(research_state, dict) else {}
                agent_reach_trace = (
                    copy.deepcopy(route.get("agent_reach"))
                    if isinstance(route, dict) and isinstance(route.get("agent_reach"), dict)
                    else None
                )
                if agent_reach_trace is not None:
                    validate_json_value(agent_reach_trace, path="$.trace.agent_reach")
                    await observer.node_finished(
                        identity,
                        "succeeded_pending",
                        attributes={"agent_reach": agent_reach_trace},
                    )
                else:
                    await observer.node_finished(identity, "succeeded_pending")
            return NodeTaskOutcome(task, patch, tuple(control.consumed_interrupt_ids), None, identity)
        except WorkflowSuspended as exc:
            return NodeTaskOutcome(task, None, tuple(control.consumed_interrupt_ids), exc, identity)
        except asyncio.CancelledError as exc:
            if observer is not None:
                await observer.node_finished(identity, "cancelled", error=exc)
            await _report(progress, identity, "cancelled")
            raise
        except _CommitUncertain as exc:
            assert exc.__cause__ is not None
            raise exc.__cause__
        except WorkflowNodeError as exc:
            return NodeTaskOutcome(task, None, tuple(control.consumed_interrupt_ids), exc, identity)
        except Exception:
            error = WorkflowNodeError(
                code=WorkflowErrorCode.PERMANENT,
                message_ref="workflow_engine:unexpected_node_failure",
                node_id=task.node_id,
            )
            return NodeTaskOutcome(task, None, tuple(control.consumed_interrupt_ids), error, identity)

    async def _run_frontier_tasks(
        self,
        execution: NativeExecution,
        context: WorkflowContext,
        config: Mapping[str, JsonValue],
        responses: Mapping[str, JsonValue],
    ) -> tuple[dict[str, StatePatch], list[str]]:
        snapshot = execution.snapshot
        patches: dict[str, StatePatch] = dict(execution.pending_results)
        pending = [task for task in sorted(snapshot.frontier, key=lambda item: item.task_id) if task.task_id not in patches]
        outcomes: list[NodeTaskOutcome] = []
        if pending:
            nodes = [self.workflow.node(task.node_id) for task in pending]
            parallel = all(
                str(node.dispatch) == "parallel" and not node.barrier and not node.interrupt_capable
                for node in nodes
            )
            if parallel and self._policy(context).max_parallel_tasks > 1:
                semaphore = asyncio.Semaphore(self._policy(context).max_parallel_tasks)

                async def bounded(task: NativeTask) -> NodeTaskOutcome:
                    async with semaphore:
                        return await self._run_task_worker(
                            snapshot, task, context, config, responses,
                            execution.first_attempt_times.get(task.task_id),
                        )

                children = [asyncio.create_task(bounded(task)) for task in pending]
                try:
                    outcomes = list(await asyncio.gather(*children))
                except asyncio.CancelledError:
                    for child in children:
                        child.cancel()
                    await asyncio.gather(*children, return_exceptions=True)
                    raise
            else:
                for task in pending:
                    outcomes.append(await self._run_task_worker(
                        snapshot, task, context, config, responses,
                        execution.first_attempt_times.get(task.task_id),
                    ))

        errors = [outcome for outcome in outcomes if outcome.error is not None]
        suspends = [outcome for outcome in errors if isinstance(outcome.error, WorkflowSuspended)]
        if suspends:
            selected = min(suspends, key=lambda item: item.task.task_id)
            node = self.workflow.node(selected.task.node_id)
            if len(snapshot.frontier) != 1 or str(node.dispatch) == "parallel":
                selected = replace(
                    selected,
                    error=WorkflowNodeError(
                        code=WorkflowErrorCode.INVALID_STATE,
                        message_ref="workflow_engine:parallel_interrupt_invariant",
                        node_id=selected.task.node_id,
                    ),
                )
            else:
                assert isinstance(selected.error, WorkflowSuspended)
                await self.store.commit_interrupt(
                    operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "interrupt", selected.task.task_id),
                    expected_head=snapshot.checkpoint_id, task=selected.task,
                    interrupt=selected.error.interrupt.to_dict(), configurable=config,
                )
                observer = context.ports.get("observer")
                if observer is not None:
                    await observer.node_finished(selected.identity, "waiting", error=selected.error)
                await _report(context.ports.get("progress"), selected.identity, "waiting")
                raise selected.error

        failures: list[tuple[int, str, NodeTaskOutcome, WorkflowNodeError, bool]] = []
        for outcome in errors:
            error = outcome.error
            if not isinstance(error, WorkflowNodeError):
                error = WorkflowNodeError(code=WorkflowErrorCode.PERMANENT, message_ref="workflow_engine:unexpected_node_failure", node_id=outcome.task.node_id)
            node = self.workflow.node(outcome.task.node_id)
            retryable = bool(
                error.retryable
                and error.code.value in node.retry_policy.retryable_codes
                and outcome.task.retry_attempt < node.retry_policy.max_attempts
            )
            failures.append((1 if retryable else 0, outcome.task.task_id, outcome, error, retryable))
        if failures:
            _, _, selected, error, retryable = min(failures, key=lambda item: (item[0], item[1]))
            observer = context.ports.get("observer")
            if retryable:
                node = self.workflow.node(selected.task.node_id)
                delay = min(node.retry_policy.max_delay_seconds, node.retry_policy.initial_delay_seconds * (node.retry_policy.backoff_multiplier ** (selected.task.retry_attempt - 1)))
                await self.store.commit_retry(operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "retry", selected.task.task_id, selected.task.retry_attempt), expected_head=snapshot.checkpoint_id, task=selected.task, error=error, next_attempt_at=time.time() + delay, configurable=config)
                if observer is not None:
                    await observer.node_finished(selected.identity, "retryable", error=error)
            else:
                await self.store.commit_failure(operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "failure", selected.task.task_id), expected_head=snapshot.checkpoint_id, task=selected.task, error=error, configurable=config)
                if observer is not None:
                    await observer.node_finished(selected.identity, "failed", error=error)
                await _report(context.ports.get("progress"), selected.identity, "failed")
            raise error

        consumed: list[str] = []
        for outcome in outcomes:
            assert outcome.patch is not None
            patches[outcome.task.task_id] = outcome.patch
            consumed.extend(outcome.consumed_interrupt_ids)
        return patches, consumed

    def _completion_intents(
        self,
        snapshot: NativeSnapshotEnvelope,
        patches: Mapping[str, StatePatch],
        context: WorkflowContext,
        first_attempt_times: Mapping[str, float],
    ) -> tuple[dict[str, JsonValue], ...]:
        if (self.manifest.workflow_name, self.manifest.workflow_version) != ("deep_research", "v2"):
            return ()
        builder = getattr(context.ports.get("progress"), "build_completion_intent", None)
        if not callable(builder):
            return ()
        intents: list[dict[str, JsonValue]] = []
        for task in sorted(snapshot.frontier, key=lambda item: item.task_id):
            patch = patches[task.task_id].to_dict()
            values = patch.get("values")
            public = values.get("public_progress") if isinstance(values, dict) else None
            projection = public.get("stage_projection") if isinstance(public, dict) else None
            if not isinstance(projection, dict):
                continue
            _, identity = self._identity(snapshot, task, first_attempt_times.get(task.task_id))
            intent = builder(identity, projection)
            if isinstance(intent, Mapping):
                intents.append(copy.deepcopy(dict(intent)))
        validate_json_value(intents, path="$.completion_intents")
        return tuple(intents)

    async def _next_frontier(
        self,
        snapshot: NativeSnapshotEnvelope,
        state: WorkflowState,
        context: WorkflowContext,
        route_selections: Mapping[str, Mapping[str, JsonValue]],
        config: Mapping[str, JsonValue],
    ) -> tuple[tuple[NativeTask, ...], Mapping[str, tuple[str, ...]], tuple[str, ...]]:
        completed = {key: list(value) for key, value in snapshot.completed_activations.items()}
        firings = set(snapshot.join_firings)
        candidates: list[tuple[str, NativeTask, str]] = []
        tasks_by_node = {task.node_id: task for task in snapshot.frontier}
        for task in sorted(snapshot.frontier, key=lambda item: item.task_id):
            conditional = self.workflow.conditional_for(task.node_id)
            targets: list[str] = []
            if conditional is not None:
                stored = route_selections.get(task.task_id)
                if stored is not None:
                    route = str(stored["selected_route"])
                else:
                    route = await self.workflow.route(conditional, state, context, NativeExecutionInfo(snapshot.thread_id, snapshot.run_id, snapshot.checkpoint_id, snapshot.checkpoint_ns, task.task_id, task.retry_attempt, None, task.activation_id, task.invocation_key))
                    target = conditional.routes[route]
                    await self.store.commit_route_selection(
                        operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "route", task.task_id),
                        expected_head=snapshot.checkpoint_id,
                        source=task.node_id,
                        selected_route=route,
                        next_frontier_payload_hash=_hash(task.task_id, route, target),
                        task_id=task.task_id,
                        configurable=config,
                    )
                targets.append(conditional.routes[route])
            else:
                targets.extend(self.workflow.single_targets(task.node_id))
            activation = _hash(snapshot.checkpoint_id, task.task_id, task.join_epoch)
            for target in targets:
                if target == "__end__":
                    continue
                candidates.append((target, task, activation))
            for edge in self.workflow.join_edges_for(task.node_id):
                key = f"{edge.target}:{task.join_epoch}"
                token = f"{task.node_id}:{task.task_id}"
                values = completed.setdefault(key, [])
                if token not in values:
                    values.append(token)
                source_tasks = {value.split(":", 1)[0]: value.split(":", 1)[1] for value in values}
                if all(source in source_tasks for source in edge.sources) and key not in firings:
                    firings.add(key)
                    synthetic = tasks_by_node.get(edge.sources[0], task)
                    candidates.append((edge.target, synthetic, _hash(snapshot.run_id, key, *(source_tasks[source] for source in edge.sources))))
        next_tasks: dict[str, NativeTask] = {}
        for target, source, activation in sorted(candidates, key=lambda item: (item[0], item[1].task_id)):
            epoch = source.join_epoch + (1 if self.workflow.is_cycle_edge(source.node_id, target) else 0)
            self.workflow.validate_loop_budget(source.node_id, target, state)
            invocation = f"{target}:{activation}:{epoch}"
            task_id = _hash(snapshot.run_id, snapshot.checkpoint_id, invocation)
            next_tasks[task_id] = NativeTask(task_id, target, invocation, activation, epoch, (*source.task_path, target))
        return tuple(next_tasks[key] for key in sorted(next_tasks)), {key: tuple(sorted(value)) for key, value in sorted(completed.items())}, tuple(sorted(firings))


__all__ = [
    "CHECKPOINT_TYPE", "ENGINE_KIND", "SNAPSHOT_VERSION", "NativeCheckpointStore",
    "InMemoryNativeCheckpointStore",
    "NativeCommitResult", "NativeExecution", "NativeExecutionInfo", "NativeExecutionPolicy",
    "NativeSnapshotEnvelope", "NativeTask", "NativeWorkflowExecutable", "NodeTaskOutcome",
]
