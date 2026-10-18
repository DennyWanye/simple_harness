"""SQLite execution and workflow unit of work."""

from __future__ import annotations

from .execution_uow_domains.attempts import ProviderAttemptMixin
from .execution_uow_domains.children import ChildCommandMixin
from .execution_uow_domains.continuations import ContinuationAdmissionMixin
from .execution_uow_domains.core import ExecutionTx
from .execution_uow_domains.decisions import DecisionMixin
from .execution_uow_domains.deliveries import DeliveryMixin
from .execution_uow_domains.effects import EffectHandoffMixin
from .execution_uow_domains.events import RunEventMixin
from .execution_uow_domains.inspection import RunInspectionMixin
from .execution_uow_domains.outcomes import RunOutcomeMixin
from .execution_uow_domains.profile_tickets import ProfileTicketMixin
from .execution_uow_domains.provider_invocations import ProviderInvocationMixin
from .execution_uow_domains.recovery import RecoveryBudgetMixin
from .execution_uow_domains.run_lifecycle import RunLifecycleMixin
from .execution_uow_domains.runtime_activation import (
    ExecutionRuntimeState,
    LegacyDrainLease,
    LegacyDrainRef,
    RuntimeActivationCommand,
    RuntimeActivationMixin,
)
from .execution_uow_domains.support import (
    CheckpointExecutionError,
    ContinuationRecord,
    RuntimeActivationError,
)
from .execution_uow_domains.task_context import TaskContextMixin
from .execution_uow_domains.tool_calls import ToolCallMixin

# Stable crash windows exercised by the R1 restart matrix.  This is kept
# separate from migration/compatibility fault points: changing this set is a
# durable-protocol change and therefore requires updating fault_matrix.json.
FAULT_HOOKS = frozenset(
    {
        "batch_boundary_after_promotion",
        "batch_boundary_after_continuation",
        "batch_boundary_after_waiting_event",
        "batch_boundary_before_commit",
        "decision_resolve_after_cas",
        "decision_resolve_after_grant",
        "decision_resolve_after_boundary",
        "decision_resolve_before_commit",
        "effect_claim_after_grant",
        "effect_claim_after_effect",
        "effect_claim_before_commit",
        "effect_settle_after_attempt",
        "effect_settle_after_effect",
        "effect_settle_after_link",
        "effect_settle_after_boundary",
        "effect_settle_before_commit",
        "grant_consume_before_commit",
        "finalize_after_outbox",
        "finalize_before_commit",
        "child_command_before_commit",
        "child_schedule_after_run",
        "child_schedule_before_commit",
        "child_terminal_before_commit",
        "child_signal_ack_before_commit",
        "child_apply_after_boundary",
        "child_apply_after_event",
        "child_apply_before_commit",
        "child_finalize_after_terminal",
        "child_finalize_after_parent_signal",
        "checkpoint_consume_decisions_after_write",
        "checkpoint_open_decision_after_write",
        "checkpoint_append_event_after_write",
        "checkpoint_link_effects_after_write",
        "checkpoint_finalize_run_after_write",
    }
)

ATOMIC_OPERATIONS = (
    ("decision_boundary", ("commit_decision",)),
    ("effect_settle_boundary", ("settle_effect",)),
    ("terminal_delivery", ("commit_run_outcome",)),
    ("child_apply_ack", ("ack_child_signal",)),
    ("grant_effect_claim", ("claim_tool_call",)),
    ("durable_promotion", ("persist_react_boundary",)),
    ("durable_child_schedule", ("commit_child_command", "schedule_child_command")),
    ("child_terminal_signal", ("finalize_child_and_enqueue_parent_signal",)),
)


class SqliteExecutionUnitOfWork(
    RuntimeActivationMixin,
    RunLifecycleMixin,
    ProviderInvocationMixin,
    RunEventMixin,
    DeliveryMixin,
    ContinuationAdmissionMixin,
    DecisionMixin,
    ToolCallMixin,
    EffectHandoffMixin,
    RunOutcomeMixin,
    ChildCommandMixin,
    RecoveryBudgetMixin,
    TaskContextMixin,
    RunInspectionMixin,
    ProviderAttemptMixin,
    ProfileTicketMixin,
):
    """CAS-based execution ledger backed by the workflow SQLite database."""


__all__ = [
    "CheckpointExecutionError",
    "ContinuationRecord",
    "ExecutionTx",
    "ExecutionRuntimeState",
    "LegacyDrainLease",
    "LegacyDrainRef",
    "RuntimeActivationCommand",
    "RuntimeActivationError",
    "SqliteExecutionUnitOfWork",
]
