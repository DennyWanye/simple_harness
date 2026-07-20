"""Thin product-neutral RunKernel control plane."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from deskpet.execution.contracts import (
    ActorContext,
    AttachmentPolicy,
    JsonValue,
    LiveCursor,
    OutcomeStatus,
    PersistenceLevel,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunNotFound,
    RunRecord,
    RunRef,
    RunStatus,
    fingerprint_json,
    root_idempotency_key,
    DecisionSignal as DurableDecisionSignal,
)
from deskpet.execution.ports import ExecutionLedgerPort

from .context import HostContextFactory
from .child_runs import ChildLauncher, ChildRunCoordinator
from .decisions import DecisionStore
from .ports import (
    CancelAcknowledgedCandidate,
    ChildAcceptedCandidate,
    DelegateRun,
    DecisionSignal as DriverDecisionSignal,
    Driver,
    DriverCandidate,
    DriverSignal,
    DriverStart,
    DriverTerminalCandidate,
    ExecuteTools,
    OpenDecision,
    PersistedEventCandidate,
    ProviderFallbackCandidate,
    TokenCandidate,
)
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
    """Immutable registration metadata around the one canonical Driver port."""

    kind: str
    driver: Driver
    durable_from_start: bool = False
    atomic_start: bool = False

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("driver kind is required")
        if self.atomic_start and not self.durable_from_start:
            raise ValueError("atomic-start drivers must be durable from start")


@dataclass(slots=True)
class _ActiveRun:
    actor: ActorContext
    events: list[RunEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue[RunEvent | None]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RunKernel:
    """Coordinate identity, routing, ownership and lifecycle only."""

    def __init__(
        self,
        *,
        ledger: ExecutionLedgerPort,
        router: RegisteredRouter,
        drivers: Sequence[RegisteredDriver],
        context_factory: HostContextFactory | None = None,
        decision_store: DecisionStore | None = None,
        child_runs: ChildRunCoordinator | None = None,
        child_launcher: ChildLauncher | None = None,
        child_scheduler_owner: str = "run-kernel",
    ) -> None:
        catalog: dict[str, RegisteredDriver] = {}
        for registration in drivers:
            if registration.kind in catalog:
                raise ValueError(f"duplicate driver kind: {registration.kind}")
            catalog[registration.kind] = registration
        if not catalog:
            raise ValueError("at least one driver is required")
        self._ledger = ledger
        self._router = router
        self._drivers = MappingProxyType(catalog)
        self._context_factory = context_factory or HostContextFactory()
        self._decisions = decision_store
        self._child_runs = child_runs
        self._child_launcher = child_launcher
        self._child_scheduler_owner = child_scheduler_owner
        self._active: dict[str, _ActiveRun] = {}
        self._lock = asyncio.Lock()

    async def start(self, request: RunRequest, host: HostContext) -> RunHandle:
        key = root_idempotency_key(host.session_id, request.request_id, request.turn_id)
        run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{key}").hex
        ref = RunRef(run_id, host.session_id)
        actor = host.actor(root_run_id=run_id)
        async with self._lock:
            active = self._active.setdefault(run_id, _ActiveRun(actor=actor))
        async with active.start_lock:
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
            registration = self._drivers.get(routing.driver_kind)
            if registration is None:
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
                driver_kind=registration.kind,
                profile_key=routing.profile_key,
                persistence_level=(
                    PersistenceLevel.DURABLE
                    if registration.durable_from_start
                    else PersistenceLevel.EPHEMERAL
                ),
                status=RunStatus.CREATED,
            )
            if registration.atomic_start:
                iterator = registration.driver.start(
                    self._driver_start(spec, request, routing.profile_key, host)
                )
                try:
                    first = await anext(iterator)
                except StopAsyncIteration as exc:
                    raise RuntimeError(
                        "atomic-start driver ended before durable acceptance"
                    ) from exc
                durable = await self._ledger.query(ref, actor)
                if not isinstance(durable, RunRecord):
                    raise RuntimeError(
                        "atomic-start driver did not commit a durable execution run"
                    )
                await self._consume_candidate(registration, durable, first)
                async with self._lock:
                    active.task = asyncio.create_task(
                        self._drive(registration, durable, iterator),
                        name=f"deskpet-run:{run_id}",
                    )
                result_record = durable
            else:
                result = await self._ledger.create(spec)
                async with self._lock:
                    if result.created and (active.task is None or active.task.done()):
                        active.task = asyncio.create_task(
                            self._drive(
                                registration,
                                result.record,
                                registration.driver.start(
                                    self._driver_start(
                                        result.record.spec,
                                        request,
                                        routing.profile_key,
                                        host,
                                    )
                                ),
                            ),
                            name=f"deskpet-run:{run_id}",
                        )
                result_record = result.record
            return RunHandle(
                ref=ref,
                root_run_id=result_record.context.root_run_id,
                driver_kind=registration.kind,
                profile_key=result_record.spec.profile_key,
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
        signal: DriverSignal,
    ) -> SignalReceipt:
        record = await self._ledger.authorize(ref, actor, "signal")
        if not isinstance(record, RunRecord):
            raise RuntimeError("legacy runs require the compatibility signal adapter")
        if signal.run_id != ref.run_id:
            return SignalReceipt(False, reason="signal_run_binding_mismatch")
        registration = self._driver(record)
        driver_signal = signal
        if isinstance(signal, DriverDecisionSignal) and self._decisions is not None:
            if signal.nonce is None or signal.version is None:
                return SignalReceipt(False, reason="decision_fence_required")
            current = await self._decisions.get(
                signal.decision_id,
                ref=ref,
                actor=actor,
            )
            request = current.request
            response = dict(signal.response)
            allow = bool(response.get("allow", response.get("approved", True)))
            resolved, authorization = await self._decisions.resolve(
                DurableDecisionSignal(
                    decision_id=request.decision_id,
                    run_id=request.run_id,
                    expected_session_id=ref.expected_session_id,
                    nonce=signal.nonce,
                    expected_version=signal.version,
                    allow=allow,
                    response_schema_version=1,
                    response=response,
                    domain_kind=request.domain_kind,
                    domain_id=request.domain_id,
                    call_id=request.call_id,
                    effect_id=request.effect_id,
                    tool_name=request.tool_name,
                    args_hash=request.args_hash,
                    capability_hash=request.capability_hash,
                    scope_hash=request.scope_hash,
                ),
                actor,
            )
            response["decision_status"] = resolved.status.value
            if authorization is not None:
                response["grant_id"] = authorization.grant_id
            driver_signal = DriverDecisionSignal(
                run_id=signal.run_id,
                decision_id=signal.decision_id,
                response=response,
                nonce=signal.nonce,
                version=signal.version,
            )
        await self._consume(
            registration,
            record,
            registration.driver.signal(driver_signal),
        )
        return SignalReceipt(True)

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
        if self._decisions is not None:
            await self._decisions.cancel_waiting(
                ref,
                actor,
                expected_run_version=updated.version,
            )
        for link in await self._ledger.list_child_links(ref, actor):
            if link.attachment_policy is AttachmentPolicy.DETACHED:
                continue
            child_ref = RunRef(link.child_run_id, ref.expected_session_id)
            child = await self._ledger.query(child_ref, actor)
            if isinstance(child, RunRecord) and child.status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                await self.cancel(child_ref, actor, f"parent_cancel:{ref.run_id}")
        registration = self._driver(updated)
        acknowledged = await self._consume(
            registration,
            updated,
            registration.driver.cancel(updated.run_id, reason),
        )
        if acknowledged:
            await self._commit_terminal(
                updated,
                registration.kind,
                DriverTerminalCandidate(
                    run_id=updated.run_id,
                    status="cancelled",
                    error=reason,
                ),
            )
            current = await self._ledger.query(ref, actor)
            if isinstance(current, RunRecord):
                updated = current
        return CancelReceipt(ref.run_id, updated.status, acknowledged)

    async def recover(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> RunHandle:
        record = await self._ledger.authorize(ref, actor, "recover")
        if not isinstance(record, RunRecord):
            raise RuntimeError("legacy runs require the compatibility recovery adapter")
        registration = self._driver(record)
        async with self._lock:
            active = self._active.setdefault(ref.run_id, _ActiveRun(actor=actor))
            if active.task is None or active.task.done():
                active.task = asyncio.create_task(
                    self._drive(
                        registration,
                        record,
                        registration.driver.recover(record.run_id),
                    ),
                    name=f"deskpet-recover:{ref.run_id}",
                )
        return RunHandle(
            ref, record.context.root_run_id, registration.kind, record.spec.profile_key
        )

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

    def _driver(self, record: RunRecord) -> RegisteredDriver:
        registration = self._drivers.get(record.spec.driver_kind)
        if registration is None:
            raise RuntimeError(f"driver is not registered: {record.spec.driver_kind}")
        return registration

    @staticmethod
    def _driver_start(
        spec: RunCreate,
        request: RunRequest,
        profile_key: str,
        host: HostContext,
    ) -> DriverStart:
        return DriverStart(
            run_id=spec.run_id,
            session_id=spec.context.session_id,
            canonical_messages=({"role": "user", "content": request.text},),
            provider_state=dict(spec.context.provider_plan),
            run_context=spec.context,
            profile_key=profile_key,
            request_payload={"text": request.text, **dict(request.payload)},
            capability_snapshot={
                "capabilities": sorted(host.available_capabilities),
                "capability_hash": host.capability_hash,
            },
        )

    async def _emit(self, event: RunEvent) -> None:
        async with self._lock:
            active = self._active.get(event.run_id)
            if active is None:
                return
            if not any(item.event_id == event.event_id for item in active.events):
                active.events.append(event)
            subscribers = tuple(active.subscribers)
        for queue in subscribers:
            queue.put_nowait(event)

    async def _emit_live(
        self,
        record: RunRecord,
        candidate: RunEventCandidate,
    ) -> None:
        async with self._lock:
            active = self._active.setdefault(
                record.run_id,
                _ActiveRun(
                    actor=ActorContext(
                        principal_id=record.context.principal_id,
                        session_id=record.context.session_id,
                        auth_epoch=record.context.auth_epoch,
                        root_run_id=record.context.root_run_id,
                    )
                ),
            )
            live_seq = len(active.events) + 1
            event = RunEvent(
                event_id=f"live:{record.run_id}:{live_seq}",
                run_id=record.run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor(f"kernel:{record.run_id}", live_seq),
                candidate=candidate,
                created_at=time.time(),
            )
            active.events.append(event)
            subscribers = tuple(active.subscribers)
        for queue in subscribers:
            queue.put_nowait(event)

    async def _drive(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidates: AsyncIterator[DriverCandidate],
    ) -> None:
        try:
            await self._consume(registration, record, candidates)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._commit_terminal(
                record,
                registration.kind,
                DriverTerminalCandidate(
                    run_id=record.run_id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                ),
            )

    async def _consume(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidates: AsyncIterator[DriverCandidate],
    ) -> bool:
        acknowledged = False
        async for candidate in candidates:
            acknowledged = (
                await self._consume_candidate(registration, record, candidate)
                or acknowledged
            )
        return acknowledged

    async def _consume_candidate(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidate: DriverCandidate,
    ) -> bool:
        if candidate.run_id != record.run_id:
            raise ValueError("driver candidate run binding mismatch")
        if isinstance(candidate, PersistedEventCandidate):
            await self._emit(candidate.event)
            return False
        if isinstance(candidate, DriverTerminalCandidate):
            await self._commit_terminal(record, registration.kind, candidate)
            return False
        if isinstance(candidate, DelegateRun) and self._child_runs is not None:
            await self._child_runs.submit(record, candidate)
            if self._child_launcher is not None:
                await self._child_runs.run_scheduler_once(
                    self._child_launcher,
                    owner=self._child_scheduler_owner,
                )
            await self._drain_child_signals(registration, record)
        acknowledged = isinstance(candidate, CancelAcknowledgedCandidate)
        event = self._event_candidate(registration.kind, candidate)
        if event is not None:
            await self._emit_live(record, event)
        return acknowledged

    async def _drain_child_signals(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
    ) -> None:
        if self._child_runs is None:
            return
        for delivery in await self._child_runs.pending_signals(record.run_id):
            await self._consume(
                registration,
                record,
                registration.driver.signal(delivery.signal),
            )
            await self._child_runs.acknowledge_signal(delivery.record.signal_id)

    @staticmethod
    def _event_candidate(
        driver_kind: str,
        candidate: DriverCandidate,
    ) -> RunEventCandidate | None:
        if isinstance(candidate, TokenCandidate):
            return RunEventCandidate(
                event_key=f"token:{uuid.uuid4().hex}",
                kind="transcript",
                status=OutcomeStatus.SUCCEEDED,
                driver_kind=driver_kind,
                payload={"text": candidate.content, "token_kind": candidate.kind},
            )
        if isinstance(candidate, ProviderFallbackCandidate):
            return RunEventCandidate(
                event_key=f"fallback:{candidate.from_provider}:{candidate.to_provider}",
                kind="provider_fallback",
                status=OutcomeStatus.ACCEPTED,
                driver_kind=driver_kind,
                payload={
                    "from_provider": candidate.from_provider,
                    "to_provider": candidate.to_provider,
                    "reason": candidate.reason,
                },
            )
        if isinstance(candidate, ExecuteTools):
            return RunEventCandidate(
                event_key=f"tools:{candidate.command_id}",
                kind="tool_requested",
                status=OutcomeStatus.WAITING,
                driver_kind=driver_kind,
                correlation={"command_id": candidate.command_id},
                payload={"tools": [call.tool_name for call in candidate.calls]},
            )
        if isinstance(candidate, OpenDecision):
            return RunEventCandidate(
                event_key=f"decision:{candidate.decision_id}",
                kind="decision",
                status=OutcomeStatus.WAITING,
                driver_kind=driver_kind,
                correlation={
                    "command_id": candidate.command_id,
                    "decision_id": candidate.decision_id,
                },
                payload={"kind": candidate.kind, "prompt": dict(candidate.prompt)},
            )
        if isinstance(candidate, DelegateRun):
            return RunEventCandidate(
                event_key=f"delegate:{candidate.command_id}",
                kind="delegate_requested",
                status=OutcomeStatus.WAITING,
                driver_kind=driver_kind,
                correlation={"command_id": candidate.command_id},
                payload={
                    "route_hint": candidate.route_hint,
                    "join_policy": candidate.join_policy.value,
                    "attachment_policy": candidate.attachment_policy.value,
                },
            )
        if isinstance(candidate, ChildAcceptedCandidate):
            return RunEventCandidate(
                event_key=f"child:{candidate.command_id}:{candidate.child_run_id}",
                kind="child_accepted",
                status=OutcomeStatus.ACCEPTED,
                driver_kind=driver_kind,
                correlation={
                    "command_id": candidate.command_id,
                    "child_run_id": candidate.child_run_id,
                },
                payload={"join_policy": candidate.join_policy.value},
            )
        if isinstance(candidate, CancelAcknowledgedCandidate):
            return RunEventCandidate(
                event_key=f"cancel-ack:{candidate.run_id}",
                kind="cancel_acknowledged",
                status=OutcomeStatus.CANCELLED,
                driver_kind=driver_kind,
                payload={"reason": candidate.reason},
            )
        return None

    async def _commit_terminal(
        self,
        record: RunRecord,
        driver_kind: str,
        terminal: DriverTerminalCandidate,
    ) -> None:
        current = await self._ledger.query(
            RunRef(record.run_id, record.context.session_id),
            ActorContext(
                principal_id=record.context.principal_id,
                session_id=record.context.session_id,
                auth_epoch=record.context.auth_epoch,
                root_run_id=record.context.root_run_id,
            ),
        )
        if not isinstance(current, RunRecord):
            raise RuntimeError("legacy run cannot be finalized by the new driver")
        status = RunStatus(terminal.status)
        outcome = {
            RunStatus.COMPLETED: OutcomeStatus.SUCCEEDED,
            RunStatus.FAILED: OutcomeStatus.FAILED,
            RunStatus.CANCELLED: OutcomeStatus.CANCELLED,
        }[status]
        result = await self._ledger.finalize(
            record.run_id,
            expected_version=current.version,
            terminal_status=status,
            event=RunEventCandidate(
                event_key=f"terminal:{record.run_id}",
                kind="final",
                status=outcome,
                driver_kind=driver_kind,
                correlation=dict(terminal.correlation),
                payload={"text": terminal.content},
                error=(
                    None
                    if terminal.error is None
                    else {"code": "driver_failed", "message": terminal.error}
                ),
            ),
        )
        await self._emit(result.event)


def kernel_public_operations() -> tuple[str, ...]:
    return ("start", "observe", "signal", "cancel", "recover", "close")


__all__ = [
    "CancelReceipt",
    "HostContext",
    "RegisteredDriver",
    "RunHandle",
    "RunKernel",
    "RunRequest",
    "SignalReceipt",
    "kernel_public_operations",
]
