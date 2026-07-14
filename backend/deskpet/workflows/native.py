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
    async def commit_task_result(self, *, operation_id: str, expected_head: str, task: NativeTask, execution_info: NativeExecutionInfo, patch: StatePatch, configurable: Mapping[str, JsonValue]) -> None: ...
    async def commit_route_selection(self, *, operation_id: str, expected_head: str, source: str, selected_route: str, next_frontier_payload_hash: str, task_id: str, configurable: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]: ...
    async def commit_frontier(self, *, operation_id: str, expected_head: str, state: WorkflowState, frontier: Sequence[NativeTask], completed_activations: Mapping[str, tuple[str, ...]], join_firings: Sequence[str], consumed_interrupt_ids: Sequence[str], intents: Sequence[Mapping[str, JsonValue]], terminal_status: str | None, terminal_error: Mapping[str, JsonValue] | None, recovery_action: str | None, configurable: Mapping[str, JsonValue]) -> NativeCommitResult: ...
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

    async def commit_frontier(self, *, operation_id: str, state: WorkflowState, frontier: Sequence[NativeTask], completed_activations: Mapping[str, tuple[str, ...]], join_firings: Sequence[str], **_: object) -> NativeCommitResult:
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
        return NativeCommitResult(self.snapshot)

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
            patches: dict[str, StatePatch] = dict(execution.pending_results)
            consumed: list[str] = []
            for task in sorted(snapshot.frontier, key=lambda item: item.task_id):
                if task.task_id in patches:
                    continue
                patch, used = await self._run_task(snapshot, task, context, config, responses, execution.first_attempt_times.get(task.task_id))
                patches[task.task_id] = patch
                consumed.extend(used)
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
                intents = self._terminal_intents(
                    state,
                    run_id=run_id,
                    status=terminal_status,
                    error=terminal_error,
                    recovery_action=recovery_action,
                )
                result = await self.store.commit_frontier(
                    operation_id=frontier_operation,
                    expected_head=snapshot.checkpoint_id,
                    state=state,
                    frontier=frontier,
                    completed_activations=completed,
                    join_firings=firings,
                    consumed_interrupt_ids=tuple(consumed),
                    intents=intents,
                    terminal_status=terminal_status,
                    terminal_error=terminal_error,
                    recovery_action=recovery_action,
                    configurable=config,
                )
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

    async def _run_task(self, snapshot: NativeSnapshotEnvelope, task: NativeTask, context: WorkflowContext, config: Mapping[str, JsonValue], responses: Mapping[str, JsonValue], first_attempt_time: float | None) -> tuple[StatePatch, tuple[str, ...]]:
        info = NativeExecutionInfo(snapshot.thread_id, snapshot.run_id, snapshot.checkpoint_id, snapshot.checkpoint_ns, task.task_id, task.retry_attempt, first_attempt_time or time.time(), task.activation_id, task.invocation_key)
        identity = NodeExecutionIdentity.from_execution_info(workflow_name=self.manifest.workflow_name, workflow_version=self.manifest.workflow_version, node_id=task.node_id, execution_info=info, state=snapshot.state)
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
            await self.store.commit_task_result(operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "task", task.task_id), expected_head=snapshot.checkpoint_id, task=task, execution_info=info, patch=patch, configurable=config)
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
            return patch, tuple(control.consumed_interrupt_ids)
        except WorkflowSuspended as exc:
            await self.store.commit_interrupt(operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "interrupt", task.task_id), expected_head=snapshot.checkpoint_id, task=task, interrupt=exc.interrupt.to_dict(), configurable=config)
            if observer is not None:
                await observer.node_finished(identity, "waiting", error=exc)
            await _report(progress, identity, "waiting")
            raise
        except asyncio.CancelledError as exc:
            if observer is not None:
                await observer.node_finished(identity, "cancelled", error=exc)
            await _report(progress, identity, "cancelled")
            raise
        except WorkflowNodeError as exc:
            node = self.workflow.node(task.node_id)
            retryable = (
                exc.retryable
                and exc.code.value in node.retry_policy.retryable_codes
                and task.retry_attempt < node.retry_policy.max_attempts
            )
            if retryable:
                delay = min(node.retry_policy.max_delay_seconds, node.retry_policy.initial_delay_seconds * (node.retry_policy.backoff_multiplier ** (task.retry_attempt - 1)))
                await self.store.commit_retry(operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "retry", task.task_id, task.retry_attempt), expected_head=snapshot.checkpoint_id, task=task, error=exc, next_attempt_at=time.time() + delay, configurable=config)
                if observer is not None:
                    await observer.node_finished(identity, "retryable", error=exc)
            else:
                await self.store.commit_failure(operation_id=_hash(snapshot.run_id, snapshot.checkpoint_id, "failure", task.task_id), expected_head=snapshot.checkpoint_id, task=task, error=exc, configurable=config)
                if observer is not None:
                    await observer.node_finished(identity, "failed", error=exc)
                await _report(progress, identity, "failed")
            raise

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
    "NativeCommitResult", "NativeExecution", "NativeExecutionInfo", "NativeSnapshotEnvelope",
    "NativeTask", "NativeWorkflowExecutable",
]
