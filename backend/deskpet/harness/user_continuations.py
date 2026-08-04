"""Durable FIFO steering for an already-running root Run."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from deskpet.execution.contracts import (
    ActorContext,
    RecoveryLease,
    RunRecord,
    RunRef,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    VersionConflict,
)
from deskpet.execution.uow_ports import UserContinuationUnitOfWork

from .contracts import RegisteredDriver, SignalReceipt
from .live_index import BoundedLiveIndex
from .ports import DriverSignal, DriverTerminalCandidate, UserContinuationSignal
from .runtime import DriverRuntime

logger = logging.getLogger(__name__)


class UserContinuationCoordinator:
    """Serialize Driver ownership while conversation ingress remains nonblocking."""

    def __init__(
        self, *, uow: UserContinuationUnitOfWork, live: BoundedLiveIndex,
        drivers: Mapping[str, RegisteredDriver],
        query: Callable[[RunRef, ActorContext], Awaitable[RunRecord]],
        observer: Callable[
            [RunRecord, UserContinuationSignal, str, str | None],
            object,
        ] | None = None,
    ) -> None:
        self._uow = uow
        self._live = live
        self._drivers = drivers
        self._query = query
        self._observer = observer
        self._runtime: DriverRuntime | None = None

    async def _notify(
        self,
        record: RunRecord,
        signal: UserContinuationSignal,
        status: str,
        error: str | None = None,
    ) -> None:
        if self._observer is None:
            return
        try:
            result = self._observer(record, signal, status, error)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001
            # UI acknowledgement is a derived projection. It must never
            # change the durable continuation result.
            logger.exception(
                "user_continuation_observer_failed",
                extra={
                    "run_id": record.run_id,
                    "message_ref": signal.message_ref,
                    "status": status,
                },
            )

    def bind_runtime(self, runtime: DriverRuntime) -> None:
        if self._runtime is not None:
            raise RuntimeError("continuation runtime is already bound")
        self._runtime = runtime

    @property
    def runtime(self) -> DriverRuntime:
        if self._runtime is None:
            raise RuntimeError("continuation runtime is not bound")
        return self._runtime

    async def run_owned(
        self, active, operation: Awaitable[None],
        ref: RunRef, actor: ActorContext,
    ) -> None:
        async with active.driver_lock:
            await operation
            while True:
                if (
                    active.record is not None
                    and (
                        active.record.status in TERMINAL_RUN_STATUSES
                        or active.record.status is RunStatus.CANCEL_REQUESTED
                    )
                ):
                    return
                await self._drain_owned(ref, actor)
                async with active.continuation_lock:
                    if (
                        await self._uow.get_next_user_continuation(ref.run_id)
                        is not None
                    ):
                        continue
                    return

    async def enqueue(
        self,
        record: RunRecord,
        ref: RunRef,
        actor: ActorContext,
        signal: DriverSignal,
    ) -> SignalReceipt:
        async with self._live.lock:
            active = self._live.get(record.run_id) or self._live.add(
                record.run_id, actor
            )
        _boundary, queued, duplicate = (
            await self._uow.enqueue_user_continuation(
                record.run_id,
                str(signal.message_ref),
                str(signal.content),
                task_scope_id=str(signal.task_scope_id),
                expected_boundary_version=int(
                    signal.expected_boundary_version
                ),
            )
        )
        if queued.status == "failed":
            return SignalReceipt(
                False,
                duplicate=True,
                reason="continuation_apply_failed",
            )
        if queued.status == "pending":
            await self.ensure_drain(active, ref, actor)
        return SignalReceipt(
            True,
            duplicate=duplicate,
            reason=(
                "continuation_already_bound"
                if queued.status == "bound"
                else "continuation_queued"
            ),
        )

    async def interrupt_owner(self, active) -> None:
        """Stop the one live owner and wait until it has released the Driver."""
        async with active.continuation_lock:
            owner = active.task
            if owner is None:
                return
            if owner.done():
                if active.task is owner:
                    active.task = None
                return
            if owner is asyncio.current_task():
                raise RuntimeError("the Driver owner cannot cancel itself")
            owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        async with active.continuation_lock:
            if active.task is owner:
                active.task = None

    async def _apply(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        queued,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        signal = UserContinuationSignal(
            record.run_id,
            task_scope_id=queued.task_scope_id,
            message_ref=queued.message_ref,
            content=queued.content,
            expected_boundary_version=queued.reserved_boundary_version,
            queued=True,
        )
        try:
            await self.runtime.consume(
                registration,
                record,
                registration.driver.signal(
                    signal,
                    **(
                        {}
                        if recovery_lease is None
                        else {"recovery_lease": recovery_lease}
                    ),
                ),
                recovery_lease=recovery_lease,
            )
            settled = await self._uow.get_user_continuation(
                record.run_id, queued.message_ref
            )
            if settled is None or settled.status != "bound":
                raise RuntimeError(
                    "driver returned before binding the queued continuation"
                )
            await self._notify(record, signal, "bound")
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            current = await self._uow.get_user_continuation(
                record.run_id, queued.message_ref
            )
            if current is not None and current.status != "failed":
                await self._uow.fail_user_continuation(
                    record.run_id,
                    queued.message_ref,
                    f"{type(exc).__name__}: {exc}",
                )
            await self._notify(
                record,
                signal,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
            authoritative = await self._query(
                RunRef(record.run_id, record.context.session_id),
                record.context.actor(),
            )
            if authoritative.status not in TERMINAL_RUN_STATUSES:
                await self.runtime.commit_terminal(
                    authoritative,
                    registration.kind,
                    DriverTerminalCandidate(
                        run_id=record.run_id,
                        status="failed",
                        error=(
                            "user_continuation_apply_failed: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    ),
                    recovery_lease=recovery_lease,
                    current=authoritative,
                )

    async def handle_terminal(
        self,
        registration: RegisteredDriver,
        record: RunRecord,
        terminal,
        *,
        recovery_lease: RecoveryLease | None = None,
    ) -> None:
        while True:
            current = await self._query(
                RunRef(record.run_id, record.context.session_id),
                record.context.actor(),
            )
            if current.status in TERMINAL_RUN_STATUSES:
                return
            if current.status is RunStatus.CANCEL_REQUESTED:
                await self.fail_pending(
                    record.run_id, "run_cancelled", record=current
                )
                terminal = DriverTerminalCandidate(
                    run_id=record.run_id,
                    status="cancelled",
                    error=current.cancel_reason or "cancelled",
                )
            elif terminal.correlation.get("recovery_terminalization") is True:
                # A frozen run whose tool catalog no longer matches the
                # current runtime cannot safely consume newer user steering.
                # Fail queued continuations and release the root so the next
                # turn can be admitted with a fresh tool snapshot.
                failure_code = str(
                    terminal.correlation.get("failure_code")
                    or "recovery_incompatible"
                )
                await self.fail_pending(
                    record.run_id, failure_code, record=current
                )
            else:
                queued = await self._uow.get_next_user_continuation(
                    record.run_id
                )
                if queued is not None:
                    await self._apply(
                        registration,
                        current,
                        queued,
                        recovery_lease=recovery_lease,
                    )
                    return
            try:
                await self.runtime.commit_terminal(
                    current,
                    registration.kind,
                    terminal,
                    recovery_lease=recovery_lease,
                    current=current,
                )
                return
            except VersionConflict:
                # Enqueue advances the Run version in the same transaction as
                # its FIFO reservation. A lost terminal CAS must arbitrate again.
                continue

    async def _drain_owned(
        self, ref: RunRef, actor: ActorContext
    ) -> None:
        while True:
            record = await self._query(ref, actor)
            if (
                record.status in TERMINAL_RUN_STATUSES
                or record.status is RunStatus.CANCEL_REQUESTED
            ):
                return
            queued = await self._uow.get_next_user_continuation(record.run_id)
            if queued is None:
                return
            await self._apply(
                self._drivers[record.spec.driver_kind], record, queued
            )

    async def ensure_drain(
        self, active, ref: RunRef, actor: ActorContext
    ) -> None:
        async with active.continuation_lock:
            existing = active.task
            if existing is not None and not existing.done():
                return

            async def no_op() -> None:
                return None

            active.task = asyncio.create_task(
                self.run_owned(active, no_op(), ref, actor),
                name=f"deskpet-continuation:{ref.run_id}",
            )

    async def fail_pending(
        self,
        run_id: str,
        reason: str,
        *,
        record: RunRecord | None = None,
    ) -> None:
        while queued := await self._uow.get_next_user_continuation(run_id):
            await self._uow.fail_user_continuation(
                run_id, queued.message_ref, reason
            )
            if record is not None:
                await self._notify(
                    record,
                    UserContinuationSignal(
                        run_id,
                        task_scope_id=queued.task_scope_id,
                        message_ref=queued.message_ref,
                        content=queued.content,
                        expected_boundary_version=(
                            queued.reserved_boundary_version
                        ),
                        queued=True,
                    ),
                    "failed",
                    reason,
                )
__all__ = ["UserContinuationCoordinator"]
