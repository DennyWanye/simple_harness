"""Durable workflow registry and fenced async execution runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass, replace
from typing import Any, Mapping

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    RecoveryLease,
    RunEventCandidate,
)

from .contracts import JsonValue, TERMINAL_RUN_STATUSES, WorkflowContext, WorkflowRunStatus
from .definition import CompiledWorkflow, WorkflowManifest
from .errors import WorkflowDependencyUnavailable, WorkflowErrorCode, WorkflowNodeError
from .lease import ActiveLease, HEARTBEAT_INTERVAL_SECONDS, LEASE_TTL_SECONDS, LeaseManager, transition_run
from .recovery import (
    FailureMapping,
    RecoveryRecord,
    expire_stale_lease,
    map_workflow_failure,
    quarantine_checkpoint,
    repair_head_projection,
)
from .replay import WorkflowReplay
from .store import NativeCheckpointStore, StaleRunFence, WorkflowRunStore
from .store.execution_uow import SqliteExecutionUnitOfWork
from .trace.observer import WorkflowExecutionObserver
from .trace.ports import instrument_ports
from .trace.store import TraceStore
from .execution_ports import WorkflowExecutionPorts


logger = logging.getLogger(__name__)


def _manifest_payload(manifest: object) -> dict[str, Any]:
    if hasattr(manifest, "to_dict"):
        return dict(getattr(manifest, "to_dict")())
    if is_dataclass(manifest):
        return asdict(manifest)
    return {
        key: value
        for key, value in vars(manifest).items()
        if not key.startswith("_") and not callable(value)
    }


def manifest_hash(manifest: object) -> str:
    payload = json.dumps(
        _manifest_payload(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(slots=True)
class RegisteredWorkflow:
    workflow: object
    manifest: WorkflowManifest | object
    executable: object | None = None
    _bound: object | None = None

    def materialize(self, saver: NativeCheckpointStore) -> object:
        if self.executable is not None:
            return self.executable
        if self._bound is None:
            bind = getattr(self.workflow, "bind", None)
            if bind is None:
                raise WorkflowDependencyUnavailable("registered workflow cannot be bound")
            self._bound = bind(checkpointer=saver)
        return self._bound


class WorkflowRegistry:
    """Immutable-version registry keyed by ``(workflow_name, workflow_version)``."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], RegisteredWorkflow] = {}

    def register(
        self,
        workflow: CompiledWorkflow | object,
        *,
        executable: object | None = None,
        replace: bool = False,
    ) -> RegisteredWorkflow:
        manifest = getattr(workflow, "manifest", None)
        if manifest is None and executable is not None:
            manifest = getattr(executable, "manifest", None)
        if manifest is None:
            raise ValueError("a registered workflow requires a manifest")
        if str(getattr(manifest, "durability")) != "sync":
            raise ValueError("durable workflow manifests must use sync durability")
        if executable is None and hasattr(workflow, "ainvoke"):
            executable = workflow
        name = str(getattr(manifest, "workflow_name"))
        version = str(getattr(manifest, "workflow_version"))
        key = (name, version)
        existing = self._entries.get(key)
        if existing is not None and not replace:
            if manifest_hash(existing.manifest) != manifest_hash(manifest):
                raise ValueError(f"workflow version already registered with different code: {name}@{version}")
            return existing
        entry = RegisteredWorkflow(workflow, manifest, executable)
        self._entries[key] = entry
        return entry

    def unregister(self, workflow_name: str, workflow_version: str) -> None:
        self._entries.pop((workflow_name, workflow_version), None)

    def get(self, workflow_name: str, workflow_version: str) -> RegisteredWorkflow | None:
        return self._entries.get((workflow_name, workflow_version))

    def require(
        self,
        workflow_name: str,
        workflow_version: str,
        *,
        expected_manifest_hash: str | None = None,
        expected_implementation_hash: str | None = None,
    ) -> RegisteredWorkflow:
        entry = self.get(workflow_name, workflow_version)
        if entry is None:
            raise WorkflowDependencyUnavailable(
                f"workflow graph version unavailable: {workflow_name}@{workflow_version}"
            )
        actual_manifest_hash = manifest_hash(entry.manifest)
        actual_implementation_hash = str(getattr(entry.manifest, "implementation_bundle_hash"))
        if expected_manifest_hash is not None and actual_manifest_hash != expected_manifest_hash:
            raise WorkflowDependencyUnavailable(
                f"workflow manifest hash mismatch: {workflow_name}@{workflow_version}"
            )
        if (
            expected_implementation_hash is not None
            and actual_implementation_hash != expected_implementation_hash
        ):
            raise WorkflowDependencyUnavailable(
                f"workflow implementation hash mismatch: {workflow_name}@{workflow_version}"
            )
        return entry

    def versions(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._entries))

    def implementation_hashes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                str(getattr(entry.manifest, "implementation_bundle_hash"))
                for entry in self._entries.values()
            )
        )


@dataclass(frozen=True, slots=True)
class WorkflowRunResult:
    run_id: str
    status: WorkflowRunStatus
    output: object | None = None
    error: dict[str, object] | None = None
    recovery_action: str | None = None


def _domain_terminal_from_output(
    output: object | None,
) -> tuple[WorkflowRunStatus, dict[str, object] | None, str | None]:
    """Map an allowlisted graph domain terminal into the durable run state."""

    if not isinstance(output, Mapping):
        return WorkflowRunStatus.COMPLETED, None, None
    values = output.get("values")
    if not isinstance(values, Mapping):
        return WorkflowRunStatus.COMPLETED, None, None
    domain_status = str(values.get("terminal_status") or "").strip().lower()
    if domain_status in {"", "success", "completed"}:
        return WorkflowRunStatus.COMPLETED, None, None
    if domain_status in {"cancelled", "canceled"}:
        return WorkflowRunStatus.CANCELLED, None, None
    if domain_status != "error":
        return (
            WorkflowRunStatus.FAILED,
            {"code": "invalid_domain_terminal_status", "message": "任务返回了无效的终态。"},
            "请重试任务；若问题持续，请查看任务 Trace。",
        )

    raw_error = values.get("terminal_error")
    if isinstance(raw_error, Mapping):
        code = str(raw_error.get("code") or "workflow_domain_error")[:80]
        message = str(raw_error.get("user_message") or "任务未能完成。")[:500]
        recovery = str(raw_error.get("recovery_action") or "调整输入后重试。")[:500]
    else:
        code = "workflow_domain_error"
        message = "任务未能完成。"
        recovery = "调整输入后重试。"
    return WorkflowRunStatus.FAILED, {"code": code, "message": message}, recovery


class WorkflowRunner:
    """Coordinates registry lookup, lease ownership, graph calls, and recovery."""

    def __init__(
        self,
        store: WorkflowRunStore,
        saver: NativeCheckpointStore,
        registry: WorkflowRegistry,
        *,
        owner: str | None = None,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        lease_ttl: float = LEASE_TTL_SECONDS,
        clock=time.time,
        sleep=asyncio.sleep,
        trace_store: TraceStore | None = None,
        execution_ports: WorkflowExecutionPorts | None = None,
    ) -> None:
        if store.path != saver.path:
            raise ValueError("workflow runner store and saver must use the same SQLite database")
        self.store = store
        self.saver = saver
        self.registry = registry
        self.owner = owner or f"runner-{uuid.uuid4().hex}"
        self._clock = clock
        self._leases = LeaseManager(
            store,
            owner=self.owner,
            heartbeat_interval=heartbeat_interval,
            ttl_seconds=lease_ttl,
            sleep=sleep,
        )
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._replay = WorkflowReplay(store, saver, registry)
        self.trace_store = trace_store or TraceStore(store.path)
        self.execution_ports: WorkflowExecutionPorts | None = None
        self._owner_uow = SqliteExecutionUnitOfWork(store.path)
        if execution_ports is not None:
            self.configure_execution_ports(execution_ports)

    def configure_execution_ports(self, ports: WorkflowExecutionPorts) -> None:
        if self.execution_ports is not None and self.execution_ports is not ports:
            raise ValueError("workflow runner already has another execution port bundle")
        configure = getattr(self.saver, "configure_execution_adapter", None)
        if not callable(configure):
            raise ValueError("workflow saver cannot accept checkpoint execution ports")
        configure(ports.checkpoint)
        self.execution_ports = ports

    async def start(
        self,
        *,
        session_id: str,
        request_id: str,
        turn_id: str,
        workflow_name: str,
        workflow_version: str,
        capability_snapshot: Mapping[str, JsonValue],
        request_key: str | None = None,
        capability_hash: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_ns: str = "",
    ) -> str:
        entry = self.registry.require(workflow_name, workflow_version)
        manifest = entry.manifest
        snapshot = dict(capability_snapshot)
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        resolved_capability_hash = capability_hash or hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        resolved_request_key = request_key or ":".join(
            (session_id, request_id, turn_id, workflow_name)
        )
        created_run_id, _ = await self.store.create_run(
            request_key=resolved_request_key,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            manifest_hash=manifest_hash(manifest),
            implementation_hash=str(getattr(manifest, "implementation_bundle_hash")),
            capability_hash=resolved_capability_hash,
            capability_snapshot=snapshot,
            state_schema_version=int(getattr(manifest, "state_schema_version")),
            run_id=run_id,
            trace_id=trace_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
        )
        return created_run_id

    async def run(
        self,
        run_id: str,
        state: object,
        context: WorkflowContext | None = None,
    ) -> WorkflowRunResult:
        if await self._owner_uow.get_execution_owner(run_id) is not None:
            raise RuntimeError("legacy workflow run rejects an execution-owned run")
        return await self._execute(run_id, state=state, responses=None, context=context)

    async def run_precreated(
        self,
        run_id: str,
        state: object,
        context: WorkflowContext | None = None,
        *, active_lease: ActiveLease | None = None,
    ) -> WorkflowRunResult:
        """Drive an already-atomic generic/workflow start without creating an id."""

        await self._require_precreated(run_id)
        return await self._execute(
            run_id,
            state=state,
            responses=None,
            context=context,
            precreated=True,
            active_lease=active_lease,
        )

    async def resume(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
        context: WorkflowContext | None = None,
    ) -> WorkflowRunResult:
        if not responses:
            raise ValueError("workflow resume requires at least one response")
        if await self._owner_uow.get_execution_owner(run_id) is not None:
            raise RuntimeError("legacy workflow resume rejects an execution-owned run")
        lock = self._run_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            row = await self._require_run(run_id)
            if row["status"] == WorkflowRunStatus.WAITING.value:
                await transition_run(
                    self.store,
                    run_id,
                    WorkflowRunStatus.RETRYABLE,
                    expected_version=int(row["run_version"]),
                    allowed_statuses=(WorkflowRunStatus.WAITING,),
                    recovery_action="resume",
                    clock=self._clock,
                )
        return await self._execute(run_id, state=None, responses=dict(responses), context=context)

    async def resume_precreated(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
        context: WorkflowContext | None = None,
        *, active_lease: ActiveLease | None = None,
    ) -> WorkflowRunResult:
        if not responses:
            raise ValueError("workflow resume requires at least one response")
        await self._require_precreated(run_id)
        lock = self._run_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            row = await self._require_run(run_id)
            if row["status"] == WorkflowRunStatus.WAITING.value:
                await transition_run(
                    self.store,
                    run_id,
                    WorkflowRunStatus.RETRYABLE,
                    expected_version=int(row["run_version"]),
                    allowed_statuses=(WorkflowRunStatus.WAITING,),
                    recovery_action="resume",
                    clock=self._clock,
                )
        return await self._execute(
            run_id,
            state=None,
            responses=dict(responses),
            context=context,
            precreated=True,
            active_lease=active_lease,
        )

    async def claim_execution_recovery(self, run_id: str, recovery_lease: RecoveryLease) -> ActiveLease:
        if self.execution_ports is None:
            raise RuntimeError("execution recovery requires configured execution ports")
        handoff = await self.execution_ports.unit_of_work.claim_workflow_recovery_handoff(
            recovery_lease, workflow_owner=self.owner, ttl_seconds=self._leases.ttl_seconds)
        if handoff.run_id != run_id:
            raise StaleRunFence(f"execution handoff names another run: {run_id}")
        return ActiveLease(handoff, self._leases.heartbeat_interval, self._leases.ttl_seconds)

    async def request_cancel_precreated(
        self, run_id: str, reason: str = "user"
    ) -> dict[str, Any]:
        """Request cancel through the generic UoW; settlement remains checkpoint-owned."""

        execution = await self._require_precreated(run_id)
        assert self.execution_ports is not None
        if str(execution["status"]) == "cancel_requested":
            native = await self._require_run(run_id)
            native_status = WorkflowRunStatus(str(native["status"]))
            if (
                native_status not in TERMINAL_RUN_STATUSES
                and native_status
                not in {
                    WorkflowRunStatus.CANCEL_REQUESTED,
                    WorkflowRunStatus.CANCELLING,
                }
            ):
                await self.store.request_cancel(run_id, reason)
            return await self._converge_cancel(run_id)
        event = RunEventCandidate(
            event_key="run:cancel_requested",
            kind="workflow.cancel_requested",
            status=OutcomeStatus.CANCEL_REQUESTED,
            driver_kind="workflow",
            correlation={"run_id": run_id},
            payload={"reason": str(reason)},
        )
        db = await self.store._connect()
        try:
            ref = await (
                await db.execute(
                    """SELECT session_id FROM workflow_session_refs WHERE run_id=?
                    AND session_kind='delivery' AND deleted_at IS NULL""",
                    (run_id,),
                )
            ).fetchone()
        finally:
            await db.close()
        deliveries = (
            (
                DeliverySpec(
                    sink_kind="session_message",
                    sink_instance="workflow",
                    target_id=str(ref["session_id"]),
                    policy=DeliveryPolicy.DURABLE_REQUIRED,
                ),
                DeliverySpec(
                    sink_kind="websocket",
                    sink_instance="workflow",
                    target_id=str(ref["session_id"]),
                    policy=DeliveryPolicy.RETRY_WHILE_BOUND,
                ),
            )
            if ref is not None
            else ()
        )
        await self.execution_ports.unit_of_work.commit_run_outcome(
            run_id,
            expected_version=int(execution["version"]),
            cancel_reason=str(reason),
            event=event,
            deliveries=deliveries,
        )
        return await self._require_run(run_id)

    async def request_cancel(self, run_id: str, reason: str = "user") -> dict[str, Any]:
        # Cancellation must be able to invalidate a fence while a local graph
        # call owns the execution lock. The store transaction is the arbiter.
        row = await self._require_run(run_id)
        status = WorkflowRunStatus(row["status"])
        if status in TERMINAL_RUN_STATUSES:
            return row
        if status not in {WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.CANCELLING}:
            await self.store.request_cancel(run_id, reason)
        return await self._converge_cancel(run_id)

    async def cancel(self, run_id: str, reason: str = "user") -> dict[str, Any]:
        return await self.request_cancel(run_id, reason)

    async def recover_expired(self) -> list[RecoveryRecord]:
        """Scan all nonterminal runs and make only deterministic recovery moves."""

        records: list[RecoveryRecord] = []
        rows = await self.store.list_runs(limit=100_000)
        for stale in rows:
            if await self._owner_uow.get_execution_owner(str(stale["run_id"])) is not None:
                continue
            status = WorkflowRunStatus(stale["status"])
            if status in TERMINAL_RUN_STATUSES:
                continue
            record = await expire_stale_lease(self.store, stale, clock=self._clock)
            if record is not None:
                records.append(record)
                stale = await self._require_run(str(stale["run_id"]))
                status = WorkflowRunStatus(stale["status"])
            if status in {WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.CANCELLING}:
                before = status.value
                updated = await self._converge_cancel(str(stale["run_id"]))
                records.append(
                    RecoveryRecord(
                        str(stale["run_id"]),
                        before,
                        str(updated["status"]),
                        "reconcile_cancel",
                        "cancel_requested",
                    )
                )
                continue
            if status is WorkflowRunStatus.RUNNING:
                # A live, unexpired owner remains authoritative. Startup scans
                # may inspect it but must not mutate or replace its graph.
                continue
            try:
                self._registration_for_row(stale)
            except WorkflowDependencyUnavailable as exc:
                updated = await self._block_run(stale, map_workflow_failure(exc))
                records.append(
                    RecoveryRecord(
                        str(stale["run_id"]),
                        status.value,
                        str(updated["status"]),
                        "restore_graph_version_or_fork",
                        "graph_version_unavailable",
                    )
                )
                continue
            projection_record = await repair_head_projection(self.store, stale, clock=self._clock)
            if projection_record is not None:
                records.append(projection_record)
                stale = await self._require_run(str(stale["run_id"]))
                status = WorkflowRunStatus(stale["status"])
            checkpoint_failure = await self._validate_head_checkpoint(stale)
            if checkpoint_failure is not None:
                updated = await self._block_run(stale, checkpoint_failure)
                records.append(
                    RecoveryRecord(
                        str(stale["run_id"]),
                        status.value,
                        str(updated["status"]),
                        checkpoint_failure.recovery_action,
                        checkpoint_failure.reason,
                    )
                )
                continue
            if await self._has_uncertain_effect(str(stale["run_id"])):
                failure = FailureMapping(
                    WorkflowErrorCode.EFFECT_UNCERTAIN,
                    WorkflowRunStatus.BLOCKED,
                    "workflow_runner:effect_uncertain",
                    False,
                    "reconcile",
                    "effect_uncertain",
                )
                updated = await self._block_run(stale, failure)
                records.append(
                    RecoveryRecord(
                        str(stale["run_id"]),
                        status.value,
                        str(updated["status"]),
                        "reconcile",
                        "effect_uncertain",
                    )
                )
                continue
            if await self._has_succeeded_pending(str(stale["run_id"])):
                records.append(
                    RecoveryRecord(
                        str(stale["run_id"]),
                        status.value,
                        status.value,
                        "resume_pending_checkpoint",
                        "succeeded_pending",
                    )
                )
        return records

    async def get_state_history(self, run_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        return await self._replay.history(run_id, limit=limit)

    async def history(self, run_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        return await self.get_state_history(run_id, limit=limit)

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
        return await self._replay.fork_checkpoint(
            run_id=run_id,
            checkpoint_id=checkpoint_id,
            expected_version=expected_version,
            state_patch=state_patch,
            fork_key=fork_key,
            confirm_dangerous_effects=confirm_dangerous_effects,
        )

    fork = fork_checkpoint

    async def _execute(
        self,
        run_id: str,
        *,
        state: object,
        responses: Mapping[str, JsonValue] | None,
        context: WorkflowContext | None,
        precreated: bool = False,
        active_lease: ActiveLease | None = None,
    ) -> WorkflowRunResult:
        lock = self._run_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            row = await self._require_run(run_id)
            await self.trace_store.start_run(
                trace_id=str(row["trace_id"]),
                run_id=run_id,
                session_id=str(row["session_id"]),
                request_id=str(row["request_id"]) if row["request_id"] is not None else None,
                turn_id=str(row["turn_id"]) if row["turn_id"] is not None else None,
                kind="workflow",
                workflow_name=str(row["workflow_name"]),
                workflow_version=str(row["workflow_version"]),
            )
            await self.trace_store.reconcile_native_node_spans(run_id)
            status = WorkflowRunStatus(row["status"])
            if status in TERMINAL_RUN_STATUSES or status in {
                WorkflowRunStatus.WAITING,
                WorkflowRunStatus.BLOCKED,
                WorkflowRunStatus.CANCEL_REQUESTED,
                WorkflowRunStatus.CANCELLING,
            }:
                if (
                    not precreated
                    and status
                    in {WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.CANCELLING}
                ):
                    row = await self._converge_cancel(run_id)
                    status = WorkflowRunStatus(row["status"])
                if precreated and status in TERMINAL_RUN_STATUSES:
                    await self._assert_precreated_terminal(run_id, status)
                return self._result_from_row(row)
            try:
                registration = self._registration_for_row(row)
            except WorkflowDependencyUnavailable as exc:
                updated = await self._block_run(row, map_workflow_failure(exc))
                return self._result_from_row(updated)

            lease = active_lease or await self._leases.claim(run_id)
            if lease.fence.run_id != run_id or lease.fence.owner != self.owner:
                raise StaleRunFence(f"preclaimed lease is not owned by this runner: {run_id}")
            executable = registration.materialize(self.saver)
            logger.info(
                "workflow_native_execute engine_kind=deskpet-native run_id=%s workflow=%s@%s",
                run_id,
                row["workflow_name"],
                row["workflow_version"],
            )
            run_context = context or WorkflowContext(
                trace_id=str(row["trace_id"]),
                request_id=str(row["request_id"]) if row["request_id"] is not None else None,
                turn_id=str(row["turn_id"]) if row["turn_id"] is not None else None,
            )
            run_context = replace(
                run_context,
                ports={
                    **instrument_ports(run_context.ports, self.trace_store, row),
                    "observer": WorkflowExecutionObserver(
                        self.store,
                        self.trace_store,
                        trace_id=str(row["trace_id"]),
                    ),
                },
                trace_id=str(row["trace_id"]),
                request_id=str(row["request_id"]) if row["request_id"] is not None else None,
                turn_id=str(row["turn_id"]) if row["turn_id"] is not None else None,
            )
            configurable = dict(lease.configurable)
            try:
                if responses is None:
                    call = getattr(executable, "ainvoke")(
                        state,
                        run_context,
                        thread_id=str(row["thread_id"]),
                        run_id=run_id,
                        checkpoint_ns=str(row["checkpoint_ns"]),
                        configurable=configurable,
                    )
                else:
                    call = getattr(executable, "resume")(
                        responses,
                        run_context,
                        thread_id=str(row["thread_id"]),
                        run_id=run_id,
                        checkpoint_ns=str(row["checkpoint_ns"]),
                        configurable=configurable,
                    )
                output = await self._leases.run_with_heartbeat(lease, call)
                current = await self._require_run(run_id)
                result_error: dict[str, object] | None = None
                result_recovery: str | None = None
                if current["status"] == WorkflowRunStatus.RUNNING.value:
                    if precreated:
                        raise RuntimeError(
                            "precreated workflow returned without a checkpoint-owned terminal commit"
                        )
                    target_status, result_error, result_recovery = (
                        _domain_terminal_from_output(output)
                    )
                    current = await transition_run(
                        self.store,
                        run_id,
                        target_status,
                        fence=lease.fence,
                        allowed_statuses=(WorkflowRunStatus.RUNNING,),
                        error=result_error,
                        recovery_action=result_recovery,
                        clock=self._clock,
                    )
                else:
                    result_error = (
                        json.loads(str(current["error_json"]))
                        if current.get("error_json")
                        else None
                    )
                    result_recovery = (
                        str(current["recovery_action"])
                        if current.get("recovery_action")
                        else None
                    )
                if precreated and WorkflowRunStatus(current["status"]) in TERMINAL_RUN_STATUSES:
                    await self._assert_precreated_terminal(
                        run_id, WorkflowRunStatus(current["status"])
                    )
                await self.trace_store.finish_run(str(row["trace_id"]), str(current["status"]))
                return WorkflowRunResult(
                    run_id,
                    WorkflowRunStatus(current["status"]),
                    output,
                    error=result_error,
                    recovery_action=result_recovery,
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                if precreated:
                    current = await self._require_run(run_id)
                    current_status = WorkflowRunStatus(current["status"])
                    if current_status in {
                        WorkflowRunStatus.WAITING,
                        WorkflowRunStatus.RETRYABLE,
                        WorkflowRunStatus.CANCEL_REQUESTED,
                        WorkflowRunStatus.CANCELLING,
                    }:
                        return self._result_from_row(current)
                    raise
                logger.exception(
                    "workflow_native_execute_failed run_id=%s workflow=%s@%s "
                    "error_type=%s",
                    run_id,
                    row["workflow_name"],
                    row["workflow_version"],
                    type(exc).__name__,
                )
                current = await self._require_run(run_id)
                current_status = WorkflowRunStatus(current["status"])
                if current_status in {WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.CANCELLING}:
                    current = await self._converge_cancel(run_id)
                    return self._result_from_row(current)
                failure = map_workflow_failure(exc)
                try:
                    current = await transition_run(
                        self.store,
                        run_id,
                        failure.target_status,
                        fence=lease.fence,
                        allowed_statuses=(WorkflowRunStatus.RUNNING,),
                        error=failure.envelope(),
                        recovery_action=failure.recovery_action,
                        clock=self._clock,
                    )
                except StaleRunFence:
                    current = await self._require_run(run_id)
                if WorkflowRunStatus(current["status"]) in TERMINAL_RUN_STATUSES:
                    await self.trace_store.finish_run(
                        str(row["trace_id"]),
                        str(current["status"]),
                        error=current.get("error_json"),
                    )
                return self._result_from_row(current)

    async def _require_precreated(self, run_id: str) -> dict[str, Any]:
        if self.execution_ports is None:
            raise RuntimeError("run_precreated requires explicit WorkflowExecutionPorts")
        await self.store.initialize()
        db = await self.store._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT execution.*,workflow.workflow_name,workflow.workflow_version,
                    workflow.trace_id AS workflow_trace_id
                    FROM execution_runs AS execution
                    JOIN workflow_runs AS workflow ON workflow.run_id=execution.run_id
                    WHERE execution.run_id=?""",
                    (run_id,),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "precreated workflow requires execution_runs and workflow_runs with one id"
                )
            result = dict(row)
        finally:
            await db.close()
        if str(result["driver_kind"]) != "workflow":
            raise RuntimeError("precreated execution is not owned by the workflow driver")
        # ``profile_key`` belongs to the product router (for example
        # ``workflow.code_complex``); workflow_name/version belong to the
        # versioned Native manifest.  Their binding is already atomic in
        # start_workflow, so deriving one namespace from the other is invalid.
        if str(result["trace_id"]) != str(result["workflow_trace_id"]):
            raise RuntimeError("precreated execution and workflow trace ids differ")
        return result

    async def _assert_precreated_terminal(
        self, run_id: str, workflow_status: WorkflowRunStatus
    ) -> None:
        execution = await self._require_precreated(run_id)
        if str(execution["status"]) != workflow_status.value:
            raise RuntimeError(
                "workflow terminal commit did not atomically finalize the generic execution"
            )
        if not execution.get("terminal_event_id"):
            raise RuntimeError("generic terminal execution has no terminal event")

    def _registration_for_row(self, row: Mapping[str, Any]) -> RegisteredWorkflow:
        return self.registry.require(
            str(row["workflow_name"]),
            str(row["workflow_version"]),
            expected_manifest_hash=str(row["manifest_hash"]),
            expected_implementation_hash=str(row["implementation_hash"]),
        )

    async def _block_run(self, row: Mapping[str, Any], failure) -> dict[str, Any]:
        status = WorkflowRunStatus(row["status"])
        if status is WorkflowRunStatus.BLOCKED:
            return dict(row)
        if status in TERMINAL_RUN_STATUSES:
            return dict(row)
        return await transition_run(
            self.store,
            str(row["run_id"]),
            WorkflowRunStatus.BLOCKED,
            expected_version=int(row["run_version"]),
            allowed_statuses=(status,),
            error=failure.envelope(),
            recovery_action=failure.recovery_action,
            clock=self._clock,
        )

    async def _converge_cancel(self, run_id: str) -> dict[str, Any]:
        row = await self._require_run(run_id)
        status = WorkflowRunStatus(row["status"])
        if status in TERMINAL_RUN_STATUSES:
            return row
        active = await self._has_active_or_uncertain_effect(run_id)
        target = WorkflowRunStatus.CANCELLING if active else WorkflowRunStatus.CANCELLED
        if status is target:
            return row
        if status not in {WorkflowRunStatus.CANCEL_REQUESTED, WorkflowRunStatus.CANCELLING}:
            return row
        try:
            updated = await transition_run(
                self.store,
                run_id,
                target,
                expected_version=int(row["run_version"]),
                allowed_statuses=(status,),
                error={
                    "schema_version": 1,
                    "code": "cancelled",
                    "message_ref": "workflow_runner:cancelled",
                    "retryable": False,
                    "reason": "cancel_requested",
                },
                recovery_action="reconcile" if active else "none",
                clock=self._clock,
            )
        except StaleRunFence:
            updated = await self._require_run(run_id)
            if WorkflowRunStatus(updated["status"]) not in {
                target,
                WorkflowRunStatus.CANCEL_REQUESTED,
                WorkflowRunStatus.CANCELLING,
                WorkflowRunStatus.CANCELLED,
            }:
                raise
        if target is WorkflowRunStatus.CANCELLED:
            db = await self.store._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                now = self._clock()
                await db.execute(
                    """UPDATE workflow_node_attempts SET status='cancelled',ended_at=?
                    WHERE ended_at IS NULL AND node_execution_id IN
                    (SELECT node_execution_id FROM workflow_nodes WHERE run_id=?)""",
                    (now, run_id),
                )
                await db.execute(
                    """UPDATE workflow_nodes SET latest_status='cancelled',updated_at=?
                    WHERE run_id=? AND latest_status NOT IN ('succeeded','failed','cancelled')""",
                    (now, run_id),
                )
                await db.execute(
                    """UPDATE workflow_decisions SET status='cancelled',resolved_at=?
                    WHERE run_id=? AND status IN ('prepared','open')""",
                    (now, run_id),
                )
                await db.execute(
                    """UPDATE workflow_grants SET status='cancelled' WHERE decision_id IN
                    (SELECT decision_id FROM workflow_decisions WHERE run_id=?)
                    AND status NOT IN ('claimed','consumed','cancelled')""",
                    (run_id,),
                )
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return updated

    async def _has_active_or_uncertain_effect(self, run_id: str) -> bool:
        db = await self.store._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT 1 FROM workflow_effects WHERE run_id=?
                    AND status IN ('running','uncertain','late_orphan') LIMIT 1""",
                    (run_id,),
                )
            ).fetchone()
            return row is not None
        finally:
            await db.close()

    async def _has_uncertain_effect(self, run_id: str) -> bool:
        db = await self.store._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT 1 FROM workflow_effects WHERE run_id=? AND status IN ('uncertain','late_orphan') LIMIT 1",
                    (run_id,),
                )
            ).fetchone()
            return row is not None
        finally:
            await db.close()

    async def _has_succeeded_pending(self, run_id: str) -> bool:
        db = await self.store._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT 1 FROM workflow_nodes WHERE run_id=? AND latest_status='succeeded_pending' LIMIT 1",
                    (run_id,),
                )
            ).fetchone()
            return row is not None
        finally:
            await db.close()

    async def _validate_head_checkpoint(self, row: Mapping[str, Any]) -> FailureMapping | None:
        checkpoint_id = row.get("head_checkpoint_id")
        if not checkpoint_id:
            return None
        try:
            item = await self.saver.get_checkpoint(
                str(row["run_id"]),
                str(checkpoint_id),
                checkpoint_ns=str(row["head_checkpoint_ns"]),
            )
            if item is None:
                raise ValueError("checkpoint head is missing")
            return None
        except Exception:
            db = await self.store._connect()
            try:
                checkpoint = await (
                    await db.execute(
                        """SELECT checkpoint_blob,metadata_blob FROM workflow_checkpoints
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                        (row["thread_id"], row["head_checkpoint_ns"], checkpoint_id),
                    )
                ).fetchone()
            finally:
                await db.close()
            if checkpoint is not None:
                quarantine_checkpoint(
                    self.store.path,
                    run_id=str(row["run_id"]),
                    checkpoint_id=str(checkpoint_id),
                    checkpoint_blob=bytes(checkpoint["checkpoint_blob"]),
                    metadata_blob=bytes(checkpoint["metadata_blob"]),
                )
            return map_workflow_failure(
                WorkflowNodeError(
                    code=WorkflowErrorCode.CHECKPOINT_CORRUPT,
                    message_ref="workflow_runner:checkpoint_corrupt",
                )
            )

    async def _require_run(self, run_id: str) -> dict[str, Any]:
        row = await self.store.get_run(run_id)
        if row is None:
            raise KeyError(f"workflow run not found: {run_id}")
        return row

    @staticmethod
    def _result_from_row(row: Mapping[str, Any]) -> WorkflowRunResult:
        error = json.loads(row["error_json"]) if row.get("error_json") else None
        return WorkflowRunResult(
            str(row["run_id"]),
            WorkflowRunStatus(row["status"]),
            error=error,
            recovery_action=str(row["recovery_action"]) if row.get("recovery_action") else None,
        )


__all__ = [
    "RegisteredWorkflow",
    "WorkflowRegistry",
    "WorkflowRunResult",
    "WorkflowRunner",
    "manifest_hash",
]
