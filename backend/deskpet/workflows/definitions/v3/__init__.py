"""Immutable v3 workflow definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .deep_research import (
    DEEP_RESEARCH_V3,
    DEEP_RESEARCH_V3_DEFINITION,
    initial_state as deep_research_initial_state,
)

DEFAULT_DEEP_RESEARCH_VERSION = "v3"

if TYPE_CHECKING:
    from ...runner import WorkflowRegistry


def register_v3_workflows(registry: "WorkflowRegistry") -> None:
    registry.register(DEEP_RESEARCH_V3)


__all__ = [
    "DEFAULT_DEEP_RESEARCH_VERSION", "DEEP_RESEARCH_V3",
    "DEEP_RESEARCH_V3_DEFINITION", "deep_research_initial_state",
    "register_v3_workflows",
]
