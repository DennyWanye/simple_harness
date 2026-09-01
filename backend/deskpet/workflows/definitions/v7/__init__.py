"""DeepResearch v7 graph surface and registry hook."""

from ...runner import WorkflowRegistry
from .deep_research import (
    DEEP_RESEARCH_V7,
    DEEP_RESEARCH_V7_DEFINITION,
    initial_state as deep_research_initial_state,
)

DEFAULT_DEEP_RESEARCH_VERSION = "v7"


def register_v7_workflows(registry: WorkflowRegistry) -> None:
    registry.register(DEEP_RESEARCH_V7)


__all__ = [
    "DEEP_RESEARCH_V7", "DEEP_RESEARCH_V7_DEFINITION",
    "DEFAULT_DEEP_RESEARCH_VERSION", "deep_research_initial_state",
    "register_v7_workflows",
]
