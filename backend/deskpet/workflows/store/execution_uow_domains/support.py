"""Shared helpers for the split class."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class RuntimeActivationError(RuntimeError):
    """Fail-closed activation or execution-owner invariant violation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


class CheckpointExecutionError(RuntimeError):
    """Stable failure raised before a joined checkpoint transaction commits."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ContinuationRecord:
    """Product-neutral JSON continuation owned by the execution UoW."""

    run_id: str
    payload: Mapping[str, Any]
    version: int
    pending_decision_id: str | None
    created_at: float
    updated_at: float
