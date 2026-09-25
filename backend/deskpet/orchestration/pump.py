# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``mission_changed`` pushes (plan §3.3, decision D7, HA-4).

A read-only connection looks at each Mission's status and highest event ``seq`` every
``interval`` seconds (and at once when a write entry pokes it); a change is broadcast as
``{"type": "mission_changed", "payload": {mission_id, status, last_seq, from_seq, events,
truncated}}``.  Since 2026-09-26 (live view plan step 2) the push carries the new events
themselves, whitelisted by ``project_event`` (payloads never leave, except a person's
comment summary); ``from_seq`` lets the UI see a gap and page the rest.  At most
``EVENTS_PER_PUSH`` rows ride along; more, or a Mission seen for the first time, set
``truncated``.  It never writes the library.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .projection import project_event

logger = logging.getLogger(__name__)

Broadcast = Callable[[dict[str, Any]], Awaitable[Any]]

_QUERY = (
    "SELECT m.mission_id, m.status, "
    "(SELECT COALESCE(MAX(e.seq), 0) FROM events e WHERE e.mission_id = m.mission_id) "
    "FROM missions m WHERE m.tenant_id = ?"
)
EVENTS_PER_PUSH = 50
# ``seq<=?`` pins the rows to the last_seq read above: a write between the two queries
# must not ride along ahead of its own announcement.
_EVENTS = (
    "SELECT seq, type, created_at, task_id, attempt_id, actor_type, "
    "CASE WHEN type='HumanCommentAdded' THEN payload_json END "
    "FROM events WHERE mission_id=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?"
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
            connection.close()
            return []
        changes = []
        try:
            for mission_id, status, last_seq in rows:
                state = (str(status), int(last_seq))
                previous = self._seen.get(mission_id)
                if previous == state:
                    continue
                change: dict[str, Any] = {"mission_id": mission_id, "status": state[0], "last_seq": state[1]}
                if previous is None:  # first sight (fresh process): the UI pages what it needs
                    change.update(from_seq=state[1], events=[], truncated=True)
                else:
                    change.update(self._events(connection, mission_id, previous[1], state[1]))
                # remembered only once the push is complete: a failed event read retries next round
                self._seen[mission_id] = state
                changes.append(change)
        except sqlite3.Error as error:
            logger.debug("mission pump event read skipped: %s", error)
        finally:
            connection.close()
        return changes

    @staticmethod
    def _events(connection: sqlite3.Connection, mission_id: str, after: int, through: int) -> dict[str, Any]:
        if through <= after:  # a status change alone
            return {"from_seq": after, "events": [], "truncated": False}
        rows = connection.execute(_EVENTS, (mission_id, after, through, EVENTS_PER_PUSH + 1)).fetchall()
        events = []
        for seq, type_, created_at, task_id, attempt_id, actor_type, payload in rows[:EVENTS_PER_PUSH]:
            try:
                body = json.loads(payload) if payload else {}
            except ValueError:
                body = {}
            events.append(project_event({"seq": seq, "type": type_, "created_at": created_at, "task_id": task_id,
                                         "attempt_id": attempt_id, "actor_type": actor_type, "payload": body}))
        return {"from_seq": after, "events": events, "truncated": len(rows) > EVENTS_PER_PUSH}

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


__all__ = ("EVENTS_PER_PUSH", "MissionChangePump")
