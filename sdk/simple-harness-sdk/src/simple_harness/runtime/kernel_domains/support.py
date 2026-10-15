# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Module-level definitions of the runtime kernel: driver contracts, ports, the run client and the runtime builder."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, runtime_checkable
from uuid import uuid4

from simple_harness.contracts import (
    ExecutionSessionId,
    FrozenJsonValue,
    HarnessError,
    JsonValue,
    Message,
    RequestId,
    RunId,
    canonical_json,
    thaw_json,
)
from simple_harness.execution.audit import (
    RunAuditUnavailable,
    RunOperationAuditSnapshotV1,
    audit_hash,
)
from simple_harness.execution.command_ingress import CommandClaim
from simple_harness.execution.context_authority import (
    ContextRouteReceipt,
    RunContextAuthorityPort,
    RuntimeDecisionSinkPort,
    TaskExecutionAuthorityPort,
    ToolCatalogSnapshot,
)
from simple_harness.execution.context_staging import (
    ContextStageKind,
    ContextStageRecord,
    ContextStageState,
    ContextStagingRepository,
)
from simple_harness.execution.delivery import DeliveryDispatcher, DeliverySpec
from simple_harness.execution.dispatch import ProviderInvocationCoordinator
from simple_harness.execution.fences import RunFenceLease, RunFencePort
from simple_harness.execution.memory_outbox import MemoryDispatcher
from simple_harness.execution.recovery import WaitBlockerSpec
from simple_harness.execution.sqlite.stage_audit import begin_prepare, finish
from simple_harness.execution.uow import (
    ContinuationRecord,
    DecisionState,
    ExecutionLease,
    ExecutionUnitOfWork,
    FaultHook,
    RunRecord,
    RunState,
    UnitOfWorkConflict,
    WorkflowCheckpoint,
)
from simple_harness.observability import ObservabilityRuntime
from simple_harness.providers import CancelToken, ProviderReconciliationPort
from simple_harness.tools.authorization import AuthorizationDecision, AuthorizationPort
from simple_harness.tools.executor import EffectExecutor, ToolAuthorizationPending
from simple_harness.tools.reconciliation import ToolReconciliationPort
from simple_harness.workflow.errors import WorkflowDependencyUnavailable
from simple_harness.workflow.runner import WorkflowRunner

from ..admission import AdmissionPort, AllowAllAdmission
from ..agent_memory import AgentMemoryPort, MemoryFailurePolicy
from ..agent_turn import AgentTurnOutcome
from ..commands import (
    CancelCommandIntent,
    CommandErrorCode,
    CommandReceipt,
    CommandSnapshot,
    CommandState,
    ContinueCommandIntent,
    StartCommandIntent,
)
from ..context import ContextPort
from ..conversation_context import claim_context_preparation, context_query_id
from ..conversation_context_provider import (
    ConversationContextBounds,
    ConversationContextProviderPort,
    ConversationContextRequest,
    CurrentMessageContextProvider,
)
from ..conversation_memory import (
    ContextPreparationMode,
    ConversationContinuationInput,
    ConversationTurnInput,
    ConversationTurnOutput,
    _message_from_json,
)
from ..orchestration import (
    RuntimeActivationClaim,
    RuntimeStartDispatchClaim,
    RuntimeStartDisposition,
    WorkflowLaunchTicketPort,
    WorkflowSpawnOrigin,
    WorkflowSpawnReadyActivation,
    WorkflowSpawnSelection,
    workflow_catalog_selection_from_authority,
    workflow_catalog_selection_from_json,
    workflow_spawn_child_request_id,
    workflow_spawn_child_run_id,
    workflow_spawn_operation_id,
)
from ..start_snapshot import HostControlRunStartV1, RunStart, StartSnapshot, bind_start_snapshot
from ..workflow_spawn import (
    WorkflowSpawnBatchAction,
    WorkflowSpawnChildControlKind,
    WorkflowSpawnToolContext,
    _create_workflow_spawn_child_control,
    _create_workflow_spawn_failed,
    _create_workflow_spawn_invocation,
    _create_workflow_spawn_tool_outcome,
    workflow_spawn_completion_receipt_id,
)

if TYPE_CHECKING:
    from simple_harness.execution.base_agent import (
        AgentBindingRecord,
        AgentTurnRecord,
        AgentTurnResultRecord,
    )
    from simple_harness.runtime.workflow_spawn import (
        WorkflowSpawnCoordinatorOutcome,
        WorkflowSpawnToolOutcome,
    )
    from simple_harness.workflow.execution_ports import (
        WorkflowRecoveryWork,
        WorkflowRetryWake,
        WorkflowTerminalOutcome,
        WorkflowTransaction,
    )
    from simple_harness.workflow.runner import WorkflowRunner


if TYPE_CHECKING:
    from ..kernel import Runtime


# 名字照拆分前的运行内核模块写死：按 simple_harness.runtime.kernel 配的级别和处理器继续生效
logger = logging.getLogger("simple_harness.runtime.kernel")

#: Re-wakes granted to a BaseAgent Run whose driver raised (per process, per Run).
BASE_AGENT_DRIVER_EXCEPTION_WAKES = 3

T = TypeVar("T")

ROOT_PROFILE_KEY = "agent.general"
WORKFLOW_DRIVER_KIND = "workflow"
BASE_AGENT_DRIVER_KIND = "base_agent"


class RuntimeLifecycleState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    READY = "ready"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    profile_key: str
    driver_kind: str

    def __post_init__(self) -> None:
        for name in ("profile_key", "driver_kind"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True, slots=True)
class DriverInvocation:
    run: RunRecord
    start: StartSnapshot
    execution_lease: ExecutionLease
    run_fence: RunFenceLease
    services: RuntimeServices
    continuations: tuple[ContinuationRecord, ...] = ()
    workflow_start_dispatch: RuntimeStartDispatchClaim | None = None
    workflow_recovery_work: WorkflowRecoveryWork | None = None
    workflow_spawn_ready_activation: WorkflowSpawnReadyActivation | None = None

    def __post_init__(self) -> None:
        ready = self.workflow_spawn_ready_activation
        if ready is not None and (
            ready.execution_lease != self.execution_lease
            or ready.run_fence != self.run_fence
            or ready.execution_lease.run_id != self.run.run_id
        ):
            raise ValueError("workflow spawn ready activation differs from Driver authority")
        recovery = self.workflow_recovery_work
        if recovery is not None and (
            recovery.run_id != self.run.run_id or self.workflow_start_dispatch is not None
        ):
            raise ValueError("workflow recovery carrier differs from Driver authority")


@dataclass(frozen=True, slots=True)
class DriverResult:
    state: RunState
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    deliveries: tuple[DeliverySpec, ...] = ()
    wait_blocker: WaitBlockerSpec | None = None
    workflow_spawn_control: WorkflowSpawnToolOutcome | None = None
    workflow_terminal: WorkflowTerminalOutcome | None = None
    workflow_retry_wake: WorkflowRetryWake | None = None
    authorization_wait: ToolAuthorizationPending | None = None
    conversation_output: ConversationTurnOutput | None = None
    agent_turn_outcome: AgentTurnOutcome | None = None

    def __post_init__(self) -> None:
        state = RunState(self.state)
        if state not in {RunState.WAITING, RunState.COMPLETED, RunState.FAILED}:
            raise ValueError("driver result must be waiting, completed, or failed")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "deliveries", tuple(self.deliveries))
        if self.agent_turn_outcome is not None:
            if not isinstance(self.agent_turn_outcome, AgentTurnOutcome):
                raise TypeError("agent_turn_outcome must use AgentTurnOutcome")
            if (
                state is not RunState.WAITING
                or self.conversation_output is not None
                or self.wait_blocker is not None
                or self.workflow_spawn_control is not None
                or self.workflow_terminal is not None
                or self.workflow_retry_wake is not None
                or self.authorization_wait is not None
            ):
                raise ValueError("agent turn outcome requires an exclusive WAITING result")
        if self.conversation_output is not None:
            if not isinstance(self.conversation_output, ConversationTurnOutput):
                raise TypeError("conversation_output must use ConversationTurnOutput")
            if state is not RunState.COMPLETED:
                raise ValueError("only COMPLETED may carry conversation_output")
        if self.workflow_spawn_control is not None and (
            state is not RunState.WAITING or self.wait_blocker is not None
        ):
            raise ValueError("workflow spawn control requires an exclusive WAITING result")
        if self.workflow_terminal is not None and state not in {
            RunState.COMPLETED,
            RunState.FAILED,
        }:
            raise ValueError("workflow terminal outcome requires COMPLETED or FAILED state")
        if self.workflow_retry_wake is not None and (
            state is not RunState.WAITING or self.wait_blocker is not None
        ):
            raise ValueError("workflow retry wake requires an exclusive WAITING result")
        if self.authorization_wait is not None and (
            state is not RunState.WAITING
            or self.wait_blocker is not None
            or self.workflow_spawn_control is not None
            or self.workflow_retry_wake is not None
        ):
            raise ValueError("authorization wait requires an exclusive WAITING result")


@runtime_checkable
class RuntimeDriver(Protocol):
    async def start(
        self,
        invocation: DriverInvocation,
        *,
        context: ContextPort,
        cancel: CancelToken,
    ) -> DriverResult: ...


@dataclass(frozen=True, slots=True)
class DriverCancellationRecovery:
    execution_lease: ExecutionLease
    run_fence: RunFenceLease


@dataclass(frozen=True, slots=True)
class DriverCancelOutcome:
    cancel_id: str
    generation: int
    phase: str
    blocker_ids: tuple[str, ...]
    terminal: bool


@runtime_checkable
class DriverCancellationCoordinator(Protocol):
    async def cancel(
        self,
        run: RunRecord,
        start_snapshot: StartSnapshot,
        *,
        reason: str,
        now: float,
        recovery: DriverCancellationRecovery,
    ) -> DriverCancelOutcome: ...


@runtime_checkable
class ToolCatalogGenerationPort(Protocol):
    def current_generation(self) -> int: ...

    def resolve(self, generation: int, content_fingerprint: str) -> ToolCatalogSnapshot | None: ...


@runtime_checkable
class RuntimeReconciliationPort(Protocol):
    async def reconcile(self) -> None: ...


@runtime_checkable
class WorkflowSpawnRuntimeCoordinator(Protocol):
    async def catalog_snapshot(self):  # type: ignore[no-untyped-def]
        ...

    async def execute(self, invocation):  # type: ignore[no-untyped-def]
        ...

    async def continue_ready(
        self, activation: WorkflowSpawnReadyActivation
    ) -> WorkflowSpawnCoordinatorOutcome: ...


@runtime_checkable
class ReactCheckpointPort(Protocol):
    def read_react_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None: ...

    def read_initial_react_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None: ...

    def cas_react_checkpoint(
        self,
        *,
        run_id: str,
        lease: ExecutionLease,
        expected_version: int | None,
        checkpoint: Mapping[str, JsonValue],
        checkpoint_hash: str,
        now: float,
        fault: Callable[[str], None] | None = None,
    ) -> WorkflowCheckpoint: ...

    def ack_spawn_child_continuation_and_continue_batch(
        self,
        *,
        run_id: str,
        continuation_claim: ContinuationRecord,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        now: float,
        fault: Callable[[str], None] | None = None,
    ) -> WorkflowCheckpoint: ...

    def commit_pending_spawn_child_completion_and_react_ready(
        self,
        *,
        run_id: str,
        expected_checkpoint_version: int,
        execution_lease: ExecutionLease,
        run_fence: RunFenceLease,
        now: float,
        fault: Callable[[str], None] | None = None,
    ) -> WorkflowCheckpoint: ...


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    provider: ProviderInvocationCoordinator
    tools: EffectExecutor
    authorization: AuthorizationPort
    context: ContextPort
    delivery: DeliveryDispatcher
    tool_reconciliation: ToolReconciliationPort
    reconciliation: RuntimeReconciliationPort
    provider_reconciliation: ProviderReconciliationPort
    react_checkpoint: ReactCheckpointPort
    tool_catalog: ToolCatalogGenerationPort | None = None
    workflow_spawn: WorkflowSpawnRuntimeCoordinator | None = None
    run_context_authority: RunContextAuthorityPort | None = None
    runtime_decision_sink: RuntimeDecisionSinkPort | None = None
    task_execution_authority: TaskExecutionAuthorityPort | None = None
    operation_audit: object | None = None
    operation_audit_clock: Callable[[], float] = time.time


class _CanonicalWorkflowSpawnRuntimeCoordinator:
    def __init__(
        self,
        *,
        uow: RuntimeUnitOfWork,
        runner: WorkflowRunner,
        owner_id: str,
        lease_ttl_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._uow = uow
        self._runner = runner
        self._owner_id = owner_id
        self._lease_ttl_seconds = lease_ttl_seconds
        self._clock = clock

    async def catalog_snapshot(self):  # type: ignore[no-untyped-def]

        async def operation(transaction):  # type: ignore[no-untyped-def]
            authority = await self._uow.read_catalog(transaction)
            return workflow_catalog_selection_from_authority(authority)

        return await self._uow.run_atomic(operation, fault_label="runtime:workflow_spawn:catalog")

    async def continue_ready(
        self, activation: WorkflowSpawnReadyActivation
    ) -> WorkflowSpawnCoordinatorOutcome:
        async def operation(
            transaction: WorkflowTransaction,
        ) -> WorkflowSpawnCoordinatorOutcome:

            issued = await self._uow.read_issued(
                transaction, activation.ready_receipt.spawn_operation_id
            )
            if issued is None:
                raise UnitOfWorkConflict("workflow spawn ticket disappeared")
            ticket, launch = issued
            terminal_result = await self._uow.read_spawn_continuation_outcome(
                transaction, activation.ready_receipt.spawn_operation_id
            )
            prior = await self._uow.read_spawn_admission_outcome(
                transaction, activation.ready_receipt.spawn_operation_id
            )
            if terminal_result is not None and prior is None:
                return _create_workflow_spawn_failed(
                    tool_result=terminal_result,
                    completion_receipt_id=workflow_spawn_completion_receipt_id(
                        activation.ready_receipt.spawn_operation_id
                    ),
                    batch_action=WorkflowSpawnBatchAction.CONTINUE,
                )
            if prior is not None:
                if terminal_result is None:
                    raise UnitOfWorkConflict(
                        "workflow spawn admission lost its terminal Tool result"
                    )
                admission = await self._uow.resume_admitted_runtime_start(
                    transaction,
                    ticket,
                    RuntimeActivationClaim(
                        self._owner_id,
                        lease_ttl_seconds=self._lease_ttl_seconds,
                    ),
                    now=self._clock(),
                )
                kind = {
                    RuntimeStartDisposition.START_NEW: WorkflowSpawnChildControlKind.START,
                    RuntimeStartDisposition.START_ORPHAN: WorkflowSpawnChildControlKind.START,
                    RuntimeStartDisposition.RECOVER_START: WorkflowSpawnChildControlKind.RECOVER,
                    RuntimeStartDisposition.RECOVER_RESUME: WorkflowSpawnChildControlKind.RECOVER,
                    RuntimeStartDisposition.ATTACH_CURRENT: WorkflowSpawnChildControlKind.ATTACH,
                    RuntimeStartDisposition.FOREIGN_ACTIVE: WorkflowSpawnChildControlKind.WAITING,
                    RuntimeStartDisposition.WAITING: WorkflowSpawnChildControlKind.WAITING,
                    RuntimeStartDisposition.CANCEL_PENDING: WorkflowSpawnChildControlKind.CANCEL,
                    RuntimeStartDisposition.TERMINAL: WorkflowSpawnChildControlKind.TERMINAL,
                }[admission.disposition]
                child_control = _create_workflow_spawn_child_control(
                    kind=kind,
                    admission=admission,
                )
                return _create_workflow_spawn_tool_outcome(
                    tool_result=terminal_result,
                    child_control=child_control,
                    child_start_ref=prior.child_start_ref,
                    suspension=prior.suspension,
                )
            try:
                verified = await self._uow.verify(transaction, ticket)
            except UnitOfWorkConflict:
                failed = await self._uow.settle_spawn_continuation_catalog_stale(
                    transaction,
                    activation.continuation_claim,
                    activation.ready_receipt,
                    now=self._clock(),
                )
                return _create_workflow_spawn_failed(
                    tool_result=failed,
                    completion_receipt_id=workflow_spawn_completion_receipt_id(
                        activation.ready_receipt.spawn_operation_id
                    ),
                    batch_action=WorkflowSpawnBatchAction.CONTINUE,
                )
            start = RunStart(
                execution_session_id=ExecutionSessionId(verified.session_id),
                run_id=RunId(verified.resolved_run_id),
                request_id=RequestId(verified.request_id),
                turn_id=verified.turn_id,
                input=launch.start_input,
                tool_catalog_generation=verified.tool_catalog_generation,
            )
            try:
                request = self._runner.prepare_start_admission(verified, start)
            except WorkflowDependencyUnavailable:
                evidence = self._runner._prove_graph_unavailable(verified, activation)
                failed = await self._uow.settle_spawn_continuation_graph_unavailable(
                    transaction,
                    activation.continuation_claim,
                    activation.ready_receipt,
                    evidence,
                    now=self._clock(),
                )
                return _create_workflow_spawn_failed(
                    tool_result=failed,
                    completion_receipt_id=workflow_spawn_completion_receipt_id(
                        activation.ready_receipt.spawn_operation_id
                    ),
                    batch_action=WorkflowSpawnBatchAction.CONTINUE,
                )
            snapshot = bind_start_snapshot(
                start,
                profile_key=verified.profile_key,
                driver_kind=WORKFLOW_DRIVER_KIND,
                workflow_admission=request,
            )
            return await self._uow.continue_spawn_admission(
                transaction,
                ticket,
                activation.continuation_claim,
                start,
                request,
                snapshot,
                RuntimeActivationClaim(
                    self._owner_id,
                    lease_ttl_seconds=self._lease_ttl_seconds,
                ),
                now=self._clock(),
            )

        return await self._uow.run_atomic(operation, fault_label="runtime:spawn_ready:continue")


class RuntimeUnitOfWork(ExecutionUnitOfWork, RunFencePort, WorkflowLaunchTicketPort, Protocol):
    @property
    def transaction_owner(self) -> object: ...

    async def run_atomic(
        self,
        operation: Callable[[WorkflowTransaction], Awaitable[T]],
        *,
        fault_label: str,
    ) -> T: ...

    def submit_start_command(self, intent: StartCommandIntent, *, now: float) -> CommandReceipt: ...

    def submit_continue_command(
        self, intent: ContinueCommandIntent, *, now: float
    ) -> CommandReceipt: ...

    def submit_cancel_command(
        self, intent: CancelCommandIntent, *, now: float
    ) -> CommandReceipt: ...

    def get_command_receipt(self, command_id: str) -> CommandReceipt: ...

    def get_command_snapshot(self, command_id: str) -> CommandSnapshot: ...

    def reserve_legacy_run_mode(self, *, run_id: str, intent_hash: str, now: float) -> None: ...

    def reserve_host_control_run_mode(
        self, *, run_id: str, intent_hash: str, now: float
    ) -> None: ...

    def require_legacy_or_unmanaged_run(self, run_id: str) -> None: ...

    # BaseAgent (execution schema v10)
    def reserve_base_agent_run_mode(self, *, run_id: str, intent_hash: str, now: float) -> None: ...

    def require_base_agent_run_mode(self, run_id: str) -> None: ...

    def submit_agent_input(
        self,
        *,
        agent_id: str,
        run_id: str,
        turn_id: str,
        input_id: str,
        input_hash: str,
        input_json: Mapping[str, JsonValue],
        continuation_payload: Mapping[str, JsonValue],
        now: float,
        max_pending_inputs: int | None = None,
    ) -> AgentTurnRecord: ...

    def read_agent_binding_for_run(self, run_id: str) -> AgentBindingRecord | None: ...

    def read_agent_turn(self, turn_id: str) -> AgentTurnRecord | None: ...

    def read_pending_agent_turn(self, run_id: str) -> AgentTurnRecord | None: ...

    def read_open_agent_turn(self, run_id: str) -> AgentTurnRecord | None: ...

    def read_agent_turn_result(self, turn_id: str) -> AgentTurnResultRecord | None: ...

    def list_runs_with_open_agent_turns(self) -> tuple[str, ...]: ...

    def stage_agent_turn_result(
        self,
        *,
        turn_id: str,
        result_hash: str,
        result_json: Mapping[str, JsonValue],
        provider_turn_ordinal_from: int | None,
        provider_turn_ordinal_to: int | None,
        execution_lease: ExecutionLease,
        now: float,
    ) -> AgentTurnRecord: ...

    def commit_agent_turn_result_and_idle(
        self,
        *,
        run_id: str,
        expected_version: int,
        turn_id: str,
        event_id: str,
        payload: Mapping[str, JsonValue],
        continuation_claim: ContinuationRecord | None,
        execution_lease: ExecutionLease,
        receipt_id: str | None,
        usage_refs: tuple[str, ...] = (),
        now: float,
    ) -> AgentTurnResultRecord: ...

    def claim_next_command(
        self, *, owner_id: str, now: float, lease_seconds: float
    ) -> CommandClaim | None: ...

    def transition_command(
        self,
        claim: CommandClaim,
        *,
        expected: CommandState,
        target: CommandState,
        now: float,
    ) -> CommandReceipt: ...

    def retry_command(
        self,
        claim: CommandClaim,
        *,
        error_code: str,
        retry_at: float,
        now: float,
    ) -> CommandReceipt: ...

    def heartbeat_command(
        self, claim: CommandClaim, *, now: float, lease_seconds: float
    ) -> CommandClaim: ...

    def reject_command(
        self,
        claim: CommandClaim,
        *,
        error_code: CommandErrorCode,
        now: float,
    ) -> CommandReceipt: ...

    def apply_start_command(
        self,
        claim: CommandClaim,
        *,
        execution_session_id: str,
        request_id: str,
        profile_key: str,
        driver_kind: str,
        snapshot: Mapping[str, JsonValue],
        event_id: str,
        now: float,
        user_id: str,
        context_stage_id: str | None = None,
        context_stage_hash: str | None = None,
        fault: FaultHook | None = None,
    ) -> RunRecord: ...

    def apply_continue_command(
        self,
        claim: CommandClaim,
        *,
        continuation_id: str,
        payload: Mapping[str, JsonValue],
        now: float,
        context_stage_id: str | None = None,
        context_stage_hash: str | None = None,
        fault: FaultHook | None = None,
    ) -> ContinuationRecord: ...

    def apply_cancel_command(
        self,
        claim: CommandClaim,
        *,
        event_id: str,
        now: float,
        fault: FaultHook | None = None,
    ) -> RunRecord: ...


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    provider: ProviderInvocationCoordinator
    tools: EffectExecutor
    authorization: AuthorizationPort
    context: ContextPort
    delivery: DeliveryDispatcher
    tool_reconciliation: ToolReconciliationPort
    reconciliation: RuntimeReconciliationPort
    provider_reconciliation: ProviderReconciliationPort
    react_checkpoint: ReactCheckpointPort
    tool_catalog: ToolCatalogGenerationPort
    admission: AdmissionPort = field(default_factory=AllowAllAdmission)
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    owner_id: str = field(default_factory=lambda: f"runtime-{uuid4().hex}")
    lease_ttl_seconds: float = 30.0
    close_timeout_seconds: float = 5.0
    conversation_memory_enabled: bool = False
    memory_dispatcher: MemoryDispatcher | None = None
    context_staging: ContextStagingRepository | None = None
    context_preparation_mode: ContextPreparationMode | None = None
    agent_memory: AgentMemoryPort | None = None
    context_provider: ConversationContextProviderPort | None = None
    memory_failure_policy: MemoryFailurePolicy = MemoryFailurePolicy.DEGRADE_RECALL_AND_RETRY_RECORD
    observability: ObservabilityRuntime | None = None
    run_context_authority: RunContextAuthorityPort | None = None
    runtime_decision_sink: RuntimeDecisionSinkPort | None = None
    task_execution_authority: TaskExecutionAuthorityPort | None = None

    def __post_init__(self) -> None:
        for name in (
            "provider",
            "tools",
            "authorization",
            "context",
            "delivery",
            "tool_reconciliation",
            "reconciliation",
            "provider_reconciliation",
            "react_checkpoint",
            "tool_catalog",
        ):
            if getattr(self, name) is None:
                raise TypeError(f"{name} Port is required")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise ValueError("owner_id is required")
        if (
            not isinstance(self.lease_ttl_seconds, (int, float))
            or isinstance(self.lease_ttl_seconds, bool)
            or not math.isfinite(float(self.lease_ttl_seconds))
            or self.lease_ttl_seconds <= 0
        ):
            raise ValueError("lease_ttl_seconds must be finite and positive")
        if (
            not isinstance(self.close_timeout_seconds, (int, float))
            or isinstance(self.close_timeout_seconds, bool)
            or not math.isfinite(float(self.close_timeout_seconds))
            or self.close_timeout_seconds <= 0
        ):
            raise ValueError("close_timeout_seconds must be finite and positive")
        if self.conversation_memory_enabled and self.context_staging is None:
            raise TypeError("enabled conversation Memory requires context staging")
        if self.conversation_memory_enabled and (
            self.memory_dispatcher is None and self.agent_memory is None
        ):
            raise TypeError("enabled conversation Memory requires an Agent Memory authority")
        if self.conversation_memory_enabled and self.context_preparation_mode is None:
            raise TypeError("enabled conversation Memory requires preparation mode")
        if self.context_preparation_mode is not None:
            object.__setattr__(
                self,
                "context_preparation_mode",
                ContextPreparationMode(self.context_preparation_mode),
            )
        if self.agent_memory is not None:
            for method_name in ("record_committed_turn",):
                if not callable(getattr(self.agent_memory, method_name, None)):
                    raise TypeError(f"memory must implement {method_name}")
            if self.context_provider is None:
                object.__setattr__(self, "context_provider", CurrentMessageContextProvider())
        object.__setattr__(
            self, "memory_failure_policy", MemoryFailurePolicy(self.memory_failure_policy)
        )


class RunClient:
    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    def read_provider_context_use(self, run_id: RunId, request_id: RequestId):
        """Read a receipt-bound invocation snapshot; no payload parsing or SQL by Host."""
        self._runtime._require_started()
        if not isinstance(run_id, RunId) or not isinstance(request_id, RequestId):
            raise TypeError("typed RunId and RequestId are required")
        return self._runtime._ports.provider.read_provider_context_use(run_id, request_id)

    async def start(self, value: RunStart) -> RunRecord:
        self._runtime._require_started()
        if not isinstance(value, RunStart):
            raise TypeError("value must use RunStart")
        if value.start_mode != "ordinary":
            raise HarnessError(
                "host_control_entrypoint_required",
                "Host control Runs require start_host_control().",
            )
        if self._runtime._ports.agent_memory is not None and value.conversation is None:
            raise HarnessError(
                "conversation_entrypoint_required",
                "Enabled Agent Memory requires start_conversation().",
            )
        self._runtime._reserve_legacy_start(value)
        return await self._runtime._start_run(value)

    async def start_host_control(self, value: HostControlRunStartV1) -> RunRecord:
        """Start one Memory-neutral Host control root through typed authority."""

        self._runtime._require_started()
        if not isinstance(value, HostControlRunStartV1):
            raise TypeError("value must use HostControlRunStartV1")
        intent_hash = hashlib.sha256(canonical_json(value.to_json()).encode("utf-8")).hexdigest()
        self._runtime._uow.reserve_host_control_run_mode(
            run_id=value.run_id.value,
            intent_hash=intent_hash,
            now=self._runtime._now(),
        )
        existing = self._runtime._uow.read_run(value.run_id.value)
        if existing is not None:
            raw = self._runtime._uow.read_start_snapshot(value.run_id.value)
            if raw is None:
                raise UnitOfWorkConflict("Host control start snapshot disappeared")
            snapshot = StartSnapshot.from_json(raw)
            expected = value.to_run_start()
            if (
                snapshot.start_mode != "host_control"
                or snapshot.host_control_authority != value.authority
                or snapshot.host_control_user_id != value.user_id
                or snapshot.turn_id != value.turn_id
                or existing.execution_session_id != value.execution_session_id.value
                or existing.request_id != value.request_id.value
                or snapshot.input != expected.input
                or snapshot.tool_catalog_generation != value.tool_catalog_generation
                or snapshot.tool_catalog_fingerprint != value.tool_catalog_fingerprint
                or snapshot.provider_budget_fingerprint != value.provider_budget_fingerprint
            ):
                raise UnitOfWorkConflict("Host control start identity was reused differently")
            return existing
        return await self._runtime._start_run(value.to_run_start())

    async def submit_start(self, intent: StartCommandIntent) -> CommandReceipt:
        self._runtime._require_started()
        if not isinstance(intent, StartCommandIntent):
            raise TypeError("intent must use StartCommandIntent")
        receipt = self._runtime._uow.submit_start_command(intent, now=self._runtime._now())
        self._runtime._command_wake.set()
        return receipt

    async def submit_continue(self, intent: ContinueCommandIntent) -> CommandReceipt:
        self._runtime._require_started()
        if not isinstance(intent, ContinueCommandIntent):
            raise TypeError("intent must use ContinueCommandIntent")
        receipt = self._runtime._uow.submit_continue_command(intent, now=self._runtime._now())
        self._runtime._command_wake.set()
        return receipt

    async def submit_cancel(self, intent: CancelCommandIntent) -> CommandReceipt:
        self._runtime._require_started()
        if not isinstance(intent, CancelCommandIntent):
            raise TypeError("intent must use CancelCommandIntent")
        receipt = self._runtime._uow.submit_cancel_command(intent, now=self._runtime._now())
        self._runtime._command_wake.set()
        return receipt

    async def read_run_operation_audit(
        self, run_id: RunId, *, limit: int = 256
    ) -> RunOperationAuditSnapshotV1:

        reader = getattr(self._runtime._uow, "read_run_operation_audit", None)
        if reader is None:
            raise RunAuditUnavailable("audit_reader_unsupported")
        return await asyncio.to_thread(reader, run_id, limit=limit)

    async def open_run_operation_audit(self, run_id: RunId, *, page_size: int = 256):

        reader = getattr(self._runtime._uow, "open_run_operation_audit", None)
        if reader is None:
            raise RunAuditUnavailable("audit_reader_unsupported")
        return await asyncio.to_thread(reader, run_id, page_size=page_size)

    async def read_run_operation_audit_page(self, run_id: RunId, *, cursor: str):

        reader = getattr(self._runtime._uow, "read_run_operation_audit_page", None)
        if reader is None:
            raise RunAuditUnavailable("audit_reader_unsupported")
        return await asyncio.to_thread(reader, run_id, cursor=cursor)

    async def get_command(self, command_id: str) -> CommandSnapshot:
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("command_id is required")
        return self._runtime._uow.get_command_snapshot(command_id)

    async def open_context_stage_operation_audit(self, stage_id: str, *, page_size: int = 256):

        reader = getattr(self._runtime._uow, "open_context_stage_operation_audit", None)
        if reader is None:
            raise RunAuditUnavailable("audit_reader_unsupported")
        return await asyncio.to_thread(reader, stage_id, page_size=page_size)

    async def read_context_stage_operation_audit_page(self, stage_id: str, *, cursor: str):

        reader = getattr(self._runtime._uow, "read_context_stage_operation_audit_page", None)
        if reader is None:
            raise RunAuditUnavailable("audit_reader_unsupported")
        return await asyncio.to_thread(reader, stage_id, cursor=cursor)

    async def open_command_operation_audit(self, command_id: str, *, page_size: int = 256):

        reader = getattr(self._runtime._uow, "open_command_operation_audit", None)
        if reader is None:
            raise RunAuditUnavailable("audit_reader_unsupported")
        return await asyncio.to_thread(reader, command_id, page_size=page_size)

    async def read_command_operation_audit_page(self, command_id: str, *, cursor: str):

        reader = getattr(self._runtime._uow, "read_command_operation_audit_page", None)
        if reader is None:
            raise RunAuditUnavailable("audit_reader_unsupported")
        return await asyncio.to_thread(reader, command_id, cursor=cursor)

    async def start_conversation(
        self,
        value: ConversationTurnInput,
        *,
        run_id: RunId | None = None,
        request_id: RequestId | None = None,
        turn_id: str | None = None,
        tool_catalog_generation: int = 1,
        tool_catalog_fingerprint: str | None = None,
        provider_budget_fingerprint: str | None = None,
        input: Mapping[str, JsonValue] | None = None,
        initial_route_receipt: ContextRouteReceipt | None = None,
        initial_route_receipt_hash: str | None = None,
    ) -> RunRecord:
        """Prepare one durable Context stage and start a trusted conversation Turn."""

        self._runtime._require_started()
        if not isinstance(value, ConversationTurnInput):
            raise TypeError("value must use ConversationTurnInput")
        resolved_run = run_id or RunId(uuid4().hex)
        resolved_request = request_id or RequestId(f"{resolved_run.value}:request")
        resolved_turn = turn_id or resolved_run.value
        start_input = cast(
            Mapping[str, JsonValue],
            dict(input or {"messages": [value.message.to_dict()]}),
        )
        preflight_start = RunStart(
            ExecutionSessionId(value.identity.session_id),
            resolved_run,
            resolved_request,
            resolved_turn,
            start_input,
            tool_catalog_generation,
            tool_catalog_fingerprint,
            provider_budget_fingerprint,
            conversation=value,
            initial_route_receipt=initial_route_receipt,
            initial_route_receipt_hash=initial_route_receipt_hash,
        )
        reserve = getattr(self._runtime._uow, "reserve_legacy_run_mode", None)
        if callable(reserve):
            reserve(
                run_id=resolved_run.value,
                intent_hash=hashlib.sha256(
                    canonical_json(
                        {
                            "run_id": resolved_run.value,
                            "request_id": resolved_request.value,
                            "turn_id": resolved_turn,
                            "conversation": value.to_json(),
                            "input": dict(start_input),
                            "tool_catalog_generation": tool_catalog_generation,
                            "initial_route_receipt": (
                                None
                                if initial_route_receipt is None
                                else initial_route_receipt.to_json()
                            ),
                            "initial_route_receipt_hash": initial_route_receipt_hash,
                        }
                    ).encode()
                ).hexdigest(),
                now=self._runtime._now(),
            )
        existing = self._runtime._uow.read_run(resolved_run.value)
        if existing is not None:
            raw = self._runtime._uow.read_start_snapshot(resolved_run.value)
            if raw is None:
                raise UnitOfWorkConflict("conversation start snapshot disappeared")
            snapshot = StartSnapshot.from_json(raw)
            if (
                snapshot.conversation != value
                or snapshot.turn_id != resolved_turn
                or existing.request_id != resolved_request.value
                or snapshot.initial_route_receipt != initial_route_receipt
                or snapshot.initial_route_receipt_hash != initial_route_receipt_hash
            ):
                raise UnitOfWorkConflict("conversation start identity was reused differently")
            return existing
        memory = self._runtime._ports.agent_memory
        if memory is None:
            return await self._runtime._start_run(preflight_start)
        staged = await self._prepare_agent_context(
            kind=ContextStageKind.ROOT,
            identity_key=resolved_run.value,
            root_run_id=resolved_run.value,
            continuation_id=None,
            turn_id=resolved_turn,
            value=value,
        )
        if staged.private_snapshot is None or staged.private_snapshot_hash is None:
            concurrent = self._runtime._uow.read_run(resolved_run.value)
            raw = self._runtime._uow.read_start_snapshot(resolved_run.value)
            if concurrent is not None and raw is not None:
                snapshot = StartSnapshot.from_json(raw)
                if (
                    snapshot.conversation == value
                    and snapshot.turn_id == resolved_turn
                    and concurrent.request_id == resolved_request.value
                    and snapshot.context_stage_id == staged.stage_id
                    and snapshot.initial_route_receipt == initial_route_receipt
                    and snapshot.initial_route_receipt_hash == initial_route_receipt_hash
                ):
                    return concurrent
            raise UnitOfWorkConflict("prepared context bytes are unavailable")
        return await self._runtime._start_run(
            RunStart(
                ExecutionSessionId(value.identity.session_id),
                resolved_run,
                resolved_request,
                resolved_turn,
                start_input,
                tool_catalog_generation,
                tool_catalog_fingerprint,
                provider_budget_fingerprint,
                conversation=value,
                context_preparation_mode=ContextPreparationMode.SDK_PREPARED,
                context_stage_id=staged.stage_id,
                context_stage_hash=staged.private_snapshot_hash,
                prepared_context=staged.private_snapshot,
                initial_route_receipt=initial_route_receipt,
                initial_route_receipt_hash=initial_route_receipt_hash,
            )
        )

    async def _prepare_agent_context(
        self,
        *,
        kind: ContextStageKind,
        identity_key: str,
        root_run_id: str,
        continuation_id: str | None,
        turn_id: str,
        value: ConversationTurnInput,
    ) -> ContextStageRecord:
        repository = self._runtime._ports.context_staging
        provider = self._runtime._ports.context_provider
        if repository is None or provider is None:
            raise RuntimeError("conversation Context composition is incomplete")
        del turn_id
        stage_id = (
            f"agent-memory-stage/v1/{context_query_id(kind, identity_key).rsplit('/', 1)[-1]}"
        )
        now = self._runtime._now()
        claim = claim_context_preparation(
            repository,
            stage_id=stage_id,
            kind=kind,
            identity_key=identity_key,
            value=value,
            mode=ContextPreparationMode.SDK_PREPARED,
            owner_id=self._runtime._ports.owner_id,
            now=now,
            lease_seconds=self._runtime._ports.lease_ttl_seconds,
        )
        if claim.record.state in {ContextStageState.STAGED, ContextStageState.CONSUMED}:
            return claim.record
        if not claim.owner:
            context_bounds = ConversationContextBounds()
            loop = asyncio.get_running_loop()
            wait_deadline = loop.time() + (
                self._runtime._ports.lease_ttl_seconds + context_bounds.deadline_seconds + 1.0
            )
            while loop.time() < wait_deadline:
                winner = repository.get(stage_id)
                if winner is not None and winner.state in {
                    ContextStageState.STAGED,
                    ContextStageState.CONSUMED,
                }:
                    return winner
                if winner is not None and winner.state is ContextStageState.ABANDONED:
                    raise UnitOfWorkConflict("context preparation was abandoned")
                current_now = self._runtime._now()
                if (
                    winner is not None
                    and winner.state is ContextStageState.PREPARING
                    and winner.lease_expires_at is not None
                    and winner.lease_expires_at <= current_now
                ):
                    claim = claim_context_preparation(
                        repository,
                        stage_id=stage_id,
                        kind=kind,
                        identity_key=identity_key,
                        value=value,
                        mode=ContextPreparationMode.SDK_PREPARED,
                        owner_id=self._runtime._ports.owner_id,
                        now=current_now,
                        lease_seconds=self._runtime._ports.lease_ttl_seconds,
                    )
                    if claim.record.state in {
                        ContextStageState.STAGED,
                        ContextStageState.CONSUMED,
                    }:
                        return claim.record
                    if claim.owner:
                        break
                await asyncio.sleep(min(0.05, max(0.0, wait_deadline - loop.time())))
            else:
                raise TimeoutError("context preparation lease did not converge")
        now = self._runtime._now()
        identity_payload = value.identity.to_json()
        identity_hash = hashlib.sha256(canonical_json(identity_payload).encode("utf-8")).hexdigest()
        with repository.database.transaction() as connection:
            existing = connection.execute(
                "SELECT identity_hash FROM agent_identity_bindings WHERE session_id=?",
                (value.identity.session_id,),
            ).fetchone()
            if existing is not None and str(existing[0]) != identity_hash:
                raise UnitOfWorkConflict("conversation session identity cannot be rebound")
            connection.execute(
                "INSERT OR IGNORE INTO agent_identity_bindings("
                "session_id,deployment_id,household_id,actor_id,identity_hash,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    value.identity.session_id,
                    value.identity.deployment_id,
                    value.identity.household_id,
                    value.identity.actor_id,
                    identity_hash,
                    now,
                ),
            )
        ref = claim.record.source_snapshot_ref
        if ref is None:
            raise UnitOfWorkConflict("context preparation claim has no source snapshot ref")
        context_request = ConversationContextRequest(
            preparation_id=stage_id,
            identity=value.identity,
            root_run_id=root_run_id,
            continuation_id=continuation_id,
            source_snapshot_ref=ref,
            current_message=value.message,
            bounds=ConversationContextBounds(),
        )

        with repository.database.transaction() as connection:
            stage_call = begin_prepare(
                connection,
                claim.record,
                request_hash=audit_hash(context_request.canonical_payload()),
                now=self._runtime._now(),
            )
        try:
            product = await asyncio.wait_for(
                provider.prepare_once(context_request),
                timeout=context_request.bounds.deadline_seconds,
            )
        except BaseException:
            with repository.database.transaction() as connection:
                finish(
                    connection,
                    stage_call,
                    state="unknown",
                    now=self._runtime._now(),
                    error_code="context_prepare_interrupted",
                )
            raise
        with repository.database.transaction() as connection:
            finish(
                connection,
                stage_call,
                state="returned",
                now=self._runtime._now(),
                result_hash=getattr(product, "result_hash", None),
            )
        if (
            product.preparation_id != stage_id
            or product.source_snapshot_ref != ref
            or product.byte_count > context_request.bounds.max_bytes
            or product.item_count > context_request.bounds.max_items
        ):
            raise ValueError("Context provider result differs from its durable request")
        product_messages = self._product_messages(product.payload, value.message)
        thawed_product = thaw_json(cast(FrozenJsonValue, product.payload))
        assert isinstance(thawed_product, dict)
        provider_messages = [message.to_dict() for message in product_messages]
        private: dict[str, JsonValue] = {
            "schema_version": 1,
            "lineage": {
                "context_query_id": context_query_id(kind, identity_key),
                "memory_result_id": None,
                "memory_result_hash": None,
                "product_result_hash": product.result_hash,
                "source_snapshot_ref": ref,
            },
            "memory": {
                "status": "not_queried",
            },
            "product_context": thawed_product,
            "current_message": value.message.to_dict(),
            "provider_messages": cast(JsonValue, provider_messages),
        }
        return repository.complete(
            claim.record,
            private_snapshot=private,
            memory_result_id=None,
            memory_result_hash=None,
            memory_query_hash=None,
            memory_write_fence=None,
            outcome="ready",
            error_code=None,
            product_result_hash=product.result_hash,
            source_snapshot_ref=ref,
            turn_started_at=now,
            now=self._runtime._now(),
        )

    @staticmethod
    def _product_messages(
        payload: Mapping[str, JsonValue], current: Message
    ) -> tuple[Message, ...]:
        raw = payload.get("provider_messages", payload.get("messages"))
        if not isinstance(raw, (list, tuple)):
            raise TypeError("Context provider must return a messages array")
        messages: list[Message] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise TypeError("Context provider messages must be objects")
            thawed_item = thaw_json(cast(FrozenJsonValue, item))
            if not isinstance(thawed_item, dict):
                raise TypeError("Context provider message is invalid")
            parsed = _message_from_json(thawed_item)
            metadata = thaw_json(cast(FrozenJsonValue, parsed.metadata))
            if isinstance(metadata, Mapping) and metadata.get("source") == "memory":
                raise ValueError("Context provider cannot forge the Memory partition")
            messages.append(parsed)
        if not messages or canonical_json(messages[-1].to_dict()) != canonical_json(
            current.to_dict()
        ):
            raise ValueError("Context provider must preserve the current message as the final item")
        return tuple(messages)

    def query(self, run_id: RunId) -> RunRecord | None:
        return self._runtime._uow.read_run(_run_id(run_id))

    def signal(
        self,
        run_id: RunId,
        *,
        signal_id: str,
        payload: Mapping[str, JsonValue],
    ) -> ContinuationRecord:
        self._runtime._require_started()
        self._runtime._require_legacy_mode(_run_id(run_id))
        if payload.get("kind") == "conversation_user":
            raise ValueError("conversation_user continuations require signal_conversation")
        if payload.get("kind") == "base_agent_input":
            raise ValueError("base_agent_input continuations require signal_base_agent_input")
        continuation = self._runtime._uow.enqueue_continuation(
            continuation_id=signal_id,
            run_id=_run_id(run_id),
            payload=payload,
            now=self._runtime._now(),
        )
        asyncio.create_task(self._runtime._wake_continuation(continuation.run_id))
        return continuation

    async def signal_conversation(
        self,
        run_id: RunId,
        *,
        continuation_id: str,
        value: ConversationContinuationInput,
        context_stage_id: str | None = None,
        context_stage_hash: str | None = None,
        prepared_context: Mapping[str, JsonValue] | None = None,
    ) -> ContinuationRecord:
        """Enqueue one ordinary user continuation with its durable context stage."""

        self._runtime._require_started()
        self._runtime._require_legacy_mode(_run_id(run_id))
        if not isinstance(value, ConversationContinuationInput):
            raise TypeError("value must use ConversationContinuationInput")
        existing_continuation = self._runtime._uow.read_continuation(continuation_id)
        if existing_continuation is not None:
            existing_payload = thaw_json(existing_continuation.payload)
            existing_conversation = (
                existing_payload.get("conversation")
                if isinstance(existing_payload, Mapping)
                else None
            )
            if (
                existing_continuation.run_id == _run_id(run_id)
                and isinstance(existing_conversation, Mapping)
                and canonical_json(dict(existing_conversation)) == canonical_json(value.to_json())
            ):
                return existing_continuation
            raise UnitOfWorkConflict("continuation identity reused differently")
        raw = self._runtime._uow.read_start_snapshot(_run_id(run_id))
        if raw is None:
            raise KeyError(run_id.value)
        run = self._runtime._uow.read_run(_run_id(run_id))
        if run is None:
            raise KeyError(run_id.value)
        if run.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            raise UnitOfWorkConflict("terminal Run rejects new continuations")
        start = StartSnapshot.from_json(raw)
        if start.conversation is None:
            raise UnitOfWorkConflict("Run has no conversation identity")
        if self._runtime._ports.agent_memory is not None:
            continuation_value = ConversationTurnInput(
                start.conversation.identity,
                value.message,
                value.memory_text,
                start.conversation.recall_scopes,
                value.context_source_snapshot_ref,
            )
            stage = await self._prepare_agent_context(
                kind=ContextStageKind.CONTINUATION,
                identity_key=continuation_id,
                root_run_id=run_id.value,
                continuation_id=continuation_id,
                turn_id=continuation_id,
                value=continuation_value,
            )
            if stage.private_snapshot is None or stage.private_snapshot_hash is None:
                raise UnitOfWorkConflict("prepared continuation context is unavailable")
            context_stage_id = stage.stage_id
            context_stage_hash = stage.private_snapshot_hash
            prepared_context = stage.private_snapshot
        if context_stage_id is None or context_stage_hash is None or prepared_context is None:
            raise ValueError("conversation continuation requires durable prepared context")
        continuation = self._runtime._uow.enqueue_continuation(
            continuation_id=continuation_id,
            run_id=_run_id(run_id),
            payload={
                "kind": "conversation_user",
                "context_use_turn_id": continuation_id,
                "conversation": value.to_json(),
                "prepared_context": dict(prepared_context),
            },
            context_stage_id=context_stage_id,
            context_stage_hash=context_stage_hash,
            now=self._runtime._now(),
        )
        asyncio.create_task(self._runtime._wake_continuation(continuation.run_id))
        return continuation

    async def decide_authorization(
        self,
        run_id: RunId,
        *,
        decision_id: str,
        nonce: str,
        expected_version: int,
        decision: AuthorizationDecision,
    ):
        """Resolve one durable Tool decision with nonce/version replay fences."""

        self._runtime._require_started()
        record = self._runtime._uow.read_decision(decision_id)
        if record is None or record.run_id != _run_id(run_id):
            raise HarnessError("authorization_decision_not_found", "Decision was not found.")
        if record.state is not DecisionState.OPEN:
            if record.response is not None and isinstance(record.response, Mapping):
                if (
                    record.response.get("nonce") == nonce
                    and record.response.get("decision") == decision.value
                ):
                    return record
            raise HarnessError("authorization_decision_late", "Decision is already closed.")
        run = self._runtime._uow.read_run(record.run_id)
        if run is None or run.state is not RunState.WAITING:
            raise HarnessError(
                "authorization_decision_late",
                "The Run no longer accepts this decision.",
            )
        if record.version != expected_version:
            raise HarnessError(
                "authorization_decision_version_conflict", "Decision version is stale."
            )
        request = thaw_json(record.request)
        if not isinstance(request, dict) or request.get("nonce") != nonce:
            raise HarnessError("authorization_decision_nonce_mismatch", "Decision nonce differs.")
        expires_at = request.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, (int, float)):
            raise HarnessError("authorization_decision_invalid", "Decision expiry is invalid.")
        if expires_at is not None and self._runtime._now() >= float(expires_at):
            decision = AuthorizationDecision.DENY
            state = DecisionState.EXPIRED
        elif decision is AuthorizationDecision.ALLOW:
            state = DecisionState.ALLOWED
        elif decision is AuthorizationDecision.DENY:
            state = DecisionState.DENIED
        else:
            raise ValueError("a user decision must be ALLOW or DENY")
        binding_ref = await self._runtime._services.tools.bind_decision(record, decision)
        resolved = self._runtime._uow.commit_decision(
            decision_id=record.decision_id,
            run_id=record.run_id,
            kind=record.kind,
            state=state,
            request=request,
            response={
                "authorization_receipt_ref": binding_ref,
                "decision": decision.value,
                "nonce": nonce,
            },
            event_id=f"{record.decision_id}:{state.value}:{record.version + 1}",
            now=self._runtime._now(),
        )
        if state is DecisionState.ALLOWED:
            asyncio.create_task(self._runtime._wake_continuation(record.run_id))
        return resolved

    async def cancel(self, run_id: RunId) -> RunRecord:
        self._runtime._require_legacy_mode(_run_id(run_id))
        return await self._runtime._cancel_run(run_id)

    async def workflow_spawn_catalog(self):  # type: ignore[no-untyped-def]
        coordinator = self._runtime._services.workflow_spawn
        if coordinator is None:
            raise RuntimeError("workflow spawn is not configured")
        return await coordinator.catalog_snapshot()

    def bind_workflow_spawn(self, context, selection):  # type: ignore[no-untyped-def]

        if not isinstance(context, WorkflowSpawnToolContext):
            raise TypeError("workflow spawn binder requires its typed Tool context")
        if not isinstance(selection, WorkflowSpawnSelection):
            raise TypeError("workflow spawn binder requires a typed selection")
        parent = self._runtime._uow.read_run(context.run_id)
        raw_snapshot = self._runtime._uow.read_start_snapshot(context.run_id)
        checkpoint = self._runtime._ports.react_checkpoint.read_react_checkpoint(context.run_id)
        if parent is None or raw_snapshot is None or checkpoint is None:
            raise UnitOfWorkConflict("workflow spawn parent authority disappeared")
        parent_start = StartSnapshot.from_json(raw_snapshot)
        checkpoint_payload = checkpoint.checkpoint
        if not isinstance(checkpoint_payload, Mapping):
            raise UnitOfWorkConflict("workflow spawn checkpoint is malformed")
        raw_catalog = checkpoint_payload.get("workflow_catalog_selection")
        if not isinstance(raw_catalog, Mapping):
            raise UnitOfWorkConflict("workflow spawn catalog pin is missing")
        catalog = workflow_catalog_selection_from_json(raw_catalog)
        if (
            catalog.canonical_hash != context.catalog_snapshot_hash
            or checkpoint.version != context.react_checkpoint_revision
            or parent.request_id != context.request_id
            or parent_start.turn_id != context.turn_id
            or selection.profile_key not in {item.profile_key for item in catalog.profiles}
        ):
            raise UnitOfWorkConflict("workflow spawn binding differs from durable pin")
        origin = WorkflowSpawnOrigin(
            context.run_id,
            context.request_id,
            context.turn_id,
            context.internal_tool_call_id,
        )
        operation_id = workflow_spawn_operation_id(origin)
        child_start = RunStart(
            ExecutionSessionId(parent.execution_session_id),
            RunId(workflow_spawn_child_run_id(operation_id)),
            RequestId(workflow_spawn_child_request_id(operation_id)),
            context.turn_id,
            selection.start_input,
            parent_start.tool_catalog_generation,
        )
        return _create_workflow_spawn_invocation(
            spawn_operation_id=operation_id,
            origin=origin,
            start=child_start,
            selection=selection,
            catalog_selection=catalog,
            issue_authority=context.issue_authority,
        )

    async def workflow_spawn(self, invocation):  # type: ignore[no-untyped-def]
        coordinator = self._runtime._services.workflow_spawn
        if coordinator is None:
            raise RuntimeError("workflow spawn is not configured")
        return await coordinator.execute(invocation)

    def prove_graph_unavailable(self, ticket, ready_activation):  # type: ignore[no-untyped-def]
        runner = self._runtime._workflow_runner
        if runner is None:
            raise RuntimeError("workflow spawn is not configured")
        return runner._prove_graph_unavailable(ticket, ready_activation)


def _run_id(value: RunId) -> str:
    if not isinstance(value, RunId):
        raise TypeError("run_id must use RunId")
    return value.value
