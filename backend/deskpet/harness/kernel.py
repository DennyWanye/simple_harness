"""Thin product-neutral RunKernel control plane."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import replace

from deskpet.execution.contracts import (
    ActorContext,
    AuthorizationError,
    AttachmentPolicy,
    ChildCommandRecord,
    DecisionConflict,
    DecisionStatus,
    GrantConsume,
    LiveCursor,
    OutcomeStatus,
    PersistenceLevel,
    RecoveryLease,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunNotFound,
    RunRecord,
    RunRef,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
    VersionConflict,
    fingerprint_json,
    root_idempotency_key,
    stable_event_id,
    DecisionSignal as DurableDecisionSignal,
    thaw_json,
)
from deskpet.execution.ports import ExecutionUnitOfWork

from .context import HostContextFactory
from .child_runs import ChildRunCoordinator
from .contracts import CancelReceipt, HostContext, RegisteredDriver, RunHandle, RunRequest, SignalReceipt, TerminalProjection
from .live_index import BoundedLiveIndex, LiveRun, LiveStreamOverflow
from .ports import (
    DecisionSignal as DriverDecisionSignal,
    Driver,
    DriverSignal,
    DriverStart,
    DriverTerminalCandidate,
)
from .router import RegisteredRouter
from .runtime import DriverRuntime
from .tool_executor import UnifiedToolExecutor


class RunKernel:
    """Coordinate identity, routing, ownership and lifecycle only."""

    def __init__(
        self,
        *,
        uow: ExecutionUnitOfWork,
        router: RegisteredRouter,
        drivers: Mapping[str, RegisteredDriver],
        context_factory: HostContextFactory | None = None,
        child_runs: ChildRunCoordinator | None = None,
        tool_executor: UnifiedToolExecutor | None = None,
        terminal_projection: TerminalProjection | None = None,
        max_live_runs: int = 4096,
        child_signal_heartbeat_interval: float = 10.0,
    ) -> None:
        self._uow = uow
        self._router = router
        self._drivers = drivers
        self._context_factory = context_factory or HostContextFactory()
        self._child_runs = child_runs
        self._tool_executor = tool_executor
        self._terminal_projection = terminal_projection
        self._child_signal_heartbeat_interval = max(
            0.001, float(child_signal_heartbeat_interval)
        )
        self._live = BoundedLiveIndex(max_runs=max_live_runs)
        self._active = self._live._runs
        self._lock = self._live.lock
        self._recovery_owner = f"kernel:{uuid.uuid4().hex}"
        self._runtime = DriverRuntime(
            uow=uow,
            live=self._live,
            query=self._query,
            finalize=self._finalize,
            child_runs=child_runs,
            tool_executor=tool_executor,
        )

    async def _drain_active(self, timeout: float) -> bool:
        """Bound shutdown work before a composition root closes its drivers."""
        async with self._lock:
            tasks = tuple(
                active.task
                for active in self._active.values()
                if active.task is not None and not active.task.done()
            )
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
        if not pending:
            return True
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return False

    @staticmethod
    def _ephemeral_record(spec: RunCreate) -> RunRecord:
        now = time.time()
        return RunRecord(
            spec=spec,
            status=spec.status,
            persistence_level=PersistenceLevel.EPHEMERAL,
            version=0,
            durable_seq=0,
            terminal_event_id=None,
            created_at=now,
            updated_at=now,
            started_at=now if spec.status is RunStatus.RUNNING else None,
        )

    @staticmethod
    def _authorize_live(ref: RunRef, actor: ActorContext, record: RunRecord) -> None:
        context = record.context
        if ref.expected_session_id != context.session_id or actor.session_id != context.session_id:
            raise AuthorizationError("actor_not_authorized", "actor does not own the run session")
        if actor.internal:
            if (
                actor.root_run_id != context.root_run_id
                or actor.capability_hash != context.capability_hash
                or actor.expires_at is None
                or actor.expires_at <= time.time()
            ):
                raise AuthorizationError("actor_not_authorized", "internal authority is stale")
            return
        if (
            actor.principal_id != context.principal_id
            or actor.auth_epoch != context.auth_epoch
            or (
                actor.root_run_id is not None
                and actor.root_run_id != context.root_run_id
            )
        ):
            raise AuthorizationError(
                "actor_not_authorized",
                "actor principal, authentication epoch, or run root differs",
            )

    async def _query(self, ref: RunRef, actor: ActorContext) -> RunRecord | object:
        async with self._lock:
            active = self._active.get(ref.run_id)
            if active is not None and active.record is not None:
                self._authorize_live(ref, actor, active.record)
                return active.record
        return await self._uow.query(ref, actor)

    async def _request_cancel(
        self,
        record: RunRecord,
        *,
        expected_version: int,
        reason: str,
        event: RunEventCandidate,
    ) -> RunRecord:
        if record.persistence_level is PersistenceLevel.DURABLE:
            return await self._uow.commit_run_outcome(
                record.run_id,
                expected_version=expected_version,
                cancel_reason=reason,
                event=event,
            )
        async with self._lock:
            active = self._active.get(record.run_id)
            current = active.record if active is not None else None
            if current is None:
                raise RunNotFound("run_not_found", f"live run does not exist: {record.run_id}")
            if current.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {current.version}",
                )
            if current.status in TERMINAL_RUN_STATUSES:
                raise TerminalConflict("run_already_terminal", "a terminal run cannot be cancelled")
            updated = replace(
                current,
                status=RunStatus.CANCEL_REQUESTED,
                version=current.version + 1,
                cancel_reason=reason,
                updated_at=time.time(),
            )
            active.record = updated
            return updated

    async def _finalize(
        self,
        record: RunRecord,
        *,
        expected_version: int,
        status: RunStatus,
        event: RunEventCandidate,
        recovery_lease: RecoveryLease | None = None,
    ) -> RunEvent:
        if record.persistence_level is PersistenceLevel.DURABLE:
            command = await self._uow.get_child_command_for_run(record.run_id)
            finalize = (
                self._uow.finalize_child_and_enqueue_parent_signal
                if command is not None else self._uow.commit_run_outcome
            )
            kwargs = dict(
                expected_version=expected_version,
                terminal_status=status,
                event=event,
                recovery_lease=recovery_lease,
            )
            if command is None and self._terminal_projection is not None:
                kwargs["deliveries"] = self._terminal_projection.deliveries(
                    record, await self._uow.list_events(record.run_id)
                )
            if command is not None:
                kwargs["value"] = thaw_json(event.payload)
            return (
                await (
                    finalize(command.operation_id, **kwargs)
                    if command is not None else finalize(record.run_id, **kwargs)
                )
            ).event
        async with self._lock:
            active = self._active.get(record.run_id)
            current = active.record if active is not None else None
            if current is None:
                raise RunNotFound("run_not_found", f"live run does not exist: {record.run_id}")
            if current.status in TERMINAL_RUN_STATUSES:
                existing = next(
                    (item for item in active.events if item.event_id == current.terminal_event_id),
                    None,
                )
                if current.status is status and existing is not None and existing.candidate == event:
                    return existing
                raise TerminalConflict("terminal_conflict", "another terminal intent already won")
            if current.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {current.version}",
                )
            now = time.time()
            terminal = RunEvent(
                event_id=stable_event_id(record.run_id, event.event_key),
                run_id=record.run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor(f"kernel:{record.run_id}", len(active.events) + 1),
                candidate=event,
                created_at=now,
            )
            active.record = replace(
                current,
                status=status,
                version=current.version + 1,
                terminal_event_id=terminal.event_id,
                updated_at=now,
                ended_at=now,
            )
            return terminal

    async def start(self, request: RunRequest, host: HostContext) -> RunHandle:
        key = root_idempotency_key(host.session_id, request.request_id, request.turn_id)
        run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{key}").hex
        ref = RunRef(run_id, host.session_id)
        actor = host.actor(root_run_id=run_id)
        async with self._lock:
            active = self._active.get(run_id)
            if active is None:
                self._live.ensure_capacity()
                active = LiveRun(actor=actor)
                self._active[run_id] = active
        async with active.start_lock:
            async with self._lock:
                existing = active.record
            if existing is None:
                try:
                    existing = await self._uow.query(ref, actor)
                except RunNotFound:
                    existing = None
            if isinstance(existing, RunRecord):
                return RunHandle(
                    ref=ref,
                    root_run_id=existing.context.root_run_id,
                    driver_kind=existing.spec.driver_kind,
                    profile_key=existing.spec.profile_key,
                )
            projection_target = (
                self._terminal_projection.resolve(host.session_id)
                if self._terminal_projection is not None else None
            )
            routing = self._router.route(
                request,
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
                    if registration.durable_from_start or projection_target is not None
                    else PersistenceLevel.EPHEMERAL
                ),
                status=RunStatus.CREATED,
            )
            association_event = (
                self._terminal_projection.association_event(spec, projection_target)
                if self._terminal_projection is not None and projection_target is not None
                else None
            )
            if registration.atomic_start:
                iterator = registration.driver.start(
                    self._driver_start(
                        spec, request, routing.profile_key, host, association_event
                    )
                )
                try:
                    first = await anext(iterator)
                except StopAsyncIteration as exc:
                    raise RuntimeError(
                        "atomic-start driver ended before durable acceptance"
                    ) from exc
                durable = await self._uow.query(ref, actor)
                if not isinstance(durable, RunRecord):
                    raise RuntimeError(
                        "atomic-start driver did not commit a durable execution run"
                    )
                await self._runtime.consume_candidate(registration, durable, first)
                async with self._lock:
                    active.task = asyncio.create_task(
                            self._runtime.drive(registration, durable, iterator),
                        name=f"deskpet-run:{run_id}",
                    )
                result_record = durable
            else:
                if spec.persistence_level is PersistenceLevel.DURABLE:
                    result = await (
                        self._uow.create(spec, initial_event=association_event)
                        if association_event is not None else self._uow.create(spec)
                    )
                    result_record = result.record
                    created = result.created
                else:
                    result_record = self._ephemeral_record(spec)
                    async with self._lock:
                        if active.record is None:
                            active.record = result_record
                            created = True
                        else:
                            result_record = active.record
                            created = False
                async with self._lock:
                    if created and (active.task is None or active.task.done()):
                        active.task = asyncio.create_task(
                            self._runtime.drive(
                                registration,
                                result_record,
                                registration.driver.start(
                                    self._driver_start(
                                        result_record.spec,
                                        request,
                                        routing.profile_key,
                                        host,
                                        association_event,
                                    )
                                ),
                            ),
                            name=f"deskpet-run:{run_id}",
                        )
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
        record = await self._query(ref, actor)
        start = max(0, int(cursor or 0))
        durable_history: tuple[RunEvent, ...] = ()
        if isinstance(record, RunRecord) and record.persistence_level is PersistenceLevel.DURABLE:
            durable_history = await self._uow.list_events(
                ref.run_id, after_durable_seq=start
            )
        queue: asyncio.Queue[RunEvent | None]
        async with self._lock:
            active = self._active.get(ref.run_id) or self._live.add(ref.run_id, actor)
            live_history = self._live.history(active, after_live_seq=start)
            seen = {event.event_id for event in durable_history}
            # A subscriber may attach after a durable terminal committed while
            # earlier token deltas still live only in the bounded in-memory
            # history.  Concatenating durable history first would project the
            # final before those earlier deltas.  Creation time is the shared
            # cross-stream ordering fact; the stable id is only a tie breaker.
            history = tuple(sorted(
                durable_history + tuple(
                    event for event in live_history if event.event_id not in seen
                ),
                key=lambda event: (
                    event.created_at,
                    self._runtime.is_terminal_event(event),
                    event.event_id,
                ),
            ))
            terminal_in_history = any(
                self._runtime.is_terminal_event(event) for event in history
            )
            queue = self._live.subscribe(active)
            if not terminal_in_history:
                pass
            else:
                active.subscribers.discard(queue)
        try:
            for event in history:
                yield event
            if terminal_in_history:
                return
            while True:
                event = await queue.get()
                if event is None:
                    break
                if isinstance(event, LiveStreamOverflow):
                    raise event
                yield event
        finally:
            async with self._lock:
                current = self._active.get(ref.run_id)
                if current is not None and queue in current.subscribers:
                    current.subscribers.discard(queue)

    async def signal(
        self,
        ref: RunRef,
        actor: ActorContext,
        signal: DriverSignal,
    ) -> SignalReceipt:
        record = await self._query(ref, actor)
        if not isinstance(record, RunRecord):
            raise RuntimeError("legacy runs require the compatibility signal adapter")
        if signal.run_id != ref.run_id:
            return SignalReceipt(False, reason="signal_run_binding_mismatch")
        registration = self._driver(record)
        driver_signal = signal
        if signal.kind == "decision":
            if signal.nonce is None or signal.version is None:
                return SignalReceipt(False, reason="decision_fence_required")
            current = await self._uow.get_decision(
                signal.decision_id,
                ref=ref,
                actor=actor,
            )
            request = current.request
            response = dict(signal.response)
            allow = bool(response.get("allow", response.get("approved", True)))
            if current.status is not DecisionStatus.OPEN:
                expected_status = DecisionStatus.ALLOWED if allow else DecisionStatus.DENIED
                if (
                    current.status is expected_status
                    and current.response_schema_version == 1
                    and dict(current.response or {}) == response
                    and current.decision_version == signal.version + 1
                    and request.nonce == signal.nonce
                ):
                    return SignalReceipt(True, duplicate=True, reason="decision_already_applied")
                raise DecisionConflict(
                    "decision_replay_conflict",
                    "resolved decision replay differs from the authoritative response",
                )
            durable_signal = DurableDecisionSignal(
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
            )
            atomic_signal = getattr(registration.driver, "signal_decision_atomically", None)
            if callable(atomic_signal):
                candidates = atomic_signal(driver_signal, durable_signal, actor)
            else:
                resolved, authorization = await self._uow.commit_decision(
                    durable_signal, actor,
                )
                response["decision_status"] = resolved.status.value
                if authorization is not None:
                    response["grant_id"] = authorization.grant_id
                    response["grant_version"] = authorization.version
                driver_signal = DriverDecisionSignal(
                    run_id=signal.run_id,
                    decision_id=signal.decision_id,
                    response=response,
                    nonce=signal.nonce,
                    version=signal.version,
                )
                candidates = registration.driver.signal(driver_signal)
        else:
            candidates = registration.driver.signal(driver_signal)
        try:
            await self._runtime.consume(
                registration,
                record,
                candidates,
            )
        except TerminalConflict:
            current = await self._query(ref, actor)
            if isinstance(current, RunRecord) and current.status in TERMINAL_RUN_STATUSES:
                return SignalReceipt(
                    True, duplicate=True, reason="terminal_already_settled"
                )
            raise
        return SignalReceipt(True)

    async def cancel(
        self,
        ref: RunRef,
        actor: ActorContext,
        reason: str,
    ) -> CancelReceipt:
        record = await self._query(ref, actor)
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
        updated = await self._request_cancel(
            record,
            expected_version=record.version,
            reason=reason,
            event=event,
        )
        if updated.persistence_level is PersistenceLevel.DURABLE:
            await self._uow.cancel_open_decisions(
                ref,
                actor,
                expected_run_version=updated.version,
            )
            links = await self._uow.list_child_links(ref, actor)
        else:
            links = ()
        for link in links:
            if link.attachment_policy is AttachmentPolicy.DETACHED:
                continue
            child_ref = RunRef(link.child_run_id, ref.expected_session_id)
            child = await self._uow.query(child_ref, actor)
            if isinstance(child, RunRecord) and child.status not in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                await self.cancel(child_ref, actor, f"parent_cancel:{ref.run_id}")
        registration = self._driver(updated)
        acknowledged = await self._runtime.consume(
            registration,
            updated,
            registration.driver.cancel(updated.run_id, reason),
        )
        if acknowledged:
            await self._runtime.commit_terminal(
                updated,
                registration.kind,
                DriverTerminalCandidate(
                    run_id=updated.run_id,
                    status="cancelled",
                    error=reason,
                ),
            )
            current = await self._query(ref, actor)
            if isinstance(current, RunRecord):
                updated = current
        return CancelReceipt(ref.run_id, updated.status, acknowledged)

    async def recover(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> RunHandle:
        record = await self._query(ref, actor)
        if not isinstance(record, RunRecord):
            raise RuntimeError("legacy runs require the compatibility recovery adapter")
        registration = self._driver(record)
        async with self._lock:
            current = self._active.get(ref.run_id)
            if current is not None and current.task is not None and not current.task.done():
                return RunHandle(
                    ref, record.context.root_run_id, registration.kind, record.spec.profile_key
                )
        lease = await self._uow.recovery_scope(
            record.run_id, owner=self._recovery_owner
        )
        async with self._lock:
            active = self._active.get(ref.run_id) or self._live.add(ref.run_id, actor)
            if active.task is None or active.task.done():
                active.task = asyncio.create_task(
                    self._runtime.drive_recovery(
                        registration,
                        record,
                        lease,
                    ),
                    name=f"deskpet-recover:{ref.run_id}",
                )
        return RunHandle(
            ref, record.context.root_run_id, registration.kind, record.spec.profile_key
        )

    async def close(self, ref: RunRef, actor: ActorContext) -> None:
        record = await self._query(ref, actor)
        async with self._lock:
            active = self._active.get(ref.run_id)
            if active is None:
                return
            self._live.finish(ref.run_id, active)
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

    async def _accept_precreated_child(self, command: ChildCommandRecord) -> None:
        spec = command.intent.child_spec
        context = spec.context
        actor = ActorContext(
            principal_id=context.principal_id,
            session_id=context.session_id,
            auth_epoch=context.auth_epoch,
            root_run_id=context.root_run_id,
        )
        record = await self._uow.query(RunRef(spec.run_id, context.session_id), actor)
        if not isinstance(record, RunRecord):
            return
        if record.status in TERMINAL_RUN_STATUSES:
            async with self._lock:
                self._active.pop(record.run_id, None)
            return
        async with self._lock:
            active = self._live.get(record.run_id) or self._live.add(record.run_id, actor)
            active.record = record
        async with active.start_lock:
            if active.task is not None and not active.task.done():
                return
            if await self._uow.load_continuation(record.run_id) is not None:
                await self.recover(RunRef(record.run_id, context.session_id), actor)
                return
            registration = self._driver(record)
            payload = thaw_json(command.intent.child_request)
            assert isinstance(payload, dict)
            text = str(payload.get("text") or payload.get("task") or payload.get("request") or "")
            start = DriverStart(
                run_id=record.run_id,
                session_id=context.session_id,
                canonical_messages=({"role": "user", "content": text},),
                provider_state=thaw_json(context.provider_plan),
                run_context=context,
                run_spec=spec,
                profile_key=spec.profile_key,
                request_payload={"text": text, **payload},
                capability_snapshot={
                    "capabilities": list(command.intent.capability_subset),
                    "capability_hash": spec.capability_fingerprint,
                },
            )
            active.task = asyncio.create_task(
                self._runtime.drive(registration, record, registration.driver.start(start)),
                name=f"deskpet-child:{command.operation_id}",
            )
            await asyncio.sleep(0)

    async def _deliver_child_signal(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
    ) -> None:
        registration = self._driver(parent)
        errors: list[BaseException] = []

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self._child_signal_heartbeat_interval)
                try:
                    await self._uow.recovery_scope(recovery_lease)
                except BaseException as exc:
                    errors.append(exc)
                    return

        heartbeat_task = asyncio.create_task(
            heartbeat(), name=f"child-signal-heartbeat:{parent.run_id}"
        )
        try:
            await self._runtime.consume(
                registration, parent,
                registration.driver.signal(
                    signal, recovery_lease=recovery_lease
                ),
                recovery_lease=recovery_lease,
            )
            if errors:
                raise errors[0]
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

    @staticmethod
    def _driver_start(
        spec: RunCreate,
        request: RunRequest,
        profile_key: str,
        host: HostContext,
        association_event: RunEventCandidate | None = None,
    ) -> DriverStart:
        return DriverStart(
            run_id=spec.run_id,
            session_id=spec.context.session_id,
            canonical_messages=(
                request.canonical_messages
                or ({"role": "user", "content": request.text},)
            ),
            provider_state=dict(spec.context.provider_plan),
            run_context=spec.context,
            run_spec=spec,
            association_event=association_event,
            profile_key=profile_key,
            request_payload={"text": request.text, **dict(request.payload)},
            capability_snapshot={
                "capabilities": sorted(host.available_capabilities),
                "capability_hash": host.capability_hash,
            },
        )

def kernel_public_operations() -> tuple[str, ...]:
    return ("start", "observe", "signal", "cancel", "recover", "close")


class KernelChildLauncher:
    """Narrow adapter from durable child commands into the existing Kernel runtime."""

    def __init__(self, kernel: RunKernel) -> None:
        self._kernel = kernel

    async def accept(self, command: ChildCommandRecord) -> None:
        await self._kernel._accept_precreated_child(command)

    async def deliver(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
    ) -> None:
        await self._kernel._deliver_child_signal(parent, signal, recovery_lease)
