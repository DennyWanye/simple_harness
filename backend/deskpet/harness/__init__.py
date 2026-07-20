"""Product-neutral execution harness contracts and adapters."""

from .context import HostContextFactory, RunContext
from .child_runs import ChildRunCoordinator
from .decisions import DecisionStore, DecisionWakeupCache
from .kernel import (
    CancelReceipt,
    HostContext,
    RegisteredDriver,
    RunHandle,
    RunKernel,
    RunRequest,
    SignalReceipt,
)
from .ports import DriverTerminalCandidate
from .projector import (
    DeliveryWorker,
    EventMergeCursor,
    ExecutionProjector,
    ProjectionContractError,
    ProjectionSink,
    SessionDBProjectionSink,
    SinkRegistration,
    TTSProjectionSink,
    WebSocketProjectionSink,
    run_event_envelope,
    standard_delivery_specs,
    tool_outcome_payload,
)
from .router import (
    RegisteredRouter,
    RouteDecision,
    RouteProfile,
    RouteRequest,
    RouteUnavailable,
)
from .tool_executor import (
    DecisionAuthorization,
    LegacyPreparedCallAdapter,
    PreparedExecutionCall,
    ToolOutcome,
    ToolOutcomeStatus,
    UnifiedToolExecutor,
)

__all__ = [
    "CancelReceipt",
    "ChildRunCoordinator",
    "DecisionAuthorization",
    "DecisionStore",
    "DecisionWakeupCache",
    "DeliveryWorker",
    "DriverTerminalCandidate",
    "EventMergeCursor",
    "ExecutionProjector",
    "HostContext",
    "HostContextFactory",
    "LegacyPreparedCallAdapter",
    "PreparedExecutionCall",
    "ProjectionContractError",
    "ProjectionSink",
    "RegisteredDriver",
    "RegisteredRouter",
    "RouteDecision",
    "RouteProfile",
    "RouteRequest",
    "RouteUnavailable",
    "RunContext",
    "RunHandle",
    "RunKernel",
    "RunRequest",
    "SessionDBProjectionSink",
    "SignalReceipt",
    "SinkRegistration",
    "TTSProjectionSink",
    "ToolOutcome",
    "ToolOutcomeStatus",
    "UnifiedToolExecutor",
    "WebSocketProjectionSink",
    "run_event_envelope",
    "standard_delivery_specs",
    "tool_outcome_payload",
]
