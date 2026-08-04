"""Small, precisely typed capability views over the single execution UoW.

Every protocol below is implemented by the same ``SqliteExecutionUnitOfWork``.
They do not create transactions or stores; they only stop a caller from
depending on the historical all-methods interface.
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
    Sequence,
    TypeAlias,
    runtime_checkable,
)

from deskpet.types.task_work_context import (
    ConversationBoundary,
    QueuedUserContinuation,
    TaskRunProjection,
    TaskWorkContext,
)
from deskpet.types.task_grants import PreparedAuthorizationCommit, TaskGrant

from .contracts import (
    ActorContext,
    AdmissionBoundary,
    AdmissionLaunchClaim,
    AdmissionLaunchUnknownFence,
    AdmissionResolution,
    AdmissionSpec,
    AttemptFailureSet,
    AttemptRecord,
    ChildCommandIntent,
    ChildCommandRecord,
    ChildSignalRecord,
    CreateRunResult,
    DecisionAuthorization,
    DecisionOpen,
    DecisionRecord,
    DecisionSignal,
    DeliveryRecord,
    DeliverySpec,
    FinalizeRunResult,
    GrantConsume,
    LegacyRunProjection,
    PlanVersionRecord,
    ProfileLaunchTicket,
    ProviderActionBatch,
    ProviderActionCall,
    ProviderBatchAdmissionResult,
    ProviderTurnFence,
    RecoveryLease,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunLinkSpec,
    RunRecord,
    RunRef,
    RunStartSnapshotRecord,
    RunStatus,
    TaskExternalWait,
    TaskFailureReport,
    TaskGoalRecord,
)
from .evidence import EvidenceContext, EvidenceSelection


RunView: TypeAlias = RunRecord | LegacyRunProjection
SinkKey: TypeAlias = tuple[str, str]


@runtime_checkable
class KernelUnitOfWork(Protocol):
    async def start_blocked_root(
        self,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        *,
        event: RunEventCandidate,
        block_reporter: Any,
        start_commit_extensions: Sequence[Any] = (),
        deliveries: Sequence[DeliverySpec] = (),
    ) -> FinalizeRunResult: ...

    async def create_with_start_snapshot(
        self,
        spec: RunCreate,
        start_snapshot: RunStartSnapshotRecord,
        *,
        initial_event: RunEventCandidate | None = None,
        start_commit_extensions: Sequence[Any] = (),
    ) -> CreateRunResult: ...

    async def read_run_start_snapshot(
        self, run_id: str
    ) -> RunStartSnapshotRecord | None: ...

    async def start_admission(
        self,
        spec: RunCreate,
        admission: AdmissionSpec,
        start_snapshot: AdmissionBoundary,
        waiting_event: RunEventCandidate,
        *,
        run_start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        deliveries: Sequence[DeliverySpec] = (),
    ) -> AdmissionBoundary: ...

    async def resolve_admission(
        self,
        ref: RunRef,
        actor: ActorContext,
        signal: DecisionSignal,
        *,
        expected_boundary_version: int,
        terminal_deliveries: Sequence[DeliverySpec] = (),
        admission_task_grant: TaskGrant | None = None,
    ) -> AdmissionResolution: ...

    async def commit_run_outcome(
        self,
        run_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus | None = None,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
        parent_signal_operation_id: str | None = None,
        parent_signal_value: Any = None,
        recovery_lease: RecoveryLease | None = None,
        cancel_reason: str | None = None,
        admission_failure: AdmissionLaunchUnknownFence | None = None,
        terminal_commit_extensions: Sequence[Any] = (),
        delivery_fence: Mapping[str, Any] | None = None,
        release_receipt_kind: str | None = None,
    ) -> FinalizeRunResult | RunRecord: ...

    async def query(self, ref: RunRef, actor: ActorContext) -> RunView: ...

    async def list_child_links(
        self, ref: RunRef, actor: ActorContext
    ) -> tuple[RunLinkSpec, ...]: ...

    async def recovery_scope(
        self,
        subject: str | RecoveryLease,
        *,
        owner: str | None = None,
        lease_seconds: float | None = 30.0,
    ) -> RecoveryLease | bool: ...

    async def list_events(
        self, run_id: str, *, after_durable_seq: int = 0
    ) -> tuple[RunEvent, ...]: ...

    async def get_decision(
        self,
        decision_id: str,
        *,
        ref: RunRef,
        actor: ActorContext,
    ) -> DecisionRecord: ...

    async def commit_decision(
        self,
        signal: DecisionOpen | DecisionSignal,
        actor: ActorContext,
        *,
        expected_run_version: int | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        resumed_event: RunEventCandidate | None = None,
        next_decision: DecisionOpen | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        authorization_commit: PreparedAuthorizationCommit | None = None,
    ) -> (
        tuple[DecisionRecord, DecisionAuthorization | None]
        | tuple[DecisionRecord, DecisionAuthorization | None, Any, RunEvent]
    ): ...

    async def cancel_open_decisions(
        self,
        ref: RunRef,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> tuple[DecisionRecord, ...]: ...

    async def load_continuation(self, run_id: str) -> Any | None: ...

    async def get_child_command_for_run(
        self, child_run_id: str
    ) -> ChildCommandRecord | None: ...

    async def finalize_child_and_enqueue_parent_signal(
        self,
        operation_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus,
        event: RunEventCandidate,
        value: Any = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        terminal_commit_extensions: Sequence[Any] = (),
    ) -> FinalizeRunResult: ...


@runtime_checkable
class DriverRuntimeUnitOfWork(Protocol):
    async def query(self, ref: RunRef, actor: ActorContext) -> RunView: ...

    async def recovery_scope(
        self,
        subject: str | RecoveryLease,
        *,
        owner: str | None = None,
        lease_seconds: float | None = 30.0,
    ) -> RecoveryLease | bool: ...

    async def assert_recovery_fence(self, lease: RecoveryLease) -> None: ...


@runtime_checkable
class ToolExecutionUnitOfWork(Protocol):
    async def claim_tool_call(
        self,
        request: GrantConsume | None,
        actor: ActorContext,
        *,
        run_id: str | None = None,
        expected_session_id: str | None = None,
        call_id: str | None = None,
        effect_id: str | None = None,
        tool_name: str | None = None,
        args_hash: str | None = None,
        capability_hash: str | None = None,
        scope_hash: str | None = None,
        effect_type: str | None = None,
        policy: Mapping[str, Any] | None = None,
        prepared: Mapping[str, Any] | None = None,
        worker_owner: str = "",
        worker_epoch: int = 0,
        recovery_lease: RecoveryLease | None = None,
    ) -> DecisionAuthorization | Any: ...

    async def settle_effect(
        self,
        effect_id: str,
        *,
        expected_effect_version: int,
        attempt_no: int,
        worker_owner: str,
        worker_epoch: int,
        status: str = "unknown",
        outcome: Mapping[str, Any],
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
        node_execution_id: str | None = None,
        checkpoint_ns: str | None = None,
        checkpoint_id: str | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        reconciliation: bool = False,
        evidence_verified: bool = False,
        transaction_hook: Callable[[Any, Any], Any] | None = None,
    ) -> Any: ...

    async def read_effect_outcome(
        self,
        *,
        run_id: str,
        call_id: str,
        effect_id: str,
        args_hash: str,
        capability_hash: str,
        scope_hash: str,
    ) -> tuple[str, Mapping[str, Any], str | None, tuple[str, ...]] | None: ...

    async def read_effect_handoff(self, effect_id: str) -> Any | None: ...

    async def suppress_late_effect_completion(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        late_outcome_hash: str,
    ) -> Any: ...

    async def reconcile_effect_handoff(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
        *,
        completed_suppressed: bool,
    ) -> Any: ...

    async def read_run_start_snapshot(
        self, run_id: str
    ) -> RunStartSnapshotRecord | None: ...

    async def load_continuation(self, run_id: str) -> Any | None: ...

    async def get_skill_scope_activation(
        self, activation_id: str
    ) -> Mapping[str, Any] | None: ...


@runtime_checkable
class ReActUnitOfWork(Protocol):
    async def create_task_goal(
        self,
        goal: TaskGoalRecord,
        initial_plan: PlanVersionRecord,
    ) -> tuple[TaskGoalRecord, PlanVersionRecord]: ...

    async def create_task_context(
        self,
        work_context: TaskWorkContext,
        conversation: ConversationBoundary,
        projection: TaskRunProjection,
    ) -> tuple[TaskWorkContext, ConversationBoundary, TaskRunProjection]: ...

    async def rebind_task_workspace_for_project(
        self,
        root_run_id: str,
        project_root: str,
        *,
        expected_binding_version: int,
    ) -> TaskWorkContext: ...

    async def get_task_goal(
        self, root_run_id: str
    ) -> TaskGoalRecord | None: ...

    async def append_plan_version(
        self, plan: PlanVersionRecord
    ) -> PlanVersionRecord: ...

    async def open_provider_turn_fence(
        self, fence: ProviderTurnFence
    ) -> ProviderTurnFence: ...

    async def accept_provider_batch_and_create_attempt(
        self,
        batch: ProviderActionBatch,
        attempt: AttemptRecord,
        calls: Sequence[ProviderActionCall],
    ) -> ProviderBatchAdmissionResult: ...

    async def mark_provider_action_prepared(
        self,
        call_record_id: str,
        *,
        expected_version: int,
        parsed_arguments_hash: str,
        prepared_call_ref: str,
        command_boundary_ref: str | None = None,
    ) -> ProviderActionCall: ...

    async def settle_provider_action_success(
        self,
        attempt_id: str,
        outcome_refs_by_call: Mapping[str, str],
    ) -> AttemptRecord: ...

    async def stage_attempt_failure_set(
        self,
        failure_set: AttemptFailureSet,
        reports: Sequence[TaskFailureReport],
    ) -> AttemptFailureSet: ...

    async def stage_external_wait(
        self, wait: TaskExternalWait
    ) -> TaskExternalWait: ...

    async def resume_external_wait(
        self,
        wait_ref: str,
        response_ref: str,
        *,
        expected_version: int,
    ) -> TaskExternalWait: ...

    async def commit_decision(
        self,
        signal: DecisionOpen | DecisionSignal,
        actor: ActorContext,
        *,
        expected_run_version: int | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        resumed_event: RunEventCandidate | None = None,
        next_decision: DecisionOpen | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        authorization_commit: PreparedAuthorizationCommit | None = None,
    ) -> (
        tuple[DecisionRecord, DecisionAuthorization | None]
        | tuple[DecisionRecord, DecisionAuthorization | None, Any, RunEvent]
    ): ...

    async def commit_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        *,
        task_scope_id: str,
        expected_boundary_version: int,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        event: RunEventCandidate,
    ) -> tuple[ConversationBoundary, Any, RunEvent]: ...

    async def commit_queued_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        *,
        task_scope_id: str,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        event: RunEventCandidate,
    ) -> tuple[ConversationBoundary, Any, RunEvent]: ...

    async def persist_react_boundary(
        self,
        run: str | RunCreate,
        expected_continuation_version: int,
        payload: Mapping[str, Any],
        decision: DecisionOpen | None = None,
        *,
        expected_run_version: int | None = None,
        waiting_event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        admission_launch: AdmissionLaunchClaim | None = None,
        skill_scope_activation_values: Mapping[str, Any] | None = None,
    ) -> Any: ...

    async def load_continuation(self, run_id: str) -> Any | None: ...

    async def lookup_completion_evidence(
        self, context: EvidenceContext
    ) -> EvidenceSelection: ...

    async def read_run_start_snapshot(
        self, run_id: str
    ) -> RunStartSnapshotRecord | None: ...

    async def read_effect_outcome(
        self,
        *,
        run_id: str,
        call_id: str,
        effect_id: str,
        args_hash: str,
        capability_hash: str,
        scope_hash: str,
    ) -> tuple[str, Mapping[str, Any], str | None, tuple[str, ...]] | None: ...

    async def settle_effect(
        self,
        effect_id: str,
        *,
        expected_effect_version: int,
        attempt_no: int,
        worker_owner: str,
        worker_epoch: int,
        status: str = "unknown",
        outcome: Mapping[str, Any],
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
        node_execution_id: str | None = None,
        checkpoint_ns: str | None = None,
        checkpoint_id: str | None = None,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        reconciliation: bool = False,
        evidence_verified: bool = False,
        transaction_hook: Callable[[Any, Any], Any] | None = None,
    ) -> Any: ...

    async def commit_skill_scope_activation(
        self,
        run_id: str,
        *,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        activation_values: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Any]: ...

    async def get_skill_scope_activation(
        self, activation_id: str
    ) -> Mapping[str, Any] | None: ...

    async def ack_child_signal(
        self,
        signal_id: str,
        *,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildSignalRecord | tuple[ChildSignalRecord, Any, RunEvent]: ...


@runtime_checkable
class WorkflowDriverUnitOfWork(Protocol):
    async def query(self, ref: RunRef, actor: ActorContext) -> RunView: ...

    async def read_run_start_snapshot(
        self, run_id: str
    ) -> RunStartSnapshotRecord | None: ...

    async def prepare_workflow_child_resume(
        self,
        signal_id: str,
        *,
        recovery_lease: RecoveryLease,
    ) -> DecisionRecord: ...

    async def read_workflow_resume_payload(
        self,
        run_id: str,
    ) -> Mapping[str, JsonValue]: ...


@runtime_checkable
class ProductProjectionUnitOfWork(Protocol):
    """Read-only source view used by product projection consistency gates."""

    async def list_session_terminal_events(
        self,
        session_id: str,
        target_id: str,
        *,
        session_epoch: int,
        limit: int | None = None,
    ) -> tuple[RunEvent, ...]: ...


@runtime_checkable
class HarnessDeliveryUnitOfWork(Protocol):
    """Small durable-delivery view used by the Harness projector."""

    async def claim_delivery(
        self,
        *,
        owner_generation: int,
        sink_keys: Sequence[SinkKey] = (),
        claim_ttl_seconds: float = 30.0,
    ) -> DeliveryRecord | None: ...

    async def get_event(self, event_id: str) -> RunEvent: ...

    async def settle_delivery(
        self,
        delivery_id: str,
        *,
        expected_version: int,
        owner_generation: int,
        error: str | None = None,
        retry_at: float | None = None,
        discard: bool = False,
    ) -> DeliveryRecord: ...


@runtime_checkable
class DelegateFactoryUnitOfWork(Protocol):
    """Durable facts needed while translating a prepared delegation call."""

    async def get_task_goal(
        self, root_run_id: str
    ) -> TaskGoalRecord | None: ...

    async def get_task_work_context(
        self, root_run_id: str
    ) -> Any | None: ...

    async def issue_profile_launch_ticket(
        self, ticket: ProfileLaunchTicket
    ) -> ProfileLaunchTicket: ...


@runtime_checkable
class TeamMigrationUnitOfWork(Protocol):
    """Narrow view for the isolated, test-only Team migration bridge."""

    async def commit_child_command(
        self,
        intent: ChildCommandIntent,
        *,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord: ...

    async def get_child_command(
        self, operation_id: str
    ) -> ChildCommandRecord | None: ...

    async def query(
        self, ref: RunRef, actor: ActorContext
    ) -> RunView: ...

    async def get_event(self, event_id: str) -> RunEvent: ...


@runtime_checkable
class ChildRunUnitOfWork(Protocol):
    async def read_run_start_snapshot(
        self, run_id: str
    ) -> RunStartSnapshotRecord | None: ...

    async def get_profile_launch_ticket(
        self, ticket_ref: str
    ) -> ProfileLaunchTicket | None: ...

    async def claim_profile_launch_and_commit_child(
        self,
        ticket_ref: str,
        intent: ChildCommandIntent,
        launch_request: Mapping[str, Any],
        *,
        expected_ticket_version: int,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord: ...

    async def commit_child_command_and_precreate_child(
        self,
        intent: ChildCommandIntent,
        start_snapshot: RunStartSnapshotRecord,
        *,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord: ...


@runtime_checkable
class UserContinuationUnitOfWork(Protocol):
    async def enqueue_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        content: str,
        *,
        task_scope_id: str,
        expected_boundary_version: int,
    ) -> tuple[ConversationBoundary, QueuedUserContinuation, bool]: ...

    async def get_next_user_continuation(
        self, root_run_id: str
    ) -> QueuedUserContinuation | None: ...

    async def get_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
    ) -> QueuedUserContinuation | None: ...

    async def fail_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        error: str,
    ) -> QueuedUserContinuation: ...


@runtime_checkable
class ReconciliationUnitOfWork(Protocol):
    async def read_run(self, run_id: str) -> RunRecord | None: ...

    async def configure_run_active_budget(
        self,
        run_id: str,
        *,
        limit_seconds: float,
    ) -> None: ...

    async def claim_expired_active_budget_runs(
        self,
        *,
        limit: int = 16,
    ) -> tuple[RunRecord, ...]: ...

    async def next_active_budget_delay(self) -> float | None: ...

    async def recovery_scope(
        self,
        subject: str | RecoveryLease,
        *,
        owner: str | None = None,
        lease_seconds: float | None = 30.0,
    ) -> RecoveryLease | bool: ...

    async def list_recoverable(
        self,
        *,
        limit: int = 10000,
        run_ids: Sequence[str] = (),
    ) -> tuple[RunRecord, ...]: ...

    async def lease_child_commands(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: float,
        parent_run_id: str | None = None,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ChildCommandRecord, ...]: ...

    async def schedule_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
        recovery_lease: RecoveryLease | None = None,
    ) -> ChildCommandRecord: ...

    async def acknowledge_child_command(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_epoch: int,
        recovery_lease: RecoveryLease | None = None,
    ) -> ChildCommandRecord: ...

    async def list_pending_child_signal_parents(
        self, *, limit: int = 10000
    ) -> tuple[RunRecord, ...]: ...

    async def list_pending_child_signals(
        self,
        parent_run_id: str,
        *,
        limit: int = 16,
        recovery_lease: RecoveryLease | None = None,
    ) -> tuple[ChildSignalRecord, ...]: ...

    async def ack_child_signal(
        self,
        signal_id: str,
        *,
        expected_continuation_version: int | None = None,
        continuation_payload: Mapping[str, Any] | None = None,
        event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildSignalRecord | tuple[ChildSignalRecord, Any, RunEvent]: ...


@runtime_checkable
class AdmissionUnitOfWork(Protocol):
    async def query(self, ref: RunRef, actor: ActorContext) -> RunView: ...

    async def load_continuation(self, run_id: str) -> Any | None: ...

    async def recovery_scope(
        self,
        subject: str | RecoveryLease,
        *,
        owner: str | None = None,
        lease_seconds: float | None = 30.0,
    ) -> RecoveryLease | bool: ...

    async def claim_admission_launch(
        self,
        recovery_lease: RecoveryLease,
        *,
        expected_boundary_version: int,
    ) -> AdmissionLaunchClaim: ...

    async def read_run_start_snapshot(
        self, run_id: str
    ) -> RunStartSnapshotRecord | None: ...

    async def commit_run_outcome(
        self,
        run_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus | None = None,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
        parent_signal_operation_id: str | None = None,
        parent_signal_value: Any = None,
        recovery_lease: RecoveryLease | None = None,
        cancel_reason: str | None = None,
        admission_failure: AdmissionLaunchUnknownFence | None = None,
        terminal_commit_extensions: Sequence[Any] = (),
        delivery_fence: Mapping[str, Any] | None = None,
        release_receipt_kind: str | None = None,
    ) -> FinalizeRunResult | RunRecord: ...


@runtime_checkable
class HarnessBootstrapUnitOfWork(
    KernelUnitOfWork,
    ReconciliationUnitOfWork,
    HarnessDeliveryUnitOfWork,
    Protocol,
):
    """Views required by the Harness composition root during startup."""

    async def initialize(self) -> None: ...


@runtime_checkable
class ProductHarnessUnitOfWork(
    HarnessBootstrapUnitOfWork,
    ReActUnitOfWork,
    WorkflowDriverUnitOfWork,
    ChildRunUnitOfWork,
    ToolExecutionUnitOfWork,
    DelegateFactoryUnitOfWork,
    Protocol,
):
    """Composition-only union of small views; not a new all-methods contract."""


__all__ = [
    "AdmissionUnitOfWork",
    "ChildRunUnitOfWork",
    "DelegateFactoryUnitOfWork",
    "DriverRuntimeUnitOfWork",
    "HarnessBootstrapUnitOfWork",
    "HarnessDeliveryUnitOfWork",
    "KernelUnitOfWork",
    "ProductHarnessUnitOfWork",
    "ProductProjectionUnitOfWork",
    "ReActUnitOfWork",
    "ReconciliationUnitOfWork",
    "TeamMigrationUnitOfWork",
    "ToolExecutionUnitOfWork",
    "UserContinuationUnitOfWork",
    "WorkflowDriverUnitOfWork",
]
