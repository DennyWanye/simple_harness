import sqlite3

import pytest

from deskpet.memory.session_db import SessionDB


@pytest.mark.asyncio
async def test_session_memory_owner_is_created_once_and_cannot_be_rebound(tmp_path):
    state_db = tmp_path / "state.db"
    session = SessionDB(state_db)
    await session.initialize()

    await session.ensure_session("session-a", memory_user_id="user-a")
    assert await session.memory_user_for_session("session-a") == "user-a"
    with pytest.raises(RuntimeError, match="memory_session_user_conflict"):
        await session.ensure_memory_user_binding("session-a", user_id="user-b")

    with sqlite3.connect(state_db) as db:
        assert db.execute(
            "SELECT user_id FROM memory_user_bindings WHERE session_id=?",
            ("session-a",),
        ).fetchone() == ("user-a",)
    await session.close()


@pytest.mark.asyncio
async def test_harness_projection_does_not_create_product_memory_intent(tmp_path):
    state_db = tmp_path / "state.db"
    session = SessionDB(state_db)
    await session.initialize()
    await session.append_message(
        "session-a",
        "assistant",
        "already owned by harness",
        user_id="user-a",
        memory_authority="harness",
    )

    with sqlite3.connect(state_db) as db:
        assert db.execute("SELECT count(*) FROM product_memory_outbox").fetchone() == (0,)
    await session.close()
