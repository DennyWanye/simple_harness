"""Typed cross-Run reference contracts shared by product context code."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskReferenceKind(str, Enum):
    DEMONSTRATIVE = "demonstrative"
    RECENT = "recent"
    CONTINUATION = "continuation"


class TaskReferenceStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class TaskReferenceIntent:
    kind: TaskReferenceKind
    source_text: str
    markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskReferenceCandidate:
    reference_id: str
    root_run_id: str | None
    task_scope_id: str | None
    row_ids: tuple[int, ...]
    preview: str
    match_tokens: tuple[str, ...]
    recency: int

    def __post_init__(self) -> None:
        if not self.reference_id:
            raise ValueError("task reference candidate requires reference_id")
        if not self.root_run_id and not self.task_scope_id:
            raise ValueError(
                "task reference candidate requires root_run_id or task_scope_id"
            )


@dataclass(frozen=True, slots=True)
class TaskReferenceResolution:
    status: TaskReferenceStatus
    intent: TaskReferenceIntent | None
    candidates: tuple[TaskReferenceCandidate, ...] = ()
    selected_reference_id: str | None = None

    def __post_init__(self) -> None:
        selected = self.selected_reference_id
        if self.status is TaskReferenceStatus.RESOLVED:
            if not selected:
                raise ValueError("resolved task reference requires selection")
            if selected not in {
                item.reference_id for item in self.candidates
            }:
                raise ValueError("selected task reference is not a candidate")
        elif selected is not None:
            raise ValueError("unresolved task reference cannot select candidate")


__all__ = [
    "TaskReferenceCandidate",
    "TaskReferenceIntent",
    "TaskReferenceKind",
    "TaskReferenceResolution",
    "TaskReferenceStatus",
]
