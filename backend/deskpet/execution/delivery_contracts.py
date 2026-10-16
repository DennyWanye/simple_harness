"""执行层契约：子命令与子信号、运行关联、投递、运行事件与实时游标（从 execution/contracts 挪出）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from deskpet.execution.contracts import (
    CONTRACT_SCHEMA_VERSION,
    AttachmentPolicy,
    ChildCommandStatus,
    ContractValidationError,
    DeliveryPolicy,
    DeliveryStatus,
    JsonValue,
    LinkKind,
    OutcomeStatus,
    PersistenceLevel,
    RunCreate,
    RunRecord,
    _Contract,
    _fingerprint,
    _integer,
    _json_object,
    _optional_text,
    _required_text,
    _schema_version,
    _strict_mapping,
    fingerprint_json,
    thaw_json,
)


@dataclass(frozen=True, slots=True)
class ChildCommandIntent(_Contract):
    """Durable, product-neutral intent reserved before a child exists."""

    operation_id: str
    parent_run_id: str
    command_id: str
    child_spec: RunCreate
    child_request: Mapping[str, JsonValue]
    capability_subset: tuple[str, ...]
    attachment_policy: AttachmentPolicy | str
    capability_snapshot_ref: str
    intent_fingerprint: str = field(init=False, metadata={"serialize": False})
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="ChildCommandIntent")
        for field_name in ("operation_id", "parent_run_id", "command_id"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "attachment_policy", AttachmentPolicy(self.attachment_policy))
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
                "child_capability_mismatch", "child capability differs from the committed snapshot"
            )
        if self.child_spec.payload_fingerprint != fingerprint_json(thaw_json(self.child_request)):
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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChildCommandIntent:
        data = _strict_mapping(
            value,
            required={
                "schema_version",
                "operation_id",
                "parent_run_id",
                "command_id",
                "child_spec",
                "child_request",
                "capability_subset",
                "attachment_policy",
                "capability_snapshot_ref",
            },
            name="ChildCommandIntent",
        )
        child_spec = data.pop("child_spec")
        if not isinstance(child_spec, Mapping):
            raise ContractValidationError("invalid_payload", "child_spec must be an object")
        return cls(child_spec=RunCreate.from_dict(child_spec), **data)


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
        object.__setattr__(self, "last_error", _optional_text(self.last_error, "last_error"))
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
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
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
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "policy", DeliveryPolicy(self.policy))


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
    delivery_fence_epoch: int | None = None
    delivery_owner_id: str | None = None
    delivery_owner_generation: int | None = None
    delivery_dependency_hash: str | None = None
    delivery_snapshot_ref: str | None = None
    delivery_snapshot_hash: str | None = None
    release_receipt_ref: str | None = None
    release_receipt_hash: str | None = None
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
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "policy", DeliveryPolicy(self.policy))
        object.__setattr__(self, "status", DeliveryStatus(self.status))
        for field_name in ("attempts", "delivery_version"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(
                    "invalid_delivery_counter", f"{field_name} must be a non-negative integer"
                )
        object.__setattr__(self, "last_error", _optional_text(self.last_error, "last_error"))
        if self.status is DeliveryStatus.DELIVERED and self.delivered_at is None:
            raise ContractValidationError(
                "invalid_delivery_terminal", "delivered rows require delivered_at"
            )
        if self.status is not DeliveryStatus.DELIVERED and self.delivered_at is not None:
            raise ContractValidationError(
                "invalid_delivery_terminal", "only delivered rows may carry delivered_at"
            )
        fenced_values = (
            self.delivery_fence_epoch,
            self.delivery_owner_id,
            self.delivery_owner_generation,
            self.delivery_dependency_hash,
            self.delivery_snapshot_ref,
            self.delivery_snapshot_hash,
            self.release_receipt_ref,
            self.release_receipt_hash,
        )
        if any(value is not None for value in fenced_values):
            if any(value is None for value in fenced_values):
                raise ContractValidationError(
                    "invalid_delivery_fence",
                    "fenced delivery identity must be complete",
                )
            if (
                isinstance(self.delivery_fence_epoch, bool)
                or self.delivery_fence_epoch < 0
                or isinstance(self.delivery_owner_generation, bool)
                or self.delivery_owner_generation < 1
            ):
                raise ContractValidationError(
                    "invalid_delivery_fence",
                    "delivery fence generations are invalid",
                )
            for digest in (
                self.delivery_dependency_hash,
                self.delivery_snapshot_hash,
                self.release_receipt_hash,
            ):
                if len(str(digest)) != 64 or any(
                    char not in "0123456789abcdef" for char in str(digest)
                ):
                    raise ContractValidationError(
                        "invalid_delivery_fence",
                        "delivery fence hashes must be lowercase SHA-256",
                    )


@dataclass(frozen=True, slots=True)
class RunEventCandidate(_Contract):
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
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "status", OutcomeStatus(self.status))
        object.__setattr__(
            self,
            "correlation",
            _json_object(self.correlation, name="RunEventCandidate.correlation"),
        )
        object.__setattr__(
            self, "payload", _json_object(self.payload, name="RunEventCandidate.payload")
        )
        object.__setattr__(
            self,
            "error",
            None
            if self.error is None
            else _json_object(self.error, name="RunEventCandidate.error"),
        )
        refs = tuple((_required_text(ref, "artifact_ref") for ref in self.artifact_refs))
        if len(set(refs)) != len(refs):
            raise ContractValidationError("duplicate_artifact_ref", "artifact_refs must be unique")
        object.__setattr__(self, "artifact_refs", refs)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OutcomeStatus.SUCCEEDED,
            OutcomeStatus.FAILED,
            OutcomeStatus.CANCELLED,
        } and (
            self.kind == "final"
            or self.kind.endswith(".final")
            or self.payload.get("kind") == "final"
        )


@dataclass(frozen=True, slots=True)
class LiveCursor:
    stream_epoch: str
    live_seq: int
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, name="LiveCursor")
        object.__setattr__(self, "stream_epoch", _required_text(self.stream_epoch, "stream_epoch"))
        _integer(self.live_seq, 1, "invalid_live_seq", "live_seq must be a positive integer")


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
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        has_durable_order = self.durable_seq is not None
        has_live_order = self.live_cursor is not None
        if has_durable_order == has_live_order:
            raise ContractValidationError(
                "invalid_event_order", "RunEvent requires exactly one of durable_seq or live_cursor"
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


@dataclass(frozen=True, slots=True)
class FinalizeRunResult:
    record: RunRecord
    event: RunEvent
    idempotent: bool


__all__ = [
    "ChildCommandIntent",
    "ChildCommandRecord",
    "ChildSignalRecord",
    "DeliveryRecord",
    "DeliverySpec",
    "FinalizeRunResult",
    "LiveCursor",
    "RunEvent",
    "RunEventCandidate",
    "RunLinkSpec",
]
