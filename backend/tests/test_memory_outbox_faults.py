import sqlite3
import time
from types import SimpleNamespace

import pytest

from deskpet.memory.product_outbox import ProductMemoryDispatcher, ProductMemoryOutboxRepository
from deskpet.memory.session_db import SessionDB


class MemoryTransient(RuntimeError):
    code = "transient"


class ApplyThenCrashSink:
    def __init__(self):
        self.applied = set()
        self.crashed = False

    async def append_message(
        self,
        session_id,
        role,
        content,
        *,
        user_id,
        source_event_id,
        payload_hash,
    ):
        self.applied.add((source_event_id, payload_hash))
        if not self.crashed:
            self.crashed = True
            raise MemoryTransient("apply-before-ack")
        return SimpleNamespace(
            source_event_id=source_event_id,
            payload_hash=payload_hash,
            status="already_applied",
            record_id="record-1",
        )


@pytest.mark.asyncio
async def test_append_success_ack_crash_replay_converges(tmp_path):
    session = SessionDB(tmp_path / "state.db")
    await session.initialize()
    await session.append_message("session-a", "user", "crash-cut", user_id="user-a")
    clock = [time.time() + 1.0]
    dispatcher = ProductMemoryDispatcher(
        ProductMemoryOutboxRepository(tmp_path / "state.db"),
        ApplyThenCrashSink(),
        owner_id="test-owner",
        clock=lambda: clock[0],
    )
    assert await dispatcher.dispatch_once() == 1
    clock[0] += 200.0
    assert await dispatcher.dispatch_once() == 1
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            "SELECT status,attempt FROM product_memory_outbox"
        ).fetchone() == ("applied", 2)
    await session.close()
