"""Stateless driver-candidate interpreter used by the thin RunKernel."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from deskpet.execution.contracts import (
    ActorContext,
    AttachmentPolicy,
    GrantConsume,
    LiveCursor,
    OutcomeStatus,
    PersistenceLevel,
    RunEvent,
    RunEventCandidate,
    RunRecord,
    RunRef,
    RunStatus,
    TerminalConflict,
)
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.workflows.store import RecoveryLease

from .child_runs import ChildLauncher, ChildRunCoordinator
from .contracts import RegisteredDriver
from .live_index import BoundedLiveIndex
from .ports import (
    CancelAcknowledgedCandidate,
    ChildAcceptedCandidate,
    DelegateRun,
    DriverCandidate,
    DriverTerminalCandidate,
    ExecuteTools,
    OpenDecision,
    PersistedEventCandidate,
    ProviderFallbackCandidate,
    TokenCandidate,
    ToolOutcomesSignal,
)
from .tool_executor import UnifiedToolExecutor


class DriverRuntime:
    """Interpret driver output; durable truth remains owned by the injected UoW."""

    def __init__(
        self,
        *,
        uow: ExecutionUnitOfWork,
        live: BoundedLiveIndex,
        query: Callable[[RunRef, ActorContext], Awaitable[RunRecord | object]],
        finalize: Callable[..., Awaitable[RunEvent]],
        child_runs: ChildRunCoordinator | None = None,
        child_launcher: ChildLauncher | None = None,
        tool_executor: UnifiedToolExecutor | None = None,
        child_scheduler_owner: str = "run-kernel",
    ) -> None:
        self._uow = uow
        self._live = live
        self._query = query
        self._finalize = finalize
        self._child_runs = child_runs
        self._child_launcher = child_launcher
        self._tool_executor = tool_executor
        self._child_scheduler_owner = child_scheduler_owner

    @staticmethod
    def is_terminal_event(event: RunEvent) -> bool:
        return event.candidate.kind == "final" and event.candidate.status in {
            OutcomeStatus.SUCCEEDED,
            OutcomeStatus.FAILED,
            OutcomeStatus.CANCELLED,
        }

    async def emit(self, event: RunEvent) -> None:
        terminal = self.is_terminal_event(event)
        async with self._live.lock:
            active = self._live.get(event.run_id)
            if active is None:
                return
            if not any(item.event_id == event.event_id for item in active.events):
                self._live.publish(active, event)
            if terminal:
                self._live.finish(active)
                active.subscribers.clear()

    async def emit_live(self, record: RunRecord, candidate: RunEventCandidate) -> None:
        async with self._live.lock:
            active = self._live.get(record.run_id) or self._live.add(
                record.run_id,
                ActorContext(
                    principal_id=record.context.principal_id,
                    session_id=record.context.session_id,
                    auth_epoch=record.context.auth_epoch,
                    root_run_id=record.context.root_run_id,
                ),
            )
            live_seq = active.next_live_seq
            active.next_live_seq += 1
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
            self._live.publish(active, event)

    async def drive(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidates: AsyncIterator[DriverCandidate],
    ) -> None:
        try:
            async for candidate in candidates:
                await self.consume_candidate(registration, record, candidate)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            try:
                await self.commit_terminal(
                    record,
                    registration.kind,
                    DriverTerminalCandidate(
                        run_id=record.run_id,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                )
            except TerminalConflict:
                return

    async def drive_recovery(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidates: AsyncIterator[DriverCandidate],
        lease: RecoveryLease,
    ) -> None:
        try:
            async for candidate in candidates:
                lease = await self._uow.renew_recovery(lease)
                await self.consume_candidate(
                    registration, record, candidate, recovery_lease=lease
                )
        finally:
            await self._uow.release_recovery(lease)

    async def consume(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidates: AsyncIterator[DriverCandidate],
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> bool:
        acknowledged = False
        async for candidate in candidates:
            acknowledged = (
                await self.consume_candidate(
                    registration, record, candidate, recovery_lease=recovery_lease
                )
                or acknowledged
            )
        return acknowledged

    async def consume_candidate(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        candidate: DriverCandidate,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> bool:
        if candidate.run_id != record.run_id:
            raise ValueError("driver candidate run binding mismatch")
        if recovery_lease is not None:
            await self._uow.assert_recovery_fence(recovery_lease)
        if isinstance(candidate, (ExecuteTools, OpenDecision, DelegateRun)):
            if record.persistence_level is PersistenceLevel.EPHEMERAL:
                actor = ActorContext(
                    principal_id=record.context.principal_id,
                    session_id=record.context.session_id,
                    auth_epoch=record.context.auth_epoch,
                    root_run_id=record.context.root_run_id,
                )
                durable = await self._uow.query(
                    RunRef(record.run_id, record.context.session_id), actor
                )
                if not isinstance(durable, RunRecord):
                    raise RuntimeError("durable boundary did not promote its execution run")
                async with self._live.lock:
                    active = self._live.get(record.run_id)
                    if active is not None:
                        active.record = None
                record = durable
        if isinstance(candidate, PersistedEventCandidate):
            await self.emit(candidate.event)
            return False
        if isinstance(candidate, DriverTerminalCandidate):
            await self.commit_terminal(
                record,
                registration.kind,
                candidate,
                recovery_lease=recovery_lease,
            )
            return False
        if isinstance(candidate, ExecuteTools):
            event = self.event_candidate(registration.kind, candidate)
            assert event is not None
            await self.emit_live(record, event)
            if self._tool_executor is None:
                raise RuntimeError("tool executor is unavailable")
            authorizations = []
            refs = candidate.grant_refs or (None,) * len(candidate.calls)
            actor = ActorContext(
                principal_id=record.context.principal_id,
                session_id=record.context.session_id,
                auth_epoch=record.context.auth_epoch,
                root_run_id=record.context.root_run_id,
            )
            for call, context, grant_ref in zip(
                candidate.calls, candidate.contexts, refs
            ):
                if grant_ref is None:
                    authorizations.append(None)
                    continue
                authorizations.append(
                    await self._uow.inspect_authorization(
                        GrantConsume(
                            grant_id=grant_ref.grant_id,
                            decision_id=grant_ref.decision_id,
                            decision_nonce=grant_ref.decision_nonce,
                            run_id=record.run_id,
                            expected_session_id=record.context.session_id,
                            call_id=call.call_id,
                            effect_id=call.effect_id,
                            tool_name=call.tool_name,
                            args_hash=call.args_hash,
                            capability_hash=context.capability_hash,
                            scope_hash=context.scope_hash,
                            expected_version=grant_ref.version,
                        ),
                        actor,
                    )
                )
            outcomes = await self._tool_executor.execute_batch(
                candidate.calls,
                candidate.contexts,
                authorizations=authorizations,
            )
            return await self.consume(
                registration,
                record,
                registration.driver.signal(
                    ToolOutcomesSignal(
                        candidate.run_id,
                        candidate.command_id,
                        tuple(outcomes),
                    )
                ),
                recovery_lease=recovery_lease,
            )
        if isinstance(candidate, DelegateRun) and self._child_runs is not None:
            await self._child_runs.submit(record, candidate)
            if self._child_launcher is not None:
                await self._child_runs.run_scheduler_once(
                    self._child_launcher,
                    owner=self._child_scheduler_owner,
                )
            await self._drain_child_signals(
                registration, record, recovery_lease=recovery_lease
            )
        acknowledged = isinstance(candidate, CancelAcknowledgedCandidate)
        event = self.event_candidate(registration.kind, candidate)
        if event is not None:
            await self.emit_live(record, event)
        return acknowledged

    async def _drain_child_signals(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        if self._child_runs is None:
            return
        for delivery in await self._child_runs.pending_signals(record.run_id):
            await self.consume(
                registration,
                record,
                registration.driver.signal(delivery.signal),
                recovery_lease=recovery_lease,
            )
            await self._child_runs.acknowledge_signal(delivery.record.signal_id)

    @staticmethod
    def event_candidate(
        driver_kind: str, candidate: DriverCandidate
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

    async def commit_terminal(
        self,
        record: RunRecord,
        driver_kind: str,
        terminal: DriverTerminalCandidate,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        current = await self._query(
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
        event = await self._finalize(
            current,
            expected_version=current.version,
            status=status,
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
            recovery_lease=recovery_lease,
        )
        await self.emit(event)


__all__ = ["DriverRuntime"]
