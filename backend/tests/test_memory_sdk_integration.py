"""SessionDB 会话账本恢复 smoke（记忆 SDK 接入 slice 1）。"""
from __future__ import annotations

import pytest

from deskpet.memory.session_db import SessionDB


@pytest.mark.asyncio
async def test_session_db_restored_import() -> None:
    assert SessionDB is not None
    assert hasattr(SessionDB, "append_message")
    assert hasattr(SessionDB, "get_recent_messages")
    assert hasattr(SessionDB, "ensure_session")


@pytest.mark.asyncio
async def test_session_db_initialize_and_roundtrip(tmp_path) -> None:
    db = SessionDB(str(tmp_path / "state.db"))
    await db.initialize()
    sid = await db.ensure_session("smoke-session-1")
    msg_id = await db.append_message(sid, "user", "我养了一只叫Max的狗")
    msgs = await db.get_recent_messages(sid, limit=10)
    assert msg_id == 1
    assert len(msgs) == 1
    assert msgs[0]["content"] == "我养了一只叫Max的狗"
    await db.close()
