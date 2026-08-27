"""Stable product-neutral contracts for the execution harness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from deskpet.execution.contracts import (
    ActorContext, AdmissionSpec, DeliverySpec, JsonValue, ProviderLaunchSnapshot, RunCreate, RunEvent,
    RunEventCandidate, RunRecord, RunRef, RunStatus, fingerprint_json,
)
from .ports import Driver


@dataclass(frozen=True, slots=True)
class RunRequest:
    text: str
    request_id: str
    turn_id: str
    venue: str = "text"
    mode: str = "general"
    workspace_context: bool = False
    proposed_tools: tuple[str, ...] = ()
    canonical_messages: tuple[Mapping[str, JsonValue], ...] = ()
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    admission: AdmissionSpec | None = None
    provider_launch_snapshot: ProviderLaunchSnapshot | None = None


class TerminalProjection(Protocol):
    def resolve(self, session_id: str) -> str | None: ...
    def association_event(self, spec: RunCreate, target_id: str) -> RunEventCandidate: ...
    def deliveries(self, record: RunRecord, events: Sequence[RunEvent]) -> Sequence[DeliverySpec]: ...


class TerminalDeliveryContributor(Protocol):
    """Freeze product-neutral terminal intents before a Run can act."""

    def requires_durable(self, request: object, host: object) -> bool: ...

    def freeze_deliveries(
        self, request: object, host: object
    ) -> Sequence[DeliverySpec]: ...


@dataclass(frozen=True, slots=True)
class HostExtensionRefV1:
    """Content-addressed descriptor persisted without a process object address."""

    kind: str
    ref: str
    content_hash: str

    def __post_init__(self) -> None:
        for name in ("kind", "ref"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if "." not in self.kind:
            raise ValueError("host extension kind must be namespaced")
        if (
            len(self.content_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.content_hash)
        ):
            raise ValueError("host extension hash must be lowercase SHA-256")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "content_hash": self.content_hash,
        }


class StartCommitExtensionV1(Protocol):
    """Host-only capability executed inside the caller-owned start transaction."""

    @property
    def descriptor(self) -> HostExtensionRefV1: ...

    async def apply_start_commit(
        self,
        transaction: Any,
        *,
        spec: RunCreate,
        start_snapshot: object,
    ) -> HostExtensionRefV1: ...


class AfterStartCommitHandshakeV1(Protocol):
    """Host-only handshake after a known committed Run start."""

    @property
    def descriptor(self) -> HostExtensionRefV1: ...

    async def activate_after_start(
        self,
        record: RunRecord,
        *,
        start_snapshot: object,
        extension_receipts: Sequence[HostExtensionRefV1],
    ) -> bool: ...


class TerminalCommitExtensionV1(Protocol):
    """Host-only capability executed in the caller-owned terminal transaction."""

    @property
    def descriptor(self) -> HostExtensionRefV1: ...

    async def apply_terminal_commit(
        self,
        transaction: Any,
        *,
        record: RunRecord,
        terminal_event: RunEventCandidate,
    ) -> HostExtensionRefV1: ...


class AfterTerminalCommitCleanupV1(Protocol):
    """In-memory cleanup that may run only after terminal commit is certain."""

    @property
    def descriptor(self) -> HostExtensionRefV1: ...

    async def cleanup_after_terminal(
        self,
        record: RunRecord,
        event: RunEvent,
        *,
        extension_receipts: Sequence[HostExtensionRefV1],
    ) -> None: ...


def _checked_hash(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class ReservedRootProfileSelectionV1:
    """Host-only selection of one non-model-visible Root profile."""

    profile_key: str
    catalog_generation: int
    driver_kind: str
    purpose: str
    intent_id: str
    operation_id: str
    content_hash: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("reserved Root profile selection schema is unsupported")
        for name in (
            "profile_key", "driver_kind", "purpose", "intent_id", "operation_id"
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.catalog_generation < 1:
            raise ValueError("reserved profile catalog generation must be positive")
        _checked_hash(self.content_hash, "content_hash")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "profile_key": self.profile_key,
            "catalog_generation": self.catalog_generation,
            "driver_kind": self.driver_kind,
            "purpose": self.purpose,
            "intent_id": self.intent_id,
            "operation_id": self.operation_id,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class PreparedRunContextV1:
    """Trusted start facts that user payload/JSON cannot construct.

    The stable descriptors enter the start fingerprint. Callback objects stay
    process-local and are invoked only by their corresponding trusted seam.
    """

    persistence_required: bool = False
    reserved_root_profile: ReservedRootProfileSelectionV1 | None = None
    prepared_tool_ref: str | None = None
    prepared_tool_hash: str | None = None
    product_snapshot_ref: str | None = None
    product_snapshot_hash: str | None = None
    capability_lease_intent_ref: str | None = None
    capability_lease_intent_hash: str | None = None
    owner_key: str | None = None
    profile_generation: int = 0
    binding_epoch: int = 0
    prepared_tool_names: tuple[str, ...] = ()
    capability_snapshot: Mapping[str, JsonValue] = field(default_factory=dict)
    frozen_terminal_deliveries: tuple[DeliverySpec, ...] = ()
    host_extensions: Mapping[str, HostExtensionRefV1] = field(default_factory=dict)
    host_extension_payloads: Mapping[str, Mapping[str, JsonValue]] = field(
        default_factory=dict
    )
    start_commit_extensions: tuple[StartCommitExtensionV1, ...] = ()
    after_start_commit_handshakes: tuple[AfterStartCommitHandshakeV1, ...] = ()
    terminal_commit_extensions: tuple[TerminalCommitExtensionV1, ...] = ()
    after_terminal_commit_cleanup: tuple[AfterTerminalCommitCleanupV1, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("PreparedRunContextV1 schema_version must be 1")
        if self.reserved_root_profile is not None and not isinstance(
            self.reserved_root_profile, ReservedRootProfileSelectionV1
        ):
            raise TypeError("reserved_root_profile must be Host-issued")
        for ref_name, hash_name in (
            ("prepared_tool_ref", "prepared_tool_hash"),
            ("product_snapshot_ref", "product_snapshot_hash"),
            ("capability_lease_intent_ref", "capability_lease_intent_hash"),
        ):
            ref = getattr(self, ref_name)
            digest = getattr(self, hash_name)
            if (ref is None) != (digest is None):
                raise ValueError(f"{ref_name} and {hash_name} must appear together")
            if ref is not None and not str(ref).strip():
                raise ValueError(f"{ref_name} must not be blank")
            _checked_hash(digest, hash_name)
        if self.owner_key is None:
            if self.profile_generation != 0 or self.binding_epoch != 0:
                raise ValueError(
                    "owner generation and binding epoch require owner_key"
                )
        else:
            if not str(self.owner_key).strip():
                raise ValueError("owner_key must not be blank")
            if self.profile_generation < 1 or self.binding_epoch < 1:
                raise ValueError(
                    "prepared owner identity requires positive generations"
                )
        deliveries = tuple(self.frozen_terminal_deliveries)
        delivery_keys = {
            (
                item.sink_kind,
                item.sink_instance,
                item.target_id,
                item.policy.value,
            )
            for item in deliveries
        }
        if len(delivery_keys) != len(deliveries):
            raise ValueError("frozen terminal deliveries must be unique")
        object.__setattr__(self, "frozen_terminal_deliveries", deliveries)
        tool_names = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.prepared_tool_names
                    if str(item).strip()
                }
            )
        )
        object.__setattr__(self, "prepared_tool_names", tool_names)
        extensions = dict(self.host_extensions)
        if any(key != value.kind for key, value in extensions.items()):
            raise ValueError("host extension map keys must equal descriptor kinds")
        object.__setattr__(self, "host_extensions", MappingProxyType(extensions))
        payloads = {
            str(key): MappingProxyType(dict(value))
            for key, value in self.host_extension_payloads.items()
        }
        for key, payload in payloads.items():
            descriptor = extensions.get(key)
            if descriptor is None:
                raise ValueError(
                    "host extension payload requires a matching descriptor"
                )
            if fingerprint_json(dict(payload)) != descriptor.content_hash:
                raise ValueError("host extension payload hash mismatch")
        object.__setattr__(
            self, "host_extension_payloads", MappingProxyType(payloads)
        )
        capability_snapshot = dict(self.capability_snapshot)
        if capability_snapshot:
            required = {
                "run_catalog_content_stamp",
                "process_catalog_stamp",
                "catalog_snapshot_ref",
                "capability_lease_intent_ref",
                "prepared_tool_set_ref",
                "capability_hash",
                "product_snapshot_ref",
                "host_extensions",
            }
            if set(capability_snapshot) != required:
                raise ValueError(
                    "trusted capability snapshot has an invalid shape"
                )
            if (
                capability_snapshot["capability_lease_intent_ref"]
                != self.capability_lease_intent_ref
                or capability_snapshot["prepared_tool_set_ref"]
                != self.prepared_tool_ref
                or capability_snapshot["product_snapshot_ref"]
                != self.product_snapshot_ref
            ):
                raise ValueError(
                    "trusted capability snapshot differs from prepared refs"
                )
            snapshot_extensions = capability_snapshot["host_extensions"]
            if not isinstance(snapshot_extensions, Mapping):
                raise ValueError(
                    "trusted capability snapshot host_extensions must be an object"
                )
            expected_selection = payloads.get(
                "deskpet.companion.selection.v1"
            )
            actual_selection = snapshot_extensions.get(
                "deskpet.companion.selection.v1"
            )
            if actual_selection != expected_selection:
                raise ValueError(
                    "trusted companion selection differs from its host extension"
                )
        object.__setattr__(
            self,
            "capability_snapshot",
            MappingProxyType(capability_snapshot),
        )
        for callbacks in (
            self.start_commit_extensions,
            self.after_start_commit_handshakes,
            self.terminal_commit_extensions,
            self.after_terminal_commit_cleanup,
        ):
            descriptors = tuple(item.descriptor for item in callbacks)
            if len({item.kind for item in descriptors}) != len(descriptors):
                raise ValueError("host callback kinds must be unique within each seam")
        if (
            self.start_commit_extensions
            or self.after_start_commit_handshakes
            or self.terminal_commit_extensions
            or self.after_terminal_commit_cleanup
        ) and not self.persistence_required:
            raise ValueError("host commit callbacks require durable persistence")

    @property
    def prepared_fingerprint(self) -> str:
        callback_groups = (
            self.start_commit_extensions,
            self.after_start_commit_handshakes,
            self.terminal_commit_extensions,
            self.after_terminal_commit_cleanup,
        )
        return fingerprint_json(
            {
                "schema_version": self.schema_version,
                "persistence_required": self.persistence_required,
                "reserved_root_profile": (
                    None
                    if self.reserved_root_profile is None
                    else self.reserved_root_profile.to_dict()
                ),
                "prepared_tool_ref": self.prepared_tool_ref,
                "prepared_tool_hash": self.prepared_tool_hash,
                "product_snapshot_ref": self.product_snapshot_ref,
                "product_snapshot_hash": self.product_snapshot_hash,
                "capability_lease_intent_ref": self.capability_lease_intent_ref,
                "capability_lease_intent_hash": self.capability_lease_intent_hash,
                "owner_key": self.owner_key,
                "profile_generation": self.profile_generation,
                "binding_epoch": self.binding_epoch,
                "prepared_tool_names": list(self.prepared_tool_names),
                "capability_snapshot": dict(self.capability_snapshot),
                "frozen_terminal_deliveries": [
                    {
                        "sink_kind": item.sink_kind,
                        "sink_instance": item.sink_instance,
                        "target_id": item.target_id,
                        "policy": item.policy.value,
                    }
                    for item in sorted(
                        self.frozen_terminal_deliveries,
                        key=lambda value: (
                            value.sink_kind,
                            value.sink_instance,
                            value.target_id,
                            value.policy.value,
                        ),
                    )
                ],
                "host_extensions": {
                    key: value.to_dict()
                    for key, value in sorted(self.host_extensions.items())
                },
                "host_extension_payloads": {
                    key: dict(value)
                    for key, value in sorted(
                        self.host_extension_payloads.items()
                    )
                },
                "callback_descriptors": [
                    [
                        item.descriptor.to_dict()
                        for item in group
                        if not bool(getattr(item, "presentation_only", False))
                    ]
                    for group in callback_groups
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class HostContext:
    session_id: str
    principal_id: str
    auth_epoch: int
    capability_hash: str
    available_capabilities: frozenset[str]
    provider_plan: tuple[str, ...]
    trace_id: str
    workspace: str | None = None
    write_scope_root: str | None = None
    project_id: str = ""
    project_revision: int = 0
    project_identity: str = ""
    # provider_id, model_id, incarnation_id, config_revision, binding_epoch.
    # Legacy two-item tuples remain accepted by HostContextFactory.
    provider_bindings: tuple[tuple[Any, ...], ...] = ()

    def actor(self, *, root_run_id: str | None = None) -> ActorContext:
        return ActorContext(
            principal_id=self.principal_id,
            session_id=self.session_id,
            auth_epoch=self.auth_epoch,
            root_run_id=root_run_id,
        )


@dataclass(frozen=True, slots=True)
class RunHandle:
    ref: RunRef
    root_run_id: str
    driver_kind: str
    profile_key: str


@dataclass(frozen=True, slots=True)
class SignalReceipt:
    accepted: bool
    duplicate: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CancelReceipt:
    run_id: str
    status: RunStatus
    acknowledged: bool


@dataclass(frozen=True, slots=True)
class RegisteredDriver:
    """Immutable metadata around the canonical Driver port."""

    kind: str
    driver: Driver
    durable_from_start: bool = False
    atomic_start: bool = False

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("driver kind is required")
        if self.atomic_start and not self.durable_from_start:
            raise ValueError("atomic-start drivers must be durable from start")


def driver_catalog(drivers: tuple[RegisteredDriver, ...]) -> Mapping[str, RegisteredDriver]:
    catalog: dict[str, RegisteredDriver] = {}
    for registration in drivers:
        if registration.kind in catalog:
            raise ValueError(f"duplicate driver kind: {registration.kind}")
        catalog[registration.kind] = registration
    if not catalog:
        raise ValueError("at least one driver is required")
    return MappingProxyType(catalog)
