"""Product-neutral durable decision coordination.

SQLite remains the authority.  ``DecisionWakeupCache`` contains only transient
notification Futures so a cache miss or process restart always falls back to
the durable decision row; it never stores responses or continuation payloads.
"""

from __future__ import annotations

import asyncio

from deskpet.execution.contracts import (
    ActorContext,
    DecisionAuthorization,
    DecisionOpen,
    DecisionRecord,
    DecisionSignal,
    DecisionStatus,
    GrantConsume,
    RunRef,
)
from deskpet.execution.ports import ExecutionDecisionStore


class DecisionWakeupCache:
    """The single allowed in-process decision notification owner."""

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Future[None]]] = {}

    def subscribe(self, decision_id: str) -> asyncio.Future[None]:
        decision_id = str(decision_id).strip()
        if not decision_id:
            raise ValueError("decision_id must be non-empty")
        future = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(decision_id, set()).add(future)
        return future

    def unsubscribe(
        self, decision_id: str, future: asyncio.Future[None]
    ) -> None:
        waiters = self._waiters.get(decision_id)
        if waiters is None:
            return
        waiters.discard(future)
        if not waiters:
            self._waiters.pop(decision_id, None)
        if not future.done():
            future.cancel()

    def notify(self, decision_id: str) -> None:
        for future in self._waiters.pop(decision_id, ()):
            if not future.done():
                future.set_result(None)

    def close(self) -> None:
        decision_ids = tuple(self._waiters)
        for decision_id in decision_ids:
            self.notify(decision_id)

    @property
    def waiter_count(self) -> int:
        return sum(len(waiters) for waiters in self._waiters.values())


class DecisionStore:
    """Durable decision facade plus a non-authoritative local wakeup seam."""

    def __init__(
        self,
        durable: ExecutionDecisionStore,
        wakeups: DecisionWakeupCache,
    ) -> None:
        self._durable = durable
        self._wakeups = wakeups

    async def open(
        self,
        request: DecisionOpen,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> DecisionRecord:
        return await self._durable.open_decision(
            request, actor, expected_run_version=expected_run_version
        )

    async def get(
        self,
        decision_id: str,
        *,
        ref: RunRef,
        actor: ActorContext,
    ) -> DecisionRecord:
        return await self._durable.get_decision(
            decision_id, ref=ref, actor=actor
        )

    async def resolve(
        self,
        signal: DecisionSignal,
        actor: ActorContext,
    ) -> tuple[DecisionRecord, DecisionAuthorization | None]:
        result = await self._durable.resolve_decision(signal, actor)
        self._wakeups.notify(signal.decision_id)
        return result

    async def wait(
        self,
        decision_id: str,
        *,
        ref: RunRef,
        actor: ActorContext,
    ) -> DecisionRecord:
        current = await self.get(decision_id, ref=ref, actor=actor)
        if current.status is not DecisionStatus.OPEN:
            return current
        wakeup = self._wakeups.subscribe(decision_id)
        try:
            # Close the resolve-before-subscribe race by consulting the store
            # after registration.  Restart/cache-miss follows the same path.
            current = await self.get(decision_id, ref=ref, actor=actor)
            if current.status is not DecisionStatus.OPEN:
                return current
            await asyncio.shield(wakeup)
            return await self.get(decision_id, ref=ref, actor=actor)
        finally:
            self._wakeups.unsubscribe(decision_id, wakeup)

    async def cancel_waiting(
        self,
        ref: RunRef,
        actor: ActorContext,
        *,
        expected_run_version: int,
    ) -> tuple[DecisionRecord, ...]:
        cancelled = await self._durable.cancel_open_decisions(
            ref,
            actor,
            expected_run_version=expected_run_version,
        )
        for decision in cancelled:
            self._wakeups.notify(decision.decision_id)
        return cancelled

    async def consume(
        self,
        request: GrantConsume,
        actor: ActorContext,
    ) -> DecisionAuthorization:
        return await self._durable.consume_authorization(request, actor)


__all__ = ["DecisionStore", "DecisionWakeupCache"]
