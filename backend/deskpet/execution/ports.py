"""Storage-facing protocols for the product-neutral execution control plane."""
from __future__ import annotations
from typing import Any, Mapping, Protocol, Sequence, TypeAlias, runtime_checkable
from deskpet.types.task_work_context import ConversationBoundary, QueuedUserContinuation, TaskRunProjection, TaskWorkContext
from deskpet.types.task_grants import PreparedAuthorizationCommit, TaskGrant
from .contracts import ActorAction, ActorContext, AdmissionBoundary, AdmissionLaunchClaim, AdmissionLaunchUnknownFence, AdmissionResolution, AdmissionSpec, AttemptFailureSet, AttemptRecord, ChildCommandIntent, ChildCommandRecord, ChildSignalRecord, CreateRunResult, DecisionAuthorization, DecisionOpen, DecisionRecord, DecisionSignal, DeliveryRecord, DeliverySpec, ExecutionRunFenceRecord, FinalizeRunResult, GrantConsume, LegacyRunProjection, PlanVersionRecord, ProfileLaunchConsumeResult, ProfileLaunchTicket, ProviderActionBatch, ProviderActionCall, ProviderBatchAdmissionResult, ProviderInvocationOutcome, ProviderInvocationRecord, ProviderTurnFence, RecoveryLease, RunCreate, RunEvent, RunEventCandidate, RunLinkSpec, RunRecord, RunRef, RunStartSnapshotRecord, RunStatus, TaskExternalWait, TaskFailureReport, TaskGoalRecord, WorkflowRunSeed, WorkflowStartResult
from .evidence import EvidenceContext, EvidenceSelection
RunView = RunRecord | LegacyRunProjection
SinkKey: TypeAlias = tuple[str, str]


@runtime_checkable
class CurrentExecutionScopeLease(Protocol):
    """Pinned immutable execution scope used by a prepared tool dispatch."""

    owner_key: str
    profile_generation: int
    binding_epoch: int
    capability_hash: str
    scope_hash: str

    async def release(self) -> None: ...


@runtime_checkable
class CurrentExecutionScopeLeasePort(Protocol):
    """Acquire the current owner/capability scope at the final effect boundary."""

    async def acquire_current_execution_scope(
        self,
        *,
        run_id: str,
        owner_key: str,
        profile_generation: int,
        binding_epoch: int,
        capability_hash: str,
        scope_hash: str,
    ) -> CurrentExecutionScopeLease: ...

@runtime_checkable
class ExecutionUnitOfWork(Protocol):
    """Compatibility aggregate for composition roots and external callers.

    Harness runtime components must depend on the smaller capability views in
    ``execution.uow_ports``.  All views are still implemented by the one
    ``SqliteExecutionUnitOfWork`` and therefore share this durable boundary.
    """

    async def create(self, spec: RunCreate, *, initial_event: RunEventCandidate | None=None) -> CreateRunResult:
        ...

    async def create_with_start_snapshot(self, spec: RunCreate, start_snapshot: RunStartSnapshotRecord, *, initial_event: RunEventCandidate | None=None, start_commit_extensions: Sequence[Any]=()) -> CreateRunResult:
        ...

    async def read_run(self, run_id: str) -> RunRecord | None:
        ...

    async def read_run_start_snapshot(self, run_id: str) -> RunStartSnapshotRecord | None:
        ...

    async def claim_provider_invocation(self, record: ProviderInvocationRecord) -> ProviderInvocationRecord:
        ...

    async def read_provider_invocation(self, run_id: str, invocation_id: str) -> ProviderInvocationRecord | None:
        ...

    async def complete_provider_invocation(self, run_id: str, invocation_id: str, *, expected_claim_epoch: int, outcome: ProviderInvocationOutcome, dispatch_started_ack_ref: str | None, dispatch_started_ack_hash: str | None, dispatch_started_at: float | None) -> ProviderInvocationRecord:
        ...

    async def fail_provider_invocation(self, run_id: str, invocation_id: str, *, expected_claim_epoch: int, outcome_ref: str | None=None, outcome_hash: str | None=None, dispatch_started_ack_ref: str | None=None, dispatch_started_ack_hash: str | None=None, dispatch_started_at: float | None=None) -> ProviderInvocationRecord:
        ...

    async def mark_unknown_provider_invocation(self, run_id: str, invocation_id: str, *, expected_claim_epoch: int, outcome_ref: str | None=None, outcome_hash: str | None=None, dispatch_started_ack_ref: str | None=None, dispatch_started_ack_hash: str | None=None, dispatch_started_at: float | None=None) -> ProviderInvocationRecord:
        ...

    async def read_provider_invocation_outcome(self, run_id: str, invocation_id: str) -> ProviderInvocationOutcome | None:
        ...

    async def write_run_fence(self, record: ExecutionRunFenceRecord) -> ExecutionRunFenceRecord:
        ...

    async def read_run_fence(self, run_id: str, fence_kind: str, fence_version: int | None=None) -> ExecutionRunFenceRecord | None:
        ...

    async def create_task_goal(self, goal: TaskGoalRecord, initial_plan: PlanVersionRecord) -> tuple[TaskGoalRecord, PlanVersionRecord]:
        ...

    async def create_task_context(self, work_context: TaskWorkContext, conversation: ConversationBoundary, projection: TaskRunProjection) -> tuple[TaskWorkContext, ConversationBoundary, TaskRunProjection]:
        ...

    async def get_task_work_context(self, root_run_id: str) -> TaskWorkContext | None:
        ...

    async def get_conversation_boundary(self, root_run_id: str) -> ConversationBoundary | None:
        ...

    async def enqueue_user_continuation(self, root_run_id: str, message_ref: str, content: str, *, task_scope_id: str, expected_boundary_version: int) -> tuple[ConversationBoundary, QueuedUserContinuation, bool]:
        ...

    async def get_next_user_continuation(self, root_run_id: str) -> QueuedUserContinuation | None:
        ...

    async def get_user_continuation(self, root_run_id: str, message_ref: str) -> QueuedUserContinuation | None:
        ...

    async def fail_user_continuation(self, root_run_id: str, message_ref: str, error: str) -> QueuedUserContinuation:
        ...

    async def commit_user_continuation(self, root_run_id: str, message_ref: str, *, task_scope_id: str, expected_boundary_version: int, expected_continuation_version: int, continuation_payload: Mapping[str, Any], event: RunEventCandidate) -> tuple[ConversationBoundary, Any, RunEvent]:
        ...

    async def commit_queued_user_continuation(self, root_run_id: str, message_ref: str, *, task_scope_id: str, expected_continuation_version: int, continuation_payload: Mapping[str, Any], event: RunEventCandidate) -> tuple[ConversationBoundary, Any, RunEvent]:
        ...

    async def get_task_run_projection(self, root_run_id: str) -> TaskRunProjection | None:
        ...

    async def list_task_run_projections(self, session_id: str, *, include_closed: bool=False) -> tuple[TaskRunProjection, ...]:
        ...

    async def get_task_run_lifecycle(self, root_run_id: str, *, expected_session_id: str) -> Mapping[str, Any] | None:
        ...

    async def set_task_run_projection_state(self, root_run_id: str, ui_state: str, *, expected_version: int) -> TaskRunProjection:
        ...

    async def get_task_goal(self, root_run_id: str) -> TaskGoalRecord | None:
        ...

    async def append_plan_version(self, plan: PlanVersionRecord) -> PlanVersionRecord:
        ...

    async def open_provider_turn_fence(self, fence: ProviderTurnFence) -> ProviderTurnFence:
        ...

    async def accept_provider_batch_and_create_attempt(self, batch: ProviderActionBatch, attempt: AttemptRecord, calls: Sequence[ProviderActionCall]) -> ProviderBatchAdmissionResult:
        ...

    async def get_attempt(self, attempt_id: str) -> AttemptRecord | None:
        ...

    async def list_provider_action_calls(self, provider_batch_id: str) -> tuple[ProviderActionCall, ...]:
        ...

    async def mark_provider_action_prepared(self, call_record_id: str, *, expected_version: int, parsed_arguments_hash: str, prepared_call_ref: str, command_boundary_ref: str | None=None) -> ProviderActionCall:
        ...

    async def stage_attempt_failure_set(self, failure_set: AttemptFailureSet, reports: Sequence[TaskFailureReport]) -> AttemptFailureSet:
        ...

    async def get_attempt_failure_set(self, failure_set_id: str) -> AttemptFailureSet | None:
        ...

    async def list_task_failure_reports(self, attempt_id: str) -> tuple[TaskFailureReport, ...]:
        ...

    async def stage_external_wait(self, wait: TaskExternalWait) -> TaskExternalWait:
        ...

    async def resume_external_wait(self, wait_ref: str, response_ref: str, *, expected_version: int) -> TaskExternalWait:
        ...

    async def get_external_wait(self, wait_ref: str) -> TaskExternalWait | None:
        ...

    async def issue_profile_launch_ticket(self, ticket: ProfileLaunchTicket) -> ProfileLaunchTicket:
        ...

    async def get_profile_launch_ticket(self, ticket_ref: str) -> ProfileLaunchTicket | None:
        ...

    async def consume_profile_launch_ticket(self, ticket_ref: str, *, request_fingerprint: str, child_command_id: str, child_run_id: str, expected_version: int) -> ProfileLaunchConsumeResult:
        ...

    async def claim_profile_launch_and_commit_child(
        self,
        ticket_ref: str,
        intent: ChildCommandIntent,
        launch_request: Mapping[str, Any],
        *,
        expected_ticket_version: int,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: Any | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        ...

    async def cancel_task_goal(self, root_run_id: str, *, expected_version: int) -> TaskGoalRecord:
        ...

    async def recover_profile_launch_tickets(self, *, parent_run_id: str | None=None, limit: int=100) -> tuple[ProfileLaunchTicket, ...]:
        ...

    async def start_admission(self, spec: RunCreate, admission: AdmissionSpec, start_snapshot: AdmissionBoundary, waiting_event: RunEventCandidate, *, run_start_snapshot: RunStartSnapshotRecord | None=None, start_commit_extensions: Sequence[Any]=(), deliveries: Sequence[DeliverySpec]=()) -> AdmissionBoundary:
        ...

    async def resolve_admission(self, ref: RunRef, actor: ActorContext, signal: DecisionSignal, *, expected_boundary_version: int, terminal_deliveries: Sequence[DeliverySpec]=(), admission_task_grant: TaskGrant | None=None) -> AdmissionResolution:
        ...

    async def claim_admission_launch(self, recovery_lease: RecoveryLease, *, expected_boundary_version: int) -> AdmissionLaunchClaim:
        ...

    async def commit_run_outcome(self, run_id: str, *, expected_version: int, terminal_status: RunStatus | None=None, event: RunEventCandidate, deliveries: Sequence[DeliverySpec]=(), recovery_lease: Any | None=None, cancel_reason: str | None=None, admission_failure: AdmissionLaunchUnknownFence | None=None, terminal_commit_extensions: Sequence[Any]=(), delivery_fence: Mapping[str, Any] | None=None, release_receipt_kind: str | None=None) -> FinalizeRunResult | RunRecord:
        ...

    async def query(self, ref: RunRef, actor: ActorContext) -> RunView:
        ...

    async def authorize(self, ref: RunRef, actor: ActorContext, action: ActorAction | str) -> RunView:
        ...

    async def list_child_links(self, ref: RunRef, actor: ActorContext) -> tuple[RunLinkSpec, ...]:
        ...

    async def list_recoverable(self, *, limit: int=10000, run_ids: Sequence[str]=()) -> tuple[RunRecord, ...]:
        ...

    async def recovery_scope(self, subject: str | Any, *, owner: str | None=None, lease_seconds: float | None=30.0) -> Any:
        ...

    async def claim_workflow_recovery_handoff(self, recovery_lease: Any, *, workflow_owner: str, ttl_seconds: float=90.0) -> Any:
        ...

    async def assert_recovery_fence(self, lease: Any) -> None:
        ...

    async def get_event(self, event_id: str) -> RunEvent:
        ...

    async def read_terminal_extension_receipts(self, run_id: str, event_id: str) -> tuple[Mapping[str, str], ...]:
        ...

    async def list_events(self, run_id: str, *, after_durable_seq: int=0) -> tuple[RunEvent, ...]:
        ...

    async def list_event_deliveries(self, event_id: str) -> tuple[DeliveryRecord, ...]:
        ...

    async def claim_delivery(self, *, owner_generation: int, sink_keys: Sequence[SinkKey]=(), claim_ttl_seconds: float=30.0) -> DeliveryRecord | None:
        ...

    async def settle_delivery(self, delivery_id: str, *, expected_version: int, owner_generation: int, error: str | None=None, retry_at: float | None=None, discard: bool=False) -> DeliveryRecord:
        ...

    async def get_decision(self, decision_id: str, *, ref: RunRef, actor: ActorContext) -> DecisionRecord:
        ...

    async def commit_decision(self, signal: DecisionOpen | DecisionSignal, actor: ActorContext, *, expected_run_version: int | None=None, expected_continuation_version: int | None=None, continuation_payload: Mapping[str, Any] | None=None, resumed_event: RunEventCandidate | None=None, next_decision: DecisionOpen | None=None, deliveries: Sequence[DeliverySpec]=(), authorization_commit: PreparedAuthorizationCommit | None=None) -> tuple[DecisionRecord, DecisionAuthorization | None] | tuple[DecisionRecord, DecisionAuthorization | None, Any, RunEvent]:
        ...

    async def cancel_open_decisions(self, ref: RunRef, actor: ActorContext, *, expected_run_version: int) -> tuple[DecisionRecord, ...]:
        ...

    async def claim_tool_call(self, request: GrantConsume | None, actor: ActorContext, **kwargs: Any) -> DecisionAuthorization | Any:
        ...

    async def get_prepared_authorization_provenance(self, *, run_id: str, call_id: str, effect_id: str) -> Mapping[str, Any] | None:
        ...

    async def settle_effect(self, effect_id: str, **kwargs: Any) -> Any:
        ...

    async def mark_effect_dispatch_not_started(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
    ) -> Any:
        ...

    async def mark_effect_dispatch_started(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        ack_ref: str,
        ack_hash: str,
        *,
        ack_at: float | None = None,
    ) -> Any:
        ...

    async def mark_effect_inflight_may_complete(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
    ) -> Any:
        ...

    async def suppress_late_effect_completion(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        late_outcome_hash: str,
    ) -> Any:
        ...

    async def read_effect_handoff(self, effect_id: str) -> Any | None:
        ...

    async def reconcile_effect_handoff(
        self,
        effect_id: str,
        attempt_no: int,
        expected_effect_version: int,
        receipt_ref: str,
        receipt_hash: str,
        *,
        completed_suppressed: bool,
    ) -> Any:
        ...

    async def read_effect_outcome(self, *, run_id: str, call_id: str, effect_id: str, args_hash: str, capability_hash: str, scope_hash: str) -> tuple[str, Mapping[str, Any], str | None, tuple[str, ...]] | None:
        ...

    async def initialize(self) -> None:
        ...

    async def get_execution_owner(self, run_id: str) -> tuple[str, int] | None:
        ...

    async def commit_skill_scope_activation(
        self,
        run_id: str,
        *,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        activation_values: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Any]:
        ...

    async def get_skill_scope_activation(
        self, activation_id: str
    ) -> Mapping[str, Any] | None:
        ...

    async def activate_runtime(self, *, require_empty: bool=True, command: Any | None=None) -> Any:
        ...

    async def persist_react_boundary(self, run: str | RunCreate, expected_continuation_version: int, payload: Mapping[str, Any], decision: DecisionOpen | None=None, **kwargs: Any) -> Any:
        ...

    async def load_continuation(self, run_id: str) -> Any | None:
        ...

    async def lookup_completion_evidence(self, context: EvidenceContext) -> EvidenceSelection:
        ...

    async def start_workflow(self, spec: RunCreate, workflow: WorkflowRunSeed, *, start_snapshot: RunStartSnapshotRecord | None=None, start_commit_extensions: Sequence[Any]=(), association_event: RunEventCandidate | None=None, accepted_event: RunEventCandidate | None=None, deliveries: Sequence[DeliverySpec]=(), admission_launch: AdmissionLaunchClaim | None=None) -> WorkflowStartResult:
        ...

    async def commit_child_command(self, intent: ChildCommandIntent, *, recovery_lease: Any | None=None, transaction_hook: Any | None=None) -> ChildCommandRecord:
        ...

    async def commit_child_command_and_precreate_child(
        self,
        intent: ChildCommandIntent,
        start_snapshot: RunStartSnapshotRecord,
        *,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: Any | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        ...

    async def get_child_command(self, operation_id: str) -> ChildCommandRecord | None:
        ...

    async def get_child_command_for_run(self, child_run_id: str) -> ChildCommandRecord | None:
        ...

    async def lease_child_commands(self, *, owner: str, limit: int, lease_seconds: float, parent_run_id: str | None=None, recovery_lease: Any | None=None) -> tuple[ChildCommandRecord, ...]:
        ...

    async def schedule_child_command(self, operation_id: str, *, lease_owner: str, lease_epoch: int, recovery_lease: Any | None=None) -> ChildCommandRecord:
        ...

    async def acknowledge_child_command(self, operation_id: str, *, lease_owner: str, lease_epoch: int, recovery_lease: Any | None=None) -> ChildCommandRecord:
        ...

    async def finalize_child_and_enqueue_parent_signal(self, operation_id: str, *, expected_version: int, terminal_status: RunStatus, event: RunEventCandidate, value: object=None, deliveries: Sequence[DeliverySpec]=(), recovery_lease: Any | None=None, terminal_commit_extensions: Sequence[Any]=()) -> FinalizeRunResult:
        ...

    async def list_pending_child_signals(self, parent_run_id: str, *, limit: int=16, recovery_lease: Any | None=None) -> tuple[ChildSignalRecord, ...]:
        ...

    async def list_pending_child_signal_parents(self, *, limit: int=10000) -> tuple[RunRecord, ...]:
        ...

    async def prepare_workflow_child_resume(self, signal_id: str, *, recovery_lease: Any) -> DecisionRecord:
        ...

    async def ack_child_signal(self, signal_id: str, *, expected_continuation_version: int | None=None, continuation_payload: Mapping[str, Any] | None=None, event: RunEventCandidate | None=None, deliveries: Sequence[DeliverySpec]=(), recovery_lease: Any | None=None, transaction_hook: Any | None=None) -> ChildSignalRecord | tuple[ChildSignalRecord, Any, RunEvent]:
        ...
__all__ = [
    'CurrentExecutionScopeLease',
    'CurrentExecutionScopeLeasePort',
    'ExecutionUnitOfWork',
    'RunView',
    'SinkKey',
]
