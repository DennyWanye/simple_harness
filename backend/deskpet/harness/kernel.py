"""Thin product-neutral RunKernel control plane."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from deskpet.execution.contracts import (
    ActorContext,
    DeliverySpec,
    JsonValue,
    OutcomeStatus,
    PersistenceLevel,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunRecord,
    RunRef,
    RunNotFound,
    RunStatus,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.execution.ports import ExecutionLedgerPort

from .context import HostContextFactory
from .router import RegisteredRouter, RouteRequest as RoutingRequest


@dataclass(frozen=True, slots=True)
class RunRequest:
    text: str
    request_id: str
    turn_id: str
    venue: str = "text"
    mode: str = "auto"
    workspace_context: bool = False
    proposed_tools: tuple[str, ...] = ()
    payload: Mapping[str, JsonValue] = field(default_factory=dict)


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
class RunSignal:
    kind: str
    decision_id: str
    nonce: str
    version: int
    payload: Mapping[str, JsonValue] = field(default_factory=dict)


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
class DriverTerminalCandidate:
    expected_version: int
    terminal_status: RunStatus
    event: RunEventCandidate
    deliveries: tuple[DeliverySpec, ...] = ()


DriverEmitter = Callable[[RunEvent], Awaitable[None]]


class RunDriver(Protocol):
    kind: str
    durable_from_start: bool

    async def start(
        self,
        record: RunRecord,
        request: RunRequest,
        emit: DriverEmitter,
    ) -> DriverTerminalCandidate | None: ...

    async def recover(
        self,
        record: RunRecord,
        emit: DriverEmitter,
    ) -> DriverTerminalCandidate | None: ...

    async def signal(
        self,
        record: RunRecord,
        signal: RunSignal,
    ) -> SignalReceipt: ...

    async def cancel(self, record: RunRecord, reason: str) -> bool: ...


@dataclass(slots=True)
class _ActiveRun:
    actor: ActorContext
    events: list[RunEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue[RunEvent | None]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


class RunKernel:
    """Coordinate identity, routing, ownership and lifecycle only."""

    def __init__(
        self,
        *,
        ledger: ExecutionLedgerPort,
        router: RegisteredRouter,
        drivers: Sequence[RunDriver],
        context_factory: HostContextFactory | None = None,
    ) -> None:
        catalog: dict[str, RunDriver] = {}
        for driver in drivers:
            if driver.kind in catalog:
                raise ValueError(f"duplicate driver kind: {driver.kind}")
            catalog[driver.kind] = driver
        if not catalog:
            raise ValueError("at least one driver is required")
        self._ledger = ledger
        self._router = router
        self._drivers = MappingProxyType(catalog)
        self._context_factory = context_factory or HostContextFactory()
        self._active: dict[str, _ActiveRun] = {}
        self._lock = asyncio.Lock()

    async def start(self, request: RunRequest, host: HostContext) -> RunHandle:
        key = root_idempotency_key(host.session_id, request.request_id, request.turn_id)
        run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{key}").hex
        ref = RunRef(run_id, host.session_id)
        actor = host.actor(root_run_id=run_id)
        try:
            existing = await self._ledger.query(ref, actor)
        except RunNotFound:
            existing = None
        if isinstance(existing, RunRecord):
            return RunHandle(
                ref=ref,
                root_run_id=existing.context.root_run_id,
                driver_kind=existing.spec.driver_kind,
                profile_key=existing.spec.profile_key,
            )
        routing = self._router.route(
            RoutingRequest(
                text=request.text,
                request_id=request.request_id,
                turn_id=request.turn_id,
                venue=request.venue,
                mode=request.mode,
                workspace_context=request.workspace_context,
                proposed_tools=request.proposed_tools,
            ),
            available_capabilities=host.available_capabilities,
        )
        driver = self._drivers.get(routing.driver_kind)
        if driver is None:
            raise RuntimeError(f"driver is not registered: {routing.driver_kind}")
        context = self._context_factory.create_run_context(
            session_id=host.session_id,
            root_run_id=run_id,
            request_id=request.request_id,
            turn_id=request.turn_id,
            venue=request.venue,
            capability_hash=host.capability_hash,
            provider_plan=host.provider_plan,
            trace_id=host.trace_id,
            principal_id=host.principal_id,
            auth_epoch=host.auth_epoch,
            workspace=host.workspace,
            write_scope_root=host.write_scope_root,
        )
        payload = {"text": request.text, "payload": dict(request.payload)}
        spec = RunCreate(
            run_id=run_id,
            idempotency_key=key,
            context=context,
            payload_fingerprint=fingerprint_json(payload),
            capability_fingerprint=host.capability_hash,
            driver_kind=driver.kind,
            profile_key=routing.profile_key,
            persistence_level=(
                PersistenceLevel.DURABLE if driver.durable_from_start else PersistenceLevel.EPHEMERAL
            ),
            status=RunStatus.CREATED,
        )
        result = await self._ledger.create(spec)
        async with self._lock:
            active = self._active.setdefault(run_id, _ActiveRun(actor=actor))
            if result.created and (active.task is None or active.task.done()):
                active.task = asyncio.create_task(
                    self._drive_start(driver, result.record, request),
                    name=f"deskpet-run:{run_id}",
                )
        return RunHandle(
            ref=ref,
            root_run_id=result.record.context.root_run_id,
            driver_kind=driver.kind,
            profile_key=result.record.spec.profile_key,
        )

    async def observe(
        self,
        ref: RunRef,
        actor: ActorContext,
        cursor: int | None = None,
    ) -> AsyncIterator[RunEvent]:
        await self._ledger.authorize(ref, actor, "observe")
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        async with self._lock:
            active = self._active.setdefault(ref.run_id, _ActiveRun(actor=actor))
            start = max(0, int(cursor or 0))
            history = tuple(active.events[start:])
            active.subscribers.append(queue)
        try:
            for event in history:
                yield event
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            async with self._lock:
                current = self._active.get(ref.run_id)
                if current is not None and queue in current.subscribers:
                    current.subscribers.remove(queue)

    async def signal(
        self,
        ref: RunRef,
        actor: ActorContext,
        signal: RunSignal,
    ) -> SignalReceipt:
        record = await self._ledger.authorize(ref, actor, "signal")
        if not isinstance(record, RunRecord):
            raise RuntimeError("legacy runs require the compatibility signal adapter")
        return await self._driver(record).signal(record, signal)

    async def cancel(
        self,
        ref: RunRef,
        actor: ActorContext,
        reason: str,
    ) -> CancelReceipt:
        record = await self._ledger.authorize(ref, actor, "cancel")
        if not isinstance(record, RunRecord):
            raise RuntimeError("legacy runs require the compatibility cancel adapter")
        event = RunEventCandidate(
            event_key=f"cancel-request:{ref.run_id}",
            kind="cancel_requested",
            status=OutcomeStatus.CANCEL_REQUESTED,
            driver_kind=record.spec.driver_kind,
            correlation={"request_id": record.context.request_id},
            payload={"reason": reason},
        )
        updated = await self._ledger.request_cancel(
            ref.run_id,
            expected_version=record.version,
            reason=reason,
            event=event,
        )
        acknowledged = await self._driver(updated).cancel(updated, reason)
        return CancelReceipt(ref.run_id, updated.status, acknowledged)

    async def recover(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> RunHandle:
        record = await self._ledger.authorize(ref, actor, "recover")
        if not isinstance(record, RunRecord):
            raise RuntimeError("legacy runs require the compatibility recovery adapter")
        driver = self._driver(record)
        async with self._lock:
            active = self._active.setdefault(ref.run_id, _ActiveRun(actor=actor))
            if active.task is None or active.task.done():
                active.task = asyncio.create_task(
                    self._drive_recover(driver, record),
                    name=f"deskpet-recover:{ref.run_id}",
                )
        return RunHandle(ref, record.context.root_run_id, driver.kind, record.spec.profile_key)

    async def close(self, ref: RunRef, actor: ActorContext) -> None:
        record = await self._ledger.authorize(ref, actor, "observe")
        async with self._lock:
            active = self._active.get(ref.run_id)
            if active is None:
                return
            for subscriber in tuple(active.subscribers):
                subscriber.put_nowait(None)
            active.subscribers.clear()
            if isinstance(record, RunRecord) and record.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            } and (active.task is None or active.task.done()):
                self._active.pop(ref.run_id, None)

    def _driver(self, record: RunRecord) -> RunDriver:
        driver = self._drivers.get(record.spec.driver_kind)
        if driver is None:
            raise RuntimeError(f"driver is not registered: {record.spec.driver_kind}")
        return driver

    async def _emit(self, event: RunEvent) -> None:
        async with self._lock:
            active = self._active.get(event.run_id)
            if active is None:
                return
            active.events.append(event)
            subscribers = tuple(active.subscribers)
        for queue in subscribers:
            queue.put_nowait(event)

    async def _drive_start(
        self,
        driver: RunDriver,
        record: RunRecord,
        request: RunRequest,
    ) -> None:
        terminal = await driver.start(record, request, self._emit)
        if terminal is not None:
            await self._commit_terminal(record.run_id, terminal)

    async def _drive_recover(self, driver: RunDriver, record: RunRecord) -> None:
        terminal = await driver.recover(record, self._emit)
        if terminal is not None:
            await self._commit_terminal(record.run_id, terminal)

    async def _commit_terminal(
        self,
        run_id: str,
        terminal: DriverTerminalCandidate,
    ) -> None:
        await self._ledger.finalize(
            run_id,
            expected_version=terminal.expected_version,
            terminal_status=terminal.terminal_status,
            event=terminal.event,
            deliveries=terminal.deliveries,
        )


def kernel_public_operations() -> tuple[str, ...]:
    return ("start", "observe", "signal", "cancel", "recover", "close")


__all__ = [
    "CancelReceipt",
    "DriverTerminalCandidate",
    "HostContext",
    "RunDriver",
    "RunHandle",
    "RunKernel",
    "RunRequest",
    "RunSignal",
    "SignalReceipt",
    "kernel_public_operations",
]
