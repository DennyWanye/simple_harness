import sqlite3

import pytest

from deskpet.memory.session_db import SessionDB


@pytest.mark.asyncio
async def test_harness_and_product_provenance_have_one_authority(tmp_path):
    session = SessionDB(tmp_path / "state.db")
    await session.initialize()
    harness_id = await session.append_message(
        "session-a",
        "user",
        "harness input",
        user_id="user-a",
        memory_authority="harness",
    )
    product_id = await session.append_message(
        "session-a",
        "assistant",
        "product projection",
        user_id="user-a",
        memory_authority="product",
    )
    with sqlite3.connect(tmp_path / "state.db") as db:
        ids = [row[0] for row in db.execute(
            "SELECT message_id FROM product_memory_outbox ORDER BY message_id"
        )]
    assert ids == [product_id]
    assert harness_id not in ids
    await session.close()


@pytest.mark.asyncio
async def test_tool_workflow_and_excluded_projections_are_not_memory_ingress(tmp_path):
    session = SessionDB(tmp_path / "state.db")
    await session.initialize()
    await session.append_message(
        "session-a", "assistant", "workflow status",
        workflow_event_id="workflow:event:1", user_id="user-a",
    )
    await session.append_message(
        "session-a", "assistant", "tool envelope",
        tool_calls=[{"id": "call-1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        user_id="user-a",
    )
    await session.append_message(
        "session-a", "assistant", "excluded progress",
        projection_kind="workflow_progress", context_visibility="exclude",
        user_id="user-a",
    )
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT count(*) FROM product_memory_outbox").fetchone() == (0,)
    await session.close()
