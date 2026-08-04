from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.memory.session_db import SessionDB


@pytest.mark.asyncio
async def test_message_routing_context_reads_frozen_identity_together(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    await db.initialize()
    message_id = await db.append_message(
        session_id="session-a",
        role="user",
        content="remember this",
        projection_kind="user_message",
        root_run_id="root-a",
        task_scope_id="turn-a",
    )
    assert await db.get_message_routing_context(message_id) == {
        "session_id": "session-a",
        "root_run_id": "root-a",
        "task_scope_id": "turn-a",
        "role": "user",
        "projection_kind": "user_message",
    }


@pytest.mark.asyncio
async def test_message_routing_context_missing_message_is_none(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    await db.initialize()
    assert await db.get_message_routing_context(999_999) is None
