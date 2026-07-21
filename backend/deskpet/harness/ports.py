"""Single tagged Driver event and signal contracts."""
from __future__ import annotations
import copy
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, AsyncIterator, Mapping, Protocol

if TYPE_CHECKING:
    from deskpet.execution.contracts import ProviderLaunchSnapshot
from deskpet.execution.contracts import AttachmentPolicy
from deskpet.execution.contracts import OutcomeStatus, RecoveryLease, RunContext, RunCreate, RunEvent, RunEventCandidate
from deskpet.execution.evidence import EvidenceSelection, UNKNOWN_EVIDENCE
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import NormalizedToolOutcome, PreparedToolCall, ToolOutcomeState

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
    run_context: RunContext | None = None
    run_spec: RunCreate | None = None
    association_event: RunEventCandidate | None = None
    profile_key: str = ""
    request_payload: Mapping[str, Any] = field(default_factory=dict)
    capability_snapshot: Mapping[str, Any] = field(default_factory=dict)
    scoped_evidence: EvidenceSelection | None = UNKNOWN_EVIDENCE
    launch_operation_id: str | None = None
    provider_launch_snapshot: ProviderLaunchSnapshot | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not self.session_id:
            raise ValueError("run_id and session_id are required")
        if self.session_projection_cursor < 0 or self.iteration < 0:
            raise ValueError("cursor and iteration must be non-negative")
        if (self.launch_operation_id is None) != (self.provider_launch_snapshot is None):
            raise ValueError("launch operation id and provider snapshot must be paired")
        object.__setattr__(self, "canonical_messages", tuple(MappingProxyType(copy.deepcopy(dict(item))) for item in self.canonical_messages))
        for name in ("provider_state", "completion_state", "request_payload", "capability_snapshot"):
            object.__setattr__(self, name, MappingProxyType(copy.deepcopy(dict(getattr(self, name)))))

@dataclass(frozen=True)
class ToolGrantRef:
    grant_id: str
    decision_id: str
    decision_nonce: str
    version: int = 0

_EVENT_KINDS = frozenset({"token", "provider_fallback", "execute_tools", "open_decision", "delegate_run", "terminal", "child_accepted", "cancel_acknowledged", "persisted_event"})
_SIGNAL_KINDS = frozenset({"tool_outcomes", "decision", "child_accepted", "child_terminal"})

@dataclass(frozen=True)
class DriverEvent:
    run_id: str
    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or self.kind not in _EVENT_KINDS:
            raise ValueError("driver event requires a run id and known kind")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)

def TokenCandidate(run_id: str, content: str, kind: str = "content") -> DriverEvent:
    return DriverEvent(run_id, "token", {"content": content, "token_kind": kind})

def ProviderFallbackCandidate(run_id: str, from_provider: str, to_provider: str, reason: str) -> DriverEvent:
    return DriverEvent(run_id, "provider_fallback", {"from_provider": from_provider, "to_provider": to_provider, "reason": reason})

def ExecuteTools(run_id: str, command_id: str, calls: tuple[PreparedToolCall, ...], contexts: tuple[ToolExecutionContext, ...], original_indexes: tuple[int, ...], grant_refs: tuple[ToolGrantRef | None, ...] = (), effectful: tuple[bool, ...] = ()) -> DriverEvent:
    if len(calls) != len(contexts) or len(calls) != len(original_indexes):
        raise ValueError("calls, contexts and original_indexes must align")
    if grant_refs and len(grant_refs) != len(calls):
        raise ValueError("grant refs and calls must align")
    if effectful and len(effectful) != len(calls):
        raise ValueError("effect flags and calls must align")
    return DriverEvent(run_id, "execute_tools", {"command_id": command_id, "calls": tuple(calls), "contexts": tuple(contexts), "original_indexes": tuple(original_indexes), "grant_refs": tuple(grant_refs), "effectful": tuple(effectful or (False,) * len(calls))})

def OpenDecision(run_id: str, command_id: str, decision_id: str, nonce: str, kind: str, prompt: Mapping[str, Any], prompt_schema_version: int = 1, expires_at: float | None = None, call_id: str | None = None, effect_id: str | None = None, tool_name: str | None = None, args_hash: str | None = None, capability_hash: str | None = None, scope_hash: str | None = None) -> DriverEvent:
    return DriverEvent(run_id, "open_decision", {"command_id": command_id, "decision_id": decision_id, "nonce": nonce, "decision_kind": kind, "prompt": MappingProxyType(copy.deepcopy(dict(prompt))), "prompt_schema_version": prompt_schema_version, "expires_at": expires_at, "call_id": call_id, "effect_id": effect_id, "tool_name": tool_name, "args_hash": args_hash, "capability_hash": capability_hash, "scope_hash": scope_hash})

def DelegateRun(run_id: str, command_id: str, child_request: Mapping[str, Any], route_hint: str, capability_subset: tuple[str, ...], attachment_policy: AttachmentPolicy | str, join_policy: JoinPolicy | str) -> DriverEvent:
    if not command_id or not route_hint:
        raise ValueError("delegate command_id and route_hint are required")
    return DriverEvent(run_id, "delegate_run", {"command_id": command_id, "child_request": MappingProxyType(copy.deepcopy(dict(child_request))), "route_hint": route_hint, "capability_subset": tuple(capability_subset), "attachment_policy": AttachmentPolicy(attachment_policy), "join_policy": JoinPolicy(join_policy)})

def DriverTerminalCandidate(run_id: str, status: str, content: str = "", error: str | None = None, correlation: Mapping[str, Any] = MappingProxyType({})) -> DriverEvent:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("driver terminal event must be terminal")
    return DriverEvent(run_id, "terminal", {"status": status, "content": content, "error": error, "correlation": MappingProxyType(copy.deepcopy(dict(correlation)))})

def ChildAcceptedCandidate(run_id: str, command_id: str, child_run_id: str, join_policy: JoinPolicy | str) -> DriverEvent:
    return DriverEvent(run_id, "child_accepted", {"command_id": command_id, "child_run_id": child_run_id, "join_policy": JoinPolicy(join_policy)})

def CancelAcknowledgedCandidate(run_id: str, reason: str) -> DriverEvent:
    return DriverEvent(run_id, "cancel_acknowledged", {"reason": reason})

def PersistedEventCandidate(event: RunEvent) -> DriverEvent:
    return DriverEvent(event.run_id, "persisted_event", {"event": event})

DriverCommand = DriverEvent

@dataclass(frozen=True)
class DriverSignal:
    run_id: str
    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or self.kind not in _SIGNAL_KINDS:
            raise ValueError("driver signal requires a run id and known kind")
        version = self.data.get("version")
        if version is not None and int(version) < 0:
            raise ValueError("decision signal version must be non-negative")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)

def ToolOutcomesSignal(run_id: str, command_id: str, outcomes: tuple[NormalizedToolOutcome, ...], statuses: tuple[OutcomeStatus, ...], original_indexes: tuple[int, ...], metadata: tuple[Mapping[str, Any], ...] = ()) -> DriverSignal:
    if not (len(outcomes) == len(statuses) == len(original_indexes)):
        raise ValueError("tool outcomes, statuses and indexes must align")
    if metadata and len(metadata) != len(outcomes):
        raise ValueError("tool outcome metadata must align")
    if len(set(original_indexes)) != len(original_indexes) or any(index < 0 for index in original_indexes):
        raise ValueError("tool outcome indexes must be unique and non-negative")
    canonical_statuses = tuple(OutcomeStatus(item) for item in statuses)
    allowed = {
        ToolOutcomeState.SUCCESS: {OutcomeStatus.SUCCEEDED, OutcomeStatus.ACCEPTED},
        ToolOutcomeState.FAILURE: {OutcomeStatus.FAILED, OutcomeStatus.CANCELLED},
        ToolOutcomeState.MALFORMED: {OutcomeStatus.FAILED, OutcomeStatus.UNKNOWN},
    }
    if any(status not in allowed[outcome.state] for outcome, status in zip(outcomes, canonical_statuses)):
        raise ValueError("tool outcome state and status are incompatible")
    return DriverSignal(run_id, "tool_outcomes", {"command_id": command_id, "outcomes": tuple(outcomes), "statuses": canonical_statuses, "original_indexes": tuple(original_indexes), "metadata": tuple(MappingProxyType(copy.deepcopy(dict(item))) for item in metadata)})

def DecisionSignal(run_id: str, decision_id: str, response: Mapping[str, Any], nonce: str | None = None, version: int | None = None) -> DriverSignal:
    return DriverSignal(run_id, "decision", {"decision_id": decision_id, "response": MappingProxyType(copy.deepcopy(dict(response))), "nonce": nonce, "version": version})

def ChildAcceptedSignal(run_id: str, command_id: str, child_run_id: str, signal_id: str | None = None) -> DriverSignal:
    return DriverSignal(run_id, "child_accepted", {"command_id": command_id, "child_run_id": child_run_id, "signal_id": signal_id})

def ChildTerminalSignal(run_id: str, command_id: str, child_run_id: str, status: str, value: Any = None, signal_id: str | None = None) -> DriverSignal:
    return DriverSignal(run_id, "child_terminal", {"command_id": command_id, "child_run_id": child_run_id, "status": status, "value": value, "signal_id": signal_id})

class Driver(Protocol):
    def start(self, request: DriverStart) -> AsyncIterator[DriverEvent]: ...
    def signal(self, signal: DriverSignal, recovery_lease: RecoveryLease | None = None) -> AsyncIterator[DriverEvent]: ...
    def cancel(self, run_id: str, reason: str) -> AsyncIterator[DriverEvent]: ...
    def recover(self, run_id: str, recovery_lease: RecoveryLease) -> AsyncIterator[DriverEvent]: ...
    async def close(self) -> None: ...

__all__ = ["AttachmentPolicy", "CancelAcknowledgedCandidate", "ChildAcceptedCandidate", "ChildAcceptedSignal", "ChildTerminalSignal", "DecisionSignal", "DelegateRun", "Driver", "DriverCommand", "DriverEvent", "DriverSignal", "DriverStart", "DriverTerminalCandidate", "ExecuteTools", "JoinPolicy", "OpenDecision", "PersistedEventCandidate", "ProviderFallbackCandidate", "TokenCandidate", "ToolGrantRef", "ToolOutcomesSignal"]
