# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``mission_changed`` pushes (plan §3.3, decision D7, HA-4).

A read-only connection looks at each Mission's status and highest event ``seq`` every
``interval`` seconds (and at once when a write entry pokes it); a change is broadcast as
``{"type": "mission_changed", "payload": {mission_id, status, last_seq}}``.  The UI then
pulls the snapshot or the next event page — the push carries no content of its own.
It never writes the library.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

Broadcast = Callable[[dict[str, Any]], Awaitable[Any]]

_QUERY = (
    "SELECT m.mission_id, m.status, "
    "(SELECT COALESCE(MAX(e.seq), 0) FROM events e WHERE e.mission_id = m.mission_id) "
    "FROM missions m WHERE m.tenant_id = ?"
)


class MissionChangePump:
    def __init__(self, service: Any, broadcast: Broadcast, *, interval: float = 1.0) -> None:
        self._service = service
        self._broadcast = broadcast
        self._interval = interval
        self._seen: dict[str, tuple[str, int]] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="mission-change-pump")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def poke(self) -> None:
        """A write entry just ran: look now instead of at the next tick."""

        self._wake.set()

    def _changes(self) -> list[dict[str, Any]]:
        path = Path(self._service.root) / "orchestrator.db"
        if not path.exists():
            return []
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = connection.execute(_QUERY, (self._service.tenant_id,)).fetchall()
        except sqlite3.Error as error:
            logger.debug("mission pump read skipped: %s", error)
            return []
        finally:
            connection.close()
        changes = []
        for mission_id, status, last_seq in rows:
            state = (str(status), int(last_seq))
            if self._seen.get(mission_id) != state:
                self._seen[mission_id] = state
                changes.append({"mission_id": mission_id, "status": state[0], "last_seq": state[1]})
        return changes

    async def _loop(self) -> None:
        while True:
            for change in self._changes():
                try:
                    await self._broadcast({"type": "mission_changed", "payload": change})
                except Exception as error:  # noqa: BLE001 - a dead socket must not stop pushes
                    logger.debug("mission_changed broadcast failed: %s", error)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except TimeoutError:
                pass
            self._wake.clear()


__all__ = ("MissionChangePump",)
