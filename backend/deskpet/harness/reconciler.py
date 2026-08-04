"""Event-triggered durable reconciliation without a resident supervisor."""
from __future__ import annotations
import asyncio
import logging
from collections.abc import Awaitable, Callable
from deskpet.execution.contracts import (
    IdempotencyConflict, RecoveryLease, RunRef, RunStatus,
    TERMINAL_RUN_STATUSES)
from deskpet.execution.uow_ports import ReconciliationUnitOfWork
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.kernel import RunHandle, RunKernel
from deskpet.harness.ports import ChildAcceptedSignal, ChildTerminalSignal, DriverSignal
from deskpet.harness.projector import ExecutionDeliveryDispatcher
from deskpet.harness.tool_executor import EffectBatchExecutor
logger = logging.getLogger(__name__)
class HarnessReconciler:
    """Run bounded recovery work only when startup or a durable event asks."""
    def __init__(self, uow: ReconciliationUnitOfWork, kernel: RunKernel | None,
                 effects: EffectBatchExecutor | None = None, *,
                 coordinator: ChildRunCoordinator | None = None,
                 delivery: ExecutionDeliveryDispatcher | None = None,
                 owner: str = "harness", item_timeout: float = 5.0,
                 batch_limit: int = 16) -> None:
        if min(item_timeout, batch_limit) <= 0:
            raise ValueError("reconciliation budgets must be positive")
        if coordinator is not None and kernel is None:
            raise ValueError("child coordinator requires the Kernel child boundary")
        self._uow, self._kernel, self._effects = uow, kernel, effects
        self._child_enabled, self._delivery = coordinator is not None, delivery
        self._owner, self._item_timeout = owner, item_timeout
        self._batch_limit = batch_limit
        self._idle = asyncio.Event()
        self._idle.set()
        self._dirty = self._worker_running = self._closing = False
        self._cancel_worker: Callable[[], bool] | None = None
        self._retry_timer: asyncio.TimerHandle | None = None
        self.last_errors: tuple[str, ...] = ()
        self.last_fatal_recovery_errors: tuple[str, ...] = ()
    def trigger(self) -> None:
        """Coalesce durable events into one short-lived reconciliation worker."""
        if self._closing:
            return
        self._dirty = True
        if self._worker_running:
            return
        self._worker_running = True
        self._idle.clear()
        async def run_worker() -> None:
            try:
                while self._dirty and not self._closing:
                    self._dirty = False
                    await self.reconcile_all()
            except Exception:
                logger.exception("event-triggered harness reconciliation failed")
            finally:
                restart = self._dirty and not self._closing
                self._worker_running = False
                self._cancel_worker = None
                if restart:
                    self.trigger()
                else:
                    self._idle.set()
        worker = asyncio.get_running_loop().create_task(
            run_worker(), name="harness-reconcile-event")
        self._cancel_worker = worker.cancel
    def _schedule_after(self, delay: float) -> None:
        """Keep at most one one-shot retry alarm, never a polling task."""
        if self._closing:
            return
        loop = asyncio.get_running_loop()
        due = loop.time() + max(0.0, delay)
        if self._retry_timer is not None:
            if not self._retry_timer.cancelled() and self._retry_timer.when() <= due:
                return
            self._retry_timer.cancel()
        def wake() -> None:
            self._retry_timer = None
            self.trigger()
        self._retry_timer = loop.call_at(due, wake)
    async def drain(self, timeout: float) -> bool:
        try:
            async with asyncio.timeout(max(0.0, timeout)):
                await self._idle.wait()
        except TimeoutError:
            return False
        return True
    async def close(self, timeout: float) -> bool:
        """Stop new work and leave no reconciliation worker behind."""
        self._closing = True
        self._dirty = False
        if self._retry_timer is not None:
            self._retry_timer.cancel()
            self._retry_timer = None
        completed = await self.drain(timeout)
        if not completed and self._cancel_worker is not None:
            self._cancel_worker()
            await self._idle.wait()
        return completed
    async def reconcile_commands_once(
        self, *, limit: int = 16, lease_seconds: float = 30.0,
        recovery_lease: RecoveryLease | None = None) -> bool:
        if not self._child_enabled:
            return False
        parent_run_id = recovery_lease.run_id if recovery_lease is not None else None
        leased = await self._uow.lease_child_commands(
            owner=self._owner, limit=limit, lease_seconds=lease_seconds,
            parent_run_id=parent_run_id, recovery_lease=recovery_lease)
        errors = []
        for command in leased:
            try:
                scheduled = await self._uow.schedule_child_command(
                    command.operation_id, lease_owner=self._owner,
                    lease_epoch=command.schedule_lease_epoch,
                    recovery_lease=recovery_lease)
                await self._kernel._accept_precreated_child(scheduled)
                await self._uow.acknowledge_child_command(
                    scheduled.operation_id, lease_owner=self._owner,
                    lease_epoch=scheduled.schedule_lease_epoch,
                    recovery_lease=recovery_lease)
            except Exception as exc:
                detail = (f"{command.operation_id}:{type(exc).__name__}:"
                          f"{getattr(exc, 'code', '')}:{exc}")
                errors.append(detail)
                logger.warning(
                    "harness_child_command_reconcile_failed operation_id=%s "
                    "parent_run_id=%s child_run_id=%s error=%s",
                    command.operation_id, command.intent.parent_run_id,
                    command.child_run_id, detail, exc_info=exc)
        self.last_errors = tuple(errors)
        return len(leased) >= limit and not errors
    async def _reconcile_signals(
        self, *, parent_limit: int = 16, signal_limit: int = 16,
        recovery_lease: RecoveryLease | None = None) -> tuple[tuple[str, ...], bool]:
        if not self._child_enabled:
            return (), False
        errors: list[str] = []
        parent_run_id = recovery_lease.run_id if recovery_lease is not None else None
        parents = await self._uow.list_pending_child_signal_parents(limit=parent_limit)
        remaining = signal_limit
        for parent in parents:
            if remaining <= 0:
                break
            if parent_run_id is not None and parent.run_id != parent_run_id:
                continue
            live = getattr(self._kernel, "_live", None)
            active = None if live is None else live.get(parent.run_id)
            if (active is not None and active.task is not None
                    and not active.task.done()):
                active.task.add_done_callback(lambda _done: self.trigger())
                continue
            if (
                parent.status in TERMINAL_RUN_STATUSES
                or parent.status is RunStatus.CANCEL_REQUESTED
            ):
                records = await self._uow.list_pending_child_signals(
                    parent.run_id, limit=remaining)
                for record in records:
                    try:
                        await self._uow.ack_child_signal(record.signal_id)
                    except Exception as exc:
                        errors.append(f"{record.signal_id}:{type(exc).__name__}:"
                                      f"{getattr(exc, 'code', '')}:{exc}")
                remaining -= len(records)
                continue
            try:
                lease = recovery_lease or await self._uow.recovery_scope(
                    parent.run_id, owner=f"{self._owner}:signal")
            except Exception as exc:
                errors.append(f"{parent.run_id}:{type(exc).__name__}:{exc}")
                continue
            lease_transferred = False
            records = ()
            try:
                records = await self._uow.list_pending_child_signals(
                    parent.run_id, limit=remaining, recovery_lease=lease)
                for record in records:
                    try:
                        signal: DriverSignal = (
                            ChildAcceptedSignal(
                                record.parent_run_id, record.command_id,
                                record.child_run_id, record.signal_id)
                            if record.kind == "accepted" else ChildTerminalSignal(
                                record.parent_run_id, record.command_id,
                                record.child_run_id, str(record.payload["status"]),
                                record.payload.get("value"), record.signal_id))
                        if recovery_lease is not None:
                            await self._kernel._deliver_child_signal_inline(
                                parent, signal, lease)
                        else:
                            lease_transferred = bool(
                                await self._kernel._deliver_child_signal(
                                    parent, signal, lease))
                        if lease_transferred:
                            break
                    except Exception as exc:
                        errors.append(
                            f"{record.signal_id}:{type(exc).__name__}:{exc}")
            finally:
                if recovery_lease is None and not lease_transferred:
                    try:
                        await self._uow.recovery_scope(lease, lease_seconds=None)
                    except Exception as exc:
                        errors.append(f"{parent.run_id}:release:"
                                      f"{type(exc).__name__}:{exc}")
            remaining -= min(len(records), 1 if lease_transferred else len(records))
        saturated = len(parents) >= parent_limit or remaining <= 0
        return tuple(errors), saturated and not errors
    async def reconcile_signals_once(
        self, *, parent_limit: int = 16, signal_limit: int = 16,
        recovery_lease: RecoveryLease | None = None) -> bool:
        errors, saturated = await self._reconcile_signals(
            parent_limit=parent_limit, signal_limit=signal_limit,
            recovery_lease=recovery_lease)
        self.last_errors = errors
        return saturated
    async def recover_pending(
        self, *, limit: int = 16,
        only_run_ids: frozenset[str] | None = None) -> tuple[RunHandle, ...]:
        if self._kernel is None:
            return ()
        handles: list[RunHandle] = []
        errors: list[str] = []
        fatal_errors: list[str] = []
        live = getattr(self._kernel, "_live", None)
        now = asyncio.get_running_loop().time()
        busy_ids = frozenset(
            run_id for run_id, active in (
                () if live is None else getattr(live, "_runs", {}).items())
            if ((active.task is not None and not active.task.done())
                or active.recovery_deferred_until > now))
        fetch_limit = limit + len(busy_ids) + 1
        records = await self._uow.list_recoverable(
            limit=fetch_limit, run_ids=tuple(only_run_ids or ()))
        for record in records:
            if record.run_id not in busy_ids or live is None:
                continue
            active = live.get(record.run_id)
            if active is not None and active.task is not None:
                active.task.add_done_callback(lambda _done: self.trigger())
        eligible = [
            record for record in records
            if record.run_id not in busy_ids
        ]
        for record in eligible[:limit]:
            actor = record.context.actor()
            try:
                ref = RunRef(record.run_id, actor.session_id)
                handles.append(await self._kernel.recover(ref, actor))
            except IdempotencyConflict:
                # A live cancel/continuation intent already owns this Run.
                # Recovery must leave that durable owner alone; treating this
                # expected fence as a startup error makes a clean restart
                # impossible while cancellation is settling.
                continue
            except Exception as exc:
                detail = f"{record.run_id}:{type(exc).__name__}:{exc}"
                errors.append(detail)
                fatal_errors.append(detail)
        self.last_errors = tuple(errors)
        self.last_fatal_recovery_errors = tuple(fatal_errors)
        if len(eligible) > limit and not errors:
            self._dirty = True
        return tuple(handles)

    async def reconcile_active_budgets_once(self, *, limit: int = 16) -> bool:
        """Cancel roots whose persisted active-time budget is exhausted."""

        claim = getattr(self._uow, "claim_expired_active_budget_runs", None)
        if not callable(claim) or self._kernel is None:
            return False
        records = await claim(limit=limit)
        errors: list[str] = []
        for record in records:
            try:
                await self._kernel.cancel(
                    RunRef(record.run_id, record.context.session_id),
                    record.context.actor(),
                    "active_execution_budget_exhausted",
                )
            except Exception as exc:
                errors.append(
                    f"{record.run_id}:{type(exc).__name__}:"
                    f"{getattr(exc, 'code', '')}:{exc}"
                )
        self.last_errors = tuple(errors)
        return len(records) >= limit and not errors

    async def _schedule_next_active_budget(self) -> None:
        next_delay = getattr(self._uow, "next_active_budget_delay", None)
        if not callable(next_delay):
            return
        delay = await next_delay()
        if delay is not None:
            self._schedule_after(delay)

    async def reconcile_all(self) -> None:
        errors: list[str] = []
        async def step(name: str, operation: Awaitable[object]) -> object | None:
            self.last_errors = ()
            try:
                async with asyncio.timeout(self._item_timeout):
                    result = await operation
            except Exception as exc:
                errors.append(f"{name}:{type(exc).__name__}:{exc}")
                return None
            errors.extend(f"{name}:{error}" for error in self.last_errors)
            await asyncio.sleep(0)
            return result
        more_commands = await step(
            "child_commands",
            self.reconcile_commands_once(limit=self._batch_limit))
        more_signals = await step(
            "child_signals",
            self.reconcile_signals_once(
                parent_limit=self._batch_limit,
                signal_limit=self._batch_limit))
        more_budgets = await step(
            "active_budgets",
            self.reconcile_active_budgets_once(limit=self._batch_limit),
        )
        if self._effects is not None and (ready := self._effects.ready_run_ids()):
            for run_id in sorted(ready)[:self._batch_limit]:
                record = await step(
                    "terminal_late_effect_read",
                    self._uow.read_run(run_id),
                )
                if record is not None and record.status in TERMINAL_RUN_STATUSES:
                    await step(
                        "terminal_late_effect",
                        self._effects.quarantine_terminal_late(run_id),
                    )
        recovered = await step(
            "recovery", self.recover_pending(limit=self._batch_limit))
        if more_commands is True or more_signals is True or more_budgets is True:
            self._dirty = True
        if isinstance(recovered, tuple) and len(recovered) >= self._batch_limit:
            self._dirty = True
        if self._effects is not None and (ready := self._effects.ready_run_ids()):
            await step("effect_ready", self.recover_pending(
                limit=self._batch_limit,
                only_run_ids=frozenset(sorted(ready)[:self._batch_limit])))
        if self._delivery is not None:
            delivered = 0
            while delivered < self._batch_limit:
                processed = await step("delivery", self._delivery.run_once())
                if processed is not True:
                    break
                delivered += 1
            if delivered >= self._batch_limit:
                self._dirty = True
        await step("active_budget_schedule", self._schedule_next_active_budget())
        self.last_errors = tuple(errors)
    async def _reconcile_startup(self) -> None:
        """Drain pre-existing durable backlog before opening product ingress."""
        for _pass in range(1024):
            self._dirty = False
            await self.reconcile_all()
            if self.last_errors:
                raise RuntimeError(
                    "harness bootstrap reconciliation failed: "
                    + "; ".join(self.last_errors))
            if self.last_fatal_recovery_errors:
                raise RuntimeError(
                    "harness bootstrap recovery failed: "
                    + "; ".join(self.last_fatal_recovery_errors))
            signal_tasks = tuple(
                active.task for active in self._kernel._live.values()
                if active.task is not None and not active.task.done()
                and active.task.get_name().startswith("deskpet-child-signal:"))
            if signal_tasks:
                await asyncio.gather(*signal_tasks, return_exceptions=True)
                self._dirty = True
            if not self._dirty:
                return
        raise RuntimeError(
            "harness bootstrap reconciliation did not converge within 1024 passes")
    async def reconcile_ready(
        self, run_ids: frozenset[str], *, timeout: float) -> bool:
        if not run_ids:
            return True
        await self.recover_pending(only_run_ids=run_ids)
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while run_ids & self._effects.ready_run_ids():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await self.recover_pending(only_run_ids=run_ids)
            await asyncio.sleep(min(0.005, remaining))
        return True
__all__ = ["HarnessReconciler"]
