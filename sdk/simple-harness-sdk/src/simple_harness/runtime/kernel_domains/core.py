# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Runtime construction, lifecycle state and the accessors every part uses."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import HarnessError, JsonValue
from simple_harness.execution.delivery import DeliverySpec
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import ContinuationRecord, ExecutionLease, RunRecord, RunState
from simple_harness.observability import CorrelationContext, Outcome
from simple_harness.providers import CancelToken
from simple_harness.workflow.runner import WorkflowRunner

from ..child_coordinator import ChildCoordinator
from ..child_signal_runtime import ChildSignalRuntime
from ..conversation_memory import ConversationTurnOutput
from ..live_index import LiveRunIndex
from ..orchestration import (
    RuntimeStartAdmission,
    RuntimeStartDispatchClaim,
    WorkflowSpawnReadyActivation,
)
from ..start_snapshot import RunStart, StartSnapshot
from ..terminal import TerminalCoordinator
from .support import (
    WORKFLOW_DRIVER_KIND,
    DriverCancellationCoordinator,
    RunClient,
    RuntimeDriver,
    RuntimeLifecycleState,
    RuntimePorts,
    RuntimeProfile,
    RuntimeServices,
    RuntimeUnitOfWork,
    WorkflowSpawnRuntimeCoordinator,
    _CanonicalWorkflowSpawnRuntimeCoordinator,
)

if TYPE_CHECKING:
    from simple_harness.runtime.workflow_spawn import WorkflowSpawnToolOutcome
    from simple_harness.workflow.execution_ports import WorkflowRecoveryWork
    from simple_harness.workflow.runner import WorkflowRunner

    from ..kernel import Runtime


class RuntimeCore:
    """Runtime construction, lifecycle state and the accessors every part uses."""

    if TYPE_CHECKING:

        async def _abandon_run_authority(self, run_id: str) -> None: ...
        async def _accept_workflow_spawn_control(
            self, parent_run_id: str, outcome: WorkflowSpawnToolOutcome
        ) -> None: ...
        async def _activate(self, run_id: str) -> RunRecord: ...
        async def _command_pump(self) -> None: ...
        async def _delivery_pump(self) -> None: ...
        async def _drain_commands_bounded(self, limit: int) -> bool: ...
        async def _drain_deliveries_bounded(self, limit: int) -> bool: ...
        async def _drain_memory_bounded(self, limit: int) -> bool: ...
        async def _drain_recall_releases(self, limit: int) -> None: ...
        async def _drain_resolved_waits_once(self) -> None: ...
        def _drop_local_authority(self, run_id: str) -> None: ...
        async def _finalize_pending_agent_turn(self, run_id: str) -> bool: ...
        async def _heartbeat(self, run_id: str) -> None: ...
        async def _memory_pump(self) -> None: ...
        def _register_recovered_spawn_child(self, admission: RuntimeStartAdmission) -> bool: ...
        def _register_spawn_ready_activation(
            self, activation: WorkflowSpawnReadyActivation
        ) -> None: ...
        async def _release_child_signal_wait_handoff(
            self, parent_run_id: str, handoff: object
        ) -> None: ...
        def _release_runtime_lease(self, run_id: str) -> None: ...
        def _schedule(self, run_id: str) -> None: ...
        async def _start_run(self, start: RunStart) -> RunRecord: ...
        def _terminalize(
            self,
            run: RunRecord,
            *,
            state: RunState,
            payload: Mapping[str, JsonValue],
            deliveries: Sequence[DeliverySpec],
            continuation_claim: ContinuationRecord | None = None,
            conversation_output: ConversationTurnOutput | None = None,
        ) -> None: ...
        async def _terminalize_cancelled(
            self, run: RunRecord, *, snapshot: StartSnapshot | None = None, reason: str = "user"
        ) -> None: ...
        async def _wake_continuation(self, run_id: str) -> None: ...
        async def _wake_drain(self) -> None: ...

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
            from ..drivers.workflow import (
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
        self.client = RunClient(cast("Runtime", self))  # 组合类 Runtime 才会走到这里
        self.children = ChildCoordinator(self)

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

    def _now(self) -> float:
        value = self._ports.clock()
        if not math.isfinite(value) or value < 0:
            raise ValueError("runtime clock must return a finite non-negative value")
        return float(value)

    def _require_started(self) -> None:
        if self._state is not RuntimeLifecycleState.READY:
            raise HarnessError("runtime_not_ready", "Runtime is not ready.")
