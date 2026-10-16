"""执行层契约：任务目标、计划版本、提供方回合与动作批次、尝试与失败报告、外部等待、启动票据（从 execution/contracts 挪出）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from deskpet.execution.contracts import (
    CONTRACT_SCHEMA_VERSION,
    AttemptStatus,
    ContractValidationError,
    ExternalWaitKind,
    ExternalWaitState,
    FailureBackfillState,
    FailureErrorClass,
    FailureSourceKind,
    ProfileLaunchTicketState,
    ProviderActionAdmissionState,
    ProviderBatchStatus,
    ProviderResumeState,
    ProviderTurnState,
    TaskGoalStatus,
    _Contract,
    _fingerprint,
    _integer,
    _optional_text,
    _required_text,
    _schema_version,
    _text_tuple,
)


@dataclass(frozen=True, slots=True)
class TaskGoalRecord(_Contract):
    goal_id: str
    root_run_id: str
    task_scope_id: str
    objective_ref: str
    status: TaskGoalStatus | str = TaskGoalStatus.ACTIVE
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    ended_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="TaskGoalRecord")
        for name in ("goal_id", "root_run_id", "task_scope_id", "objective_ref"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "status", TaskGoalStatus(self.status))
        _integer(self.version, 0, "invalid_version", "goal version must be non-negative")
        terminal = self.status in {
            TaskGoalStatus.COMPLETED,
            TaskGoalStatus.BLOCKED,
            TaskGoalStatus.CANCELLED,
        }
        if terminal != (self.ended_at is not None):
            raise ContractValidationError(
                "invalid_goal_terminal", "terminal goal state and ended_at must agree"
            )


@dataclass(frozen=True, slots=True)
class PlanVersionRecord(_Contract):
    root_run_id: str
    plan_version: int
    trigger_failure_set_id: str | None = None
    created_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="PlanVersionRecord")
        object.__setattr__(self, "root_run_id", _required_text(self.root_run_id, "root_run_id"))
        _integer(
            self.plan_version,
            1,
            "invalid_plan_version",
            "plan_version must be a positive integer",
        )
        object.__setattr__(
            self,
            "trigger_failure_set_id",
            _optional_text(self.trigger_failure_set_id, "trigger_failure_set_id"),
        )


@dataclass(frozen=True, slots=True)
class ProviderTurnFence(_Contract):
    provider_turn_id: str
    root_run_id: str
    idempotency_key: str
    request_hash: str
    state: ProviderTurnState | str = ProviderTurnState.PENDING
    accepted_batch_id: str | None = None
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    accepted_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ProviderTurnFence")
        for name in ("provider_turn_id", "root_run_id", "idempotency_key"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "request_hash", _fingerprint(self.request_hash, "request_hash"))
        object.__setattr__(self, "state", ProviderTurnState(self.state))
        object.__setattr__(
            self,
            "accepted_batch_id",
            _optional_text(self.accepted_batch_id, "accepted_batch_id"),
        )
        _integer(self.version, 0, "invalid_version", "provider turn version must be non-negative")
        accepted = self.state is ProviderTurnState.ACCEPTED
        if accepted != (self.accepted_batch_id is not None and self.accepted_at is not None):
            raise ContractValidationError(
                "invalid_provider_turn_state",
                "accepted provider turns require batch identity and accepted_at",
            )


@dataclass(frozen=True, slots=True)
class ProviderActionBatch(_Contract):
    provider_batch_id: str
    root_run_id: str
    provider_turn_id: str
    canonical_assistant_batch_ref: str
    batch_fingerprint: str
    pending_call_count: int
    status: ProviderBatchStatus | str = ProviderBatchStatus.ADMITTED
    failure_set_id: str | None = None
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    settled_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ProviderActionBatch")
        for name in (
            "provider_batch_id",
            "root_run_id",
            "provider_turn_id",
            "canonical_assistant_batch_ref",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(
            self,
            "batch_fingerprint",
            _fingerprint(self.batch_fingerprint, "batch_fingerprint"),
        )
        object.__setattr__(self, "status", ProviderBatchStatus(self.status))
        minimum_pending = 0 if self.status is ProviderBatchStatus.SETTLED else 1
        _integer(
            self.pending_call_count,
            minimum_pending,
            "invalid_call_count",
            (
                "settled provider batches may contain no pending calls"
                if minimum_pending == 0
                else "live provider batches must contain at least one pending call"
            ),
        )
        if self.status is ProviderBatchStatus.SETTLED and self.pending_call_count != 0:
            raise ContractValidationError(
                "invalid_call_count",
                "settled provider batches must contain no pending calls",
            )
        object.__setattr__(
            self,
            "failure_set_id",
            _optional_text(self.failure_set_id, "failure_set_id"),
        )
        _integer(self.version, 0, "invalid_version", "batch version must be non-negative")
        if (self.status is ProviderBatchStatus.SETTLED) != (self.settled_at is not None):
            raise ContractValidationError(
                "invalid_batch_terminal", "settled state and settled_at must agree"
            )


@dataclass(frozen=True, slots=True)
class ProviderActionCall(_Contract):
    call_record_id: str
    root_run_id: str
    provider_batch_id: str
    call_order: int
    provider_call_id: str
    raw_tool_name: str
    raw_arguments_ref: str
    raw_arguments_hash: str
    parsed_arguments_hash: str | None = None
    admission_state: ProviderActionAdmissionState | str = ProviderActionAdmissionState.ADMITTED
    prepared_call_ref: str | None = None
    command_boundary_ref: str | None = None
    terminal_outcome_ref: str | None = None
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ProviderActionCall")
        for name in (
            "call_record_id",
            "root_run_id",
            "provider_batch_id",
            "provider_call_id",
            "raw_tool_name",
            "raw_arguments_ref",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        _integer(self.call_order, 0, "invalid_call_order", "call_order must be non-negative")
        object.__setattr__(
            self,
            "raw_arguments_hash",
            _fingerprint(self.raw_arguments_hash, "raw_arguments_hash"),
        )
        if self.parsed_arguments_hash is not None:
            object.__setattr__(
                self,
                "parsed_arguments_hash",
                _fingerprint(self.parsed_arguments_hash, "parsed_arguments_hash"),
            )
        object.__setattr__(
            self,
            "admission_state",
            ProviderActionAdmissionState(self.admission_state),
        )
        for name in (
            "prepared_call_ref",
            "command_boundary_ref",
            "terminal_outcome_ref",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        _integer(self.version, 0, "invalid_version", "call version must be non-negative")
        if self.admission_state is ProviderActionAdmissionState.PREPARED and (
            self.parsed_arguments_hash is None or self.prepared_call_ref is None
        ):
            raise ContractValidationError(
                "invalid_prepared_call",
                "prepared calls require parsed hash and prepared_call_ref",
            )
        terminal = self.admission_state in {
            ProviderActionAdmissionState.REJECTED,
            ProviderActionAdmissionState.SETTLED,
        }
        if terminal != (self.terminal_outcome_ref is not None):
            raise ContractValidationError(
                "invalid_call_terminal",
                "terminal call state and terminal_outcome_ref must agree",
            )


@dataclass(frozen=True, slots=True)
class AttemptRecord(_Contract):
    attempt_id: str
    root_run_id: str
    run_id: str
    provider_turn_id: str
    provider_batch_id: str
    plan_version: int
    strategy_fingerprint: str
    planned_call_refs: Sequence[str]
    status: AttemptStatus | str = AttemptStatus.RUNNING
    trigger_failure_set_id: str | None = None
    supersedes_attempt_id: str | None = None
    checkpoint_ref: str | None = None
    budget_eligible: bool = True
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    ended_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="AttemptRecord")
        for name in (
            "attempt_id",
            "root_run_id",
            "run_id",
            "provider_turn_id",
            "provider_batch_id",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        _integer(
            self.plan_version,
            1,
            "invalid_plan_version",
            "attempt plan_version must be positive",
        )
        object.__setattr__(
            self,
            "strategy_fingerprint",
            _fingerprint(self.strategy_fingerprint, "strategy_fingerprint"),
        )
        refs = _text_tuple(self.planned_call_refs, "planned_call_ref")
        if not refs:
            raise ContractValidationError(
                "empty_attempt", "an attempt requires at least one provider call"
            )
        object.__setattr__(self, "planned_call_refs", refs)
        object.__setattr__(self, "status", AttemptStatus(self.status))
        for name in (
            "trigger_failure_set_id",
            "supersedes_attempt_id",
            "checkpoint_ref",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        if not isinstance(self.budget_eligible, bool):
            raise ContractValidationError(
                "invalid_budget_eligibility", "budget_eligible must be boolean"
            )
        _integer(self.version, 0, "invalid_version", "attempt version must be non-negative")
        terminal = self.status in {
            AttemptStatus.FAILED,
            AttemptStatus.REJECTED,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.BLOCKED,
            AttemptStatus.CANCELLED,
        }
        if terminal != (self.ended_at is not None):
            raise ContractValidationError(
                "invalid_attempt_terminal",
                "terminal attempt state and ended_at must agree",
            )


@dataclass(frozen=True, slots=True)
class TaskFailureReport(_Contract):
    report_ref: str
    root_run_id: str
    run_id: str
    task_scope_id: str
    attempt_id: str
    plan_version: int
    call_record_id: str
    source_kind: FailureSourceKind | str
    source_identity: str
    failed_step: str
    error_class: FailureErrorClass | str
    error_code: str
    error_fingerprint: str
    action_fingerprint: str
    provider_call_id: str | None = None
    child_run_id: str | None = None
    inner_failure_ref: str | None = None
    failed_call_id: str | None = None
    failed_effect_id: str | None = None
    exit_code: int | None = None
    evidence_refs: Sequence[str] = field(default_factory=tuple)
    completed_step_refs: Sequence[str] = field(default_factory=tuple)
    artifact_refs: Sequence[str] = field(default_factory=tuple)
    checkpoint_ref: str | None = None
    prior_strategy_fingerprints: Sequence[str] = field(default_factory=tuple)
    created_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="TaskFailureReport")
        for name in (
            "report_ref",
            "root_run_id",
            "run_id",
            "task_scope_id",
            "attempt_id",
            "call_record_id",
            "source_identity",
            "failed_step",
            "error_code",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        _integer(
            self.plan_version,
            1,
            "invalid_plan_version",
            "failure plan_version must be positive",
        )
        object.__setattr__(self, "source_kind", FailureSourceKind(self.source_kind))
        object.__setattr__(self, "error_class", FailureErrorClass(self.error_class))
        for name in ("error_fingerprint", "action_fingerprint"):
            object.__setattr__(self, name, _fingerprint(getattr(self, name), name))
        for name in (
            "provider_call_id",
            "child_run_id",
            "inner_failure_ref",
            "failed_call_id",
            "failed_effect_id",
            "checkpoint_ref",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        for name in ("evidence_refs", "completed_step_refs", "artifact_refs"):
            object.__setattr__(self, name, _text_tuple(getattr(self, name), name[:-1]))
        strategies = tuple(
            _fingerprint(item, "prior_strategy_fingerprint")
            for item in self.prior_strategy_fingerprints
        )
        if len(set(strategies)) != len(strategies):
            raise ContractValidationError(
                "duplicate_strategy", "prior strategy fingerprints must be unique"
            )
        object.__setattr__(self, "prior_strategy_fingerprints", strategies)


@dataclass(frozen=True, slots=True)
class AttemptFailureSet(_Contract):
    failure_set_id: str
    root_run_id: str
    failed_attempt_id: str
    report_refs: Sequence[str]
    primary_report_ref: str
    backfill_state: FailureBackfillState | str = FailureBackfillState.PENDING
    provider_resume_state: ProviderResumeState | str = ProviderResumeState.PENDING
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="AttemptFailureSet")
        for name in (
            "failure_set_id",
            "root_run_id",
            "failed_attempt_id",
            "primary_report_ref",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        refs = _text_tuple(self.report_refs, "report_ref")
        if not refs or self.primary_report_ref not in refs:
            raise ContractValidationError(
                "invalid_primary_report",
                "failure set requires reports and a primary member",
            )
        object.__setattr__(self, "report_refs", refs)
        object.__setattr__(self, "backfill_state", FailureBackfillState(self.backfill_state))
        object.__setattr__(
            self,
            "provider_resume_state",
            ProviderResumeState(self.provider_resume_state),
        )
        _integer(self.version, 0, "invalid_version", "failure set version must be non-negative")


@dataclass(frozen=True, slots=True)
class TaskExternalWait(_Contract):
    wait_ref: str
    root_run_id: str
    attempt_id: str
    call_record_id: str
    provider_call_id: str
    wait_kind: ExternalWaitKind | str
    required_action_ref: str
    resume_admission_state: ProviderActionAdmissionState | str
    command_boundary_ref: str | None = None
    effect_id: str | None = None
    checkpoint_ref: str | None = None
    evidence_refs: Sequence[str] = field(default_factory=tuple)
    state: ExternalWaitState | str = ExternalWaitState.OPEN
    response_ref: str | None = None
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    resolved_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="TaskExternalWait")
        for name in (
            "wait_ref",
            "root_run_id",
            "attempt_id",
            "call_record_id",
            "provider_call_id",
            "required_action_ref",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "wait_kind", ExternalWaitKind(self.wait_kind))
        resume = ProviderActionAdmissionState(self.resume_admission_state)
        if resume not in {
            ProviderActionAdmissionState.ADMITTED,
            ProviderActionAdmissionState.PREPARED,
        }:
            raise ContractValidationError(
                "invalid_resume_state",
                "external wait resumes to admitted or prepared",
            )
        object.__setattr__(self, "resume_admission_state", resume)
        for name in ("command_boundary_ref", "effect_id", "checkpoint_ref", "response_ref"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_ref"))
        object.__setattr__(self, "state", ExternalWaitState(self.state))
        _integer(self.version, 0, "invalid_version", "wait version must be non-negative")
        if self.state is ExternalWaitState.OPEN:
            if self.response_ref is not None or self.resolved_at is not None:
                raise ContractValidationError(
                    "invalid_wait_state", "open waits cannot carry a response"
                )
        elif self.state is ExternalWaitState.SATISFIED:
            if self.response_ref is None or self.resolved_at is None:
                raise ContractValidationError(
                    "invalid_wait_state", "satisfied waits require response and time"
                )
        elif self.resolved_at is None:
            raise ContractValidationError(
                "invalid_wait_state", "cancelled waits require resolved_at"
            )


@dataclass(frozen=True, slots=True)
class ProfileLaunchTicket(_Contract):
    ticket_ref: str
    parent_run_id: str
    root_run_id: str
    task_scope_id: str
    attempt_id: str
    provider_turn_id: str
    profile_key: str
    driver_kind: str
    profile_catalog_generation: int
    capability_snapshot_ref: str
    task_grant_ref: str
    spawn_call_id: str
    request_fingerprint: str
    trigger_failure_set_id: str | None = None
    personal_selection_id: str | None = None
    personal_selection_fingerprint: str | None = None
    state: ProfileLaunchTicketState | str = ProfileLaunchTicketState.ISSUED
    child_command_id: str | None = None
    child_run_id: str | None = None
    version: int = 0
    created_at: float | None = None
    updated_at: float | None = None
    consumed_at: float | None = None
    cancelled_at: float | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ProfileLaunchTicket")
        for name in (
            "ticket_ref",
            "parent_run_id",
            "root_run_id",
            "task_scope_id",
            "attempt_id",
            "provider_turn_id",
            "profile_key",
            "driver_kind",
            "capability_snapshot_ref",
            "task_grant_ref",
            "spawn_call_id",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        _integer(
            self.profile_catalog_generation,
            1,
            "invalid_catalog_generation",
            "profile catalog generation must be positive",
        )
        object.__setattr__(
            self,
            "request_fingerprint",
            _fingerprint(self.request_fingerprint, "request_fingerprint"),
        )
        object.__setattr__(
            self,
            "trigger_failure_set_id",
            _optional_text(self.trigger_failure_set_id, "trigger_failure_set_id"),
        )
        object.__setattr__(
            self,
            "personal_selection_id",
            _optional_text(self.personal_selection_id, "personal_selection_id"),
        )
        if self.personal_selection_fingerprint is not None:
            object.__setattr__(
                self,
                "personal_selection_fingerprint",
                _fingerprint(
                    self.personal_selection_fingerprint,
                    "personal_selection_fingerprint",
                ),
            )
        if (self.personal_selection_id is None) != (self.personal_selection_fingerprint is None):
            raise ContractValidationError(
                "personal_selection_identity_incomplete",
                "personal selection id and fingerprint must appear together",
            )
        if self.profile_key != "workflow.personal_v1" and self.personal_selection_id is not None:
            raise ContractValidationError(
                "personal_selection_not_applicable",
                "personal selection is only valid for workflow.personal_v1",
            )
        object.__setattr__(self, "state", ProfileLaunchTicketState(self.state))
        for name in ("child_command_id", "child_run_id"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        _integer(self.version, 0, "invalid_version", "ticket version must be non-negative")
        if self.state is ProfileLaunchTicketState.ISSUED:
            valid = (
                self.child_command_id is None
                and self.child_run_id is None
                and self.consumed_at is None
                and self.cancelled_at is None
            )
        elif self.state is ProfileLaunchTicketState.CONSUMED:
            valid = (
                self.child_command_id is not None
                and self.child_run_id is not None
                and self.consumed_at is not None
                and self.cancelled_at is None
            )
        else:
            valid = (
                self.child_command_id is None
                and self.child_run_id is None
                and self.consumed_at is None
                and self.cancelled_at is not None
            )
        if not valid:
            raise ContractValidationError(
                "invalid_profile_ticket_state",
                "ticket state does not match child and terminal fields",
            )


@dataclass(frozen=True, slots=True)
class ProfileLaunchConsumeResult:
    ticket: ProfileLaunchTicket
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ProviderBatchAdmissionResult:
    attempt: AttemptRecord
    calls: tuple[ProviderActionCall, ...]
    idempotent: bool


__all__ = [
    "AttemptFailureSet",
    "AttemptRecord",
    "PlanVersionRecord",
    "ProfileLaunchConsumeResult",
    "ProfileLaunchTicket",
    "ProviderActionBatch",
    "ProviderActionCall",
    "ProviderBatchAdmissionResult",
    "ProviderTurnFence",
    "TaskExternalWait",
    "TaskFailureReport",
    "TaskGoalRecord",
]
