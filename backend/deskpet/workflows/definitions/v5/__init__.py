"""DeepResearch v5 registration surface.

Product bootstrap registration is intentionally performed by the shared wiring
slice; importing this module is side-effect free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .deep_research import (
    DEEP_RESEARCH_V5,
    DEEP_RESEARCH_V5_DEFINITION,
    initial_state as deep_research_initial_state,
)

if TYPE_CHECKING:
    from ...runner import WorkflowRegistry


DEFAULT_DEEP_RESEARCH_VERSION = "v5"


def register_v5_workflows(registry: "WorkflowRegistry") -> None:
    registry.register(DEEP_RESEARCH_V5)


__all__ = [
    "DEFAULT_DEEP_RESEARCH_VERSION",
    "DEEP_RESEARCH_V5",
    "DEEP_RESEARCH_V5_DEFINITION",
    "deep_research_initial_state",
    "register_v5_workflows",
]
