"""Run-owned delivery of durable child completion signals."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from deskpet.execution.contracts import (
    IdempotencyConflict,
    RecoveryLease,
    RunRecord,
    RunRef,
)

from .contracts import RegisteredDriver
from .live_index import BoundedLiveIndex
from .ports import DriverSignal
from .runtime import DriverRuntime
from .user_continuations import UserContinuationCoordinator

logger = logging.getLogger(__name__)


class ChildSignalRuntime:
    """Transfer child-signal delivery into the parent's single execution task."""

    def __init__(
        self,
        *,
        live: BoundedLiveIndex,
        runtime: DriverRuntime,
        continuations: UserContinuationCoordinator,
        heartbeat_interval: float,
        reconcile_trigger: Callable[[], None] | None = None,
    ) -> None:
        self._live = live
        self._lock = live.lock
        self._runtime = runtime
        self._continuations = continuations
        self._heartbeat_interval = heartbeat_interval
        self._reconcile_trigger = reconcile_trigger

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """Observe every background owner and wake durable recovery.

        A child completion can commit a new waiting decision before its live UI
        event is published.  If the background owner then fails, leaving the
        exception unobserved strands that decision until process restart.  The
        durable reconciler can replay the boundary immediately, so always wake
        it after the owner settles and retain the original exception in logs.
        """

        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "child_signal_owner_failed task=%s error_type=%s error=%s",
                    task.get_name(),
                    type(error).__name__,
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                )
        if self._reconcile_trigger is not None:
            try:
                self._reconcile_trigger()
            except Exception:
                # asyncio done callbacks must never leak a second exception;
                # the original child-owner failure is already preserved above.
                logger.exception(
                    "child_signal_reconcile_wakeup_failed task=%s",
                    task.get_name(),
                )

    async def deliver(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
        registration: RegisteredDriver,
    ) -> bool:
        async with self._lock:
            active = self._live.get(parent.run_id) or self._live.add(
                parent.run_id, parent.context.actor()
            )
            active.record = parent
        async with active.control_lock:
            previous = active.task
            if previous is not None and not previous.done():
                raise IdempotencyConflict(
                    "run_owner_busy",
                    "parent Run already has an active execution owner",
                )
            if previous is not None:
                active.task = None
                if previous.cancelled():
                    raise RuntimeError(
                        "previous parent Run execution owner was cancelled"
                    )
                previous.result()

            async def resume_parent() -> None:
                async with active.control_lock:
                    await self._runtime.consume_fenced(
                        registration,
                        parent,
                        lambda lease: registration.driver.signal(
                            signal, recovery_lease=lease
                        ),
                        recovery_lease,
                        heartbeat_interval=self._heartbeat_interval,
                        release=True,
                    )

            async with self._lock:
                active.task = asyncio.create_task(
                    self._continuations.run_owned(
                        active,
                        resume_parent(),
                        RunRef(parent.run_id, parent.context.session_id),
                        parent.context.actor(),
                    ),
                    name=f"deskpet-child-signal:{signal.signal_id}",
                )
                active.task.add_done_callback(self._task_done)
            # Queue the new owner before a competing signal can take the lock.
            # asyncio.Lock wakes waiters in FIFO order.
            await asyncio.sleep(0)
        return True

    async def deliver_inline(
        self,
        parent: RunRecord,
        signal: DriverSignal,
        recovery_lease: RecoveryLease,
        registration: RegisteredDriver,
    ) -> None:
        """Consume synchronously when the caller already owns the lease."""
        async with self._lock:
            active = self._live.get(parent.run_id) or self._live.add(
                parent.run_id, parent.context.actor()
            )
            active.record = parent
        async with active.control_lock:
            await self._runtime.consume_fenced(
                registration,
                parent,
                lambda lease: registration.driver.signal(
                    signal, recovery_lease=lease
                ),
                recovery_lease,
                heartbeat_interval=self._heartbeat_interval,
            )

    def in_flight(self, run_id: str) -> bool:
        active = self._live.get(run_id)
        return bool(
            active is not None
            and active.task is not None
            and not active.task.done()
            and active.task.get_name().startswith("deskpet-child-signal:")
        )


__all__ = ["ChildSignalRuntime"]
