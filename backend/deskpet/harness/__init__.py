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
from .kernel import (
    CancelReceipt,
    DriverTerminalCandidate,
    HostContext,
    RunHandle,
    RunKernel,
    RunRequest,
    RunSignal,
    SignalReceipt,
)

__all__ = [
    "DecisionAuthorization",
    "CancelReceipt",
    "DriverTerminalCandidate",
    "HostContext",
    "HostContextFactory",
    "LegacyPreparedCallAdapter",
    "PreparedExecutionCall",
    "RegisteredRouter",
    "RouteDecision",
    "RouteProfile",
    "RouteRequest",
    "RouteUnavailable",
    "RunHandle",
    "RunKernel",
    "RunRequest",
    "RunSignal",
    "RunContext",
    "SignalReceipt",
    "ToolOutcome",
    "ToolOutcomeStatus",
    "UnifiedToolExecutor",
]
