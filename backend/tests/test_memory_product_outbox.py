import sqlite3

import pytest

from deskpet.memory.product_outbox import ProductMemoryOutboxRepository
from deskpet.memory.session_db import SessionDB
from simple_harness_memory import MemoryManager


@pytest.mark.asyncio
async def test_message_and_product_intent_commit_once_then_apply(tmp_path):
    memory = await MemoryManager.build_development(tmp_path / "memory.db")
    session = SessionDB(tmp_path / "state.db", memory_backend=memory)
    await session.initialize()
    message_id = await session.append_message(
        "session-a", "user", "PRODUCT-OUTBOX-CANARY", user_id="user-a"
    )
    with sqlite3.connect(tmp_path / "state.db") as db:
        row = db.execute(
            "SELECT message_id,user_id,status,source_event_id,memory_text "
            "FROM product_memory_outbox"
        ).fetchone()
    assert row[:3] == (message_id, "user-a", "applied")
    assert row[3].endswith(f"/{message_id}")
    assert row[4] == "PRODUCT-OUTBOX-CANARY"
    recalled = await memory.recall(
        "PRODUCT-OUTBOX-CANARY", session_id="session-a", user_id="user-a"
    )
    assert [item.text for item in recalled].count("PRODUCT-OUTBOX-CANARY") == 1
    await session.close()


@pytest.mark.asyncio
async def test_dispatch_uses_frozen_user_not_later_session_owner(tmp_path):
    session = SessionDB(tmp_path / "state.db")
    await session.initialize()
    await session.append_message(
        "session-a", "assistant", "frozen-user", user_id="user-a"
    )
    with pytest.raises(RuntimeError, match="memory_session_user_conflict"):
        await session.ensure_memory_user_binding("session-a", user_id="user-b")
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute(
            "SELECT user_id FROM product_memory_outbox"
        ).fetchone()[0] == "user-a"
    await session.close()


@pytest.mark.asyncio
async def test_applied_cleanup_is_bounded_and_never_removes_pending(tmp_path):
    state_db = tmp_path / "state.db"
    session = SessionDB(state_db)
    await session.initialize()
    for index in range(3):
        await session.append_message(
            "session-a", "user", f"message-{index}", user_id="user-a"
        )
    with sqlite3.connect(state_db) as db:
        db.execute(
            "UPDATE product_memory_outbox SET status='applied',applied_at=1 "
            "WHERE message_id IN (SELECT message_id FROM product_memory_outbox "
            "ORDER BY message_id LIMIT 2)"
        )
        db.commit()
    repository = ProductMemoryOutboxRepository(state_db)
    assert await repository.cleanup_applied(before=2, limit=1) == 1
    assert await repository.counts() == {"applied": 1, "pending": 1}
    await session.close()
