from __future__ import annotations

import asyncio

import pytest

from deskpet.execution import (
    ActorContext,
    LiveCursor,
    OutcomeStatus,
    RunEvent,
    RunEventCandidate,
)
from deskpet.harness.live_index import BoundedLiveIndex, LiveStreamOverflow


ACTOR = ActorContext("user", "session", 0, "root")


def _event(seq: int) -> RunEvent:
    return RunEvent(
        event_id=f"event-{seq}",
        run_id="run",
        root_run_id="root",
        session_id="session",
        durable_seq=None,
        live_cursor=LiveCursor("epoch", seq),
        candidate=RunEventCandidate(
            event_key=f"event-{seq}",
            kind="transcript",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
        ),
        created_at=float(seq),
    )


def test_live_history_is_bounded_and_stale_cursor_fails_closed() -> None:
    index = BoundedLiveIndex(max_runs=1, max_events_per_run=2)
    active = index.add("run", ACTOR)
    for seq in range(1, 4):
        index.publish(active, _event(seq))

    assert [event.live_cursor.live_seq for event in index.history(active, after_live_seq=1)] == [2, 3]
    with pytest.raises(LiveStreamOverflow, match="cursor fell behind"):
        index.history(active, after_live_seq=0)


@pytest.mark.asyncio
async def test_slow_subscriber_is_bounded_and_terminal_releases_task_reference() -> None:
    index = BoundedLiveIndex(max_runs=1, subscriber_queue_size=1)
    active = index.add("run", ACTOR)
    active.task = asyncio.current_task()
    queue = index.subscribe(active)

    index.publish(active, _event(1))
    index.publish(active, _event(2))
    overflow = queue.get_nowait()
    assert isinstance(overflow, LiveStreamOverflow)
    assert queue.maxsize == 1
    assert queue not in active.subscribers

    index.finish(active)
    assert active.task is None
    assert queue.empty()
