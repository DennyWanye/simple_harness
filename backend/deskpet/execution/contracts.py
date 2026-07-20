"""Product-neutral immutable contracts for DeskPet execution control.

The execution package deliberately contains no storage or workflow imports.
Every object crossing the execution boundary is immutable, versioned, and
validated before it can be used by a driver or persistence adapter.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Sequence, TypeAlias


JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]

CONTRACT_SCHEMA_VERSION = 1


class ExecutionError(RuntimeError):
    """Stable execution-layer failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ContractValidationError(ExecutionError, ValueError):
    pass


class IdempotencyConflict(ExecutionError):
    pass


class RunIdentityConflict(ExecutionError):
    pass


class VersionConflict(ExecutionError):
    pass


class TerminalConflict(ExecutionError):
    pass


class AuthorizationError(ExecutionError):
    pass


class RunNotFound(ExecutionError):
    pass


class EventNotFound(ExecutionError):
    pass


class DeliveryNotFound(ExecutionError):
    pass


class DeliveryClaimConflict(ExecutionError):
    pass


class ParentCycleError(ExecutionError):
    pass


class PersistenceRequired(ExecutionError):
    pass


class ActiveRunCapacityExceeded(ExecutionError):
    pass


def validate_json_value(value: object, *, path: str = "$") -> None:
    """Reject values that cannot have one stable canonical JSON spelling."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError(
                "invalid_json", f"non-finite number at {path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError(
                    "invalid_json", f"non-string object key at {path}"
                )
            validate_json_value(item, path=f"{path}.{key}")
        return
    raise ContractValidationError(
        "invalid_json", f"unsupported JSON value at {path}: {type(value).__name__}"
    )


def canonical_json(value: object) -> str:
    validate_json_value(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def fingerprint_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _freeze_json(value: Any) -> Any:
    validate_json_value(value)
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in copy.deepcopy(dict(value)).items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in copy.deepcopy(value))
    return copy.deepcopy(value)


def thaw_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return copy.deepcopy(value)


def _required_text(value: object, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ContractValidationError(
            "missing_field", f"{field_name} must be a non-empty string"
        )
    return text


def _optional_text(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _fingerprint(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ContractValidationError(
            "invalid_fingerprint", f"{field_name} must be a lowercase SHA-256 digest"
        )
    return text


def _strict_mapping(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("invalid_payload", f"{name} must be an object")
    allowed = required | (optional or set())
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - allowed)
    if missing or unknown:
        detail = []
        if missing:
            detail.append(f"missing={','.join(missing)}")
        if unknown:
            detail.append(f"unknown={','.join(unknown)}")
        raise ContractValidationError(
            "invalid_payload", f"{name} fields differ: {'; '.join(detail)}"
        )
    return dict(value)


def _json_object(value: object, *, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("invalid_payload", f"{name} must be an object")
    frozen = _freeze_json(value)
    assert isinstance(frozen, Mapping)
    return frozen


def _schema_version(value: object, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value != CONTRACT_SCHEMA_VERSION:
        raise ContractValidationError(
            "unsupported_schema", f"{name}.schema_version must be {CONTRACT_SCHEMA_VERSION}"
        )
    return value


class PersistenceLevel(StrEnum):
    EPHEMERAL = "ephemeral"
    DURABLE = "durable"


class RunStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


class OutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ACCEPTED = "accepted"
    WAITING = "waiting"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AttachmentPolicy(StrEnum):
    ATTACHED = "attached"
    DETACHED = "detached"
    ROOT_TERMINAL_CHILD = "root_terminal_child"


class LinkKind(StrEnum):
    STRUCTURAL = "structural"
    DOMAIN = "domain"


class ActorAction(StrEnum):
    OBSERVE = "observe"
    SIGNAL = "signal"
    CANCEL = "cancel"
    RECOVER = "recover"
    CLOSE = "close"
    LINK = "link"


class DecisionKind(StrEnum):
    PERMISSION = "permission"
    PLAN = "plan"
    CLARIFICATION = "clarification"
    PPT_OUTLINE = "ppt_outline"
    SKILL_CANDIDATE = "skill_candidate"
    WORKFLOW_HITL = "workflow_hitl"


class DecisionStatus(StrEnum):
    OPEN = "open"
    ALLOWED = "allowed"
    DENIED = "denied"
    EXPIRED = "expired"


class GrantStatus(StrEnum):
    ISSUED = "issued"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EffectStatus(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    LATE_RECONCILED = "late_reconciled"


class DeliveryPolicy(StrEnum):
    DURABLE_REQUIRED = "durable_required"
    RETRY_WHILE_BOUND = "retry_while_bound"
    BEST_EFFORT = "best_effort"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    DISCARDED = "discarded"


class ChildCommandStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SCHEDULED = "scheduled"
    ACKED = "acked"
    FAILED = "failed"
    CANCELLED = "cancelled"


def root_idempotency_key(session_id: str, request_id: str, turn_id: str) -> str:
    return ":".join(
        (
            "root",
            _required_text(session_id, "session_id"),
            _required_text(request_id, "request_id"),
            _required_text(turn_id, "turn_id"),
        )
    )


def delegate_idempotency_key(parent_run_id: str, command_id: str, profile_key: str) -> str:
    return ":".join(
        (
            "delegate",
            _required_text(parent_run_id, "parent_run_id"),
            _required_text(command_id, "command_id"),
            _required_text(profile_key, "profile_key"),
        )
    )


def team_idempotency_key(team_id: str, task_id: str, claim_epoch: int) -> str:
    if not isinstance(claim_epoch, int) or isinstance(claim_epoch, bool) or claim_epoch < 0:
        raise ContractValidationError(
            "invalid_claim_epoch", "claim_epoch must be a non-negative integer"
        )
    return ":".join(
        (
            "team",
            _required_text(team_id, "team_id"),
            _required_text(task_id, "task_id"),
            str(claim_epoch),
        )
    )


def workflow_idempotency_key(
    parent_run_id: str, node_execution_id: str, logical_slot: str
) -> str:
    return ":".join(
        (
            "workflow",
            _required_text(parent_run_id, "parent_run_id"),
            _required_text(node_execution_id, "node_execution_id"),
            _required_text(logical_slot, "logical_slot"),
        )
    )


@dataclass(frozen=True, slots=True)
class RunContext:
    session_id: str
    root_run_id: str
    parent_run_id: str | None
    request_id: str
    turn_id: str
    venue: str
    workspace: Mapping[str, JsonValue]
    capability_hash: str
    provider_plan: Mapping[str, JsonValue]
    trace_id: str
    principal_id: str
    auth_epoch: int = 0
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunContext")
        for field_name in (
            "session_id",
            "root_run_id",
            "request_id",
            "turn_id",
            "venue",
            "trace_id",
            "principal_id",
        ):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(
            self, "parent_run_id", _optional_text(self.parent_run_id, "parent_run_id")
        )
        object.__setattr__(
            self, "capability_hash", _fingerprint(self.capability_hash, "capability_hash")
        )
        if not isinstance(self.auth_epoch, int) or isinstance(self.auth_epoch, bool) or self.auth_epoch < 0:
            raise ContractValidationError(
                "invalid_auth_epoch", "auth_epoch must be a non-negative integer"
            )
        object.__setattr__(
            self, "workspace", _json_object(self.workspace, name="RunContext.workspace")
        )
        object.__setattr__(
            self,
            "provider_plan",
            _json_object(self.provider_plan, name="RunContext.provider_plan"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "root_run_id": self.root_run_id,
            "parent_run_id": self.parent_run_id,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "venue": self.venue,
            "workspace": thaw_json(self.workspace),
            "capability_hash": self.capability_hash,
            "provider_plan": thaw_json(self.provider_plan),
            "trace_id": self.trace_id,
            "principal_id": self.principal_id,
            "auth_epoch": self.auth_epoch,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunContext":
        data = _strict_mapping(
            value,
            required={
                "schema_version",
                "session_id",
                "root_run_id",
                "parent_run_id",
                "request_id",
                "turn_id",
                "venue",
                "workspace",
                "capability_hash",
                "provider_plan",
                "trace_id",
                "principal_id",
                "auth_epoch",
            },
            name="RunContext",
        )
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ActorContext:
    principal_id: str
    session_id: str
    auth_epoch: int
    root_run_id: str | None = None
    capability_hash: str | None = None
    expires_at: float | None = None
    internal: bool = False
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ActorContext")
        object.__setattr__(self, "principal_id", _required_text(self.principal_id, "principal_id"))
        object.__setattr__(self, "session_id", _required_text(self.session_id, "session_id"))
        if not isinstance(self.auth_epoch, int) or isinstance(self.auth_epoch, bool) or self.auth_epoch < 0:
            raise ContractValidationError(
                "invalid_auth_epoch", "auth_epoch must be a non-negative integer"
            )
        object.__setattr__(self, "root_run_id", _optional_text(self.root_run_id, "root_run_id"))
        if self.capability_hash is not None:
            object.__setattr__(
                self,
                "capability_hash",
                _fingerprint(self.capability_hash, "capability_hash"),
            )
        if self.internal and (
            self.root_run_id is None
            or self.capability_hash is None
            or self.expires_at is None
        ):
            raise ContractValidationError(
                "invalid_internal_authority",
                "internal actors require root_run_id, capability_hash, and expires_at",
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "principal_id": self.principal_id,
            "session_id": self.session_id,
            "auth_epoch": self.auth_epoch,
            "root_run_id": self.root_run_id,
            "capability_hash": self.capability_hash,
            "expires_at": self.expires_at,
            "internal": self.internal,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActorContext":
        data = _strict_mapping(
            value,
            required={
                "schema_version",
                "principal_id",
                "session_id",
                "auth_epoch",
                "root_run_id",
                "capability_hash",
                "expires_at",
                "internal",
            },
            name="ActorContext",
        )
        return cls(**data)


@dataclass(frozen=True, slots=True)
class RunRef:
    run_id: str
    expected_session_id: str
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunRef")
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "expected_session_id",
            _required_text(self.expected_session_id, "expected_session_id"),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunRef":
        return cls(
            **_strict_mapping(
                value,
                required={"schema_version", "run_id", "expected_session_id"},
                name="RunRef",
            )
        )


@dataclass(frozen=True, slots=True)
class RunCreate:
    run_id: str
    idempotency_key: str
    context: RunContext
    payload_fingerprint: str
    capability_fingerprint: str
    driver_kind: str
    profile_key: str
    persistence_level: PersistenceLevel | str
    status: RunStatus | str = RunStatus.CREATED
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunCreate")
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(
            self, "idempotency_key", _required_text(self.idempotency_key, "idempotency_key")
        )
        prefix = self.idempotency_key.split(":", 1)[0]
        if prefix not in {"root", "delegate", "team", "workflow"}:
            raise ContractValidationError(
                "invalid_idempotency_key",
                "idempotency_key must use a root/delegate/team/workflow namespace",
            )
        object.__setattr__(
            self,
            "payload_fingerprint",
            _fingerprint(self.payload_fingerprint, "payload_fingerprint"),
        )
        object.__setattr__(
            self,
            "capability_fingerprint",
            _fingerprint(self.capability_fingerprint, "capability_fingerprint"),
        )
        if self.context.capability_hash != self.capability_fingerprint:
            raise ContractValidationError(
                "capability_fingerprint_mismatch",
                "RunContext capability_hash differs from capability_fingerprint",
            )
        object.__setattr__(self, "driver_kind", _required_text(self.driver_kind, "driver_kind"))
        object.__setattr__(self, "profile_key", _required_text(self.profile_key, "profile_key"))
        object.__setattr__(self, "persistence_level", PersistenceLevel(self.persistence_level))
        object.__setattr__(self, "status", RunStatus(self.status))
        if self.status in TERMINAL_RUN_STATUSES:
            raise ContractValidationError(
                "invalid_initial_status", "a new run cannot start terminal"
            )
        if self.context.parent_run_id == self.run_id:
            raise ContractValidationError(
                "self_parent", "a run cannot be its own parent"
            )
        if self.context.parent_run_id is None and self.context.root_run_id != self.run_id:
            raise ContractValidationError(
                "invalid_root", "a root run must name itself as root_run_id"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "idempotency_key": self.idempotency_key,
            "context": self.context.to_dict(),
            "payload_fingerprint": self.payload_fingerprint,
            "capability_fingerprint": self.capability_fingerprint,
            "driver_kind": self.driver_kind,
            "profile_key": self.profile_key,
            "persistence_level": self.persistence_level.value,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunCreate":
        data = _strict_mapping(
            value,
            required={
                "schema_version",
                "run_id",
                "idempotency_key",
                "context",
                "payload_fingerprint",
                "capability_fingerprint",
                "driver_kind",
                "profile_key",
                "persistence_level",
                "status",
            },
            name="RunCreate",
        )
        context = data.pop("context")
        if not isinstance(context, Mapping):
            raise ContractValidationError(
                "invalid_payload", "RunCreate.context must be an object"
            )
        return cls(context=RunContext.from_dict(context), **data)


@dataclass(frozen=True, slots=True)
class RunRecord:
    spec: RunCreate
    status: RunStatus | str
    persistence_level: PersistenceLevel | str
    version: int
    durable_seq: int
    terminal_event_id: str | None
    created_at: float
    updated_at: float
    started_at: float | None = None
    ended_at: float | None = None
    cancel_reason: str | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunRecord")
        object.__setattr__(self, "status", RunStatus(self.status))
        object.__setattr__(self, "persistence_level", PersistenceLevel(self.persistence_level))
        if self.version < 0 or self.durable_seq < 0:
            raise ContractValidationError(
                "invalid_counter", "run version and durable_seq must be non-negative"
            )
        object.__setattr__(
            self, "terminal_event_id", _optional_text(self.terminal_event_id, "terminal_event_id")
        )
        object.__setattr__(
            self, "cancel_reason", _optional_text(self.cancel_reason, "cancel_reason")
        )
        if self.status in TERMINAL_RUN_STATUSES:
            if self.persistence_level is PersistenceLevel.DURABLE and self.terminal_event_id is None:
                raise ContractValidationError(
                    "missing_terminal_event", "durable terminal runs require terminal_event_id"
                )
            if self.ended_at is None:
                raise ContractValidationError(
                    "missing_ended_at", "terminal runs require ended_at"
                )
        elif self.terminal_event_id is not None or self.ended_at is not None:
            raise ContractValidationError(
                "invalid_terminal_fields", "non-terminal runs cannot carry terminal fields"
            )

    @property
    def run_id(self) -> str:
        return self.spec.run_id

    @property
    def context(self) -> RunContext:
        return self.spec.context


@dataclass(frozen=True, slots=True)
class CreateRunResult:
    record: RunRecord
    created: bool


@dataclass(frozen=True, slots=True)
class ChildCommandIntent:
    """Durable, product-neutral intent reserved before a child exists."""

    operation_id: str
    parent_run_id: str
    command_id: str
    child_spec: RunCreate
    child_request: Mapping[str, JsonValue]
    capability_subset: tuple[str, ...]
    attachment_policy: AttachmentPolicy | str
    capability_snapshot_ref: str
    intent_fingerprint: str = field(init=False)
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ChildCommandIntent")
        for field_name in ("operation_id", "parent_run_id", "command_id"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self, "attachment_policy", AttachmentPolicy(self.attachment_policy)
        )
        object.__setattr__(
            self,
            "child_request",
            _json_object(self.child_request, name="ChildCommandIntent.child_request"),
        )
        subset = tuple(
            sorted({_required_text(item, "capability_subset") for item in self.capability_subset})
        )
        object.__setattr__(self, "capability_subset", subset)
        object.__setattr__(
            self,
            "capability_snapshot_ref",
            _fingerprint(self.capability_snapshot_ref, "capability_snapshot_ref"),
        )
        if self.child_spec.context.parent_run_id != self.parent_run_id:
            raise ContractValidationError(
                "child_parent_mismatch", "child spec names another parent run"
            )
        if self.child_spec.persistence_level is not PersistenceLevel.DURABLE:
            raise ContractValidationError(
                "child_not_durable", "delegated child runs must be durable"
            )
        if self.child_spec.capability_fingerprint != self.capability_snapshot_ref:
            raise ContractValidationError(
                "child_capability_mismatch",
                "child capability differs from the committed snapshot",
            )
        if self.child_spec.payload_fingerprint != fingerprint_json(
            thaw_json(self.child_request)
        ):
            raise ContractValidationError(
                "child_payload_mismatch",
                "child request differs from the committed payload fingerprint",
            )
        fingerprint = fingerprint_json(
            {
                "operation_id": self.operation_id,
                "parent_run_id": self.parent_run_id,
                "command_id": self.command_id,
                "child_spec": self.child_spec.to_dict(),
                "child_request": thaw_json(self.child_request),
                "capability_subset": list(self.capability_subset),
                "attachment_policy": self.attachment_policy.value,
                "capability_snapshot_ref": self.capability_snapshot_ref,
            }
        )
        object.__setattr__(self, "intent_fingerprint", fingerprint)

    @property
    def child_run_id(self) -> str:
        return self.child_spec.run_id


@dataclass(frozen=True, slots=True)
class ChildCommandRecord:
    intent: ChildCommandIntent
    status: ChildCommandStatus | str
    schedule_lease_owner: str | None
    schedule_lease_epoch: int
    schedule_lease_expires_at: float | None
    attempts: int
    next_attempt_at: float | None
    last_error: str | None
    created_at: float
    updated_at: float
    ack_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ChildCommandStatus(self.status))
        object.__setattr__(
            self,
            "schedule_lease_owner",
            _optional_text(self.schedule_lease_owner, "schedule_lease_owner"),
        )
        object.__setattr__(
            self, "last_error", _optional_text(self.last_error, "last_error")
        )
        if self.schedule_lease_epoch < 0 or self.attempts < 0:
            raise ContractValidationError(
                "invalid_counter", "child command counters must be non-negative"
            )
        if self.status in {ChildCommandStatus.LEASED, ChildCommandStatus.SCHEDULED}:
            if self.schedule_lease_owner is None or self.schedule_lease_expires_at is None:
                raise ContractValidationError(
                    "missing_child_lease", "active child command requires a lease"
                )
        if self.status is ChildCommandStatus.ACKED and self.ack_at is None:
            raise ContractValidationError(
                "missing_child_ack", "acked child command requires ack_at"
            )

    @property
    def operation_id(self) -> str:
        return self.intent.operation_id

    @property
    def child_run_id(self) -> str:
        return self.intent.child_run_id


@dataclass(frozen=True, slots=True)
class ChildSignalRecord:
    signal_id: str
    operation_id: str
    parent_run_id: str
    command_id: str
    child_run_id: str
    kind: str
    payload: Mapping[str, JsonValue]
    attempts: int
    created_at: float
    updated_at: float
    delivered_at: float | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "signal_id",
            "operation_id",
            "parent_run_id",
            "command_id",
            "child_run_id",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.kind not in {"accepted", "terminal"}:
            raise ContractValidationError(
                "invalid_child_signal", "child signal kind must be accepted or terminal"
            )
        if self.attempts < 0:
            raise ContractValidationError(
                "invalid_counter", "child signal attempts must be non-negative"
            )
        object.__setattr__(
            self, "payload", _json_object(self.payload, name="ChildSignalRecord.payload")
        )


@dataclass(frozen=True, slots=True)
class RunLinkSpec:
    link_id: str
    root_run_id: str
    parent_run_id: str
    child_run_id: str
    attachment_policy: AttachmentPolicy | str
    link_kind: LinkKind | str = LinkKind.STRUCTURAL
    domain_kind: str | None = None
    domain_id: str | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunLinkSpec")
        for field_name in ("link_id", "root_run_id", "parent_run_id", "child_run_id"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        if self.parent_run_id == self.child_run_id:
            raise ContractValidationError("self_parent", "a run link cannot point to itself")
        object.__setattr__(self, "attachment_policy", AttachmentPolicy(self.attachment_policy))
        object.__setattr__(self, "link_kind", LinkKind(self.link_kind))
        object.__setattr__(self, "domain_kind", _optional_text(self.domain_kind, "domain_kind"))
        object.__setattr__(self, "domain_id", _optional_text(self.domain_id, "domain_id"))
        if self.link_kind is LinkKind.STRUCTURAL and (
            self.domain_kind is not None or self.domain_id is not None
        ):
            raise ContractValidationError(
                "invalid_structural_link", "structural links cannot carry domain fields"
            )
        if self.link_kind is LinkKind.DOMAIN and (
            self.domain_kind is None or self.domain_id is None
        ):
            raise ContractValidationError(
                "invalid_domain_link", "domain links require domain_kind and domain_id"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunLinkSpec":
        return cls(
            **_strict_mapping(
                value,
                required={
                    "schema_version",
                    "link_id",
                    "root_run_id",
                    "parent_run_id",
                    "child_run_id",
                    "attachment_policy",
                    "link_kind",
                    "domain_kind",
                    "domain_id",
                },
                name="RunLinkSpec",
            )
        )


@dataclass(frozen=True, slots=True)
class DeliverySpec:
    sink_kind: str
    sink_instance: str
    target_id: str
    policy: DeliveryPolicy | str
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="DeliverySpec")
        for field_name in ("sink_kind", "sink_instance", "target_id"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(self, "policy", DeliveryPolicy(self.policy))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeliverySpec":
        return cls(
            **_strict_mapping(
                value,
                required={
                    "schema_version",
                    "sink_kind",
                    "sink_instance",
                    "target_id",
                    "policy",
                },
                name="DeliverySpec",
            )
        )


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    """Persisted, independently fenced projection intent for one sink."""

    delivery_id: str
    event_id: str
    run_id: str
    sink_kind: str
    sink_instance: str
    target_id: str
    policy: DeliveryPolicy | str
    status: DeliveryStatus | str
    attempts: int
    delivery_version: int
    next_attempt_at: float | None
    last_error: str | None
    created_at: float
    updated_at: float
    delivered_at: float | None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="DeliveryRecord")
        for field_name in (
            "delivery_id",
            "event_id",
            "run_id",
            "sink_kind",
            "sink_instance",
            "target_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "policy", DeliveryPolicy(self.policy))
        object.__setattr__(self, "status", DeliveryStatus(self.status))
        for field_name in ("attempts", "delivery_version"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(
                    "invalid_delivery_counter",
                    f"{field_name} must be a non-negative integer",
                )
        object.__setattr__(
            self, "last_error", _optional_text(self.last_error, "last_error")
        )
        if self.status is DeliveryStatus.DELIVERED and self.delivered_at is None:
            raise ContractValidationError(
                "invalid_delivery_terminal",
                "delivered rows require delivered_at",
            )
        if self.status is not DeliveryStatus.DELIVERED and self.delivered_at is not None:
            raise ContractValidationError(
                "invalid_delivery_terminal",
                "only delivered rows may carry delivered_at",
            )


@dataclass(frozen=True, slots=True)
class RunEventCandidate:
    event_key: str
    kind: str
    status: OutcomeStatus | str
    driver_kind: str
    correlation: Mapping[str, JsonValue] = field(default_factory=dict)
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    error: Mapping[str, JsonValue] | None = None
    artifact_refs: Sequence[str] = field(default_factory=tuple)
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunEventCandidate")
        for field_name in ("event_key", "kind", "driver_kind"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(self, "status", OutcomeStatus(self.status))
        object.__setattr__(
            self,
            "correlation",
            _json_object(self.correlation, name="RunEventCandidate.correlation"),
        )
        object.__setattr__(
            self,
            "payload",
            _json_object(self.payload, name="RunEventCandidate.payload"),
        )
        object.__setattr__(
            self,
            "error",
            None
            if self.error is None
            else _json_object(self.error, name="RunEventCandidate.error"),
        )
        refs = tuple(_required_text(ref, "artifact_ref") for ref in self.artifact_refs)
        if len(set(refs)) != len(refs):
            raise ContractValidationError(
                "duplicate_artifact_ref", "artifact_refs must be unique"
            )
        object.__setattr__(self, "artifact_refs", refs)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunEventCandidate":
        return cls(
            **_strict_mapping(
                value,
                required={
                    "schema_version",
                    "event_key",
                    "kind",
                    "status",
                    "driver_kind",
                    "correlation",
                    "payload",
                    "error",
                    "artifact_refs",
                },
                name="RunEventCandidate",
            )
        )


@dataclass(frozen=True, slots=True)
class LiveCursor:
    stream_epoch: str
    live_seq: int
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="LiveCursor")
        object.__setattr__(
            self, "stream_epoch", _required_text(self.stream_epoch, "stream_epoch")
        )
        if (
            not isinstance(self.live_seq, int)
            or isinstance(self.live_seq, bool)
            or self.live_seq < 1
        ):
            raise ContractValidationError(
                "invalid_live_seq", "live_seq must be a positive integer"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LiveCursor":
        return cls(
            **_strict_mapping(
                value,
                required={"schema_version", "stream_epoch", "live_seq"},
                name="LiveCursor",
            )
        )


@dataclass(frozen=True, slots=True)
class RunEvent:
    event_id: str
    run_id: str
    root_run_id: str
    session_id: str
    durable_seq: int | None
    candidate: RunEventCandidate
    created_at: float
    live_cursor: LiveCursor | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunEvent")
        for field_name in ("event_id", "run_id", "root_run_id", "session_id"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        has_durable_order = self.durable_seq is not None
        has_live_order = self.live_cursor is not None
        if has_durable_order == has_live_order:
            raise ContractValidationError(
                "invalid_event_order",
                "RunEvent requires exactly one of durable_seq or live_cursor",
            )
        if has_durable_order and (
            not isinstance(self.durable_seq, int)
            or isinstance(self.durable_seq, bool)
            or self.durable_seq < 1
        ):
            raise ContractValidationError(
                "invalid_durable_seq", "durable_seq must be a positive integer"
            )

    @property
    def kind(self) -> str:
        return self.candidate.kind

    @property
    def status(self) -> OutcomeStatus:
        return self.candidate.status

    @property
    def driver_kind(self) -> str:
        return self.candidate.driver_kind

    @property
    def correlation(self) -> Mapping[str, JsonValue]:
        return self.candidate.correlation

    @property
    def error(self) -> Mapping[str, JsonValue] | None:
        return self.candidate.error

    @property
    def artifact_refs(self) -> Sequence[str]:
        return self.candidate.artifact_refs

    @property
    def durable(self) -> bool:
        return self.durable_seq is not None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunEvent":
        data = _strict_mapping(
            value,
            required={
                "schema_version",
                "event_id",
                "run_id",
                "root_run_id",
                "session_id",
                "durable_seq",
                "live_cursor",
                "candidate",
                "created_at",
            },
            name="RunEvent",
        )
        candidate = data.pop("candidate")
        cursor = data.pop("live_cursor")
        if not isinstance(candidate, Mapping):
            raise ContractValidationError(
                "invalid_payload", "RunEvent.candidate must be an object"
            )
        if cursor is not None and not isinstance(cursor, Mapping):
            raise ContractValidationError(
                "invalid_payload", "RunEvent.live_cursor must be an object or null"
            )
        return cls(
            candidate=RunEventCandidate.from_dict(candidate),
            live_cursor=None if cursor is None else LiveCursor.from_dict(cursor),
            **data,
        )


@dataclass(frozen=True, slots=True)
class FinalizeRunResult:
    record: RunRecord
    event: RunEvent
    idempotent: bool


@dataclass(frozen=True, slots=True)
class WorkflowSessionRef:
    session_kind: str
    session_id: str
    session_epoch: int = 0
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="WorkflowSessionRef")
        object.__setattr__(self, "session_kind", _required_text(self.session_kind, "session_kind"))
        object.__setattr__(self, "session_id", _required_text(self.session_id, "session_id"))
        if self.session_epoch < 0:
            raise ContractValidationError(
                "invalid_session_epoch", "session_epoch must be non-negative"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowSessionRef":
        return cls(
            **_strict_mapping(
                value,
                required={
                    "schema_version",
                    "session_kind",
                    "session_id",
                    "session_epoch",
                },
                name="WorkflowSessionRef",
            )
        )


@dataclass(frozen=True, slots=True)
class WorkflowRunSeed:
    request_key: str
    workflow_name: str
    workflow_version: str
    manifest_hash: str
    implementation_hash: str
    capability_hash: str
    capability_snapshot: Mapping[str, JsonValue]
    state_schema_version: int
    trace_id: str
    thread_id: str
    checkpoint_ns: str = ""
    source_checkpoint_id: str | None = None
    session_refs: Sequence[WorkflowSessionRef] = field(default_factory=tuple)
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="WorkflowRunSeed")
        for field_name in (
            "request_key",
            "workflow_name",
            "workflow_version",
            "manifest_hash",
            "implementation_hash",
            "trace_id",
            "thread_id",
        ):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(self, "capability_hash", _fingerprint(self.capability_hash, "capability_hash"))
        if not isinstance(self.state_schema_version, int) or self.state_schema_version < 1:
            raise ContractValidationError(
                "invalid_state_schema", "state_schema_version must be positive"
            )
        object.__setattr__(
            self,
            "capability_snapshot",
            _json_object(
                self.capability_snapshot, name="WorkflowRunSeed.capability_snapshot"
            ),
        )
        object.__setattr__(self, "checkpoint_ns", str(self.checkpoint_ns or ""))
        object.__setattr__(
            self,
            "source_checkpoint_id",
            _optional_text(self.source_checkpoint_id, "source_checkpoint_id"),
        )
        refs = tuple(self.session_refs)
        if len({ref.session_kind for ref in refs}) != len(refs):
            raise ContractValidationError(
                "duplicate_session_ref", "workflow session kinds must be unique"
            )
        object.__setattr__(self, "session_refs", refs)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkflowRunSeed":
        data = _strict_mapping(
            value,
            required={
                "schema_version",
                "request_key",
                "workflow_name",
                "workflow_version",
                "manifest_hash",
                "implementation_hash",
                "capability_hash",
                "capability_snapshot",
                "state_schema_version",
                "trace_id",
                "thread_id",
                "checkpoint_ns",
                "source_checkpoint_id",
                "session_refs",
            },
            name="WorkflowRunSeed",
        )
        refs = data.pop("session_refs")
        if not isinstance(refs, (list, tuple)):
            raise ContractValidationError(
                "invalid_payload", "WorkflowRunSeed.session_refs must be an array"
            )
        parsed: list[WorkflowSessionRef] = []
        for ref in refs:
            if not isinstance(ref, Mapping):
                raise ContractValidationError(
                    "invalid_payload", "WorkflowRunSeed.session_refs entries must be objects"
                )
            parsed.append(WorkflowSessionRef.from_dict(ref))
        return cls(session_refs=tuple(parsed), **data)


@dataclass(frozen=True, slots=True)
class DecisionAuthorization:
    grant_id: str
    decision_id: str
    run_id: str
    call_id: str
    effect_id: str
    tool_name: str
    args_hash: str
    capability_hash: str
    scope_hash: str
    expires_at: float
    version: int = 0
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="DecisionAuthorization")
        for field_name in (
            "grant_id",
            "decision_id",
            "run_id",
            "call_id",
            "effect_id",
            "tool_name",
        ):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        for field_name in ("args_hash", "capability_hash", "scope_hash"):
            object.__setattr__(self, field_name, _fingerprint(getattr(self, field_name), field_name))
        if self.version < 0:
            raise ContractValidationError("invalid_version", "grant version must be non-negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DecisionAuthorization":
        return cls(
            **_strict_mapping(
                value,
                required={
                    "schema_version",
                    "grant_id",
                    "decision_id",
                    "run_id",
                    "call_id",
                    "effect_id",
                    "tool_name",
                    "args_hash",
                    "capability_hash",
                    "scope_hash",
                    "expires_at",
                    "version",
                },
                name="DecisionAuthorization",
            )
        )


@dataclass(frozen=True, slots=True)
class LegacyRunProjection:
    run_id: str
    session_id: str
    root_run_id: str
    parent_run_id: str | None
    status: RunStatus | str
    driver_kind: str
    profile_key: str
    created_at: float
    updated_at: float
    ended_at: float | None
    read_only: bool = True
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="LegacyRunProjection")
        for field_name in ("run_id", "session_id", "root_run_id", "driver_kind", "profile_key"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(
            self, "parent_run_id", _optional_text(self.parent_run_id, "parent_run_id")
        )
        object.__setattr__(self, "status", RunStatus(self.status))
        if self.read_only is not True:
            raise ContractValidationError(
                "mutable_legacy_projection", "legacy projections are always read-only"
            )


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Provider-neutral result; accepted/unknown are never aliases for success."""

    call_id: str
    effect_id: str | None
    status: OutcomeStatus | str
    value: JsonValue = None
    error: Mapping[str, JsonValue] | None = None
    receipt_ref: str | None = None
    artifact_refs: Sequence[str] = field(default_factory=tuple)
    retryable: bool = False
    reconciliation: Mapping[str, JsonValue] | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ToolOutcome")
        object.__setattr__(self, "call_id", _required_text(self.call_id, "call_id"))
        object.__setattr__(
            self, "effect_id", _optional_text(self.effect_id, "effect_id")
        )
        object.__setattr__(self, "status", OutcomeStatus(self.status))
        validate_json_value(self.value, path="ToolOutcome.value")
        object.__setattr__(self, "value", _freeze_json(self.value))
        object.__setattr__(
            self,
            "error",
            None
            if self.error is None
            else _json_object(self.error, name="ToolOutcome.error"),
        )
        object.__setattr__(
            self, "receipt_ref", _optional_text(self.receipt_ref, "receipt_ref")
        )
        refs = tuple(_required_text(ref, "artifact_ref") for ref in self.artifact_refs)
        if len(set(refs)) != len(refs):
            raise ContractValidationError(
                "duplicate_artifact_ref", "artifact_refs must be unique"
            )
        object.__setattr__(self, "artifact_refs", refs)
        if not isinstance(self.retryable, bool):
            raise ContractValidationError(
                "invalid_retryable", "retryable must be a boolean"
            )
        object.__setattr__(
            self,
            "reconciliation",
            None
            if self.reconciliation is None
            else _json_object(
                self.reconciliation, name="ToolOutcome.reconciliation"
            ),
        )

    @property
    def succeeded(self) -> bool:
        return self.status is OutcomeStatus.SUCCEEDED

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ToolOutcome":
        return cls(
            **_strict_mapping(
                value,
                required={
                    "schema_version",
                    "call_id",
                    "effect_id",
                    "status",
                    "value",
                    "error",
                    "receipt_ref",
                    "artifact_refs",
                    "retryable",
                    "reconciliation",
                },
                name="ToolOutcome",
            )
        )


def assert_idempotent_run_intent(
    existing: RunCreate,
    requested: RunCreate,
    *,
    allow_persistence_upgrade: bool = False,
) -> None:
    """Validate a replay without treating a caller-proposed run id as authority."""

    if existing.idempotency_key != requested.idempotency_key:
        raise RunIdentityConflict(
            "run_identity_conflict", "run intents use different idempotency keys"
        )
    for field_name in ("payload_fingerprint", "capability_fingerprint"):
        if getattr(existing, field_name) != getattr(requested, field_name):
            raise IdempotencyConflict(
                "idempotency_conflict",
                f"idempotency key already names different {field_name}",
            )
    existing_context = existing.context.to_dict()
    requested_context = requested.context.to_dict()
    # A retry may propose a fresh root id/trace before discovering the first
    # winner.  Those generated values are not idempotency authority.
    existing_context.pop("trace_id", None)
    requested_context.pop("trace_id", None)
    if (
        existing.context.parent_run_id is None
        and requested.context.parent_run_id is None
        and existing.context.root_run_id == existing.run_id
        and requested.context.root_run_id == requested.run_id
    ):
        existing_context.pop("root_run_id", None)
        requested_context.pop("root_run_id", None)
    if existing_context != requested_context:
        raise RunIdentityConflict(
            "run_identity_conflict", "idempotent RunContext differs"
        )
    if (existing.driver_kind, existing.profile_key) != (
        requested.driver_kind,
        requested.profile_key,
    ):
        raise RunIdentityConflict(
            "run_identity_conflict", "idempotent driver/profile differs"
        )
    if existing.persistence_level != requested.persistence_level and not (
        allow_persistence_upgrade
        and existing.persistence_level is PersistenceLevel.EPHEMERAL
        and requested.persistence_level is PersistenceLevel.DURABLE
    ):
        raise RunIdentityConflict(
            "run_identity_conflict", "idempotent persistence level differs"
        )


def stable_event_id(run_id: str, event_key: str) -> str:
    run = _required_text(run_id, "run_id")
    key = _required_text(event_key, "event_key")
    return hashlib.sha256(f"execution-event|{run}|{key}".encode("utf-8")).hexdigest()


def stable_delivery_id(event_id: str, delivery: DeliverySpec) -> str:
    event = _required_text(event_id, "event_id")
    return hashlib.sha256(
        "|".join(
            (
                "execution-delivery",
                event,
                delivery.sink_kind,
                delivery.sink_instance,
                delivery.target_id,
            )
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ActiveRunCapacityExceeded",
    "ActorAction",
    "ActorContext",
    "AttachmentPolicy",
    "AuthorizationError",
    "ChildCommandIntent",
    "ChildCommandRecord",
    "ChildCommandStatus",
    "ChildSignalRecord",
    "CONTRACT_SCHEMA_VERSION",
    "ContractValidationError",
    "CreateRunResult",
    "DecisionAuthorization",
    "DecisionKind",
    "DecisionStatus",
    "DeliveryClaimConflict",
    "DeliveryNotFound",
    "DeliveryPolicy",
    "DeliveryRecord",
    "DeliverySpec",
    "DeliveryStatus",
    "EffectStatus",
    "EventNotFound",
    "ExecutionError",
    "FinalizeRunResult",
    "GrantStatus",
    "IdempotencyConflict",
    "JsonPrimitive",
    "JsonValue",
    "LegacyRunProjection",
    "LiveCursor",
    "LinkKind",
    "OutcomeStatus",
    "ParentCycleError",
    "PersistenceLevel",
    "PersistenceRequired",
    "RunContext",
    "RunCreate",
    "RunEvent",
    "RunEventCandidate",
    "RunIdentityConflict",
    "RunLinkSpec",
    "RunNotFound",
    "RunRecord",
    "RunRef",
    "RunStatus",
    "TERMINAL_RUN_STATUSES",
    "TerminalConflict",
    "ToolOutcome",
    "VersionConflict",
    "WorkflowRunSeed",
    "WorkflowSessionRef",
    "canonical_json",
    "assert_idempotent_run_intent",
    "delegate_idempotency_key",
    "fingerprint_json",
    "root_idempotency_key",
    "stable_delivery_id",
    "stable_event_id",
    "team_idempotency_key",
    "thaw_json",
    "validate_json_value",
    "workflow_idempotency_key",
]
