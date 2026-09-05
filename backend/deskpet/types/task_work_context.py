"""Root-local task, workspace, and conversation contracts.

The main chat session is only an ingress/delivery container.  Every executable
task receives its own immutable root identity and a versioned workspace
binding, so switching UI projections cannot silently move execution to another
task.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal


WorkspaceSource = Literal["existing", "user_path", "task_default", "none"]
ProjectionState = Literal["open", "background", "closed"]
UserContinuationState = Literal["pending", "bound", "failed"]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(
        "\0".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:32]}"


def _safe_task_directory(task_scope_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", task_scope_id).strip(".-")
    return (value or hashlib.sha256(task_scope_id.encode()).hexdigest()[:24])[:80]


@dataclass(frozen=True, slots=True)
class TaskWorkContext:
    session_id: str
    root_run_id: str
    task_scope_id: str
    workspace_root: str | None
    workspace_source: WorkspaceSource
    binding_version: int = 1

    def __post_init__(self) -> None:
        if not self.session_id or not self.root_run_id or not self.task_scope_id:
            raise ValueError("task work context identity is required")
        if self.binding_version < 1:
            raise ValueError("workspace binding version must be positive")
        if self.workspace_source == "none" and self.workspace_root is not None:
            raise ValueError("workspace_source=none cannot carry a workspace")
        if self.workspace_source != "none" and not self.workspace_root:
            raise ValueError("a workspace source requires a workspace root")


@dataclass(frozen=True, slots=True)
class PrimaryRunWorkContext:
    """An unscoped primary Run carries no project/workspace authority."""
    session_id: str
    root_run_id: str
    task_scope_id: None = None
    workspace_root: None = None
    workspace_source: WorkspaceSource = "none"
    binding_version: int = 0

    def __post_init__(self) -> None:
        if not self.session_id or not self.root_run_id:
            raise ValueError("primary run identity is required")
        if (self.task_scope_id is not None or self.workspace_root is not None
                or self.workspace_source != "none" or type(self.binding_version) is not int or self.binding_version != 0):
            raise ValueError("unscoped primary context cannot carry workspace authority")


@dataclass(frozen=True, slots=True)
class MainSessionBinding:
    session_id: str
    generation: int
    created_at: str


@dataclass(frozen=True, slots=True)
class TaskIngressEnvelope:
    session_id: str
    message_ref: str
    task_scope_id: str | None = None
    target_root_run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id or not self.message_ref:
            raise ValueError("ingress session and message reference are required")
        if bool(self.task_scope_id) != bool(self.target_root_run_id):
            raise ValueError(
                "continuation ingress must bind both task_scope_id and target_root_run_id"
            )


@dataclass(frozen=True, slots=True)
class TaskRunProjection:
    projection_id: str
    session_id: str
    root_run_id: str
    task_scope_id: str
    ui_state: ProjectionState = "open"
    version: int = 0

    def __post_init__(self) -> None:
        if (
            not self.projection_id
            or not self.session_id
            or not self.root_run_id
            or not self.task_scope_id
        ):
            raise ValueError("task run projection identity is required")
        if self.ui_state not in {"open", "background", "closed"}:
            raise ValueError(f"unsupported projection state: {self.ui_state}")
        if self.version < 0:
            raise ValueError("projection version cannot be negative")


@dataclass(frozen=True, slots=True)
class ConversationBoundary:
    boundary_ref: str
    session_id: str
    root_run_id: str
    task_scope_id: str
    seed_message_refs: tuple[str, ...]
    continuation_message_refs: tuple[str, ...] = ()
    version: int = 1

    def append(self, message_ref: str, *, expected_version: int) -> "ConversationBoundary":
        if expected_version != self.version:
            raise ValueError("conversation boundary version changed")
        if not message_ref:
            raise ValueError("continuation message reference is required")
        if message_ref in self.seed_message_refs or message_ref in self.continuation_message_refs:
            return self
        return replace(
            self,
            continuation_message_refs=(*self.continuation_message_refs, message_ref),
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class QueuedUserContinuation:
    root_run_id: str
    task_scope_id: str
    message_ref: str
    content: str
    reserved_boundary_version: int
    status: UserContinuationState = "pending"
    created_at: float = 0.0
    settled_at: float | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.root_run_id
            or not self.task_scope_id
            or not self.message_ref
            or not self.content.strip()
        ):
            raise ValueError("queued continuation identity and content are required")
        if self.reserved_boundary_version < 2:
            raise ValueError("queued continuation must reserve a later boundary")
        if self.status not in {"pending", "bound", "failed"}:
            raise ValueError(f"unsupported queued continuation state: {self.status}")
        if self.status == "pending" and (
            self.settled_at is not None or self.error is not None
        ):
            raise ValueError("a pending continuation cannot be settled")
        if self.status == "bound" and (
            self.settled_at is None or self.error is not None
        ):
            raise ValueError("a bound continuation requires a clean timestamp")
        if self.status == "failed" and (
            self.settled_at is None or not self.error
        ):
            raise ValueError("a failed continuation requires error evidence")


class TaskWorkContextResolver:
    """Canonicalize task workspaces without consulting a UI mode.

    Persistence is deliberately owned by the execution UoW.  This resolver is
    a deterministic host helper used both before the first commit and while
    hydrating an already committed binding.
    """

    def __init__(self, default_workspace_root: str | Path | None = None) -> None:
        self._default_root = (
            None
            if default_workspace_root is None
            else Path(default_workspace_root).expanduser().resolve(strict=False)
        )

    @staticmethod
    def task_scope_id(session_id: str, request_id: str, turn_id: str) -> str:
        if not session_id or not request_id or not turn_id:
            raise ValueError("stable task identity requires session/request/turn ids")
        return _stable_id("task", session_id, request_id, turn_id)

    def resolve(
        self,
        *,
        session_id: str,
        root_run_id: str,
        task_scope_id: str,
        explicit_workspace: str | Path | None = None,
        existing_workspace: str | Path | None = None,
        require_workspace: bool = False,
        create_default: bool = False,
        binding_version: int = 1,
    ) -> TaskWorkContext:
        selected: Path | None = None
        source: WorkspaceSource = "none"
        if explicit_workspace is not None:
            selected = Path(explicit_workspace).expanduser().resolve(strict=False)
            source = "user_path"
        elif existing_workspace is not None:
            selected = Path(existing_workspace).expanduser().resolve(strict=False)
            source = "existing"
        elif require_workspace or create_default:
            if self._default_root is None:
                raise RuntimeError("default workspace root is not configured")
            selected = (
                self._default_root / _safe_task_directory(task_scope_id)
            ).resolve(strict=False)
            source = "task_default"
            if create_default:
                selected.mkdir(parents=True, exist_ok=True)
        return TaskWorkContext(
            session_id=session_id,
            root_run_id=root_run_id,
            task_scope_id=task_scope_id,
            workspace_root=None if selected is None else str(selected),
            workspace_source=source,
            binding_version=binding_version,
        )

    @staticmethod
    def conversation_boundary(
        context: TaskWorkContext, seed_message_refs: tuple[str, ...]
    ) -> ConversationBoundary:
        refs = tuple(dict.fromkeys(ref for ref in seed_message_refs if ref))
        if not refs:
            raise ValueError("a root conversation requires at least one seed message")
        return ConversationBoundary(
            boundary_ref=_stable_id(
                "conversation",
                context.session_id,
                context.root_run_id,
                context.task_scope_id,
            ),
            session_id=context.session_id,
            root_run_id=context.root_run_id,
            task_scope_id=context.task_scope_id,
            seed_message_refs=refs,
        )

    @staticmethod
    def projection(context: TaskWorkContext) -> TaskRunProjection:
        return TaskRunProjection(
            projection_id=_stable_id(
                "projection",
                context.session_id,
                context.root_run_id,
                context.task_scope_id,
            ),
            session_id=context.session_id,
            root_run_id=context.root_run_id,
            task_scope_id=context.task_scope_id,
        )


__all__ = [
    "ConversationBoundary",
    "MainSessionBinding",
    "ProjectionState",
    "QueuedUserContinuation",
    "TaskIngressEnvelope",
    "TaskRunProjection",
    "TaskWorkContext",
    "PrimaryRunWorkContext",
    "TaskWorkContextResolver",
    "UserContinuationState",
    "WorkspaceSource",
]
