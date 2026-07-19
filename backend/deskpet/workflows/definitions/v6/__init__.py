"""DeepResearch v6 graph surface and registry hook."""

from ...runner import WorkflowRegistry

from .deep_research import (
    DEEP_RESEARCH_V6,
    DEEP_RESEARCH_V6_DEFINITION,
    build_continuation_start_payload,
    decode_continuation_snapshot,
    initial_state as deep_research_initial_state,
)

DEFAULT_DEEP_RESEARCH_VERSION = "v6"


def register_v6_workflows(registry: WorkflowRegistry) -> None:
    registry.register(DEEP_RESEARCH_V6)

__all__ = [
    "DEEP_RESEARCH_V6",
    "DEEP_RESEARCH_V6_DEFINITION",
    "DEFAULT_DEEP_RESEARCH_VERSION",
    "build_continuation_start_payload",
    "decode_continuation_snapshot",
    "deep_research_initial_state",
    "register_v6_workflows",
]
