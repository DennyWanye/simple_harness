"""One bounded owner for child, recovery, late-effect and delivery work."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import suppress

from deskpet.execution.contracts import (
    ActorContext, RecoveryLease, RunRef,
    TERMINAL_RUN_STATUSES,
)
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.child_runs import ChildLauncher, ChildRunCoordinator
from deskpet.harness.kernel import RunHandle, RunKernel
from deskpet.harness.ports import ChildAcceptedSignal, ChildTerminalSignal, DriverSignal
from deskpet.harness.projector import ExecutionDeliveryDispatcher
from deskpet.harness.tool_executor import EffectBatchExecutor


class HarnessSupervisor:
    """The sole task owner for every bounded background reconciliation lane."""

    def __init__(self, uow: ExecutionUnitOfWork, kernel: RunKernel | None,
                 effects: EffectBatchExecutor | None = None, *,
                 coordinator: ChildRunCoordinator | None = None,
                 launcher: ChildLauncher | None = None,
                 delivery: ExecutionDeliveryDispatcher | None = None,
                 owner: str = "harness", interval: float = 0.05,
                 item_timeout: float = 5.0, batch_limit: int = 16) -> None:
        if min(interval, item_timeout, batch_limit) <= 0:
            raise ValueError("supervisor budgets must be positive")
        if (coordinator is None) != (launcher is None):
            raise ValueError("child coordinator and launcher must be configured together")
        self._uow, self._kernel, self._effects = uow, kernel, effects
        self._launcher, self._delivery, self._owner = launcher, delivery, owner
        self._interval, self._item_timeout, self._batch_limit = interval, item_timeout, batch_limit
        self._wakeup = coordinator._wakeup if coordinator is not None else asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_errors: tuple[str, ...] = ()

    async def reconcile_commands_once(
        self, *, limit: int = 16, lease_seconds: float = 30.0,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        if self._launcher is None:
            return
        parent_run_id = recovery_lease.run_id if recovery_lease is not None else None
        leased = await self._uow.lease_child_commands(
            owner=self._owner, limit=limit, lease_seconds=lease_seconds,
            parent_run_id=parent_run_id, recovery_lease=recovery_lease,
        )
        errors = []
        for command in leased:
            try:
                scheduled = await self._uow.schedule_child_command(
                    command.operation_id, lease_owner=self._owner,
                    lease_epoch=command.schedule_lease_epoch, recovery_lease=recovery_lease,
                )
                await self._launcher.accept(scheduled)
                await self._uow.acknowledge_child_command(
                    scheduled.operation_id, lease_owner=self._owner,
                    lease_epoch=scheduled.schedule_lease_epoch, recovery_lease=recovery_lease,
                )
            except Exception as exc:
                errors.append(f"{command.operation_id}:{type(exc).__name__}:{getattr(exc, 'code', '')}:{exc}")
        self.last_errors = tuple(errors)

    async def reconcile_signals_once(
        self, *, parent_limit: int = 16, signal_limit: int = 16,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        if self._launcher is None:
            return
        errors: list[str] = []
        parent_run_id = recovery_lease.run_id if recovery_lease is not None else None
        parents = await self._uow.list_pending_child_signal_parents(limit=parent_limit)
        remaining = signal_limit
        for parent in parents:
            if remaining <= 0:
                break
            if parent_run_id is not None and parent.run_id != parent_run_id:
                continue
            if parent.status in TERMINAL_RUN_STATUSES:
                records = await self._uow.list_pending_child_signals(
                    parent.run_id, limit=remaining)
                for record in records:
                    try:
                        await self._uow.ack_child_signal(record.signal_id)
                    except Exception as exc:
                        errors.append(f"{record.signal_id}:{type(exc).__name__}:{getattr(exc, 'code', '')}:{exc}")
                remaining -= len(records)
                continue
            try:
                lease = recovery_lease or await self._uow.recovery_scope(
                    parent.run_id, owner=f"{self._owner}:signal")
            except Exception as exc:
                errors.append(f"{parent.run_id}:{type(exc).__name__}:{exc}")
                continue
            try:
                records = await self._uow.list_pending_child_signals(
                    parent.run_id, limit=remaining, recovery_lease=lease
                )
                for record in records:
                    try:
                        signal: DriverSignal = ChildAcceptedSignal(
                            record.parent_run_id, record.command_id,
                            record.child_run_id, record.signal_id,
                        ) if record.kind == "accepted" else ChildTerminalSignal(
                            record.parent_run_id, record.command_id, record.child_run_id,
                            str(record.payload["status"]), record.payload.get("value"),
                            record.signal_id,
                        )
                        await self._launcher.deliver(parent, signal, lease)
                    except Exception as exc:
                        errors.append(f"{record.signal_id}:{type(exc).__name__}:{exc}")
            finally:
                if recovery_lease is None:
                    await self._uow.recovery_scope(lease, lease_seconds=None)
            remaining -= len(records)
        self.last_errors = tuple(errors)

    async def recover_pending(
        self, *, limit: int = 16,
        only_run_ids: frozenset[str] | None = None,
    ) -> tuple[RunHandle, ...]:
        if self._kernel is None:
            return ()
        handles = []
        for record in await self._uow.list_recoverable(
            limit=limit, run_ids=tuple(only_run_ids or ()),
        ):
            context = record.context
            actor = ActorContext(context.principal_id, context.session_id, context.auth_epoch, context.root_run_id)
            handles.append(await self._kernel.recover(RunRef(record.run_id, context.session_id), actor))
        return tuple(handles)

    async def run_once(self) -> None:
        errors: list[str] = []
        async def step(name: str, operation: Awaitable[object]) -> None:
            try:
                async with asyncio.timeout(self._item_timeout):
                    await operation
            except Exception as exc:
                errors.append(f"{name}:{type(exc).__name__}:{exc}")
            await asyncio.sleep(0)

        self.last_errors = ()
        await step("child_commands", self.reconcile_commands_once(limit=self._batch_limit))
        errors.extend(self.last_errors)
        self.last_errors = ()
        await step("child_signals", self.reconcile_signals_once(
            parent_limit=self._batch_limit, signal_limit=self._batch_limit))
        errors.extend(self.last_errors)
        await step("recovery", self.recover_pending(limit=self._batch_limit))
        if self._effects is not None and (ready := self._effects.ready_run_ids()):
            run_ids = frozenset(sorted(ready)[:self._batch_limit])
            await step("effect_ready", self.recover_pending(
                limit=self._batch_limit, only_run_ids=run_ids))
        if self._delivery is not None:
            await step("delivery", self._delivery.run_once())
        self.last_errors = tuple(errors)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        async def run() -> None:
            loop = asyncio.get_running_loop()
            while True:
                self._wakeup.clear()
                deadline = loop.time() + self._interval
                await self.run_once()
                if self._wakeup.is_set():
                    continue
                with suppress(TimeoutError):
                    async with asyncio.timeout(max(self._interval, deadline - loop.time())):
                        await self._wakeup.wait()
        self._task = asyncio.create_task(run(), name="harness-supervisor")

    async def reconcile_ready(self, run_ids: frozenset[str], *, timeout: float) -> bool:
        if not run_ids:
            return True
        await self.recover_pending(only_run_ids=run_ids)
        if self._effects is None:
            return True
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while run_ids & self._effects.ready_run_ids():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await self.recover_pending(only_run_ids=run_ids)
            await asyncio.sleep(min(0.005, remaining))
        return True

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
