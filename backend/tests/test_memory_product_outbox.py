import sqlite3

import pytest

from deskpet.memory.product_outbox import ProductMemoryOutboxRepository
from deskpet.memory.session_db import SessionDB
from simple_harness_memory import MemoryManager


@pytest.mark.asyncio
async def test_session_close_drains_dispatcher_and_closes_manager_exactly_once(
    tmp_path,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    async def append_message(_session_id, _role, _content, **kwargs):
        return SimpleNamespace(
            source_event_id=kwargs["source_event_id"],
            payload_hash=kwargs["payload_hash"],
        )

    manager = SimpleNamespace(
        append_message=AsyncMock(side_effect=append_message),
        close=AsyncMock(),
    )
    state = tmp_path / "state.db"
    session = SessionDB(state, memory_backend=manager)
    await session.initialize()
    dispatcher = session._product_memory_dispatcher  # noqa: SLF001
    dispatcher_task = dispatcher._task  # noqa: SLF001
    await session.append_message("session-a", "user", "remember on shutdown")

    await session.close(timeout_seconds=1.0)
    await session.close(timeout_seconds=1.0)

    manager.append_message.assert_awaited_once()
    manager.close.assert_awaited_once()
    assert dispatcher_task is not None and dispatcher_task.done()
    assert session._product_memory_dispatcher is None  # noqa: SLF001
    assert session._memory_backend is None  # noqa: SLF001


def test_lifespan_closes_runtime_before_session_memory_owner() -> None:
    from pathlib import Path

    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    runtime_close = source.index("await _sdk_runtime_stack.close()")
    owner_close = source.index("_owned_session_db.close(timeout_seconds=5.0)")
    assert runtime_close < owner_close


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
