"""The one bounded in-process index for ephemeral RunKernel state."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from deskpet.execution.contracts import (
    ActiveRunCapacityExceeded,
    ActorContext,
    RunEvent,
    RunRecord,
    TERMINAL_RUN_STATUSES,
)


class LiveStreamOverflow(RuntimeError):
    pass


LiveQueueItem = RunEvent | LiveStreamOverflow | None


@dataclass(slots=True)
class LiveRun:
    actor: ActorContext
    record: RunRecord | None = None
    events: deque[RunEvent] = field(default_factory=deque)
    subscribers: set[asyncio.Queue[LiveQueueItem]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    driver_state: object | None = None
    driver_iterator: AsyncIterator[object] | None = None
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_live_seq: int = 1

class BoundedLiveIndex:
    """Bounds runs, retained events and every subscriber queue."""

    def __init__(
        self,
        *,
        max_runs: int = 4096,
        max_events_per_run: int = 256,
        subscriber_queue_size: int = 128,
    ) -> None:
        if min(max_runs, max_events_per_run, subscriber_queue_size) < 1:
            raise ValueError("live index bounds must be positive")
        self.max_runs = max_runs
        self.max_events_per_run = max_events_per_run
        self.subscriber_queue_size = subscriber_queue_size
        self._runs: dict[str, LiveRun] = {}
        self.lock = asyncio.Lock()

    def get(self, run_id: str) -> LiveRun | None:
        return self._runs.get(run_id)

    def pop(self, run_id: str) -> LiveRun | None:
        return self._runs.pop(run_id, None)

    def values(self) -> tuple[LiveRun, ...]:
        return tuple(self._runs.values())

    def add(self, run_id: str, actor: ActorContext) -> LiveRun:
        existing = self._runs.get(run_id)
        if existing is not None:
            return existing
        self.ensure_capacity()
        live = LiveRun(actor=actor, events=deque(maxlen=self.max_events_per_run))
        self._runs[run_id] = live
        return live

    def ensure_capacity(self) -> None:
        if len(self._runs) < self.max_runs:
            return
        for run_id, active in tuple(self._runs.items()):
            if active.subscribers or (active.task is not None and not active.task.done()):
                continue
            if active.record is None or active.record.status not in TERMINAL_RUN_STATUSES:
                continue
            self._runs.pop(run_id, None)
            return
        raise ActiveRunCapacityExceeded(
            "active_run_capacity_exceeded",
            "Kernel live index is full; close or finish a live run before starting another",
        )

    def history(self, active: LiveRun, *, after_live_seq: int) -> tuple[RunEvent, ...]:
        if active.events:
            oldest = active.events[0].live_cursor
            if oldest is not None and after_live_seq < oldest.live_seq - 1:
                raise LiveStreamOverflow("live cursor fell behind bounded history")
        return tuple(
            event
            for event in active.events
            if event.live_cursor is not None
            and event.live_cursor.live_seq > after_live_seq
        )

    def subscribe(self, active: LiveRun) -> asyncio.Queue[LiveQueueItem]:
        queue: asyncio.Queue[LiveQueueItem] = asyncio.Queue(
            self.subscriber_queue_size
        )
        active.subscribers.add(queue)
        return queue

    @staticmethod
    def publish(active: LiveRun, event: RunEvent) -> None:
        active.events.append(event)
        for queue in tuple(active.subscribers):
            if queue.full():
                active.subscribers.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(LiveStreamOverflow("live subscriber fell behind bounded buffer"))
            else:
                queue.put_nowait(event)

    def finish(self, run_id: str, active: LiveRun, *, release: bool = False) -> None:
        for queue in tuple(active.subscribers):
            while queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        active.task = None
        active.driver_state = None
        active.driver_iterator = None
        if release and self._runs.get(run_id) is active:
            self._runs.pop(run_id, None)

    def finish_all(self) -> None:
        for run_id, active in tuple(self._runs.items()):
            self.finish(run_id, active, release=True)

__all__ = ["BoundedLiveIndex", "LiveRun", "LiveStreamOverflow"]
