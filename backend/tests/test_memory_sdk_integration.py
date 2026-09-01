"""SessionDB 会话账本恢复 smoke（记忆 SDK 接入 slice 1）。"""
from __future__ import annotations

import pytest

from deskpet.memory.session_db import SessionDB
from simple_harness_memory import MemoryManager


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


@pytest.mark.asyncio
async def test_session_db_without_memory_backend_degrades(tmp_path) -> None:
    db = SessionDB(str(tmp_path / "state.db"))
    await db.initialize()
    await db.append_message("s1", "user", "hello")
    assert await db.recall("hello") == []
    assert await db.get_facts() == []
    assert await db.get_digital_twin() is None
    await db.close()


@pytest.mark.asyncio
async def test_session_db_wires_memory_backend(tmp_path) -> None:
    backend = await MemoryManager.build_development(
        tmp_path / "memory.db"
    )
    db = SessionDB(str(tmp_path / "state.db"), memory_backend=backend)
    await db.initialize()
    await db.append_message("s1", "user", "我养了一只叫Max的狗，很喜欢吃披萨")
    # Memory SDK 0.6 removed automatic regex fact extraction (the legacy
    # extractor is now a test-only fixture); facts require an explicit write.
    assert await db.get_facts() == []
    assert len(await db.recall("Max")) >= 1
    await db.close()


@pytest.mark.asyncio
async def test_main_wiring_construction_smoke(tmp_path) -> None:
    """slice 3: main.py 的 SessionDB + MemoryBackend 构造模式 smoke。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_db = data_dir / "state.db"
    memory_db = data_dir / "memory.db"
    from deskpet.memory.session_db import SessionDB
    from simple_harness_memory import MemoryManager
    backend = await MemoryManager.build_development(
        memory_db
    )
    sdb = SessionDB(db_path=state_db, memory_backend=backend)
    await sdb.initialize()
    await sdb.append_message("s1", "user", "我养了一只叫Max的狗，很喜欢吃披萨")
    # 0.6: no automatic fact extraction; wiring is proven by message recall.
    assert await sdb.get_facts() == []
    assert len(await sdb.recall("Max")) >= 1
    await sdb.close()
