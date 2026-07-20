"""Product-neutral execution harness contracts.

The package is add-only until the WI-12 atomic production cut-over.  Current
production entry points intentionally remain owned by their legacy adapters.
"""

from .context import HostContextFactory, RunContext
from .tool_executor import (
    DecisionAuthorization,
    LegacyPreparedCallAdapter,
    PreparedExecutionCall,
    ToolOutcome,
    ToolOutcomeStatus,
    UnifiedToolExecutor,
)
from .router import (
    RegisteredRouter,
    RouteDecision,
    RouteProfile,
    RouteRequest,
    RouteUnavailable,
)

__all__ = [
    "DecisionAuthorization",
    "HostContextFactory",
    "LegacyPreparedCallAdapter",
    "PreparedExecutionCall",
    "RegisteredRouter",
    "RouteDecision",
    "RouteProfile",
    "RouteRequest",
    "RouteUnavailable",
    "RunContext",
    "ToolOutcome",
    "ToolOutcomeStatus",
    "UnifiedToolExecutor",
]
