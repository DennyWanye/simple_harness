"""Immutable production workflow definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..personal_workflow import (
    PERSONAL_WORKFLOW_V1,
    PERSONAL_WORKFLOW_V1_DEFINITION,
)
from ..personal_workflow import (
    initial_state as personal_workflow_initial_state,
)
from .code_task import (
    CODE_COMPLEX_V1,
    CODE_COMPLEX_V1_DEFINITION,
)
from .code_task import (
    initial_state as code_complex_initial_state,
)
from .deep_research import (
    DEEP_RESEARCH_V1,
    DEEP_RESEARCH_V1_DEFINITION,
)
from .deep_research import (
    initial_state as deep_research_initial_state,
)
from .durable_task import (
    DURABLE_TASK_V1,
    DURABLE_TASK_V1_DEFINITION,
)
from .durable_task import (
    initial_state as durable_task_initial_state,
)
from .ppt_pro import (
    PPT_PRO_V1,
    PPT_PRO_V1_DEFINITION,
)
from .ppt_pro import (
    initial_state as ppt_pro_initial_state,
)

if TYPE_CHECKING:
    from ...runner import WorkflowRegistry


def register_v1_workflows(registry: WorkflowRegistry) -> None:
    """Register every available immutable v1 graph."""

    registry.register(DEEP_RESEARCH_V1)
    registry.register(PPT_PRO_V1)
    registry.register(CODE_COMPLEX_V1)
    registry.register(DURABLE_TASK_V1)
    registry.register(PERSONAL_WORKFLOW_V1)


__all__ = [
    "CODE_COMPLEX_V1",
    "CODE_COMPLEX_V1_DEFINITION",
    "DEEP_RESEARCH_V1",
    "DEEP_RESEARCH_V1_DEFINITION",
    "DURABLE_TASK_V1",
    "DURABLE_TASK_V1_DEFINITION",
    "PERSONAL_WORKFLOW_V1",
    "PERSONAL_WORKFLOW_V1_DEFINITION",
    "PPT_PRO_V1",
    "PPT_PRO_V1_DEFINITION",
    "code_complex_initial_state",
    "deep_research_initial_state",
    "durable_task_initial_state",
    "personal_workflow_initial_state",
    "ppt_pro_initial_state",
    "register_v1_workflows",
]
