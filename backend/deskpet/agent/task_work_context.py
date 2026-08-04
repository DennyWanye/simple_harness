"""Compatibility exports for task context contracts moved to ``deskpet.types``."""

from deskpet.types.task_work_context import (
    ConversationBoundary,
    MainSessionBinding,
    ProjectionState,
    QueuedUserContinuation,
    TaskIngressEnvelope,
    TaskRunProjection,
    TaskWorkContext,
    TaskWorkContextResolver,
    UserContinuationState,
    WorkspaceSource,
)

__all__ = [
    "ConversationBoundary",
    "MainSessionBinding",
    "ProjectionState",
    "QueuedUserContinuation",
    "TaskIngressEnvelope",
    "TaskRunProjection",
    "TaskWorkContext",
    "TaskWorkContextResolver",
    "UserContinuationState",
    "WorkspaceSource",
]
