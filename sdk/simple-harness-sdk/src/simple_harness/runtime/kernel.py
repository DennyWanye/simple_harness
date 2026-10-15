# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Durable RunKernel lifecycle and fixed-root public client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Self, cast

from simple_harness.contracts import (
    ExecutionSessionId,
    FrozenJsonValue,
    HarnessError,
    JsonValue,
    RunId,
    canonical_json,
    thaw_json,
)
from simple_harness.execution.command_ingress import CommandClaim
from simple_harness.execution.context_staging import ContextStageKind, ContextStageRecord
from simple_harness.execution.contracts.children import AttachmentPolicy
from simple_harness.execution.delivery import DeliverySpec
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.memory_outbox import CommittedTurnSpec
from simple_harness.execution.runtime_audit import runtime_operation
from simple_harness.execution.sqlite import SqliteExecutionUnitOfWork
from simple_harness.execution.sqlite.stage_audit import begin_release, settle_release
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ContinuationRecord,
    ContinuationState,
    DecisionState,
    ExecutionLease,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
    UnitOfWorkNotFound,
)
from simple_harness.observability import CorrelationContext, Outcome
from simple_harness.providers import CancelToken
from simple_harness.tools.executor import ToolAuthorizationPending
from simple_harness.tools.sidecar import resource_digest
from simple_harness.workflow.runner import WorkflowRunner

from .agent_memory import CommittedTurn, MemoryScopeRef
from .agent_turn import AgentTurnOutcome
from .child_coordinator import ChildCoordinator
from .child_signal_runtime import ChildSignalRuntime
from .commands import (
    CommandError,
    CommandErrorCode,
    CommandState,
    ContinueCommandIntent,
    StartCommandIntent,
    command_intent_from_json,
)
from .conversation_memory import (
    ContextPreparationMode,
    ConversationContinuationInput,
    ConversationTurnInput,
    ConversationTurnOutput,
)
from .live_index import LiveRunIndex
from .orchestration import (
    RuntimeActivationClaim,
    RuntimeStartAdmission,
    RuntimeStartDispatchClaim,
    RuntimeStartDisposition,
    WorkflowSpawnReadyActivation,
)
from .start_snapshot import RunStart, StartSnapshot, bind_start_snapshot
from .terminal import TerminalCoordinator, ToolCatalogStale
from .workflow_spawn import WorkflowSpawnChildControlKind

if TYPE_CHECKING:
    from simple_harness.execution.base_agent import AgentTurnRecord
    from simple_harness.runtime.workflow_spawn import WorkflowSpawnToolOutcome
    from simple_harness.workflow.execution_ports import WorkflowRecoveryWork
    from simple_harness.workflow.runner import WorkflowRunner
from .kernel_domains.support import (
    BASE_AGENT_DRIVER_EXCEPTION_WAKES,
    BASE_AGENT_DRIVER_KIND,
    ROOT_PROFILE_KEY,
    WORKFLOW_DRIVER_KIND,
    DriverCancellationCoordinator,
    DriverCancellationRecovery,
    DriverCancelOutcome,
    DriverInvocation,
    DriverResult,
    ReactCheckpointPort,
    RunClient,
    RuntimeDriver,
    RuntimeLifecycleState,
    RuntimePorts,
    RuntimeProfile,
    RuntimeReconciliationPort,
    RuntimeServices,
    RuntimeUnitOfWork,
    ToolCatalogGenerationPort,
    WorkflowSpawnRuntimeCoordinator,
    _CanonicalWorkflowSpawnRuntimeCoordinator,
    _run_id,
    logger,
)


class Runtime:
    def __init__(
        self,
        *,
        uow: RuntimeUnitOfWork,
        profiles: Mapping[str, RuntimeProfile],
        drivers: Mapping[str, RuntimeDriver],
        workflow_runner: object | None,
        ports: RuntimePorts,
        root_profile_key: str,
        close_hook: Callable[[], None] | None = None,
        start_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
        async_close_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
    ) -> None:
        self._close_hook = close_hook
        self._start_hooks = tuple(start_hooks)
        self._async_close_hooks = tuple(async_close_hooks)
        self._started_hooks = 0
        if WORKFLOW_DRIVER_KIND in drivers:
            raise ValueError("workflow is an SDK-reserved driver key")
        workflow_profiles = tuple(
            item for item in profiles.values() if item.driver_kind == WORKFLOW_DRIVER_KIND
        )
        workflow_driver: RuntimeDriver | None = None
        workflow_spawn: WorkflowSpawnRuntimeCoordinator | None = None
        if workflow_runner is not None:
            # 按需导入：循环依赖（kernel ↔ workflow）
            from .drivers.workflow import (
                WorkflowRuntimeDriver,
                build_workflow_runtime_driver,
            )

            if type(workflow_runner) is not WorkflowRunner:
                raise TypeError("official workflow runner identity is invalid")
            if (
                workflow_runner.execution_ports.unit_of_work.transaction_owner
                is not uow.transaction_owner
            ):
                raise ValueError("workflow Runtime and Runner transaction owners differ")
            workflow_driver = build_workflow_runtime_driver(workflow_runner)
            if type(workflow_driver) is not WorkflowRuntimeDriver:
                raise TypeError("official workflow driver factory is invalid")
            workflow_spawn = cast(
                WorkflowSpawnRuntimeCoordinator,
                _CanonicalWorkflowSpawnRuntimeCoordinator(
                    uow=uow,
                    runner=workflow_runner,
                    owner_id=ports.owner_id,
                    lease_ttl_seconds=ports.lease_ttl_seconds,
                    clock=ports.clock,
                ),
            )
        if workflow_profiles and workflow_driver is None:
            raise ValueError("workflow profile requires the SDK-owned workflow driver")
        self._uow = uow
        self._workflow_runner = workflow_runner
        self._profiles = dict(profiles)
        self._drivers = dict(drivers)
        self._cancellation_coordinators: dict[str, DriverCancellationCoordinator] = {}
        if workflow_driver is not None:
            self._drivers[WORKFLOW_DRIVER_KIND] = workflow_driver
            self._cancellation_coordinators[WORKFLOW_DRIVER_KIND] = workflow_driver  # type: ignore[assignment]
        self._ports = ports
        self._root_profile_key = root_profile_key
        self._terminal = TerminalCoordinator(uow)
        self._services = RuntimeServices(
            operation_audit=self._uow,
            operation_audit_clock=self._now,
            provider=ports.provider,
            tools=ports.tools,
            authorization=ports.authorization,
            context=ports.context,
            delivery=ports.delivery,
            tool_reconciliation=ports.tool_reconciliation,
            reconciliation=ports.reconciliation,
            provider_reconciliation=ports.provider_reconciliation,
            react_checkpoint=ports.react_checkpoint,
            tool_catalog=ports.tool_catalog,
            workflow_spawn=workflow_spawn,
            run_context_authority=ports.run_context_authority,
            runtime_decision_sink=ports.runtime_decision_sink,
            task_execution_authority=ports.task_execution_authority,
        )
        self._live = LiveRunIndex()
        self._leases: dict[str, ExecutionLease] = {}
        self._fences: dict[str, RunFenceLease] = {}
        self._cancels: dict[str, CancelToken] = {}
        self._heartbeats: dict[str, asyncio.Task[None]] = {}
        # A lost epoch owns its driver cancellation/join until cleanup finishes.
        # A concurrent wake must not install authority that the old heartbeat
        # would then remove by run_id.
        self._retiring_leases: set[str] = set()
        self._pending_wakes: set[str] = set()
        # BaseAgent driver exceptions re-wake the Run a bounded number of times per
        # process (review K1); the counter resets when a drive returns normally.
        self._driver_exception_wakes: dict[str, int] = {}
        self._workflow_spawn_ready_activations: dict[str, WorkflowSpawnReadyActivation] = {}
        self._workflow_start_dispatches: dict[str, RuntimeStartDispatchClaim] = {}
        self._workflow_recovery_work: dict[str, WorkflowRecoveryWork] = {}
        self._child_signals = ChildSignalRuntime(uow, owner_id=ports.owner_id)
        self._child_signal_wait_handoffs: dict[str, object] = {}
        self._wake_drain_task: asyncio.Task[None] | None = None
        self._command_pump_task: asyncio.Task[None] | None = None
        self._command_wake = asyncio.Event()
        self._delivery_pump_task: asyncio.Task[None] | None = None
        self._delivery_wake = asyncio.Event()
        self._memory_pump_task: asyncio.Task[None] | None = None
        self._memory_wake = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._startup_task: asyncio.Task[None] | None = None
        self._state = RuntimeLifecycleState.NEW
        self._started = False
        self._closing = False
        self._production_authorities: object | None = None
        self._observability: object | None = None
        self.client = RunClient(self)
        self.children = ChildCoordinator(self)

    def _reserve_legacy_start(self, start: RunStart) -> None:
        self._uow.reserve_legacy_run_mode(
            run_id=start.run_id.value,
            intent_hash=hashlib.sha256(
                canonical_json(
                    {
                        "execution_session_id": start.execution_session_id.value,
                        "run_id": start.run_id.value,
                        "request_id": start.request_id.value,
                        "turn_id": start.turn_id,
                        "input": thaw_json(cast(FrozenJsonValue, start.input)),
                        "tool_catalog_generation": start.tool_catalog_generation,
                        "conversation": (
                            None if start.conversation is None else start.conversation.to_json()
                        ),
                        "initial_route_receipt": (
                            None
                            if start.initial_route_receipt is None
                            else start.initial_route_receipt.to_json()
                        ),
                        "initial_route_receipt_hash": start.initial_route_receipt_hash,
                    }
                ).encode()
            ).hexdigest(),
            now=self._now(),
        )

    def _reserve_base_agent_start(self, start: RunStart) -> None:
        self._uow.reserve_base_agent_run_mode(
            run_id=start.run_id.value,
            intent_hash=hashlib.sha256(
                canonical_json(
                    {
                        "api_mode": "base_agent_v1",
                        "execution_session_id": start.execution_session_id.value,
                        "run_id": start.run_id.value,
                        "request_id": start.request_id.value,
                        "turn_id": start.turn_id,
                        "input": thaw_json(cast(FrozenJsonValue, start.input)),
                        "tool_catalog_generation": start.tool_catalog_generation,
                    }
                ).encode()
            ).hexdigest(),
            now=self._now(),
        )

    async def start_base_agent_run(self, start: RunStart) -> RunRecord:
        """Public BaseAgent creation entry: fence the identity first, then start the Run.

        Same shape as ``RunClient.start_conversation`` (reserve mode → ``_start_run``),
        but under the ``base-agent/v1`` namespace so every legacy public Run API
        rejects this identity and vice versa.
        """

        self._require_started()
        if not isinstance(start, RunStart):
            raise TypeError("start must use RunStart")
        if start.start_mode != "ordinary" or start.conversation is not None:
            raise ValueError("BaseAgent Runs use the ordinary start mode without envelopes")
        self._reserve_base_agent_start(start)
        return await self._start_run(start)

    async def signal_base_agent_input(
        self,
        run_id: RunId,
        *,
        agent_id: str,
        turn_id: str,
        input_id: str,
        input_hash: str,
        input_json: Mapping[str, JsonValue],
        message: Mapping[str, JsonValue],
        max_pending_inputs: int | None = None,
    ) -> AgentTurnRecord:
        """Public BaseAgent input entry (mirror of ``signal_conversation`` for base-agent Runs).

        One short transaction persists the queued turn row and its
        ``base_agent_input`` continuation; the Run is then woken.  Legacy and
        unmanaged Runs are rejected with ``RUN_MODE_CONFLICT``.
        """

        self._require_started()
        value = _run_id(run_id)
        self._uow.require_base_agent_run_mode(value)
        run = self._uow.read_run(value)
        if run is None:
            raise KeyError(run_id.value)
        if run.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            raise UnitOfWorkConflict("terminal Run rejects new continuations")
        record = self._uow.submit_agent_input(
            agent_id=agent_id,
            run_id=value,
            turn_id=turn_id,
            input_id=input_id,
            input_hash=input_hash,
            input_json=input_json,
            continuation_payload={
                "kind": "base_agent_input",
                "agent_id": agent_id,
                "turn_id": turn_id,
                "input_id": input_id,
                "input_hash": input_hash,
                "message": dict(message),
            },
            now=self._now(),
            max_pending_inputs=max_pending_inputs,
        )
        asyncio.create_task(self._wake_continuation(value))
        return record

    def _require_legacy_mode(self, run_id: str) -> None:
        require = getattr(self._uow, "require_legacy_or_unmanaged_run", None)
        if callable(require):
            require(run_id)

    def _emit_transition(
        self,
        event_name: str,
        *,
        run_id: str,
        outcome: Outcome,
        operation: str,
        request_id: str | None = None,
        execution_session_id: str | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        observability = self._ports.observability
        if observability is None:
            return
        observability.emit_transition(
            event_name,
            component="runtime",
            operation=operation,
            outcome=outcome,
            correlation=CorrelationContext.from_authority_ids(
                run_id=run_id,
                request_id=request_id,
                execution_session_id=execution_session_id,
                operation_id=operation,
            ),
            attributes={} if attributes is None else attributes,
        )

    @property
    def state(self) -> RuntimeLifecycleState:
        return self._state

    @property
    def production_authorities(self) -> object | None:
        """Explicit 0.1.5+ authorities retained by production composition."""

        return self._production_authorities

    @property
    def observability(self) -> object | None:
        """Optional Host-injected observability composition object."""

        return self._observability

    def diagnostics_snapshot(self) -> dict[str, object]:
        """Return the stable observability snapshot base without business payloads."""

        if self._observability is None:
            return {
                "schema_version": 1,
                "sdk_version": "unconfigured",
                "lifecycle": "disabled",
                "health": "healthy",
                "queue_depth": 0,
                "queue_capacity": 0,
                "counters": {},
                "active_runs": len(self._live.active_run_ids()),
                "authorities": self._authority_diagnostics_snapshot(),
            }
        snapshot = getattr(self._observability, "diagnostics_snapshot", None)
        if not callable(snapshot):
            return {
                "schema_version": 1,
                "sdk_version": "unknown",
                "lifecycle": "degraded",
                "health": "degraded",
                "queue_depth": 0,
                "queue_capacity": 0,
                "counters": {},
                "active_runs": len(self._live.active_run_ids()),
                "authorities": self._degraded_authority_snapshot(
                    "observability_snapshot_unavailable"
                ),
            }
        try:
            value = snapshot()
            result = dict(value)
            result["active_runs"] = len(self._live.active_run_ids())
            result["authorities"] = self._authority_diagnostics_snapshot()
            return result
        except BaseException:
            return {
                "schema_version": 1,
                "sdk_version": "unknown",
                "lifecycle": "degraded",
                "health": "degraded",
                "queue_depth": 0,
                "queue_capacity": 0,
                "counters": {},
                "active_runs": 0,
                "authorities": self._degraded_authority_snapshot("snapshot_failed"),
            }

    @staticmethod
    def _degraded_authority_snapshot(error_code: str) -> dict[str, object]:
        empty: dict[str, object] = {
            "health": "degraded",
            "counts": {},
            "oldest_age_ms": None,
        }
        return {
            "health": "degraded",
            "error_code": error_code,
            "commands": dict(empty),
            "context": dict(empty),
            "outbox": dict(empty),
            "recovery": dict(empty),
            "recent_error_codes": {},
        }

    def _authority_diagnostics_snapshot(self) -> dict[str, object]:
        staging = self._ports.context_staging
        owner = self._uow.transaction_owner
        try:
            database_connection = getattr(owner, "connection", None)
        except BaseException:
            return self._degraded_authority_snapshot("authority_query_failed")
        command_rows = (
            []
            if database_connection is None
            else database_connection.execute(
                "SELECT state,COUNT(*),MIN(created_at) FROM conversation_commands GROUP BY state"
            ).fetchall()
        )
        command_counts = {str(row[0]): int(row[1]) for row in command_rows[:16]}
        command_oldest = min(
            (float(row[2]) for row in command_rows[:16] if row[2] is not None),
            default=None,
        )
        commands = {
            "health": "healthy",
            "counts": command_counts,
            "oldest_age_ms": (
                None
                if command_oldest is None
                else max(0, int((self._now() - command_oldest) * 1000))
            ),
        }
        if staging is None:
            return {
                "health": "healthy",
                "commands": commands,
                "context": {"health": "disabled", "counts": {}, "oldest_age_ms": None},
                "outbox": {"health": "disabled", "counts": {}, "oldest_age_ms": None},
                "recovery": {"health": "disabled", "counts": {}, "oldest_age_ms": None},
                "recent_error_codes": {},
            }
        started = time.monotonic()
        try:
            connection = staging.database.connection
            now = self._now()

            def group(table: str, state: str, created: str) -> dict[str, object]:
                rows = connection.execute(
                    f"SELECT {state},COUNT(*),MIN({created}) FROM {table} GROUP BY {state}"
                ).fetchall()
                counts = {str(row[0]): int(row[1]) for row in rows[:16]}
                oldest = min(
                    (float(row[2]) for row in rows[:16] if row[2] is not None),
                    default=None,
                )
                return {
                    "health": "healthy",
                    "counts": counts,
                    "oldest_age_ms": (
                        None if oldest is None else max(0, int((now - oldest) * 1000))
                    ),
                }

            context = group("context_preparation_staging", "state", "created_at")
            outbox = group("memory_outbox", "state", "created_at")
            recovery_rows = connection.execute(
                "SELECT CASE WHEN resolved_at IS NULL THEN 'observed' "
                "WHEN wake_consumed=0 THEN 'resolved' ELSE 'consumed' END AS status,"
                "COUNT(*),MIN(created_at) FROM run_wait_blockers GROUP BY status"
            ).fetchall()
            recovery_counts = {str(row[0]): int(row[1]) for row in recovery_rows[:8]}
            recovery_oldest = min(
                (float(row[2]) for row in recovery_rows[:8] if row[2] is not None),
                default=None,
            )
            errors = connection.execute(
                "SELECT error_code,COUNT(*) FROM ("
                "SELECT error_code FROM memory_outbox WHERE error_code IS NOT NULL UNION ALL "
                "SELECT error_code FROM context_preparation_staging WHERE error_code IS NOT NULL"
                ") GROUP BY error_code ORDER BY COUNT(*) DESC,error_code LIMIT 20"
            ).fetchall()
            if time.monotonic() - started > 0.25:
                return self._degraded_authority_snapshot("snapshot_deadline")
            return {
                "health": "healthy",
                "commands": commands,
                "context": context,
                "outbox": outbox,
                "recovery": {
                    "health": "healthy",
                    "counts": recovery_counts,
                    "oldest_age_ms": (
                        None
                        if recovery_oldest is None
                        else max(0, int((now - recovery_oldest) * 1000))
                    ),
                },
                "recent_error_codes": {str(row[0]): int(row[1]) for row in errors},
            }
        except BaseException:
            return self._degraded_authority_snapshot("authority_query_failed")

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state is RuntimeLifecycleState.READY:
                return
            if self._state in {
                RuntimeLifecycleState.CLOSING,
                RuntimeLifecycleState.CLOSED,
                RuntimeLifecycleState.FAILED,
            }:
                raise RuntimeError(f"Runtime cannot start from {self._state.value}")
            if self._state is RuntimeLifecycleState.NEW:
                self._state = RuntimeLifecycleState.STARTING
                self._startup_task = asyncio.create_task(
                    self._start_once(), name="simple-harness-startup"
                )
            startup = self._startup_task
        assert startup is not None
        await asyncio.shield(startup)

    async def _start_once(self) -> None:
        try:
            validate_context_use = getattr(self._uow, "validate_context_use_recovery", None)
            if validate_context_use is not None:
                validate_context_use()
            await self._ports.reconciliation.reconcile()
            await self._drain_commands_bounded(100)
            await self.recover(_startup=True)
            await self._drain_resolved_waits_once()
            await self._drain_deliveries_bounded(100)
            await self._drain_memory_bounded(100)
            await self._drain_recall_releases(100)
            for hook in self._start_hooks:
                await hook()
                self._started_hooks += 1
            async with self._lifecycle_lock:
                if self._state is not RuntimeLifecycleState.STARTING:
                    raise asyncio.CancelledError
                self._wake_drain_task = asyncio.create_task(
                    self._wake_drain(), name="simple-harness-wake-drain"
                )
                self._command_pump_task = asyncio.create_task(
                    self._command_pump(), name="simple-harness-command-pump"
                )
                self._delivery_pump_task = asyncio.create_task(
                    self._delivery_pump(), name="simple-harness-delivery-pump"
                )
                if self._ports.memory_dispatcher is not None:
                    self._memory_pump_task = asyncio.create_task(
                        self._memory_pump(), name="simple-harness-memory-pump"
                    )
                self._started = True
                self._closing = False
                self._state = RuntimeLifecycleState.READY
        except asyncio.CancelledError:
            raise
        except BaseException:
            async with self._lifecycle_lock:
                if self._state is RuntimeLifecycleState.STARTING:
                    self._state = RuntimeLifecycleState.FAILED
            await self._stop_background_tasks()
            raise

    async def recover(self, *, _startup: bool = False) -> None:
        if not _startup:
            self._require_started()
        root_runs = self._uow.list_recoverable_root_runs()
        child_runs = self._uow.list_recoverable_child_runs()
        # BaseAgent Runs rest in WAITING between turns, so queued / running /
        # result_pending turns must be woken explicitly (finalize-first in _drive).
        recovered_ids = {run.run_id for run in (*root_runs, *child_runs)}
        for run_id in self._uow.list_runs_with_open_agent_turns():
            if run_id in recovered_ids or run_id in self._live.active_run_ids():
                continue
            await self._wake_continuation(run_id)
        logger.info(
            "reconcile.recovered",
            extra={"roots": len(root_runs), "children": len(child_runs)},
        )
        for run in (*root_runs, *child_runs):
            if run.run_id in self._live.active_run_ids():
                continue
            self._emit_transition(
                "recovery.observed_state",
                run_id=run.run_id,
                outcome=Outcome.STARTED,
                operation="recovery",
                attributes={
                    "entity_kind": "run",
                    "entity_id": run.run_id,
                    "to_state": run.state.value,
                    "replayed": True,
                    "history_complete": False,
                    "state_version": run.version,
                },
            )
            retry_on_conflict = False
            try:
                prior_ready = await self._uow.run_atomic(
                    lambda tx, run_id=run.run_id: self._uow.read_spawn_ready_activation(  # type: ignore[misc]
                        tx, run_id
                    ),
                    fault_label="runtime:spawn_ready:read",
                )
                if prior_ready is None:
                    if (
                        run.driver_kind == BASE_AGENT_DRIVER_KIND
                        and self._uow.read_pending_agent_turn(run.run_id) is not None
                    ):
                        # BaseAgent RESULT_PENDING: finalize first, never re-drive.
                        retry_on_conflict = True  # a later drive also finalizes first
                        await self._activate(run.run_id)
                        await self._finalize_pending_agent_turn(run.run_id)
                        activated = self._uow.read_run(run.run_id)
                        if activated is None:
                            raise UnitOfWorkConflict("BaseAgent Run disappeared during recovery")
                    elif (
                        run.parent_run_id is not None
                        and run.driver_kind == WORKFLOW_DRIVER_KIND
                        and self._uow.is_workflow_spawn_child(run.run_id)
                    ):
                        admission = await self._uow.run_atomic(
                            lambda tx, run_id=run.run_id: self._uow.resume_spawn_child_start(  # type: ignore[misc]
                                tx,
                                run_id,
                                RuntimeActivationClaim(
                                    self._ports.owner_id,
                                    lease_ttl_seconds=(self._ports.lease_ttl_seconds),
                                ),
                                now=self._now(),
                            ),
                            fault_label="runtime:spawn_child:resume",
                        )
                        if not self._register_recovered_spawn_child(admission):
                            continue
                        activated = self._uow.read_run(run.run_id)
                        if activated is None:
                            raise UnitOfWorkConflict("workflow spawn child Run disappeared")
                    else:
                        retry_on_conflict = True
                        activated = await self._activate(run.run_id)
                else:
                    ready_activation = await self._uow.run_atomic(
                        lambda tx, prior=prior_ready: self._uow.reclaim_spawn_ready_activation(  # type: ignore[misc]
                            tx,
                            prior,
                            self._ports.owner_id,
                            now=self._now(),
                            ttl_seconds=self._ports.lease_ttl_seconds,
                        ),
                        fault_label="runtime:spawn_ready:reclaim",
                    )
                    self._register_spawn_ready_activation(ready_activation)
                    activated = self._uow.read_run(run.run_id)
                    if activated is None:
                        raise UnitOfWorkConflict("workflow spawn parent Run disappeared")
            except UnitOfWorkConflict:
                if retry_on_conflict:
                    # The previous owner's lease has not expired yet (a quick restart
                    # after a crash).  Keep the wake so the drain loop takes the Run
                    # over once it expires; startup recovery runs only once.
                    self._pending_wakes.add(run.run_id)
                continue
            if activated.state is RunState.CANCEL_REQUESTED:
                await self._terminalize_cancelled(activated, reason="startup_recovery")
            else:
                self._schedule(activated.run_id)
            self._emit_transition(
                "recovery.resolved",
                run_id=run.run_id,
                outcome=Outcome.SUCCEEDED,
                operation="recovery",
                attributes={
                    "entity_kind": "run",
                    "entity_id": run.run_id,
                    "recovery_result": "scheduled",
                    "history_complete": False,
                    "state_version": activated.version,
                },
            )

    async def reconcile(self) -> None:
        self._require_started()
        await self._ports.reconciliation.reconcile()
        await self._drain_resolved_waits_once()
        await self.recover()

    async def dispatch_deliveries_once(self) -> bool:
        self._require_started()
        return await self._ports.delivery.run_once()

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._state is RuntimeLifecycleState.CLOSED:
                return
            if self._state is RuntimeLifecycleState.NEW:
                self._state = RuntimeLifecycleState.CLOSED
                for hook in reversed(self._async_close_hooks):
                    await hook()
                if self._ports.memory_dispatcher is not None:
                    await self._ports.memory_dispatcher.close()
                if self._close_hook is not None:
                    self._close_hook()
                return
            self._state = RuntimeLifecycleState.CLOSING
            self._closing = True
            startup = self._startup_task
        if startup is not None and not startup.done():
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        await self._drain_deliveries_bounded(100)
        await self._drain_memory_bounded(100)
        await self._stop_background_tasks()
        for token in self._cancels.values():
            token.cancel()
        await self._live.close(timeout_seconds=self._ports.close_timeout_seconds)
        heartbeat_tasks = tuple(task for task in self._heartbeats.values() if not task.done())
        for task in heartbeat_tasks:
            task.cancel()
        if heartbeat_tasks:
            await asyncio.gather(*heartbeat_tasks, return_exceptions=True)
        self._heartbeats.clear()
        now = self._now()
        for run_id, fence in tuple(self._fences.items()):
            try:
                await self._uow.release(fence)
            except UnitOfWorkConflict:
                pass
            self._fences.pop(run_id, None)
        for run_id, lease in tuple(self._leases.items()):
            try:
                self._uow.release_runtime_lease(lease, now=now)
            except UnitOfWorkConflict:
                pass
            self._leases.pop(run_id, None)
        self._cancels.clear()
        self._workflow_spawn_ready_activations.clear()
        self._workflow_start_dispatches.clear()
        self._workflow_recovery_work.clear()
        self._started = False
        for hook in reversed(self._async_close_hooks):
            await hook()
        self._started_hooks = 0
        if self._ports.memory_dispatcher is not None:
            await self._ports.memory_dispatcher.close()
        if self._close_hook is not None:
            self._close_hook()
        async with self._lifecycle_lock:
            self._state = RuntimeLifecycleState.CLOSED

    async def _stop_background_tasks(self) -> None:
        tasks = tuple(
            task
            for task in (
                self._wake_drain_task,
                self._command_pump_task,
                self._delivery_pump_task,
                self._memory_pump_task,
            )
            if task is not None and not task.done()
        )
        self._wake_drain_task = None
        self._command_pump_task = None
        self._delivery_pump_task = None
        self._memory_pump_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wake_drain(self) -> None:
        interval = min(0.05, max(0.001, self._ports.lease_ttl_seconds / 3.0))
        try:
            while self._state is RuntimeLifecycleState.READY:
                await self._drain_resolved_waits_once()
                for run_id in tuple(self._pending_wakes):
                    if run_id in self._leases:
                        self._pending_wakes.discard(run_id)
                        continue
                    if run_id in self._live.active_run_ids():
                        # The previous drive is still unwinding (e.g. a BaseAgent
                        # driver exception dropping its authority): keep the wake for
                        # the next tick instead of losing it.
                        continue
                    await self._wake_continuation(run_id)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    async def _drain_commands_bounded(self, limit: int) -> bool:
        for _ in range(limit):
            claim = self._uow.claim_next_command(
                owner_id=self._ports.owner_id,
                now=self._now(),
                lease_seconds=self._ports.lease_ttl_seconds,
            )
            if claim is None:
                return True
            heartbeat = asyncio.create_task(
                self._command_heartbeat(claim),
                name=f"simple-harness-command-heartbeat:{claim.receipt.command_id}",
            )
            try:
                await self._process_command(claim)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                try:
                    current = self._uow.get_command_receipt(claim.receipt.command_id)
                    if current.state.terminal:
                        continue
                    definite = isinstance(
                        error, (TypeError, ValueError, HarnessError, CommandError)
                    )
                    if definite or claim.attempt_count >= 8:
                        self._uow.reject_command(
                            claim,
                            error_code=(
                                CommandErrorCode.PERMANENT_FAILURE
                                if definite
                                else CommandErrorCode.RETRY_EXHAUSTED
                            ),
                            now=self._now(),
                        )
                    else:
                        self._uow.retry_command(
                            claim,
                            error_code=CommandErrorCode.TRANSIENT_FAILURE.value,
                            retry_at=self._now() + min(60.0, 2.0**claim.attempt_count),
                            now=self._now(),
                        )
                except CommandError as settlement_error:
                    if settlement_error.code is not CommandErrorCode.INTENT_CONFLICT:
                        logger.warning(
                            "command.settlement_failed",
                            extra={
                                "command_id": claim.receipt.command_id,
                                "run_id": claim.receipt.run_id.value,
                                "error_code": settlement_error.code.value,
                            },
                        )
                except Exception:
                    logger.warning(
                        "command.settlement_failed",
                        extra={
                            "command_id": claim.receipt.command_id,
                            "run_id": claim.receipt.run_id.value,
                            "error_code": CommandErrorCode.TRANSIENT_FAILURE.value,
                        },
                    )
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
        return False

    async def _command_heartbeat(self, claim: CommandClaim) -> None:
        interval = max(0.01, self._ports.lease_ttl_seconds / 3.0)
        try:
            while True:
                await asyncio.sleep(interval)
                self._uow.heartbeat_command(
                    claim,
                    now=self._now(),
                    lease_seconds=self._ports.lease_ttl_seconds,
                )
        except (asyncio.CancelledError, Exception):
            return

    async def _command_pump(self) -> None:
        backoff = 0.01
        try:
            while self._state is RuntimeLifecycleState.READY:
                try:
                    await asyncio.wait_for(self._command_wake.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                self._command_wake.clear()
                empty = await self._drain_commands_bounded(100)
                if empty:
                    backoff = min(1.0, backoff * 2)
                else:
                    backoff = 0.01
                    self._command_wake.set()
        except asyncio.CancelledError:
            return

    async def _process_command(self, claim: CommandClaim) -> None:
        raw = json.loads(claim.raw_payload_json)
        if not isinstance(raw, Mapping):
            raise TypeError("durable command payload is corrupt")
        intent = command_intent_from_json(raw)
        state = claim.receipt.state
        if state is CommandState.ACCEPTED:
            self._uow.transition_command(
                claim,
                expected=CommandState.ACCEPTED,
                target=CommandState.CONTEXT_CALL_INTENT,
                now=self._now(),
            )
            state = CommandState.CONTEXT_CALL_INTENT
        if isinstance(intent, StartCommandIntent):
            await self._process_start_command(claim, intent, state)
        elif isinstance(intent, ContinueCommandIntent):
            await self._process_continue_command(claim, intent, state)
        else:
            if state is CommandState.CONTEXT_CALL_INTENT:
                self._uow.transition_command(
                    claim,
                    expected=state,
                    target=CommandState.CONTEXT_READY,
                    now=self._now(),
                )
            self._uow.apply_cancel_command(
                claim,
                event_id=f"{intent.command_id}:cancel",
                now=self._now(),
            )
            current = self._uow.read_run(intent.run_id.value)
            if current is not None:
                token = self._cancels.get(intent.run_id.value)
                if token is not None:
                    token.cancel()
                if intent.run_id.value in self._live.active_run_ids():
                    await self._live.cancel(intent.run_id.value)
                latest = self._uow.read_run(intent.run_id.value)
                if latest is not None and latest.state is RunState.CANCEL_REQUESTED:
                    await self._terminalize_cancelled(latest)

    async def _process_start_command(
        self, claim: CommandClaim, intent: StartCommandIntent, state: CommandState
    ) -> None:
        if intent.profile_key != self._root_profile_key:
            raise UnitOfWorkConflict("command profile differs from Runtime root")
        profile = self._profiles[self._root_profile_key]
        driver = self._drivers[profile.driver_kind]
        stage: ContextStageRecord | None = None
        if self._ports.agent_memory is not None:
            stage = await self.client._prepare_agent_context(
                kind=ContextStageKind.ROOT,
                identity_key=intent.run_id.value,
                root_run_id=intent.run_id.value,
                continuation_id=None,
                turn_id=intent.turn_id,
                value=intent.conversation,
            )
            if stage.private_snapshot is None or stage.private_snapshot_hash is None:
                raise UnitOfWorkConflict("prepared command context is unavailable")
        if state is CommandState.CONTEXT_CALL_INTENT:
            self._uow.transition_command(
                claim,
                expected=state,
                target=CommandState.CONTEXT_READY,
                now=self._now(),
            )
        start = RunStart(
            ExecutionSessionId(intent.conversation.identity.session_id),
            intent.run_id,
            intent.request_id,
            intent.turn_id,
            (
                {"messages": [intent.conversation.message.to_dict()]}
                if not intent.input
                else cast(
                    Mapping[str, JsonValue],
                    thaw_json(cast(FrozenJsonValue, intent.input)),
                )
            ),
            intent.tool_catalog_generation,
            intent.tool_catalog_fingerprint,
            conversation=intent.conversation,
            context_preparation_mode=(
                None if stage is None else ContextPreparationMode.SDK_PREPARED
            ),
            context_stage_id=None if stage is None else stage.stage_id,
            context_stage_hash=None if stage is None else stage.private_snapshot_hash,
            prepared_context=None if stage is None else stage.private_snapshot,
        )
        verdict = await self._ports.admission.evaluate(start)
        if not verdict.allowed:
            raise HarnessError("admission_denied", "The Run was denied by admission.")
        snapshot = bind_start_snapshot(
            start,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            policy_fingerprint=getattr(driver, "policy_fingerprint", None),
        )
        created = self._uow.apply_start_command(
            claim,
            execution_session_id=start.execution_session_id.value,
            request_id=start.request_id.value,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            snapshot=snapshot.to_json(),
            event_id=f"{start.run_id.value}:created",
            now=self._now(),
            user_id=intent.conversation.user_id,
            context_stage_id=start.context_stage_id,
            context_stage_hash=start.context_stage_hash,
        )
        if created.state not in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            activated = await self._activate(created.run_id)
            self._schedule(activated.run_id)

    async def _process_continue_command(
        self, claim: CommandClaim, intent: ContinueCommandIntent, state: CommandState
    ) -> None:
        start_raw = self._uow.read_start_snapshot(intent.run_id.value)
        if start_raw is None:
            raise UnitOfWorkNotFound(intent.run_id.value)
        start = StartSnapshot.from_json(start_raw)
        stage: ContextStageRecord | None = None
        if self._ports.agent_memory is not None:
            if start.conversation is None:
                raise UnitOfWorkConflict("command Run has no conversation identity")
            stage = await self.client._prepare_agent_context(
                kind=ContextStageKind.CONTINUATION,
                identity_key=intent.continuation_id,
                root_run_id=intent.run_id.value,
                continuation_id=intent.continuation_id,
                turn_id=intent.turn_id,
                value=ConversationTurnInput(
                    start.conversation.identity,
                    intent.conversation.message,
                    intent.conversation.memory_text,
                    start.conversation.recall_scopes,
                    intent.conversation.context_source_snapshot_ref,
                ),
            )
            if stage.private_snapshot is None or stage.private_snapshot_hash is None:
                raise UnitOfWorkConflict("prepared continuation command context is unavailable")
        if state is CommandState.CONTEXT_CALL_INTENT:
            self._uow.transition_command(
                claim,
                expected=state,
                target=CommandState.CONTEXT_READY,
                now=self._now(),
            )
        self._uow.apply_continue_command(
            claim,
            continuation_id=intent.continuation_id,
            payload={
                "kind": "conversation_user",
                "context_use_turn_id": intent.turn_id,
                "conversation": intent.conversation.to_json(),
                **(
                    {}
                    if stage is None or stage.private_snapshot is None
                    else {"prepared_context": dict(stage.private_snapshot)}
                ),
            },
            now=self._now(),
            context_stage_id=None if stage is None else stage.stage_id,
            context_stage_hash=None if stage is None else stage.private_snapshot_hash,
        )
        await self._wake_continuation(intent.run_id.value)

    async def _drain_deliveries_bounded(self, limit: int) -> bool:
        run_once = getattr(self._ports.delivery, "run_once", None)
        if run_once is None:
            return True
        for _ in range(limit):
            if not await run_once():
                return True
        return False

    async def _delivery_pump(self) -> None:
        backoff = 0.01
        try:
            while self._state is RuntimeLifecycleState.READY:
                try:
                    await asyncio.wait_for(self._delivery_wake.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                self._delivery_wake.clear()
                empty = await self._drain_deliveries_bounded(100)
                await self._drain_recall_releases(100)
                if empty:
                    backoff = min(1.0, backoff * 2)
                else:
                    backoff = 0.01
                    self._delivery_wake.set()
        except asyncio.CancelledError:
            return

    async def _drain_recall_releases(self, limit: int) -> None:
        memory = self._ports.agent_memory
        staging = self._ports.context_staging
        if memory is None or staging is None:
            return
        now = self._now()
        rows = staging.database.connection.execute(
            "SELECT release_id,query_id,query_hash,result_id,result_hash,write_fence,"
            "attempt_count "
            "FROM memory_recall_releases WHERE state='pending' AND retry_at<=? "
            "ORDER BY retry_at,release_id LIMIT ?",
            (now, limit),
        ).fetchall()

        for row in rows:
            with staging.database.transaction() as connection:
                pending = begin_release(connection, str(row[0]), now=self._now())
            if pending is None:
                continue
            call, request = pending
            try:
                await asyncio.wait_for(memory.release_recall(request), timeout=1.0)
            except Exception:
                with staging.database.transaction() as connection:
                    settle_release(connection, str(row[0]), call, now=self._now(), returned=False)
            else:
                with staging.database.transaction() as connection:
                    settle_release(connection, str(row[0]), call, now=self._now(), returned=True)

    async def _drain_memory_bounded(self, limit: int) -> bool:
        dispatcher = self._ports.memory_dispatcher
        if dispatcher is None:
            return True
        return await dispatcher.drain(limit=limit)

    async def _memory_pump(self) -> None:
        dispatcher = self._ports.memory_dispatcher
        if dispatcher is None:
            return
        backoff = 0.01
        try:
            while self._state is RuntimeLifecycleState.READY:
                try:
                    await asyncio.wait_for(self._memory_wake.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                self._memory_wake.clear()
                progressed = await dispatcher.run_once()
                backoff = 0.01 if progressed else min(1.0, backoff * 2)
        except asyncio.CancelledError:
            return

    async def _drain_resolved_waits_once(self) -> None:
        await self._drain_child_signals_once()
        for blocker in self._uow.list_resolved_wait_blockers(
            owner_id=self._ports.owner_id,
            namespace=RUNTIME_LEASE_NAMESPACE,
            now=self._now(),
        ):
            try:
                run, lease, _receipt = self._uow.consume_resolved_wait_and_claim_activation(
                    blocker_id=blocker.blocker_id,
                    owner_id=self._ports.owner_id,
                    namespace=RUNTIME_LEASE_NAMESPACE,
                    now=self._now(),
                    lease_ttl_seconds=self._ports.lease_ttl_seconds,
                )
                fence = await self._uow.acquire(RunId(run.run_id), lease, now=self._now())
            except UnitOfWorkConflict:
                continue
            self._leases[run.run_id] = lease
            self._fences[run.run_id] = fence
            self._cancels.setdefault(run.run_id, CancelToken())
            heartbeat = self._heartbeats.get(run.run_id)
            if heartbeat is None or heartbeat.done():
                self._heartbeats[run.run_id] = asyncio.create_task(
                    self._heartbeat(run.run_id),
                    name=f"simple-harness-heartbeat:{run.run_id}",
                )
            if run.run_id not in self._live.active_run_ids():
                self._schedule(run.run_id)
        await self._drain_spawn_ready_once()

    async def _drain_child_signals_once(self) -> None:
        active_child_parents = set(self._child_signal_wait_handoffs)
        for active_run_id in self._live.active_run_ids():
            active_run = self._uow.read_run(active_run_id)
            if active_run is not None and active_run.parent_run_id is not None:
                active_child_parents.add(active_run.parent_run_id)
        for result in self._child_signals.reconcile_all(
            now=self._now(), blocked_parent_run_ids=active_child_parents
        ):
            run_id = result.signal.parent_run_id
            if run_id not in self._leases:
                try:
                    await self._activate(run_id)
                except UnitOfWorkConflict:
                    continue
            if run_id not in self._live.active_run_ids():
                self._schedule(run_id)

    async def _drain_spawn_ready_once(self) -> None:
        cursor: str | None = None
        while True:
            ready_values, cursor = self._uow.list_ready_spawn_continuations(cursor, limit=100)
            for ready in ready_values:
                blocker = self._uow.read_spawn_ready_blocker(ready)
                if blocker is None:
                    continue
                try:
                    activation = await self._uow.run_atomic(
                        lambda tx, ready=ready, blocker=blocker: (  # type: ignore[misc]
                            self._uow.consume_spawn_ready_and_claim_activation(
                                tx,
                                ready,
                                blocker,
                                self._ports.owner_id,
                                now=self._now(),
                                ttl_seconds=self._ports.lease_ttl_seconds,
                            )
                        ),
                        fault_label="runtime:spawn_ready:consume",
                    )
                except UnitOfWorkConflict:
                    continue
                self._register_spawn_ready_activation(activation)
                run_id = activation.execution_lease.run_id
                if run_id not in self._live.active_run_ids():
                    self._schedule(run_id)
            if cursor is None:
                return

    def _register_spawn_ready_activation(self, activation: WorkflowSpawnReadyActivation) -> None:
        run_id = activation.execution_lease.run_id
        self._workflow_spawn_ready_activations[run_id] = activation
        self._leases[run_id] = activation.execution_lease
        self._fences[run_id] = activation.run_fence
        self._cancels.setdefault(run_id, CancelToken())
        heartbeat = self._heartbeats.get(run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[run_id] = asyncio.create_task(
                self._heartbeat(run_id),
                name=f"simple-harness-heartbeat:{run_id}",
            )

    async def wait_idle(self, run_id: RunId) -> None:
        value = _run_id(run_id)
        run = self._uow.read_run(value)
        parent_run_id = None if run is None else run.parent_run_id
        handoff = object()
        if parent_run_id is not None:
            self._child_signal_wait_handoffs[parent_run_id] = handoff
        try:
            await self._live.wait(value)
        finally:
            if parent_run_id is not None:
                asyncio.create_task(self._release_child_signal_wait_handoff(parent_run_id, handoff))

    async def _release_child_signal_wait_handoff(self, parent_run_id: str, handoff: object) -> None:
        await asyncio.sleep(0)
        if self._child_signal_wait_handoffs.get(parent_run_id) is handoff:
            self._child_signal_wait_handoffs.pop(parent_run_id, None)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _start_run(self, start: RunStart) -> RunRecord:
        self._require_started()
        host_control = start.start_mode == "host_control"
        if (
            self._ports.conversation_memory_enabled
            and start.conversation is None
            and not host_control
        ):
            raise HarnessError(
                "conversation_envelope_required",
                "Enabled conversation Memory requires an explicit envelope.",
            )
        if (
            self._ports.conversation_memory_enabled
            and not host_control
            and (start.context_stage_id is None or start.prepared_context is None)
        ):
            raise HarnessError(
                "context_stage_required",
                "Enabled conversation Memory requires durable prepared context.",
            )
        if (
            self._ports.conversation_memory_enabled
            and not host_control
            and (start.context_preparation_mode is not self._ports.context_preparation_mode)
        ):
            raise HarnessError(
                "context_preparation_mode_mismatch",
                "Run context preparation mode differs from Runtime composition.",
            )
        verdict = await self._ports.admission.evaluate(start)
        if not verdict.allowed:
            logger.warning(
                "run.admission_denied",
                extra={
                    "run_id": start.run_id.value,
                    "session_id": start.execution_session_id.value,
                },
            )
            raise HarnessError("admission_denied", "The Run was denied by admission.")
        profile = self._profiles[self._root_profile_key]
        driver = self._drivers[profile.driver_kind]
        snapshot = bind_start_snapshot(
            start,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            policy_fingerprint=getattr(driver, "policy_fingerprint", None),
        )
        user_id = (
            start.host_control_user_id
            if host_control
            else ("harness-system" if start.conversation is None else start.conversation.user_id)
        )
        assert user_id is not None
        created = self._uow.create_with_start_snapshot(
            execution_session_id=start.execution_session_id.value,
            run_id=start.run_id.value,
            request_id=start.request_id.value,
            profile_key=self._root_profile_key,
            driver_kind=profile.driver_kind,
            snapshot=snapshot.to_json(),
            event_id=f"{start.run_id.value}:created",
            now=self._now(),
            user_id=user_id,
            context_stage_id=start.context_stage_id,
            context_stage_hash=start.context_stage_hash,
        )
        self._emit_transition(
            "run.started",
            run_id=created.run_id,
            request_id=start.request_id.value,
            execution_session_id=start.execution_session_id.value,
            outcome=Outcome.STARTED,
            operation="run_lifecycle",
            attributes={
                "entity_kind": "run",
                "entity_id": created.run_id,
                "run_id": created.run_id,
                "to_state": created.state.value,
                "state_version": created.version,
                "replayed": created.state is not RunState.CREATED,
            },
        )
        if start.context_stage_id is not None:
            self._emit_transition(
                "context.consumed",
                run_id=created.run_id,
                request_id=start.request_id.value,
                execution_session_id=start.execution_session_id.value,
                outcome=Outcome.SUCCEEDED,
                operation="context_staging",
                attributes={
                    "entity_kind": "context_stage",
                    "entity_id": start.context_stage_id,
                    "from_state": "staged",
                    "to_state": "consumed",
                    "run_id": created.run_id,
                },
            )
        logger.info(
            "run.start",
            extra={
                "run_id": start.run_id.value,
                "session_id": start.execution_session_id.value,
                "profile": self._root_profile_key,
            },
        )
        if created.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return created
        activated = await self._activate(created.run_id)
        self._schedule(activated.run_id)
        return activated

    async def _wake_continuation(self, run_id: str) -> None:
        if run_id in self._retiring_leases:
            self._pending_wakes.add(run_id)
            return
        if run_id not in self._leases:
            try:
                await self._activate(run_id)
            except UnitOfWorkConflict:
                # Another owner holds the Run (or its lease has not expired yet).
                # Remember the wake so the drain loop retries it instead of leaving
                # a queued input behind until the next recover().
                self._pending_wakes.add(run_id)
                return
        self._pending_wakes.discard(run_id)
        if run_id in self._live.active_run_ids():
            # A drive is already running and may have passed its claim point before
            # this input was enqueued (and, for idle BaseAgent Runs, may be about to
            # release the Run's authority).  Let it finish, then wake again so the
            # input is claimed by a fresh drive instead of waiting for recover().
            await self._live.wait(run_id)
            if self._closing:
                return
            blockers = getattr(self._uow, "list_open_wait_blockers_for_run", None)
            if callable(blockers) and blockers(run_id):
                # The drive ended in an UNKNOWN wait: the resolved-wait drain wakes
                # it once reconciliation settles the blocker; do not re-drive now.
                return
            await self._wake_continuation(run_id)
            return
        self._schedule(run_id)

    async def _activate(self, run_id: str) -> RunRecord:
        if run_id in self._retiring_leases:
            raise UnitOfWorkConflict("previous runtime epoch is still retiring")
        previous_lease = self._leases.get(run_id)
        run, lease = self._uow.claim_runtime_activation(
            run_id=run_id,
            owner_id=self._ports.owner_id,
            namespace=RUNTIME_LEASE_NAMESPACE,
            now=self._now(),
            lease_ttl_seconds=self._ports.lease_ttl_seconds,
        )
        self._leases[run_id] = lease
        fence = await self._uow.acquire(RunId(run_id), lease, now=self._now())
        self._fences[run_id] = fence
        if (
            previous_lease is None
            or previous_lease.epoch != lease.epoch
            or previous_lease.owner_id != lease.owner_id
            or run_id not in self._cancels
        ):
            # Cancellation due to lease loss belongs to that epoch, not to the
            # durable AgentTurn. A real user cancellation still fences recovery.
            token = CancelToken()
            if run.state is RunState.CANCEL_REQUESTED:
                token.cancel()
            self._cancels[run_id] = token
        heartbeat = self._heartbeats.get(run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[run_id] = asyncio.create_task(
                self._heartbeat(run_id), name=f"simple-harness-heartbeat:{run_id}"
            )
        return run

    async def _heartbeat(self, run_id: str) -> None:
        interval = max(0.001, self._ports.lease_ttl_seconds / 3.0)
        try:
            # Closing first cancels and joins every driver while ownership is still
            # renewed.  Only Runtime.close may stop the heartbeat after that join.
            while run_id in self._leases:
                await self._ports.sleep(interval)
                lease = self._leases.get(run_id)
                if lease is None:
                    return
                try:
                    renewed = self._uow.renew_runtime_lease(
                        lease,
                        now=self._now(),
                        lease_ttl_seconds=self._ports.lease_ttl_seconds,
                    )
                except UnitOfWorkConflict:
                    self._retiring_leases.add(run_id)
                    token = self._cancels.get(run_id)
                    fence = self._fences.get(run_id)
                    try:
                        if token is not None:
                            token.cancel()
                        await self._live.cancel(run_id)
                        current = self._leases.get(run_id)
                        same_epoch = current is not None and (
                            current.owner_id == lease.owner_id and current.epoch == lease.epoch
                        )
                        if current is None or same_epoch:
                            # The old driver may already have released its lease.
                            # Never remove authority installed by a different epoch.
                            if same_epoch:
                                self._leases.pop(run_id, None)
                            if self._cancels.get(run_id) is token:
                                self._cancels.pop(run_id, None)
                            if self._fences.get(run_id) is fence:
                                self._fences.pop(run_id, None)
                            self._workflow_spawn_ready_activations.pop(run_id, None)
                            self._workflow_start_dispatches.pop(run_id, None)
                            self._workflow_recovery_work.pop(run_id, None)
                        if fence is not None:
                            try:
                                # SDK release is fenced by the original fence epoch.
                                await self._uow.release(fence)
                            except UnitOfWorkConflict:
                                pass
                    finally:
                        if self._heartbeats.get(run_id) is asyncio.current_task():
                            self._heartbeats.pop(run_id, None)
                        self._retiring_leases.discard(run_id)
                    current_run = self._uow.read_run(run_id)
                    if (
                        not self._closing
                        and current_run is not None
                        and current_run.state
                        in {
                            RunState.RUNNING,
                            RunState.WAITING,
                            RunState.CANCEL_REQUESTED,
                        }
                    ):
                        self._pending_wakes.add(run_id)
                    return
                self._leases[run_id] = renewed
                ready = self._workflow_spawn_ready_activations.get(run_id)
                if ready is not None:
                    refreshed = await self._uow.run_atomic(
                        lambda tx, receipt_id=ready.activation_receipt_id: (  # type: ignore[misc]
                            self._uow.read_spawn_ready_activation(tx, run_id, receipt_id)
                        ),
                        fault_label="runtime:spawn_ready:heartbeat_read",
                    )
                    if refreshed is None:
                        raise UnitOfWorkConflict(
                            "workflow spawn activation disappeared after heartbeat"
                        )
                    self._workflow_spawn_ready_activations[run_id] = refreshed
        except asyncio.CancelledError:
            return

    def _schedule(self, run_id: str) -> None:
        current = self._uow.read_run(run_id)
        if current is not None and current.state in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            self._pending_wakes.discard(run_id)
            return
        if run_id in self._retiring_leases or run_id not in self._leases:
            # A submit/reschedule can finish waiting for the old driver before
            # its heartbeat finishes retiring. Defer instead of starting a
            # driver with absent or soon-to-be-removed authority.
            if not self._closing:
                self._pending_wakes.add(run_id)
            return
        self._live.schedule(run_id, self._drive(run_id))

    async def _drive(self, run_id: str) -> None:
        # Recovery or a cancel command can terminalize after this drive was
        # scheduled but before it starts. Its released fence is not a driver
        # failure, and the stale queued wake must not revive the terminal Run.
        current = self._uow.read_run(run_id)
        if current is not None and current.state in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            return
        continuation_claim: ContinuationRecord | None = None
        try:
            with runtime_operation(
                self._uow,
                "runtime.preflight",
                lease=self._leases[run_id],
                clock=self._now,
                identity={"run": run_id},
            ) as preflight_receipt:
                run = self._uow.read_run(run_id)
                raw_snapshot = self._uow.read_start_snapshot(run_id)
                if run is None or raw_snapshot is None:
                    raise RuntimeError("durable Run start state is incomplete")
                snapshot = StartSnapshot.from_json(raw_snapshot)
                driver = self._drivers[snapshot.driver_kind]
                if snapshot.policy_fingerprint is not None and (
                    getattr(driver, "policy_fingerprint", None) != snapshot.policy_fingerprint
                ):
                    error = HarnessError(
                        "runtime_policy_mismatch",
                        "The frozen Runtime policy is unavailable.",
                    )
                    preflight_receipt["audit_outcome"] = "rejected"
                    self._terminalize(
                        run,
                        state=RunState.FAILED,
                        payload=error.to_dict(),
                        deliveries=(),
                    )
                    return
                catalog_matches = False
                if snapshot.tool_catalog_fingerprint is not None:
                    resolver = getattr(self._ports.tool_catalog, "resolve", None)
                    resolved_catalog = (
                        None
                        if resolver is None
                        else resolver(
                            snapshot.tool_catalog_generation,
                            snapshot.tool_catalog_fingerprint,
                        )
                    )
                    catalog_matches = (
                        resolved_catalog is not None
                        and resolved_catalog.generation == snapshot.tool_catalog_generation
                        and resolved_catalog.content_fingerprint
                        == snapshot.tool_catalog_fingerprint
                    )
                else:
                    catalog_matches = (
                        snapshot.tool_catalog_generation
                        == self._ports.tool_catalog.current_generation()
                    )
                if not catalog_matches:
                    error = ToolCatalogStale()
                    preflight_receipt["audit_outcome"] = "rejected"
                    self._terminalize(
                        run,
                        state=RunState.FAILED,
                        payload=error.to_dict(),
                        deliveries=(),
                    )
                    return
                if run.state is RunState.CANCEL_REQUESTED:
                    preflight_receipt["audit_outcome"] = "rejected"
                    await self._terminalize_cancelled(
                        run, snapshot=snapshot, reason="pre_drive_cancel"
                    )
                    return
                continuation_claim = self._uow.claim_continuation(
                    run_id=run_id,
                    execution_lease=self._leases[run_id],
                    now=self._now(),
                )
                if run.driver_kind == BASE_AGENT_DRIVER_KIND:
                    pending_turn = self._uow.read_pending_agent_turn(run_id)
                    if pending_turn is not None:
                        # RESULT_PENDING survives any wake path: commit the staged result
                        # first and never hand the same input to the driver again.
                        if (
                            continuation_claim is not None
                            and continuation_claim.continuation_id != pending_turn.continuation_id
                        ):
                            raise UnitOfWorkConflict(
                                "pending agent turn is not at the continuation head"
                            )
                        if continuation_claim is None and pending_turn.continuation_id is not None:
                            durable = self._uow.read_continuation(pending_turn.continuation_id)
                            if durable is not None and durable.state is not ContinuationState.ACKED:
                                # Never finalize silently past an unacked input (review F5).
                                raise UnitOfWorkConflict(
                                    "pending agent turn input is claimed elsewhere"
                                )
                        await self._finalize_agent_turn(
                            run_id,
                            pending_turn.turn_id,
                            continuation_claim,
                            {"recovered": True},
                            reschedule=True,
                        )
                        return
                if run.state is RunState.WAITING and continuation_claim is None:
                    if run.driver_kind == BASE_AGENT_DRIVER_KIND and not self._closing:
                        # An idle BaseAgent keeps no lease, fence or heartbeat: N idle
                        # Agents must not cost N renewing tasks.  The next input
                        # re-activates the Run through ``_wake_continuation``.
                        await self._abandon_run_authority(run_id)
                    return
            # 按需导入：循环依赖（kernel ↔ react）
            from simple_harness.runtime.drivers.react import ReActDriver

            # 按需导入：循环依赖（kernel ↔ drivers）
            from simple_harness.runtime.drivers.start_mode import StartModeDriverRouter

            # 按需导入：循环依赖（kernel ↔ workflow）
            from simple_harness.runtime.drivers.workflow import WorkflowRuntimeDriver

            # Only this concrete SDK selector is transparent to recording.
            # Resolve after validating the persisted snapshot and before both
            # contract selection and invocation; never trust a Host contract tag.
            if type(driver) is StartModeDriverRouter:
                driver = driver.select(snapshot.start_mode)
            driver_contract = None
            if type(self._uow) is SqliteExecutionUnitOfWork:
                if type(driver) is ReActDriver:
                    driver_contract = "sdk.react.v2"
                elif type(driver) is WorkflowRuntimeDriver:
                    driver_contract = "sdk.workflow.v2"
                elif (
                    type(driver).__module__ == "simple_harness.agents.execution"
                    and type(driver).__name__ == "AgentExecutionDriver"
                ):
                    # SDK-owned BaseAgent driver; matched by its exact SDK module so the
                    # kernel never imports simple_harness.agents (layering rule).
                    driver_contract = "sdk.base_agent.v1"
            with runtime_operation(
                self._uow,
                "runtime.driver",
                contract=driver_contract,
                parent_operation_id=preflight_receipt.operation_id,
                lease=self._leases[run_id],
                clock=self._now,
                identity={"driver": run.driver_kind, "run": run_id},
            ) as audit_receipt:
                result = await driver.start(
                    DriverInvocation(
                        run=run,
                        start=snapshot,
                        execution_lease=self._leases[run_id],
                        run_fence=self._fences[run_id],
                        services=self._services,
                        continuations=(() if continuation_claim is None else (continuation_claim,)),
                        workflow_spawn_ready_activation=(
                            self._workflow_spawn_ready_activations.get(run_id)
                        ),
                        workflow_start_dispatch=self._workflow_start_dispatches.get(run_id),
                        workflow_recovery_work=self._workflow_recovery_work.get(run_id),
                    ),
                    context=self._ports.context,
                    cancel=self._cancels[run_id],
                )
                audit_receipt["state"] = result.state.value
                self._driver_exception_wakes.pop(run_id, None)
            if continuation_claim is not None:
                durable_continuation = self._uow.read_continuation(
                    continuation_claim.continuation_id
                )
                if (
                    durable_continuation is not None
                    and durable_continuation.state is ContinuationState.ACKED
                ):
                    continuation_claim = None
            if result.workflow_spawn_control is not None:
                await self._accept_workflow_spawn_control(run_id, result.workflow_spawn_control)
                return
            current = self._uow.read_run(run_id)
            if current is None:
                raise RuntimeError("Run disappeared during execution")
            if result.agent_turn_outcome is not None:
                # BaseAgent: stage + finalize the turn result; the Run stays WAITING
                # and never reaches the terminal branch below.  A failure here must
                # not terminalize the Agent (review F3): the stage is durable, so drop
                # this executor's authority and let finalize-first recover the turn.
                try:
                    await self._commit_agent_turn(
                        current, result.agent_turn_outcome, result.payload, continuation_claim
                    )
                except UnitOfWorkConflict:
                    raise
                except Exception:  # noqa: BLE001 - staged result survives; never FAILED
                    logger.exception(
                        "sdk_agent_turn_finalize_failed", extra={"run_id": str(run_id)}
                    )
                    await self._abandon_run_authority(run_id)
                return
            if (
                run.driver_kind == BASE_AGENT_DRIVER_KIND
                and continuation_claim is not None
                and result.state is RunState.WAITING
                and (result.wait_blocker is not None or result.authorization_wait is not None)
            ):
                # BaseAgent turn suspended mid-way (UNKNOWN outbound work / authorization):
                # keep the input continuation claimed (not acked) so the same turn resumes
                # under the fresh lease epoch once the blocker resolves (review F2).
                if result.authorization_wait is not None:
                    self._commit_authorization_wait(run_id, result.authorization_wait)
                else:
                    assert result.wait_blocker is not None
                    self._uow.commit_runtime_wait_with_blocker(
                        run_id=run_id,
                        expected_version=current.version,
                        event_id=f"{run_id}:waiting:{current.version + 1}",
                        payload=result.payload,
                        blocker=result.wait_blocker,
                        lease=self._leases[run_id],
                        now=self._now(),
                    )
                # Release this executor's lease: the resolved-wait drain re-activates the
                # Run under a new epoch, which is what lets the claimed input be re-claimed.
                await self._abandon_run_authority(run_id)
                return
            if result.state is RunState.WAITING:
                if result.authorization_wait is not None:
                    if continuation_claim is not None:
                        raise UnitOfWorkConflict("authorization wait cannot ack a continuation")
                    self._commit_authorization_wait(run_id, result.authorization_wait)
                    return
                if continuation_claim is None:
                    if result.wait_blocker is None:
                        self._uow.commit_runtime_state(
                            run_id=run_id,
                            expected_version=current.version,
                            state=RunState.WAITING,
                            event_id=f"{run_id}:waiting:{current.version + 1}",
                            payload=result.payload,
                            lease=self._leases[run_id],
                            now=self._now(),
                        )
                        if run.driver_kind == BASE_AGENT_DRIVER_KIND and not self._closing:
                            # Idle BaseAgent (fresh start, nothing queued): drop authority.
                            await self._abandon_run_authority(run_id)
                    else:
                        self._uow.commit_runtime_wait_with_blocker(
                            run_id=run_id,
                            expected_version=current.version,
                            event_id=f"{run_id}:waiting:{current.version + 1}",
                            payload=result.payload,
                            blocker=result.wait_blocker,
                            lease=self._leases[run_id],
                            now=self._now(),
                        )
                else:
                    if result.wait_blocker is not None:
                        raise UnitOfWorkConflict(
                            "uncertain outbound work cannot ack a continuation"
                        )
                    self._uow.commit_runtime_state_and_ack_continuation(
                        run_id=run_id,
                        expected_version=current.version,
                        state=RunState.WAITING,
                        event_id=(
                            f"{run_id}:waiting:continuation:"
                            f"{continuation_claim.continuation_id}:"
                            f"{continuation_claim.claim_epoch}"
                        ),
                        payload=result.payload,
                        continuation_claim=continuation_claim,
                        execution_lease=self._leases[run_id],
                        receipt_id=(
                            f"{run_id}:progress:{continuation_claim.continuation_id}:"
                            f"{continuation_claim.claim_epoch}"
                        ),
                        now=self._now(),
                    )
                    asyncio.create_task(self._reschedule(run_id))
            else:
                self._terminalize(
                    current,
                    state=result.state,
                    payload=result.payload,
                    deliveries=result.deliveries,
                    continuation_claim=continuation_claim,
                    conversation_output=result.conversation_output,
                )
        except asyncio.CancelledError:
            current = self._uow.read_run(run_id)
            if (
                not self._closing
                and current is not None
                and current.state is RunState.CANCEL_REQUESTED
            ):
                if continuation_claim is None:
                    await self._terminalize_cancelled(current, reason="driver_cancelled")
                else:
                    await self._abandon_run_authority(run_id)
        except UnitOfWorkConflict:
            if continuation_claim is not None:
                await self._abandon_run_authority(run_id)
            return
        except Exception as error:  # noqa: BLE001 - driver boundary becomes a durable failure
            # private_cause 不进 HarnessError.to_dict()（对外只暴露稳定 public 契约），
            # 但必须落到日志供 host 排障；否则 driver 失败完全不可追踪。
            logger.exception(
                "sdk_run_driver_failed",
                extra={"run_id": str(run_id)},
            )
            current = self._uow.read_run(run_id)
            if current is not None and current.driver_kind == BASE_AGENT_DRIVER_KIND:
                # A BaseAgent never dies of a driver exception (BA-v1.0 §1.3): keep the
                # input claimed under the lapsed lease and drop this executor's
                # authority; the next wake either finalizes a result the loop already
                # staged (BA31) or re-drives the same turn.  Kernel-integrity
                # failures reach here as explicit FAILED DriverResults, not exceptions.
                # The driver itself turns exceptions inside a turn into a visible
                # failed turn; what reaches here is re-woken through the drain loop a
                # bounded number of times, after which only recover() re-drives it
                # (review K1: a claimed input must never wait silently for recover()).
                await self._abandon_run_authority(run_id)
                attempts = self._driver_exception_wakes.get(run_id, 0) + 1
                self._driver_exception_wakes[run_id] = attempts
                if attempts <= BASE_AGENT_DRIVER_EXCEPTION_WAKES:
                    self._pending_wakes.add(run_id)
                else:
                    logger.error(
                        "sdk_base_agent_driver_exception_wakes_exhausted",
                        extra={"run_id": str(run_id), "attempts": attempts},
                    )
                return
            if current is not None and current.state not in {
                RunState.COMPLETED,
                RunState.FAILED,
                RunState.CANCELLED,
            }:
                failure = HarnessError(
                    "driver_failed",
                    "The Run driver failed.",
                    private_cause=error,
                )
                try:
                    self._terminalize(
                        current,
                        state=RunState.FAILED,
                        payload=failure.to_dict(),
                        deliveries=(),
                        continuation_claim=continuation_claim,
                    )
                except UnitOfWorkConflict:
                    pass
        finally:
            current = self._uow.read_run(run_id)
            if current is not None and current.state in {
                RunState.COMPLETED,
                RunState.FAILED,
                RunState.CANCELLED,
            }:
                self._release_runtime_lease(run_id)

    async def _cancel_run(self, run_id: RunId) -> RunRecord:
        self._require_started()
        value = _run_id(run_id)
        current = self._uow.read_run(value)
        if current is None:
            raise KeyError(value)
        snapshot_raw = self._uow.read_start_snapshot(value)
        if snapshot_raw is None:
            raise RuntimeError("durable Run start state is incomplete")
        snapshot = StartSnapshot.from_json(snapshot_raw)
        if snapshot.driver_kind == WORKFLOW_DRIVER_KIND:
            token = self._cancels.get(value)
            latest = self._uow.read_run(value)
            assert latest is not None
            await self._terminalize_cancelled(latest, snapshot=snapshot, reason="user")
            if token is not None:
                token.cancel()
            if value in self._live.active_run_ids():
                await self._live.cancel(value)
            result = self._uow.read_run(value)
            assert result is not None
            return result
        self._uow.request_run_cancel(
            run_id=value,
            expected_version=current.version,
            event_id=f"{value}:cancel-requested",
            now=self._now(),
        )
        token = self._cancels.get(value)
        if token is not None:
            token.cancel()
        task_active = value in self._live.active_run_ids()
        if task_active:
            await self._live.cancel(value)
        latest = self._uow.read_run(value)
        assert latest is not None
        if latest.state is RunState.CANCEL_REQUESTED:
            await self._terminalize_cancelled(latest)
        result = self._uow.read_run(value)
        assert result is not None
        return result

    def _commit_authorization_wait(self, run_id: str, pending: ToolAuthorizationPending) -> None:
        prepared = pending.prepared
        arguments = thaw_json(prepared.call.arguments)
        metadata = thaw_json(pending.request.metadata)
        if not isinstance(arguments, dict) or not isinstance(metadata, dict):
            raise TypeError("authorization request payload must be objects")
        self._uow.commit_decision(
            decision_id=pending.decision_id,
            run_id=run_id,
            kind="tool_authorization",
            state=DecisionState.OPEN,
            request={
                "arguments": arguments,
                "call_id": prepared.call.call_id.value,
                "effect_id": prepared.effect_id.value,
                "expires_at": pending.request.expires_at,
                "metadata": metadata,
                "nonce": pending.request.nonce,
                "prompt": pending.request.prompt,
                "resources": [resource.to_json() for resource in prepared.resources],
                "resources_digest": resource_digest(prepared.resources),
                "sidecar_digest": (None if prepared.sidecar is None else prepared.sidecar.digest),
                "tool_name": prepared.call.name,
            },
            response=None,
            event_id=f"{pending.decision_id}:open",
            now=self._now(),
        )

    async def _commit_agent_turn(
        self,
        current: RunRecord,
        outcome: AgentTurnOutcome,
        payload: Mapping[str, JsonValue],
        continuation_claim: ContinuationRecord | None,
    ) -> None:
        """stage (RESULT_PENDING) then finalize one BaseAgent turn; Run stays WAITING."""

        run_id = current.run_id
        turn = self._uow.read_agent_turn(outcome.turn_id)
        if turn is None or turn.agent_id != outcome.agent_id:
            raise UnitOfWorkConflict("agent turn row does not match the driver outcome")
        if turn.phase in {"queued", "running"}:
            self._uow.stage_agent_turn_result(
                turn_id=outcome.turn_id,
                result_hash=outcome.result_hash,
                result_json=outcome.result_object(),
                provider_turn_ordinal_from=outcome.provider_turn_ordinal_from,
                provider_turn_ordinal_to=outcome.provider_turn_ordinal_to,
                execution_lease=self._leases[run_id],
                now=self._now(),
            )
        elif turn.phase == "result_pending":
            if turn.staged_result_hash != outcome.result_hash:
                raise UnitOfWorkConflict("staged agent turn result differs from driver outcome")
        elif self._uow.read_agent_turn_result(outcome.turn_id) is None:
            raise UnitOfWorkConflict("agent turn is closed without a result row")
        await self._finalize_agent_turn(
            run_id,
            outcome.turn_id,
            continuation_claim,
            dict(payload),
            usage_refs=outcome.usage_refs,
            reschedule=True,
        )

    async def _finalize_agent_turn(
        self,
        run_id: str,
        turn_id: str,
        continuation_claim: ContinuationRecord | None,
        payload: dict[str, JsonValue],
        *,
        usage_refs: tuple[str, ...] = (),
        reschedule: bool,
    ) -> None:
        current = self._uow.read_run(run_id)
        if current is None:
            raise RuntimeError("Run disappeared during agent turn finalize")
        receipt_id = (
            None
            if continuation_claim is None
            else (
                f"{run_id}:progress:{continuation_claim.continuation_id}:"
                f"{continuation_claim.claim_epoch}"
            )
        )
        binding = self._uow.read_agent_binding_for_run(run_id)
        finalize_payload: dict[str, JsonValue] = {
            **payload,
            "base_agent_stage": "idle",
            "turn_id": turn_id,
            "agent_id": None if binding is None else binding.agent_id,
        }
        self._uow.commit_agent_turn_result_and_idle(
            run_id=run_id,
            expected_version=current.version,
            turn_id=turn_id,
            event_id=f"{run_id}:agent_turn:{turn_id}:committed",
            payload=finalize_payload,
            continuation_claim=continuation_claim,
            execution_lease=self._leases[run_id],
            receipt_id=receipt_id,
            usage_refs=usage_refs,
            now=self._now(),
        )
        if reschedule:
            asyncio.create_task(self._reschedule(run_id))

    async def _finalize_pending_agent_turn(self, run_id: str) -> bool:
        """Recovery: commit an already staged turn without re-entering the driver.

        Claims the turn's own input continuation (if it is still unacked) so the
        same input is never handed to the driver a second time.
        """

        pending = self._uow.read_pending_agent_turn(run_id)
        if pending is None:
            return False
        claim: ContinuationRecord | None = None
        if pending.continuation_id is not None:
            durable = self._uow.read_continuation(pending.continuation_id)
            if durable is not None and durable.state is not ContinuationState.ACKED:
                claim = self._uow.claim_continuation(
                    run_id=run_id,
                    execution_lease=self._leases[run_id],
                    now=self._now(),
                )
                if claim is None:
                    raise UnitOfWorkConflict("pending agent turn input is claimed elsewhere")
                if claim.continuation_id != pending.continuation_id:
                    raise UnitOfWorkConflict("pending agent turn is not at the continuation head")
        await self._finalize_agent_turn(
            run_id, pending.turn_id, claim, {"recovered": True}, reschedule=False
        )
        return True

    async def _reschedule(self, run_id: str) -> None:
        while run_id in self._live.active_run_ids():
            await asyncio.sleep(0)
        if run_id not in self._leases or self._closing:
            return
        self._schedule(run_id)

    async def _terminalize_cancelled(
        self,
        run: RunRecord,
        *,
        snapshot: StartSnapshot | None = None,
        reason: str = "user",
    ) -> None:
        if snapshot is None:
            raw = self._uow.read_start_snapshot(run.run_id)
            if raw is None:
                raise UnitOfWorkConflict("cancelled Run has no durable start snapshot")
            snapshot = StartSnapshot.from_json(raw)
        if snapshot.driver_kind == WORKFLOW_DRIVER_KIND:
            coordinator = self._cancellation_coordinators.get(WORKFLOW_DRIVER_KIND)
            if coordinator is None:
                raise UnitOfWorkConflict("workflow cancellation coordinator is not registered")
            if run.run_id not in self._leases or run.run_id not in self._fences:
                await self._activate(run.run_id)
            outcome = await coordinator.cancel(
                run,
                snapshot,
                reason=reason,
                now=self._now(),
                recovery=DriverCancellationRecovery(
                    self._leases[run.run_id], self._fences[run.run_id]
                ),
            )
            if outcome.terminal and not self._uow.verify_workflow_cancel_terminal(
                run_id=run.run_id,
                cancel_id=outcome.cancel_id,
                generation=outcome.generation,
            ):
                raise UnitOfWorkConflict("workflow cancel terminal receipt is not durable")
            self._drop_local_authority(run.run_id)
            return
        if run.run_id not in self._fences:
            await self._activate(run.run_id)
        self._terminalize(
            run,
            state=RunState.CANCELLED,
            payload={"code": "cancelled", "public_message": "The Run was cancelled."},
            deliveries=(),
        )

    def _terminalize(
        self,
        run: RunRecord,
        *,
        state: RunState,
        payload: Mapping[str, JsonValue],
        deliveries: Sequence[DeliverySpec],
        continuation_claim: ContinuationRecord | None = None,
        conversation_output: ConversationTurnOutput | None = None,
    ) -> None:
        raw_snapshot = self._uow.read_start_snapshot(run.run_id)
        snapshot = None if raw_snapshot is None else StartSnapshot.from_json(raw_snapshot)
        committed_turn: CommittedTurnSpec | None = None
        legacy_cursor = self._uow.read_legacy_turn_cursor(run.run_id)
        legacy_cursor_version = (
            None
            if legacy_cursor is None or legacy_cursor.state != "active"
            else legacy_cursor.cursor_version
        )
        if (
            self._ports.agent_memory is not None
            and snapshot is not None
            and snapshot.conversation is not None
        ):
            if state is RunState.COMPLETED and conversation_output is None:
                raise UnitOfWorkConflict(
                    "completed conversation requires typed conversation_output"
                )
            if state is not RunState.COMPLETED and conversation_output is not None:
                raise UnitOfWorkConflict("non-completed conversation rejects conversation_output")
            if conversation_output is not None and conversation_output.memory_text is not None:
                if legacy_cursor_version is not None:
                    assert legacy_cursor is not None
                    user_text: str | None = legacy_cursor.user_text
                    turn_id: str = legacy_cursor.turn_id
                    assert user_text is not None
                    committed_turn = CommittedTurnSpec.from_domain(
                        CommittedTurn(
                            turn_id,
                            snapshot.conversation.identity,
                            user_text,
                            conversation_output.memory_text,
                            MemoryScopeRef.personal(snapshot.conversation.identity.actor_id),
                            legacy_cursor.write_fence,
                            legacy_cursor.turn_started_at,
                        )
                    )
                elif continuation_claim is None:
                    user_text = snapshot.conversation.memory_text
                    turn_id = snapshot.turn_id
                    stage_where = "consumed_run_id=?"
                    stage_identity = run.run_id
                else:
                    continuation_payload = thaw_json(continuation_claim.payload)
                    if not isinstance(continuation_payload, dict):
                        raise UnitOfWorkConflict("conversation continuation payload is invalid")
                    raw_conversation = continuation_payload.get("conversation")
                    if not isinstance(raw_conversation, Mapping):
                        raise UnitOfWorkConflict("conversation continuation value is invalid")
                    continuation_value = ConversationContinuationInput.from_json(raw_conversation)
                    user_text = continuation_value.memory_text
                    turn_id = continuation_claim.continuation_id
                    stage_where = "consumed_continuation_id=?"
                    stage_identity = continuation_claim.continuation_id
                if legacy_cursor_version is None and user_text is not None:
                    staging = self._ports.context_staging
                    if staging is None:
                        raise UnitOfWorkConflict("committed turn lacks Context staging")
                    stage = staging.database.connection.execute(
                        "SELECT memory_write_fence,turn_started_at "
                        f"FROM context_preparation_staging WHERE {stage_where}",
                        (stage_identity,),
                    ).fetchone()
                    if stage is None or stage["turn_started_at"] is None:
                        raise UnitOfWorkConflict("committed turn lacks durable recall lineage")
                    committed_turn = CommittedTurnSpec.from_domain(
                        CommittedTurn(
                            turn_id,
                            snapshot.conversation.identity,
                            user_text,
                            conversation_output.memory_text,
                            MemoryScopeRef.personal(snapshot.conversation.identity.actor_id),
                            (
                                None
                                if stage["memory_write_fence"] is None
                                else str(stage["memory_write_fence"])
                            ),
                            float(stage["turn_started_at"]),
                        )
                    )
        event = (
            "run.complete"
            if state is RunState.COMPLETED
            else "run.fail"
            if state is RunState.FAILED
            else "run.cancelled"
        )
        logger.info(
            event,
            extra={
                "run_id": run.run_id,
                "state": state.value,
                "payload_keys": list(payload)[:10],
            },
        )
        fence = self._fences[run.run_id]
        if run.parent_run_id is not None:
            if continuation_claim is not None:
                raise UnitOfWorkConflict("child continuation terminalization has no atomic command")
            committed = self._uow.read_child_terminal_result_for_run(run.run_id)
            if committed is not None:
                if committed.terminal_state != state.value:
                    raise UnitOfWorkConflict(
                        "workflow child terminal result differs from Driver result"
                    )
                self._drop_local_authority(run.run_id)
                self._emit_run_terminal(run, state, replayed=True)
                return
            command = self._uow.read_child_command_for_run(run.run_id)
            if command is None:
                raise UnitOfWorkConflict("child Run has no durable launch command")
            identity = f"{run.run_id}:{run.version}:{state.value}"
            policy = self._uow.read_child_attachment_policy(run.run_id)
            terminal_payload: dict[str, JsonValue] = {
                "status": state.value,
                "result": dict(payload),
            }
            if policy is AttachmentPolicy.DETACHED:
                self._uow.commit_detached_child_terminal(
                    command_id=command.command_id,
                    expected_child_version=run.version,
                    terminal_state=state,
                    terminal_payload=terminal_payload,
                    event_id=f"{identity}:event",
                    receipt_id=f"{identity}:receipt",
                    run_fence=fence,
                    execution_lease=self._leases[run.run_id],
                    now=self._now(),
                )
            else:
                self._uow.finalize_child_and_enqueue_parent_signal(
                    command_id=command.command_id,
                    expected_child_version=run.version,
                    terminal_state=state,
                    signal_id=f"{identity}:signal",
                    signal_payload=terminal_payload,
                    event_id=f"{identity}:event",
                    receipt_id=f"{identity}:receipt",
                    run_fence=fence,
                    execution_lease=self._leases[run.run_id],
                    now=self._now(),
                )
            self._emit_run_terminal(run, state)
            self._fences.pop(run.run_id, None)
            return
        if continuation_claim is not None:
            identity = (
                f"{run.run_id}:terminal:continuation:"
                f"{continuation_claim.continuation_id}:"
                f"{continuation_claim.claim_epoch}:{state.value}"
            )
            self._uow.commit_root_terminal_with_deliveries_and_ack_continuation(
                run_id=run.run_id,
                expected_version=run.version,
                terminal_state=state,
                event_id=f"{identity}:event",
                terminal_payload=payload,
                deliveries=deliveries,
                continuation_claim=continuation_claim,
                run_fence=fence,
                execution_lease=self._leases[run.run_id],
                receipt_id=f"{identity}:receipt",
                terminal_fence_receipt_ref=(f"runtime-fence:{fence.owner_id}:{fence.epoch}"),
                now=self._now(),
                committed_turn=committed_turn,
                conversation_output=(
                    None if conversation_output is None else conversation_output.to_json()
                ),
                legacy_cursor_version=legacy_cursor_version,
            )
            self._emit_run_terminal(run, state)
            self._fences.pop(run.run_id, None)
            if deliveries:
                self._delivery_wake.set()
            if committed_turn is not None:
                self._memory_wake.set()
            return
        self._terminal.commit(
            run,
            state=state,
            payload=payload,
            deliveries=deliveries,
            fence=fence,
            execution_lease=self._leases[run.run_id],
            now=self._now(),
            committed_turn=committed_turn,
            conversation_output=conversation_output,
            legacy_cursor_version=legacy_cursor_version,
        )
        self._emit_run_terminal(run, state)
        self._fences.pop(run.run_id, None)
        if deliveries:
            self._delivery_wake.set()
        if committed_turn is not None:
            self._memory_wake.set()

    def _emit_run_terminal(
        self, run: RunRecord, state: RunState, *, replayed: bool = False
    ) -> None:
        self._emit_transition(
            "run.terminal",
            run_id=run.run_id,
            outcome=Outcome.TERMINAL,
            operation="run_lifecycle",
            attributes={
                "entity_kind": "run",
                "entity_id": run.run_id,
                "run_id": run.run_id,
                "from_state": run.state.value,
                "to_state": state.value,
                "state_version": run.version + (0 if replayed else 1),
                "replayed": replayed,
                "error_code": None if state is RunState.COMPLETED else state.value,
            },
        )

    async def _abandon_run_authority(self, run_id: str) -> None:
        fence = self._fences.pop(run_id, None)
        if fence is not None:
            try:
                await self._uow.release(fence)
            except UnitOfWorkConflict:
                pass
        self._release_runtime_lease(run_id)

    def _release_runtime_lease(self, run_id: str) -> None:
        lease = self._leases.pop(run_id, None)
        self._workflow_spawn_ready_activations.pop(run_id, None)
        self._workflow_start_dispatches.pop(run_id, None)
        self._workflow_recovery_work.pop(run_id, None)
        heartbeat = self._heartbeats.pop(run_id, None)
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
        self._cancels.pop(run_id, None)
        if lease is None:
            return
        try:
            self._uow.release_runtime_lease(lease, now=self._now())
        except UnitOfWorkConflict:
            pass

    def _drop_local_authority(self, run_id: str) -> None:
        self._leases.pop(run_id, None)
        self._fences.pop(run_id, None)
        self._workflow_spawn_ready_activations.pop(run_id, None)
        self._workflow_start_dispatches.pop(run_id, None)
        self._workflow_recovery_work.pop(run_id, None)
        heartbeat = self._heartbeats.pop(run_id, None)
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
        self._cancels.pop(run_id, None)

    async def _accept_workflow_spawn_control(
        self, parent_run_id: str, outcome: WorkflowSpawnToolOutcome
    ) -> None:

        child_control = outcome.child_control
        admission = child_control.admission
        child_run_id = admission.receipt.run_id
        if child_run_id != outcome.child_start_ref.child_run_id:
            raise UnitOfWorkConflict("workflow spawn child control identity differs")
        self._drop_local_authority(parent_run_id)
        if child_control.kind is WorkflowSpawnChildControlKind.START:
            self._register_workflow_child_start(admission)
        elif child_control.kind is WorkflowSpawnChildControlKind.RECOVER:
            self._register_workflow_child_recovery(admission)
        elif child_control.kind is WorkflowSpawnChildControlKind.CANCEL:
            child = self._uow.read_run(child_run_id)
            if child is None or child.state is not RunState.CANCEL_REQUESTED:
                raise UnitOfWorkConflict(
                    "workflow spawn CANCEL control lacks a cancel-pending child"
                )
            await self._terminalize_cancelled(child, reason="workflow_spawn_child_control")
            return
        elif child_control.kind is WorkflowSpawnChildControlKind.TERMINAL:
            if self._uow.read_child_terminal_result_for_run(child_run_id) is None:
                raise UnitOfWorkConflict("workflow spawn TERMINAL control lacks terminal receipt")
            return
        elif child_control.kind in {
            WorkflowSpawnChildControlKind.ATTACH,
            WorkflowSpawnChildControlKind.WAITING,
        }:
            return
        else:  # pragma: no cover - closed enum exhaustiveness
            raise AssertionError(child_control.kind)
        if child_run_id not in self._live.active_run_ids():
            self._schedule(child_run_id)

    def _register_recovered_spawn_child(self, admission: RuntimeStartAdmission) -> bool:
        if admission.disposition in {
            RuntimeStartDisposition.START_NEW,
            RuntimeStartDisposition.START_ORPHAN,
        }:
            self._register_workflow_child_start(admission)
            return True
        if admission.disposition in {
            RuntimeStartDisposition.RECOVER_START,
            RuntimeStartDisposition.RECOVER_RESUME,
        }:
            self._register_workflow_child_recovery(admission)
            return True
        if admission.disposition in {
            RuntimeStartDisposition.FOREIGN_ACTIVE,
            RuntimeStartDisposition.ATTACH_CURRENT,
            RuntimeStartDisposition.WAITING,
            RuntimeStartDisposition.TERMINAL,
        }:
            return False
        if admission.disposition is RuntimeStartDisposition.CANCEL_PENDING:
            # No Driver authority is returned for this disposition.  Let the
            # canonical cancellation coordinator reacquire and converge it.
            return True
        raise UnitOfWorkConflict("workflow child recovery control is not implemented")

    def _register_workflow_child_recovery(self, admission: RuntimeStartAdmission) -> None:
        activation = admission.activation
        recovery_work = admission.recovery_work
        if activation is None or recovery_work is None:
            raise UnitOfWorkConflict("workflow spawn RECOVER control lacks child authority")
        child_run_id = activation.execution_lease.run_id
        self._leases[child_run_id] = activation.execution_lease
        self._fences[child_run_id] = activation.run_fence
        self._workflow_recovery_work[child_run_id] = recovery_work
        self._cancels.setdefault(child_run_id, CancelToken())
        heartbeat = self._heartbeats.get(child_run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[child_run_id] = asyncio.create_task(
                self._heartbeat(child_run_id),
                name=f"simple-harness-heartbeat:{child_run_id}",
            )

    def _register_workflow_child_start(self, admission: RuntimeStartAdmission) -> None:
        activation = admission.activation
        dispatch_claim = admission.dispatch_claim
        if activation is None or dispatch_claim is None:
            raise UnitOfWorkConflict("workflow spawn START control lacks child authority")
        child_run_id = activation.execution_lease.run_id
        self._leases[child_run_id] = activation.execution_lease
        self._fences[child_run_id] = activation.run_fence
        self._workflow_start_dispatches[child_run_id] = dispatch_claim
        self._cancels.setdefault(child_run_id, CancelToken())
        heartbeat = self._heartbeats.get(child_run_id)
        if heartbeat is None or heartbeat.done():
            self._heartbeats[child_run_id] = asyncio.create_task(
                self._heartbeat(child_run_id),
                name=f"simple-harness-heartbeat:{child_run_id}",
            )

    def _now(self) -> float:
        value = self._ports.clock()
        if not math.isfinite(value) or value < 0:
            raise ValueError("runtime clock must return a finite non-negative value")
        return float(value)

    def _require_started(self) -> None:
        if self._state is not RuntimeLifecycleState.READY:
            raise HarnessError("runtime_not_ready", "Runtime is not ready.")


def build_runtime(
    uow: RuntimeUnitOfWork,
    profiles: Mapping[str, RuntimeProfile],
    drivers: Mapping[str, RuntimeDriver],
    ports: RuntimePorts,
    root_profile_key: str = ROOT_PROFILE_KEY,
    *,
    workflow_runner: object | None = None,
    close_hook: Callable[[], None] | None = None,
    start_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
    async_close_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
) -> Runtime:
    """Build a Runtime with one fixed root Profile and no classifier path."""

    if root_profile_key != ROOT_PROFILE_KEY:
        raise ValueError("root_profile_key is fixed to agent.general")
    bound_profiles = dict(profiles)
    if ROOT_PROFILE_KEY not in bound_profiles:
        raise ValueError("agent.general profile is required")
    profile = bound_profiles[ROOT_PROFILE_KEY]
    if profile.profile_key != ROOT_PROFILE_KEY:
        raise ValueError("agent.general profile binding is inconsistent")
    bound_drivers = dict(drivers)
    if WORKFLOW_DRIVER_KIND in bound_drivers:
        raise ValueError("workflow is an SDK-reserved driver key")
    workflow_profiles = tuple(
        item for item in bound_profiles.values() if item.driver_kind == WORKFLOW_DRIVER_KIND
    )
    if workflow_profiles and workflow_runner is None:
        raise ValueError("workflow profile requires the SDK-owned workflow runner")
    if profile.driver_kind not in bound_drivers and not (
        profile.driver_kind == WORKFLOW_DRIVER_KIND and workflow_runner is not None
    ):
        raise ValueError("agent.general driver is not registered")
    return Runtime(
        uow=uow,
        profiles=bound_profiles,
        drivers=bound_drivers,
        workflow_runner=workflow_runner,
        ports=ports,
        root_profile_key=ROOT_PROFILE_KEY,
        close_hook=close_hook,
        start_hooks=start_hooks,
        async_close_hooks=async_close_hooks,
    )


__all__ = (
    "BASE_AGENT_DRIVER_EXCEPTION_WAKES",
    "ROOT_PROFILE_KEY",
    "WORKFLOW_DRIVER_KIND",
    "DriverCancelOutcome",
    "DriverCancellationCoordinator",
    "DriverCancellationRecovery",
    "DriverInvocation",
    "DriverResult",
    "ReactCheckpointPort",
    "RunClient",
    "Runtime",
    "RuntimeLifecycleState",
    "RuntimeDriver",
    "RuntimePorts",
    "RuntimeProfile",
    "RuntimeReconciliationPort",
    "RuntimeServices",
    "RuntimeUnitOfWork",
    "ToolCatalogGenerationPort",
    "build_runtime",
)
