"""Durable child commands and parent-signal replay."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from deskpet.execution.contracts import (
    AttachmentPolicy,
    ChildCommandIntent,
    ChildCommandRecord,
    ChildSignalRecord,
    PersistenceLevel,
    RecoveryLease,
    RunContext,
    RunCreate,
    RunRecord,
    RunStatus,
    delegate_idempotency_key,
    fingerprint_json,
)
from deskpet.execution.contracts import TERMINAL_RUN_STATUSES, thaw_json
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.ports import (
    ChildAcceptedSignal,
    ChildTerminalSignal,
    DriverEvent,
    DriverSignal,
    JoinPolicy,
)


class ChildLauncher(Protocol):
    async def accept(self, command: ChildCommandRecord) -> None: ...

    async def deliver(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
    ) -> None: ...


class ChildRunCoordinator:
    def __init__(self, store: ExecutionUnitOfWork) -> None:
        self._store = store
        self._wakeup = asyncio.Event()

    @staticmethod
    def _intent(parent: RunRecord, command: DriverEvent) -> ChildCommandIntent:
        if parent.run_id != command.run_id:
            raise ValueError("delegate command names another parent run")
        if parent.persistence_level is not PersistenceLevel.DURABLE:
            raise ValueError("delegate parent must cross a durable boundary first")
        attachment = {
            JoinPolicy.JOIN_BEFORE_FINAL: AttachmentPolicy.ATTACHED,
            JoinPolicy.ROOT_TERMINAL_CHILD: AttachmentPolicy.ROOT_TERMINAL_CHILD,
            JoinPolicy.DETACHED: AttachmentPolicy.DETACHED,
        }[JoinPolicy(command.join_policy)]
        if command.attachment_policy.value != attachment.value:
            raise ValueError("delegate attachment and join policy disagree")
        operation_id = hashlib.sha256(
            f"execution-child-operation|{parent.run_id}|{command.command_id}".encode()
        ).hexdigest()
        child_run_id = "child-" + hashlib.sha256(
            f"execution-child-run|{parent.run_id}|{command.command_id}".encode()
        ).hexdigest()[:32]
        child_request = thaw_json(command.child_request)
        assert isinstance(child_request, dict)
        capability_subset = tuple(sorted(set(command.capability_subset)))
        capability_snapshot = {"tools": list(capability_subset)}
        capability_ref = fingerprint_json(capability_snapshot)
        context = parent.context
        child_spec = RunCreate(
            run_id=child_run_id,
            idempotency_key=delegate_idempotency_key(
                parent.run_id, command.command_id, command.route_hint
            ),
            context=RunContext(
                session_id=context.session_id,
                root_run_id=context.root_run_id,
                parent_run_id=parent.run_id,
                request_id=f"{context.request_id}:child:{command.command_id}",
                turn_id=context.turn_id,
                venue=context.venue,
                workspace=thaw_json(context.workspace),
                capability_hash=capability_ref,
                provider_plan=thaw_json(context.provider_plan),
                trace_id=f"child-trace-{operation_id[:32]}",
                principal_id=context.principal_id,
                auth_epoch=context.auth_epoch,
            ),
            payload_fingerprint=fingerprint_json(child_request),
            capability_fingerprint=capability_ref,
            driver_kind=str(child_request.get("driver_kind") or "react"),
            profile_key=command.route_hint,
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.QUEUED,
        )
        return ChildCommandIntent(
            operation_id=operation_id,
            parent_run_id=parent.run_id,
            command_id=command.command_id,
            child_spec=child_spec,
            child_request=child_request,
            capability_subset=capability_subset,
            attachment_policy=attachment,
            capability_snapshot_ref=capability_ref,
        )

    async def submit(
        self, parent: RunRecord, command: DriverEvent, *, recovery_lease: RecoveryLease | None = None
    ) -> ChildCommandRecord:
        record = await self._store.commit_child_command(
            self._intent(parent, command), recovery_lease=recovery_lease
        )
        self._wakeup.set()
        return record

class ChildRunScheduler:
    """Replay durable child commands and parent inbox signals without inline owners."""

    def __init__(
        self, coordinator: ChildRunCoordinator, launcher: ChildLauncher, *, owner: str
    ) -> None:
        self._coordinator, self._launcher, self._owner = coordinator, launcher, owner
        self._store = coordinator._store
        self._task: asyncio.Task[None] | None = None
        self.last_errors: tuple[str, ...] = ()

    async def reconcile_commands_once(
        self,
        *,
        limit: int = 16,
        lease_seconds: float = 30.0,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        parent_run_id = recovery_lease.run_id if recovery_lease is not None else None
        leased = await self._store.lease_child_commands(
            owner=self._owner, limit=limit, lease_seconds=lease_seconds,
            parent_run_id=parent_run_id, recovery_lease=recovery_lease,
        )
        errors: list[str] = []
        for command in leased:
            try:
                scheduled = await self._store.schedule_child_command(
                    command.operation_id, lease_owner=self._owner,
                    lease_epoch=command.schedule_lease_epoch,
                    recovery_lease=recovery_lease,
                )
                await self._launcher.accept(scheduled)
                await self._store.acknowledge_child_command(
                    scheduled.operation_id, lease_owner=self._owner,
                    lease_epoch=scheduled.schedule_lease_epoch,
                    recovery_lease=recovery_lease,
                )
            except Exception as exc:
                errors.append(
                    f"{command.operation_id}:{type(exc).__name__}:"
                    f"{getattr(exc, 'code', '')}:{exc}"
                )
                continue
        self.last_errors = tuple(errors)

    async def reconcile_signals_once(
        self, *, recovery_lease: RecoveryLease | None = None
    ) -> None:
        errors: list[str] = []
        parent_run_id = recovery_lease.run_id if recovery_lease is not None else None
        parents = await self._store.list_pending_child_signal_parents(limit=10000)
        for parent in parents:
            if parent_run_id is not None and parent.run_id != parent_run_id:
                continue
            if parent.status in TERMINAL_RUN_STATUSES:
                records = await self._store.list_pending_child_signals(parent.run_id)
                for record in records:
                    try:
                        await self._store.ack_child_signal(
                            record.signal_id
                        )
                    except Exception as exc:
                        errors.append(
                            f"{record.signal_id}:{type(exc).__name__}:"
                            f"{getattr(exc, 'code', '')}:{exc}"
                        )
                continue
            try:
                lease = recovery_lease or await self._store.recovery_scope(
                    parent.run_id, owner=f"{self._owner}:signal"
                )
            except Exception as exc:
                errors.append(f"{parent.run_id}:{type(exc).__name__}:{exc}")
                continue
            try:
                records = await self._store.list_pending_child_signals(
                    parent.run_id, recovery_lease=lease
                )
                for record in records:
                    try:
                        if record.kind == "accepted":
                            signal: DriverSignal = ChildAcceptedSignal(
                                record.parent_run_id, record.command_id,
                                record.child_run_id, record.signal_id,
                            )
                        else:
                            signal = ChildTerminalSignal(
                                record.parent_run_id, record.command_id,
                                record.child_run_id, str(record.payload["status"]),
                                record.payload.get("value"), record.signal_id,
                            )
                        await self._launcher.deliver(parent, signal, lease)
                    except Exception as exc:
                        errors.append(f"{record.signal_id}:{type(exc).__name__}:{exc}")
            finally:
                if recovery_lease is None:
                    await self._store.recovery_scope(lease, lease_seconds=None)
        self.last_errors = tuple(errors)

    async def start(self, *, interval: float = 0.25) -> None:
        if self._task is not None and not self._task.done():
            return
        self._coordinator._wakeup.clear()

        async def run() -> None:
            while True:
                try:
                    await asyncio.wait_for(
                        self._coordinator._wakeup.wait(), timeout=interval
                    )
                except TimeoutError:
                    pass
                self._coordinator._wakeup.clear()
                try:
                    await self.reconcile_commands_once()
                    command_errors = self.last_errors
                    await self.reconcile_signals_once()
                    self.last_errors = command_errors + self.last_errors
                except Exception as exc:
                    self.last_errors = (f"scheduler:{type(exc).__name__}:{exc}",)

        self._task = asyncio.create_task(run(), name=f"child-scheduler:{self._owner}")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
