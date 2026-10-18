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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:  # 挪出的模块反向导入本模块，运行期不能导回
    from .delivery_contracts import DeliverySpec, RunEvent

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


class DecisionNotFound(ExecutionError):
    pass


class DecisionConflict(ExecutionError):
    pass


class GrantNotFound(ExecutionError):
    pass


class GrantConsumeConflict(ExecutionError):
    pass


class ParentCycleError(ExecutionError):
    pass


class PersistenceRequired(ExecutionError):
    pass


class ActiveRunCapacityExceeded(ExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class RecoveryLease:
    """Opaque per-run recovery ownership token shared across execution ports."""

    run_id: str
    owner: str
    epoch: int
    expires_at: float


class StaleRecoveryLease(RuntimeError):
    """Raised when a recovered writer no longer owns the current lease epoch."""


def validate_json_value(value: object, *, path: str = "$") -> None:
    """Reject values that cannot have one stable canonical JSON spelling."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError("invalid_json", f"non-finite number at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError("invalid_json", f"non-string object key at {path}")
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
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_json(item) for item in value)
    return copy.deepcopy(value)


def thaw_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [thaw_json(item) for item in value]
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return copy.deepcopy(value)


def _required_text(value: object, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ContractValidationError("missing_field", f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _fingerprint(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    if len(text) != 64 or any((ch not in "0123456789abcdef" for ch in text)):
        raise ContractValidationError(
            "invalid_fingerprint", f"{field_name} must be a lowercase SHA-256 digest"
        )
    return text


def _strict_mapping(
    value: Mapping[str, Any], *, required: set[str], optional: set[str] | None = None, name: str
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


def _contract_dict(value: object) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for item in fields(value):
        if item.metadata.get("serialize") is False:
            continue
        current = getattr(value, item.name)
        if isinstance(current, StrEnum):
            current = current.value
        elif hasattr(current, "to_dict"):
            current = current.to_dict()
        result[item.name] = thaw_json(current)
    return result


def _contract_payload(value: Mapping[str, Any], contract: type, name: str) -> dict[str, Any]:
    return _strict_mapping(value, required={item.name for item in fields(contract)}, name=name)


class _Contract:
    def to_dict(self) -> dict[str, JsonValue]:
        return _contract_dict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        return cls(**_contract_payload(value, cls, cls.__name__))


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


def _integer(value: object, minimum: int, code: str, message: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ContractValidationError(code, message)


def _text_tuple(value: Sequence[object], field_name: str) -> tuple[str, ...]:
    items = tuple(_required_text(item, field_name) for item in value)
    if len(set(items)) != len(items):
        raise ContractValidationError(
            "duplicate_ref", f"{field_name} must not contain duplicate refs"
        )
    return items


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


TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


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


class AdmissionPhase(StrEnum):
    PENDING = "pending"
    ACCEPTED_START_PENDING = "accepted_start_pending"
    LAUNCH_CLAIMED = "launch_claimed"
    LAUNCHED = "launched"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    LAUNCH_UNKNOWN = "launch_unknown"


class TaskGoalStatus(StrEnum):
    ACTIVE = "active"
    WAITING_EXTERNAL = "waiting_external"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ProviderTurnState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"


class ProviderInvocationStatus(StrEnum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ExecutionRunFenceStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    CANCELLED = "cancelled"


class EffectHandoffState(StrEnum):
    UNRESOLVED = "unresolved"
    NOT_STARTED = "not_started"
    STARTED = "started"
    STARTED_MAY_COMPLETE = "started_may_complete"
    RECONCILED = "reconciled"


class EffectCompletionDisposition(StrEnum):
    NORMAL = "normal"
    CONFIRMED_NOT_STARTED = "confirmed_not_started"
    INFLIGHT_EFFECT_MAY_COMPLETE = "inflight_effect_may_complete"
    RECONCILED_NOT_COMPLETED = "reconciled_not_completed"
    RECONCILED_COMPLETED_SUPPRESSED = "reconciled_completed_suppressed"


class ProviderBatchStatus(StrEnum):
    ADMITTED = "admitted"
    RUNNING = "running"
    WAITING_EXTERNAL = "waiting_external"
    READY_BACKFILL = "ready_backfill"
    SETTLED = "settled"


class ProviderActionAdmissionState(StrEnum):
    ADMITTED = "admitted"
    PREPARED = "prepared"
    REJECTED = "rejected"
    WAITING_EXTERNAL = "waiting_external"
    SETTLED = "settled"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    FAILED = "failed"
    REJECTED = "rejected"
    SUCCEEDED = "succeeded"
    WAITING_EXTERNAL = "waiting_external"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class FailureSourceKind(StrEnum):
    TOOL_PARSE = "tool_parse"
    TOOL_UNKNOWN = "tool_unknown"
    TOOL_PREFLIGHT = "tool_preflight"
    TOOL_PREPARE = "tool_prepare"
    TOOL_AUTHORIZATION = "tool_authorization"
    TOOL_EXECUTOR = "tool_executor"
    CHILD_LAUNCH = "child_launch"
    CHILD_TERMINAL = "child_terminal"
    STRATEGY_REJECTED = "strategy_rejected"


class FailureErrorClass(StrEnum):
    TRANSIENT = "transient"
    TASK_PROJECT = "task_project"
    CAPABILITY = "capability"
    ENVIRONMENT = "environment"


class FailureBackfillState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    COMMITTED = "committed"


class ProviderResumeState(StrEnum):
    PENDING = "pending"
    REQUESTED = "requested"
    ACCEPTED = "accepted"


class ExternalWaitKind(StrEnum):
    CREDENTIAL = "credential"
    USER_CONTENT = "user_content"
    UAC = "uac"
    THIRD_PARTY = "third_party"


class ExternalWaitState(StrEnum):
    OPEN = "open"
    SATISFIED = "satisfied"
    CANCELLED = "cancelled"


class ProfileLaunchTicketState(StrEnum):
    ISSUED = "issued"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"


TERMINAL_ADMISSION_PHASES = frozenset(
    {
        AdmissionPhase.LAUNCHED,
        AdmissionPhase.REJECTED,
        AdmissionPhase.CANCELLED,
        AdmissionPhase.EXPIRED,
        AdmissionPhase.LAUNCH_UNKNOWN,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderLaunchSnapshot(_Contract):
    provider_id: str
    adapter_id: str
    adapter_version: str
    supports_idempotent_launch: bool
    token_field: str | None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ProviderLaunchSnapshot")
        for name in ("provider_id", "adapter_id", "adapter_version"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "token_field", _optional_text(self.token_field, "token_field"))
        if self.supports_idempotent_launch is not (self.token_field is not None):
            raise ContractValidationError(
                "invalid_provider_launch_policy",
                "idempotent launch support requires exactly one token field",
            )


def _canonical_json_text(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(
            "invalid_json", f"{field_name} must be canonical JSON"
        ) from exc
    canonical = canonical_json(decoded)
    if text != canonical:
        raise ContractValidationError(
            "noncanonical_json", f"{field_name} must use canonical JSON spelling"
        )
    return text


@dataclass(frozen=True, slots=True)
class RunStartSnapshotRecord(_Contract):
    run_id: str
    snapshot_schema_version: int
    start_fingerprint: str
    canonical_messages_json: str
    session_cursor_json: str
    prepared_refs_json: str
    sanitized_request_json: str
    run_context_json: str
    run_spec_json: str
    capability_snapshot_json: str
    capability_snapshot_hash: str
    provider_launch_policy_json: str
    terminal_deliveries_json: str
    terminal_deliveries_hash: str
    capability_lease_intent_ref: str | None
    capability_lease_intent_hash: str | None
    created_at: float
    start_extension_receipts_json: str = "[]"
    start_extension_receipts_hash: str = (
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    )
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="RunStartSnapshotRecord")
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        _integer(
            self.snapshot_schema_version,
            1,
            "invalid_schema_version",
            "snapshot_schema_version must be positive",
        )
        for name in (
            "start_fingerprint",
            "capability_snapshot_hash",
            "terminal_deliveries_hash",
        ):
            object.__setattr__(self, name, _fingerprint(getattr(self, name), name))
        for name in (
            "canonical_messages_json",
            "session_cursor_json",
            "prepared_refs_json",
            "sanitized_request_json",
            "run_context_json",
            "run_spec_json",
            "capability_snapshot_json",
            "provider_launch_policy_json",
            "terminal_deliveries_json",
            "start_extension_receipts_json",
        ):
            object.__setattr__(self, name, _canonical_json_text(getattr(self, name), name))
        object.__setattr__(
            self,
            "capability_lease_intent_ref",
            _optional_text(self.capability_lease_intent_ref, "capability_lease_intent_ref"),
        )
        if self.capability_lease_intent_hash is not None:
            object.__setattr__(
                self,
                "capability_lease_intent_hash",
                _fingerprint(
                    self.capability_lease_intent_hash,
                    "capability_lease_intent_hash",
                ),
            )
        object.__setattr__(
            self,
            "start_extension_receipts_hash",
            _fingerprint(
                self.start_extension_receipts_hash,
                "start_extension_receipts_hash",
            ),
        )
        expected_receipts_hash = hashlib.sha256(
            self.start_extension_receipts_json.encode("utf-8")
        ).hexdigest()
        if self.start_extension_receipts_hash != expected_receipts_hash:
            raise ContractValidationError(
                "extension_receipts_hash_mismatch",
                "start extension receipts hash does not match canonical payload",
            )
        if (self.capability_lease_intent_ref is None) != (
            self.capability_lease_intent_hash is None
        ):
            raise ContractValidationError(
                "invalid_lease_intent",
                "capability lease intent ref and hash must be supplied together",
            )


@dataclass(frozen=True, slots=True)
class ProviderInvocationRecord(_Contract):
    run_id: str
    invocation_id: str
    provider_id: str
    model_id: str
    adapter_id: str
    policy_snapshot_json: str
    request_hash: str
    idempotency_group_id: str
    attempt_ordinal: int
    status: ProviderInvocationStatus | str
    claim_owner: str
    claim_epoch: int
    claimed_at: float
    revocation_epoch: int
    dispatch_started_ack_ref: str | None
    dispatch_started_ack_hash: str | None
    dispatch_started_at: float | None
    stream_epoch: int
    outcome_ref: str | None
    outcome_hash: str | None
    updated_at: float
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ProviderInvocationRecord")
        for name in (
            "run_id",
            "invocation_id",
            "provider_id",
            "model_id",
            "adapter_id",
            "idempotency_group_id",
            "claim_owner",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(
            self,
            "policy_snapshot_json",
            _canonical_json_text(self.policy_snapshot_json, "policy_snapshot_json"),
        )
        object.__setattr__(self, "request_hash", _fingerprint(self.request_hash, "request_hash"))
        object.__setattr__(self, "status", ProviderInvocationStatus(self.status))
        for name, minimum in (
            ("attempt_ordinal", 0),
            ("claim_epoch", 1),
            ("revocation_epoch", 0),
            ("stream_epoch", 0),
        ):
            _integer(
                getattr(self, name),
                minimum,
                "invalid_ordinal",
                f"{name} must be >= {minimum}",
            )
        for ref_name, hash_name in (
            ("dispatch_started_ack_ref", "dispatch_started_ack_hash"),
            ("outcome_ref", "outcome_hash"),
        ):
            ref = _optional_text(getattr(self, ref_name), ref_name)
            digest = getattr(self, hash_name)
            if digest is not None:
                digest = _fingerprint(digest, hash_name)
            if (ref is None) != (digest is None):
                raise ContractValidationError(
                    "invalid_ref_hash", f"{ref_name} and {hash_name} must be paired"
                )
            object.__setattr__(self, ref_name, ref)
            object.__setattr__(self, hash_name, digest)


@dataclass(frozen=True, slots=True)
class ProviderInvocationOutcome(_Contract):
    run_id: str
    invocation_id: str
    payload_json: str
    payload_hash: str
    created_at: float
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ProviderInvocationOutcome")
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(
            self, "invocation_id", _required_text(self.invocation_id, "invocation_id")
        )
        object.__setattr__(
            self, "payload_json", _canonical_json_text(self.payload_json, "payload_json")
        )
        digest = hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()
        object.__setattr__(self, "payload_hash", _fingerprint(self.payload_hash, "payload_hash"))
        if self.payload_hash != digest:
            raise ContractValidationError(
                "payload_hash_mismatch",
                "provider outcome hash does not match canonical payload",
            )


@dataclass(frozen=True, slots=True)
class ExecutionRunFenceRecord(_Contract):
    run_id: str
    fence_kind: str
    fence_version: int
    status: ExecutionRunFenceStatus | str
    reason: str
    source_ref: str
    created_at: float
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ExecutionRunFenceRecord")
        for name in ("run_id", "fence_kind", "reason", "source_ref"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        _integer(
            self.fence_version,
            0,
            "invalid_fence_version",
            "fence_version must be non-negative",
        )
        object.__setattr__(self, "status", ExecutionRunFenceStatus(self.status))


@dataclass(frozen=True, slots=True)
class AdmissionSpec(_Contract):
    kind: DecisionKind | str
    prompt_schema_version: int
    response_schema_version: int
    prompt: Mapping[str, JsonValue]
    presentation: Mapping[str, JsonValue]
    expires_at: float | None
    intent_fingerprint: str = field(init=False)
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="AdmissionSpec")
        object.__setattr__(self, "kind", DecisionKind(self.kind))
        for name in ("prompt_schema_version", "response_schema_version"):
            _integer(getattr(self, name), 1, "invalid_admission_schema", f"{name} must be positive")
        object.__setattr__(self, "prompt", _json_object(self.prompt, name="AdmissionSpec.prompt"))
        object.__setattr__(
            self, "presentation", _json_object(self.presentation, name="AdmissionSpec.presentation")
        )
        if self.expires_at is not None and not math.isfinite(float(self.expires_at)):
            raise ContractValidationError("invalid_admission_expiry", "expires_at must be finite")
        intent = {
            name: thaw_json(getattr(self, name))
            for name in (
                "prompt_schema_version",
                "response_schema_version",
                "prompt",
                "presentation",
                "expires_at",
            )
        }
        intent["kind"] = self.kind.value
        object.__setattr__(self, "intent_fingerprint", fingerprint_json(intent))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AdmissionSpec:
        data = _contract_payload(value, cls, "AdmissionSpec")
        expected = str(data.pop("intent_fingerprint"))
        result = cls(**data)
        if result.intent_fingerprint != expected:
            raise ContractValidationError(
                "admission_intent_mismatch", "admission intent fingerprint is corrupt"
            )
        return result


@dataclass(frozen=True, slots=True)
class AdmissionBoundary(_Contract):
    run_id: str
    decision_id: str
    nonce: str
    launch_operation_id: str
    driver_kind: str
    profile_key: str
    admission: AdmissionSpec
    phase: AdmissionPhase | str
    boundary_version: int
    canonical_messages: Sequence[Mapping[str, JsonValue]]
    request_payload: Mapping[str, JsonValue]
    provider_snapshot: ProviderLaunchSnapshot
    capability_snapshot: Mapping[str, JsonValue]
    session_projection_cursor: int = 0
    prepared_context_ref: str | None = None
    tool_set_snapshot_ref: str | None = None
    association_event: Mapping[str, JsonValue] | None = None
    projection_refs: Mapping[str, JsonValue] = field(default_factory=dict)
    resolution_fingerprint: str | None = None
    consumed: bool = False
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="AdmissionBoundary")
        for name in (
            "run_id",
            "decision_id",
            "nonce",
            "launch_operation_id",
            "driver_kind",
            "profile_key",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(self, "phase", AdmissionPhase(self.phase))
        _integer(
            self.boundary_version,
            1,
            "invalid_admission_version",
            "boundary_version must be positive",
        )
        _integer(
            self.session_projection_cursor,
            0,
            "invalid_admission_cursor",
            "session_projection_cursor must be non-negative",
        )
        object.__setattr__(
            self,
            "canonical_messages",
            tuple(
                _json_object(item, name="AdmissionBoundary.canonical_messages")
                for item in self.canonical_messages
            ),
        )
        object.__setattr__(
            self,
            "request_payload",
            _json_object(self.request_payload, name="AdmissionBoundary.request_payload"),
        )
        object.__setattr__(
            self,
            "capability_snapshot",
            _json_object(self.capability_snapshot, name="AdmissionBoundary.capability_snapshot"),
        )
        object.__setattr__(
            self,
            "prepared_context_ref",
            _optional_text(self.prepared_context_ref, "prepared_context_ref"),
        )
        object.__setattr__(
            self,
            "tool_set_snapshot_ref",
            _optional_text(self.tool_set_snapshot_ref, "tool_set_snapshot_ref"),
        )
        object.__setattr__(
            self,
            "association_event",
            None
            if self.association_event is None
            else _json_object(self.association_event, name="AdmissionBoundary.association_event"),
        )
        object.__setattr__(
            self,
            "projection_refs",
            _json_object(self.projection_refs, name="AdmissionBoundary.projection_refs"),
        )
        if self.resolution_fingerprint is not None:
            object.__setattr__(
                self,
                "resolution_fingerprint",
                _fingerprint(self.resolution_fingerprint, "resolution_fingerprint"),
            )
        if not isinstance(self.consumed, bool) or self.consumed != (
            self.phase in TERMINAL_ADMISSION_PHASES
        ):
            raise ContractValidationError(
                "invalid_admission_consumed",
                "consumed must exactly match a terminal admission phase",
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AdmissionBoundary:
        data = _contract_payload(value, cls, "AdmissionBoundary")
        admission = data.pop("admission")
        provider = data.pop("provider_snapshot")
        if not isinstance(admission, Mapping) or not isinstance(provider, Mapping):
            raise ContractValidationError(
                "invalid_admission_boundary", "admission and provider_snapshot must be objects"
            )
        return cls(
            admission=AdmissionSpec.from_dict(admission),
            provider_snapshot=ProviderLaunchSnapshot.from_dict(provider),
            **data,
        )


class DecisionStatus(StrEnum):
    OPEN = "open"
    ALLOWED = "allowed"
    DENIED = "denied"
    EXPIRED = "expired"


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


def workflow_idempotency_key(parent_run_id: str, node_execution_id: str, logical_slot: str) -> str:
    return ":".join(
        (
            "workflow",
            _required_text(parent_run_id, "parent_run_id"),
            _required_text(node_execution_id, "node_execution_id"),
            _required_text(logical_slot, "logical_slot"),
        )
    )


def stable_decision_grant_id(decision_id: str) -> str:
    """Return the replay-stable authorization id for one permission decision."""
    value = _required_text(decision_id, "decision_id")
    return hashlib.sha256(f"execution-grant:{value}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RunContext(_Contract):
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
    owner_key: str | None = None
    profile_generation: int = 0
    binding_epoch: int = 0
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
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self, "parent_run_id", _optional_text(self.parent_run_id, "parent_run_id")
        )
        object.__setattr__(
            self, "capability_hash", _fingerprint(self.capability_hash, "capability_hash")
        )
        _integer(
            self.auth_epoch, 0, "invalid_auth_epoch", "auth_epoch must be a non-negative integer"
        )
        object.__setattr__(self, "owner_key", _optional_text(self.owner_key, "owner_key"))
        _integer(
            self.profile_generation,
            0,
            "invalid_profile_generation",
            "profile_generation must be a non-negative integer",
        )
        _integer(
            self.binding_epoch,
            0,
            "invalid_binding_epoch",
            "binding_epoch must be a non-negative integer",
        )
        if self.owner_key is None and (self.profile_generation or self.binding_epoch):
            raise ContractValidationError(
                "invalid_owner_identity", "owner generations require owner_key"
            )
        if self.owner_key is not None and (self.profile_generation < 1 or self.binding_epoch < 1):
            raise ContractValidationError(
                "invalid_owner_identity", "owner identity requires positive generations"
            )
        object.__setattr__(
            self, "workspace", _json_object(self.workspace, name="RunContext.workspace")
        )
        object.__setattr__(
            self, "provider_plan", _json_object(self.provider_plan, name="RunContext.provider_plan")
        )

    def actor(self) -> ActorContext:
        return ActorContext(self.principal_id, self.session_id, self.auth_epoch, self.root_run_id)


@dataclass(frozen=True, slots=True)
class ActorContext(_Contract):
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
        _integer(
            self.auth_epoch, 0, "invalid_auth_epoch", "auth_epoch must be a non-negative integer"
        )
        object.__setattr__(self, "root_run_id", _optional_text(self.root_run_id, "root_run_id"))
        if self.capability_hash is not None:
            object.__setattr__(
                self, "capability_hash", _fingerprint(self.capability_hash, "capability_hash")
            )
        if self.internal and (
            self.root_run_id is None or self.capability_hash is None or self.expires_at is None
        ):
            raise ContractValidationError(
                "invalid_internal_authority",
                "internal actors require root_run_id, capability_hash, and expires_at",
            )


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


@dataclass(frozen=True, slots=True)
class RunCreate(_Contract):
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
            raise ContractValidationError("self_parent", "a run cannot be its own parent")
        if self.context.parent_run_id is None and self.context.root_run_id != self.run_id:
            raise ContractValidationError(
                "invalid_root", "a root run must name itself as root_run_id"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunCreate:
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
            raise ContractValidationError("invalid_payload", "RunCreate.context must be an object")
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
            if (
                self.persistence_level is PersistenceLevel.DURABLE
                and self.terminal_event_id is None
            ):
                raise ContractValidationError(
                    "missing_terminal_event", "durable terminal runs require terminal_event_id"
                )
            if self.ended_at is None:
                raise ContractValidationError("missing_ended_at", "terminal runs require ended_at")
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
class DecisionOpen:
    """Immutable intent persisted before a driver suspends for input."""

    decision_id: str
    run_id: str
    nonce: str
    kind: DecisionKind | str
    prompt_schema_version: int
    prompt: Mapping[str, JsonValue]
    expires_at: float | None
    domain_kind: str | None = None
    domain_id: str | None = None
    call_id: str | None = None
    effect_id: str | None = None
    tool_name: str | None = None
    args_hash: str | None = None
    capability_hash: str | None = None
    scope_hash: str | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="DecisionOpen")
        for field_name in ("decision_id", "run_id", "nonce"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "kind", DecisionKind(self.kind))
        _integer(
            self.prompt_schema_version,
            1,
            "invalid_schema_version",
            "prompt_schema_version must be positive",
        )
        object.__setattr__(self, "prompt", _json_object(self.prompt, name="DecisionOpen.prompt"))
        for field_name in ("domain_kind", "domain_id", "call_id", "effect_id", "tool_name"):
            object.__setattr__(
                self, field_name, _optional_text(getattr(self, field_name), field_name)
            )
        if (self.domain_kind is None) != (self.domain_id is None):
            raise ContractValidationError(
                "invalid_domain_ref", "domain_kind and domain_id must be supplied together"
            )
        for field_name in ("args_hash", "capability_hash", "scope_hash"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _fingerprint(value, field_name))
        if self.expires_at is not None and (not math.isfinite(float(self.expires_at))):
            raise ContractValidationError("invalid_expiry", "decision expiry must be finite")
        if self.kind is DecisionKind.PERMISSION:
            required = (
                self.call_id,
                self.effect_id,
                self.tool_name,
                self.args_hash,
                self.capability_hash,
                self.scope_hash,
                self.expires_at,
            )
            if any((value is None for value in required)):
                raise ContractValidationError(
                    "incomplete_permission_binding",
                    "permission decisions require call/effect/tool/hash bindings and expiry",
                )


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    request: DecisionOpen
    status: DecisionStatus | str
    response_schema_version: int | None
    response: Mapping[str, JsonValue] | None
    decision_version: int
    created_at: float
    resolved_at: float | None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="DecisionRecord")
        object.__setattr__(self, "status", DecisionStatus(self.status))
        _integer(
            self.decision_version, 0, "invalid_version", "decision_version must be non-negative"
        )
        if self.status is DecisionStatus.OPEN:
            if self.response_schema_version is not None or self.response is not None:
                raise ContractValidationError(
                    "invalid_open_decision", "open decisions cannot carry a response"
                )
            if self.resolved_at is not None:
                raise ContractValidationError(
                    "invalid_open_decision", "open decisions cannot be resolved"
                )
        elif self.status in (DecisionStatus.ALLOWED, DecisionStatus.DENIED):
            if (
                self.response_schema_version is None
                or self.response_schema_version < 1
                or self.response is None
                or (self.resolved_at is None)
            ):
                raise ContractValidationError(
                    "invalid_resolved_decision",
                    "resolved decisions require versioned response and timestamp",
                )
        elif self.status is DecisionStatus.EXPIRED and self.resolved_at is None:
            raise ContractValidationError(
                "invalid_expired_decision", "expired decisions require resolved_at"
            )
        if self.response is not None:
            object.__setattr__(
                self, "response", _json_object(self.response, name="DecisionRecord.response")
            )

    @property
    def decision_id(self) -> str:
        return self.request.decision_id

    @property
    def run_id(self) -> str:
        return self.request.run_id


@dataclass(frozen=True, slots=True)
class DecisionSignal(_Contract):
    """Trusted signal request with the complete decision fence."""

    decision_id: str
    run_id: str
    expected_session_id: str
    nonce: str
    expected_version: int
    allow: bool
    response_schema_version: int
    response: Mapping[str, JsonValue]
    domain_kind: str | None = None
    domain_id: str | None = None
    call_id: str | None = None
    effect_id: str | None = None
    tool_name: str | None = None
    args_hash: str | None = None
    capability_hash: str | None = None
    scope_hash: str | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="DecisionSignal")
        for field_name in ("decision_id", "run_id", "expected_session_id", "nonce"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        _integer(
            self.expected_version, 0, "invalid_version", "expected_version must be non-negative"
        )
        if not isinstance(self.allow, bool):
            raise ContractValidationError("invalid_signal", "allow must be a boolean")
        _integer(
            self.response_schema_version,
            1,
            "invalid_schema_version",
            "response_schema_version must be positive",
        )
        object.__setattr__(
            self, "response", _json_object(self.response, name="DecisionSignal.response")
        )
        for field_name in ("domain_kind", "domain_id", "call_id", "effect_id", "tool_name"):
            object.__setattr__(
                self, field_name, _optional_text(getattr(self, field_name), field_name)
            )
        if (self.domain_kind is None) != (self.domain_id is None):
            raise ContractValidationError(
                "invalid_domain_ref", "domain_kind and domain_id must be supplied together"
            )
        for field_name in ("args_hash", "capability_hash", "scope_hash"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _fingerprint(value, field_name))


@dataclass(frozen=True, slots=True)
class GrantConsume:
    """Complete one-shot fence presented immediately before an Effect starts."""

    grant_id: str
    decision_id: str
    decision_nonce: str
    run_id: str
    expected_session_id: str
    call_id: str
    effect_id: str
    tool_name: str
    args_hash: str
    capability_hash: str
    scope_hash: str
    expected_version: int = 0
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="GrantConsume")
        for field_name in (
            "grant_id",
            "decision_id",
            "decision_nonce",
            "run_id",
            "expected_session_id",
            "call_id",
            "effect_id",
            "tool_name",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        for field_name in ("args_hash", "capability_hash", "scope_hash"):
            object.__setattr__(
                self, field_name, _fingerprint(getattr(self, field_name), field_name)
            )
        _integer(
            self.expected_version, 0, "invalid_version", "expected_version must be non-negative"
        )


@dataclass(frozen=True, slots=True)
class CreateRunResult:
    record: RunRecord
    created: bool


@dataclass(frozen=True, slots=True)
class AdmissionResolution:
    boundary: AdmissionBoundary
    decision: DecisionRecord
    event: RunEvent
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionLaunchClaim:
    boundary: AdmissionBoundary
    recovery_lease: RecoveryLease
    duplicate: bool = False

    def __post_init__(self) -> None:
        if self.boundary.phase is not AdmissionPhase.LAUNCH_CLAIMED:
            raise ContractValidationError(
                "invalid_admission_claim", "launch claim requires launch_claimed phase"
            )
        if self.recovery_lease.run_id != self.boundary.run_id:
            raise ContractValidationError(
                "invalid_admission_claim", "recovery lease belongs to another run"
            )


@dataclass(frozen=True, slots=True)
class AdmissionLaunchUnknownFence:
    run_id: str
    decision_id: str
    launch_operation_id: str
    expected_boundary_version: int
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="AdmissionLaunchUnknownFence")
        for name in ("run_id", "decision_id", "launch_operation_id"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        _integer(
            self.expected_boundary_version,
            1,
            "invalid_admission_version",
            "expected_boundary_version must be positive",
        )


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
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        for field_name in ("args_hash", "capability_hash", "scope_hash"):
            object.__setattr__(
                self, field_name, _fingerprint(getattr(self, field_name), field_name)
            )
        if self.version < 0:
            raise ContractValidationError("invalid_version", "grant version must be non-negative")


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
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self, "parent_run_id", _optional_text(self.parent_run_id, "parent_run_id")
        )
        object.__setattr__(self, "status", RunStatus(self.status))
        if self.read_only is not True:
            raise ContractValidationError(
                "mutable_legacy_projection", "legacy projections are always read-only"
            )


def assert_idempotent_run_intent(
    existing: RunCreate, requested: RunCreate, *, allow_persistence_upgrade: bool = False
) -> None:
    """Validate a replay without treating a caller-proposed run id as authority."""
    if existing.idempotency_key != requested.idempotency_key:
        raise RunIdentityConflict(
            "run_identity_conflict", "run intents use different idempotency keys"
        )
    for field_name in ("payload_fingerprint", "capability_fingerprint"):
        if getattr(existing, field_name) != getattr(requested, field_name):
            raise IdempotencyConflict(
                "idempotency_conflict", f"idempotency key already names different {field_name}"
            )
    existing_context = existing.context.to_dict()
    requested_context = requested.context.to_dict()
    existing_context.pop("trace_id", None)
    requested_context.pop("trace_id", None)
    if (
        existing.context.parent_run_id is None
        and requested.context.parent_run_id is None
        and (existing.context.root_run_id == existing.run_id)
        and (requested.context.root_run_id == requested.run_id)
    ):
        existing_context.pop("root_run_id", None)
        requested_context.pop("root_run_id", None)
    if existing_context != requested_context:
        raise RunIdentityConflict("run_identity_conflict", "idempotent RunContext differs")
    if (existing.driver_kind, existing.profile_key) != (
        requested.driver_kind,
        requested.profile_key,
    ):
        raise RunIdentityConflict("run_identity_conflict", "idempotent driver/profile differs")
    if existing.persistence_level != requested.persistence_level and (
        not (
            allow_persistence_upgrade
            and existing.persistence_level is PersistenceLevel.EPHEMERAL
            and (requested.persistence_level is PersistenceLevel.DURABLE)
        )
    ):
        raise RunIdentityConflict("run_identity_conflict", "idempotent persistence level differs")


def stable_event_id(run_id: str, event_key: str) -> str:
    run = _required_text(run_id, "run_id")
    key = _required_text(event_key, "event_key")
    return hashlib.sha256(f"execution-event|{run}|{key}".encode()).hexdigest()


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
    "AdmissionBoundary",
    "AdmissionLaunchClaim",
    "AdmissionLaunchUnknownFence",
    "AdmissionPhase",
    "AdmissionResolution",
    "AdmissionSpec",
    "AttachmentPolicy",
    "AuthorizationError",
    "ChildCommandStatus",
    "CONTRACT_SCHEMA_VERSION",
    "ContractValidationError",
    "CreateRunResult",
    "DecisionAuthorization",
    "DecisionConflict",
    "DecisionKind",
    "DecisionNotFound",
    "DecisionOpen",
    "DecisionRecord",
    "DecisionSignal",
    "DecisionStatus",
    "DeliveryClaimConflict",
    "DeliveryNotFound",
    "DeliveryPolicy",
    "DeliveryStatus",
    "EventNotFound",
    "ExecutionError",
    "GrantConsume",
    "GrantConsumeConflict",
    "GrantNotFound",
    "IdempotencyConflict",
    "JsonPrimitive",
    "JsonValue",
    "LegacyRunProjection",
    "LinkKind",
    "OutcomeStatus",
    "ParentCycleError",
    "PersistenceLevel",
    "PersistenceRequired",
    "ProviderLaunchSnapshot",
    "RecoveryLease",
    "RunContext",
    "RunCreate",
    "RunIdentityConflict",
    "RunNotFound",
    "RunRecord",
    "RunRef",
    "RunStatus",
    "TERMINAL_ADMISSION_PHASES",
    "TERMINAL_RUN_STATUSES",
    "TerminalConflict",
    "VersionConflict",
    "WorkflowSessionRef",
    "canonical_json",
    "assert_idempotent_run_intent",
    "delegate_idempotency_key",
    "fingerprint_json",
    "root_idempotency_key",
    "stable_delivery_id",
    "stable_decision_grant_id",
    "stable_event_id",
    "team_idempotency_key",
    "thaw_json",
    "validate_json_value",
    "workflow_idempotency_key",
]
__all__ += [
    "AttemptStatus",
    "ExternalWaitKind",
    "ExternalWaitState",
    "FailureBackfillState",
    "FailureErrorClass",
    "FailureSourceKind",
    "EffectCompletionDisposition",
    "EffectHandoffState",
    "ExecutionRunFenceRecord",
    "ExecutionRunFenceStatus",
    "ProfileLaunchTicketState",
    "ProviderActionAdmissionState",
    "ProviderBatchStatus",
    "ProviderResumeState",
    "ProviderInvocationOutcome",
    "ProviderInvocationRecord",
    "ProviderInvocationStatus",
    "ProviderTurnState",
    "TaskGoalStatus",
    "RunStartSnapshotRecord",
]
