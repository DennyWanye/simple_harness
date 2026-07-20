"""Product-neutral Driver contracts used by the test-only harness wiring."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, AsyncIterator, Mapping, Protocol

from deskpet.execution import AttachmentPolicy
from deskpet.harness.tool_executor import PreparedExecutionCall, ToolOutcome
from deskpet.tools.capabilities import ToolExecutionContext


class JoinPolicy(str, Enum):
    JOIN_BEFORE_FINAL = "join_before_final"
    ROOT_TERMINAL_CHILD = "root_terminal_child"
    DETACHED = "detached"


@dataclass(frozen=True)
class DriverStart:
    run_id: str
    session_id: str
    canonical_messages: tuple[Mapping[str, Any], ...]
    session_projection_cursor: int = 0
    prepared_context_ref: str | None = None
    tool_set_snapshot_ref: str | None = None
    provider_state: Mapping[str, Any] = field(default_factory=dict)
    iteration: int = 0
    completion_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or not self.session_id:
            raise ValueError("run_id and session_id are required")
        if self.session_projection_cursor < 0 or self.iteration < 0:
            raise ValueError("cursor and iteration must be non-negative")
        object.__setattr__(
            self,
            "canonical_messages",
            tuple(MappingProxyType(copy.deepcopy(dict(item))) for item in self.canonical_messages),
        )
        object.__setattr__(
            self, "provider_state", MappingProxyType(copy.deepcopy(dict(self.provider_state)))
        )
        object.__setattr__(
            self,
            "completion_state",
            MappingProxyType(copy.deepcopy(dict(self.completion_state))),
        )


@dataclass(frozen=True)
class TokenCandidate:
    run_id: str
    content: str
    kind: str = "content"


@dataclass(frozen=True)
class ProviderFallbackCandidate:
    run_id: str
    from_provider: str
    to_provider: str
    reason: str


@dataclass(frozen=True)
class ExecuteTools:
    run_id: str
    command_id: str
    calls: tuple[PreparedExecutionCall, ...]
    contexts: tuple[ToolExecutionContext, ...]
    original_indexes: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.calls) != len(self.contexts) or len(self.calls) != len(self.original_indexes):
            raise ValueError("calls, contexts and original_indexes must align")


@dataclass(frozen=True)
class OpenDecision:
    run_id: str
    command_id: str
    decision_id: str
    nonce: str
    kind: str
    prompt: Mapping[str, Any]
    prompt_schema_version: int = 1
    expires_at: float | None = None
    call_id: str | None = None
    effect_id: str | None = None
    tool_name: str | None = None
    args_hash: str | None = None
    capability_hash: str | None = None
    scope_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt", MappingProxyType(copy.deepcopy(dict(self.prompt))))


@dataclass(frozen=True)
class DelegateRun:
    run_id: str
    command_id: str
    child_request: Mapping[str, Any]
    route_hint: str
    capability_subset: tuple[str, ...]
    attachment_policy: AttachmentPolicy | str
    join_policy: JoinPolicy | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "attachment_policy", AttachmentPolicy(self.attachment_policy))
        object.__setattr__(self, "join_policy", JoinPolicy(self.join_policy))
        object.__setattr__(
            self, "child_request", MappingProxyType(copy.deepcopy(dict(self.child_request)))
        )
        if not self.command_id or not self.route_hint:
            raise ValueError("delegate command_id and route_hint are required")


@dataclass(frozen=True)
class DriverTerminalCandidate:
    run_id: str
    status: str
    content: str = ""
    error: str | None = None
    correlation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("driver terminal candidate must be terminal")
        object.__setattr__(
            self, "correlation", MappingProxyType(copy.deepcopy(dict(self.correlation)))
        )


@dataclass(frozen=True)
class ChildAcceptedCandidate:
    run_id: str
    command_id: str
    child_run_id: str
    join_policy: JoinPolicy | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "join_policy", JoinPolicy(self.join_policy))


@dataclass(frozen=True)
class CancelAcknowledgedCandidate:
    run_id: str
    reason: str


DriverCandidate = (
    TokenCandidate
    | ProviderFallbackCandidate
    | ExecuteTools
    | OpenDecision
    | DelegateRun
    | DriverTerminalCandidate
    | ChildAcceptedCandidate
    | CancelAcknowledgedCandidate
)
DriverCommand = ExecuteTools | OpenDecision | DelegateRun | DriverTerminalCandidate


@dataclass(frozen=True)
class ToolOutcomesSignal:
    run_id: str
    command_id: str
    outcomes: tuple[ToolOutcome, ...]


@dataclass(frozen=True)
class DecisionSignal:
    run_id: str
    decision_id: str
    response: Mapping[str, Any]
    nonce: str | None = None
    version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "response", MappingProxyType(copy.deepcopy(dict(self.response))))
        if self.version is not None and self.version < 0:
            raise ValueError("decision signal version must be non-negative")


@dataclass(frozen=True)
class ChildAcceptedSignal:
    run_id: str
    command_id: str
    child_run_id: str


@dataclass(frozen=True)
class ChildTerminalSignal:
    run_id: str
    command_id: str
    child_run_id: str
    status: str
    value: Any = None


DriverSignal = ToolOutcomesSignal | DecisionSignal | ChildAcceptedSignal | ChildTerminalSignal


class Driver(Protocol):
    def start(self, request: DriverStart) -> AsyncIterator[DriverCandidate]: ...
    def signal(self, signal: DriverSignal) -> AsyncIterator[DriverCandidate]: ...
    def cancel(self, run_id: str, reason: str) -> AsyncIterator[DriverCandidate]: ...
    def recover(self, run_id: str) -> AsyncIterator[DriverCandidate]: ...
    async def close(self) -> None: ...


__all__ = [
    "AttachmentPolicy",
    "CancelAcknowledgedCandidate",
    "ChildAcceptedCandidate",
    "ChildAcceptedSignal",
    "ChildTerminalSignal",
    "DecisionSignal",
    "DelegateRun",
    "Driver",
    "DriverCandidate",
    "DriverCommand",
    "DriverSignal",
    "DriverStart",
    "DriverTerminalCandidate",
    "ExecuteTools",
    "JoinPolicy",
    "OpenDecision",
    "ProviderFallbackCandidate",
    "TokenCandidate",
    "ToolOutcomesSignal",
]
