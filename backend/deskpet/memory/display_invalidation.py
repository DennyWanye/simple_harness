"""Content-free UI invalidation, shared by the real HUMAN write producers.

This process-local generation rejects reads crossed by a Host completion. It is
not a durable SDK privacy epoch, and never authorizes a read or a business retry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


class MemoryDisplayInvalidation:
    def __init__(self, broadcast: Callable[[dict], Awaitable[None]]):
        self._broadcast = broadcast
        self.generation = 0

    async def changed(self) -> None:
        # Bump before any network await: a concurrent slow graph cannot disclose
        # its old snapshot merely because sending this refresh hint is delayed.
        self.generation += 1
        try:
            await asyncio.wait_for(
                self._broadcast({"type": "human_memory_changed", "payload": {}}),
                timeout=0.5,
            )
        except Exception:  # noqa: BLE001 - refresh failure cannot undo/retry a SDK write
            log.warning("memory_display_notification_unavailable")
