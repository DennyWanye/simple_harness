import sqlite3
import time

import pytest

from deskpet.memory.product_outbox import ProductMemoryDispatcher, ProductMemoryOutboxRepository
from deskpet.memory.session_db import SessionDB
from simple_harness.runtime import ConversationMemoryApplyResult, ConversationMemoryApplyStatus, ConversationMemoryError, ConversationMemoryErrorCode


class ApplyThenCrashSink:
    def __init__(self):
        self.applied = set()
        self.crashed = False

    async def apply(self, intent):
        self.applied.add((intent.source_event_id, intent.payload_hash))
        if not self.crashed:
            self.crashed = True
            raise ConversationMemoryError(ConversationMemoryErrorCode.TRANSIENT)
        return ConversationMemoryApplyResult(
            intent.source_event_id,
            intent.payload_hash,
            ConversationMemoryApplyStatus.ALREADY_APPLIED,
            "record-1",
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
