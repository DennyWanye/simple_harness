"""Process-shared, provider-scoped asynchronous bulkheads."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(slots=True)
class ProviderSlotLease:
    """An idempotently releasable provider slot."""

    provider: str
    _slot: asyncio.Semaphore
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._slot.release()

    async def __aenter__(self) -> "ProviderSlotLease":
        return self

    async def __aexit__(self, *_exc_info) -> None:
        self.release()


class ProviderLimiter:
    """One bounded semaphore per provider, shared by the whole gateway."""

    def __init__(
        self,
        providers: Iterable[str],
        *,
        max_concurrency: int,
        max_wait_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.max_wait_s = max(0.001, float(max_wait_s))
        self._clock = clock
        self._slots = {
            str(provider): asyncio.Semaphore(self.max_concurrency)
            for provider in providers
        }

    def slot_for(self, provider: str) -> asyncio.Semaphore:
        return self._slots.setdefault(
            str(provider), asyncio.Semaphore(self.max_concurrency)
        )

    async def acquire(
        self,
        provider: str,
        deadline: float,
    ) -> ProviderSlotLease | None:
        """Acquire before the absolute deadline, or return ``None``.

        A dedicated task closes the cancellation/timeout race: if the
        semaphore acquired just as the caller was cancelled, the slot is
        synchronously returned before the cancellation propagates.
        """

        wait_s = min(self.max_wait_s, max(0.0, float(deadline) - self._clock()))
        if wait_s <= 0:
            return None
        slot = self.slot_for(provider)
        # Avoid an extra task turn on the overwhelmingly common uncontended
        # path.  ``Semaphore.acquire`` does not suspend while capacity exists,
        # so the check-and-acquire remains atomic within this event-loop turn.
        if not slot.locked():
            await slot.acquire()
            return ProviderSlotLease(str(provider), slot)
        waiter = asyncio.create_task(slot.acquire())
        async def abandon() -> None:
            if waiter.done() and not waiter.cancelled():
                try:
                    acquired = bool(waiter.result())
                except Exception:
                    acquired = False
                if acquired:
                    slot.release()
            else:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)

        try:
            await asyncio.wait_for(asyncio.shield(waiter), timeout=wait_s)
        except asyncio.TimeoutError:
            await abandon()
            return None
        except asyncio.CancelledError:
            await abandon()
            raise
        return ProviderSlotLease(str(provider), slot)


__all__ = ["ProviderLimiter", "ProviderSlotLease"]
