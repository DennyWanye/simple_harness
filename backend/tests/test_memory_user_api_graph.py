import pytest

from deskpet.memory.session_db import SessionDB
from simple_harness_memory.backends.sqlite import SQLiteMemoryBackend


@pytest.mark.asyncio
async def test_two_users_are_sql_scoped_across_append_recall_facts_and_twin(tmp_path):
    memory = SQLiteMemoryBackend(str(tmp_path / "memory.db"), auto_extract_facts=True)
    session = SessionDB(tmp_path / "state.db", memory_backend=memory)
    await session.initialize()
    await session.append_message("session-a", "user", "我养了一只叫Max的狗", user_id="user-a")
    await session.append_message("session-b", "user", "我养了一只叫Mia的猫", user_id="user-b")
    a = await session.recall("Max", "session-a", user_id="user-a")
    b = await session.recall("Max", "session-b", user_id="user-b")
    assert any("Max" in item.text for item in a)
    assert all("Max" not in item.text for item in b)
    assert all(fact.user_id == "user-a" for fact in await session.get_facts(user_id="user-a"))
    assert all(fact.user_id == "user-b" for fact in await session.get_facts(user_id="user-b"))
    await session.get_digital_twin(user_id="user-a")
    with pytest.raises(RuntimeError, match="memory_session_user_conflict"):
        await session.ensure_memory_user_binding("session-a", user_id="user-b")
    await session.close()
