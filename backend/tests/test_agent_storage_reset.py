import os
from pathlib import Path
import sqlite3

import pytest

from deskpet.memory.session_db import SessionDB
from scripts.reset_agent_data import AgentDataResetRefused, reset_storage_set


@pytest.mark.asyncio
async def test_dev_reset_rebuilds_three_databases_and_removes_sidecars(tmp_path, monkeypatch):
    data = tmp_path / "userdata" / "data"
    state = data / "state.db"
    execution = data / "simple-harness-sdk" / "execution-v1.sqlite3"
    memory = data / "memory.db"
    session = SessionDB(state)
    await session.initialize()
    await session.append_message("session-a", "user", "stale-pending", user_id="user-a")
    await session.close()
    for database in (state, execution, memory):
        database.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{database}-wal").write_bytes(b"stale")
        Path(f"{database}-shm").write_bytes(b"stale")
    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    await reset_storage_set(
        state_db=str(state), execution_db=str(execution), memory_db=str(memory)
    )
    assert all(path.is_file() for path in (state, execution, memory))
    assert all(not Path(f"{path}-shm").exists() for path in (state, execution, memory))
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT count(*) FROM product_memory_outbox").fetchone()[0] == 0
        assert db.execute("PRAGMA user_version").fetchone()[0] == 31
    with sqlite3.connect(execution) as db:
        assert db.execute("SELECT max(version) FROM sdk_schema_migrations").fetchone()[0] == 4


@pytest.mark.asyncio
async def test_dev_reset_rejects_disabled_relative_and_symlink_without_mutation(tmp_path, monkeypatch):
    data = tmp_path / "userdata" / "data"
    paths = (
        data / "state.db",
        data / "simple-harness-sdk" / "execution-v1.sqlite3",
        data / "memory.db",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"KEEP")
    monkeypatch.delenv("DESKPET_DEV_MODE", raising=False)
    with pytest.raises(AgentDataResetRefused):
        await reset_storage_set(
            state_db=str(paths[0]), execution_db=str(paths[1]), memory_db=str(paths[2])
        )
    assert [path.read_bytes() for path in paths] == [b"KEEP"] * 3
    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    paths[2].unlink()
    paths[2].symlink_to(paths[0])
    with pytest.raises(AgentDataResetRefused):
        await reset_storage_set(
            state_db=str(paths[0]), execution_db=str(paths[1]), memory_db=str(paths[2])
        )
    assert paths[0].read_bytes() == b"KEEP"
