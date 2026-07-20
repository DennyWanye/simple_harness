"""Recovery coordinator for execution-owned durable runs and effects."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from deskpet.execution.contracts import ActorContext, RunRef
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.kernel import RunHandle, RunKernel
from deskpet.harness.tool_executor import UnifiedToolExecutor


class HarnessRecoveryCoordinator:
    def __init__(self, uow: ExecutionUnitOfWork, kernel: RunKernel,
                 effects: UnifiedToolExecutor | None = None) -> None:
        self._uow = uow
        self._kernel = kernel
        self._effects = effects
        self._task: asyncio.Task[None] | None = None

    async def recover_pending(
        self,
        *,
        limit: int = 10_000,
        only_run_ids: frozenset[str] | None = None,
    ) -> tuple[RunHandle, ...]:
        handles: list[RunHandle] = []
        for record in await self._uow.list_recoverable(limit=limit):
            if only_run_ids is not None and record.run_id not in only_run_ids:
                continue
            context = record.context
            actor = ActorContext(
                principal_id=context.principal_id,
                session_id=context.session_id,
                auth_epoch=context.auth_epoch,
                root_run_id=context.root_run_id,
            )
            handles.append(
                await self._kernel.recover(
                    RunRef(record.run_id, context.session_id),
                    actor,
                )
            )
        return tuple(handles)

    async def start(self, *, interval: float = 0.05) -> None:
        effects = self._effects
        if effects is None or self._task is not None:
            return
        async def run() -> None:
            while True:
                await asyncio.sleep(interval)
                with suppress(Exception):
                    await self.recover_pending(
                        only_run_ids=effects.ready_late_run_ids())
        self._task = asyncio.create_task(run(), name="harness-effect-recovery")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None


__all__ = ["HarnessRecoveryCoordinator"]
