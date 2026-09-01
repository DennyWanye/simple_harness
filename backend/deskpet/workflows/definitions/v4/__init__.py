"""DeepResearch v4 registration surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .deep_research import (
    DEEP_RESEARCH_V4,
    DEEP_RESEARCH_V4_DEFINITION,
)
from .deep_research import (
    initial_state as deep_research_initial_state,
)

if TYPE_CHECKING:
    from ...runner import WorkflowRegistry


DEFAULT_DEEP_RESEARCH_VERSION = "v4"


def register_v4_workflows(registry: WorkflowRegistry) -> None:
    registry.register(DEEP_RESEARCH_V4)


__all__ = [
    "DEEP_RESEARCH_V4",
    "DEEP_RESEARCH_V4_DEFINITION",
    "DEFAULT_DEEP_RESEARCH_VERSION",
    "deep_research_initial_state",
    "register_v4_workflows",
]
