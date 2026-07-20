"""Recovery coordinator for execution-owned durable runs."""

from __future__ import annotations

from dataclasses import dataclass

from deskpet.execution.contracts import ActorContext, RunRef
from deskpet.execution.ports import ExecutionUnitOfWork
from deskpet.harness.kernel import RunHandle, RunKernel


@dataclass(frozen=True, slots=True)
class RecoveryBatch:
    handles: tuple[RunHandle, ...]

    @property
    def count(self) -> int:
        return len(self.handles)


class HarnessRecoveryCoordinator:
    def __init__(self, uow: ExecutionUnitOfWork, kernel: RunKernel) -> None:
        self._uow = uow
        self._kernel = kernel

    async def recover_pending(self, *, limit: int = 10_000) -> RecoveryBatch:
        handles: list[RunHandle] = []
        for record in await self._uow.list_recoverable(limit=limit):
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
        return RecoveryBatch(tuple(handles))


__all__ = ["HarnessRecoveryCoordinator", "RecoveryBatch"]
