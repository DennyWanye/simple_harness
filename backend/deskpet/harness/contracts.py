"""Stable product-neutral contracts for the execution harness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from deskpet.execution.contracts import (
    ActorContext, AdmissionSpec, DeliverySpec, JsonValue, RunCreate, RunEvent,
    RunEventCandidate, RunRecord, RunRef, RunStatus,
)
from .ports import Driver


@dataclass(frozen=True, slots=True)
class RunRequest:
    text: str
    request_id: str
    turn_id: str
    venue: str = "text"
    mode: str = "auto"
    workspace_context: bool = False
    proposed_tools: tuple[str, ...] = ()
    canonical_messages: tuple[Mapping[str, JsonValue], ...] = ()
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    admission: AdmissionSpec | None = None


class TerminalProjection(Protocol):
    def resolve(self, session_id: str) -> str | None: ...
    def association_event(self, spec: RunCreate, target_id: str) -> RunEventCandidate: ...
    def deliveries(self, record: RunRecord, events: Sequence[RunEvent]) -> Sequence[DeliverySpec]: ...


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
