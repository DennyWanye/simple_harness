"""The timer extension must publish its DDL, fences and marker atomically."""
import asyncio
import sqlite3

import pytest

from deskpet.memory.s5c_schema import initialize_s5c_state_db, validate_s5c_state_db
from deskpet.memory.s5c_timer_schema import (
    TIMER_TABLE, initialize_s5c_timer_state_db, validate_s5c_timer_state_db,
)
from deskpet.memory.schema import HumanMemoryProgramEpochError


@pytest.mark.asyncio
async def test_timer_concurrent_publication_after_initial_version_read(tmp_path, monkeypatch):
    from deskpet.memory import migrator
    path = tmp_path / 'state.db'
    await initialize_s5c_state_db(path)
    original = migrator.read_user_version
    published = False
    async def read_with_concurrent_publication(candidate):
        nonlocal published
        version = await original(candidate)
        if not published:
            published = True
            assert version == 50
            await initialize_s5c_timer_state_db(candidate)
        return version
    monkeypatch.setattr(migrator, 'read_user_version', read_with_concurrent_publication)
    await initialize_s5c_timer_state_db(path)
    assert published
    validate_s5c_timer_state_db(path)


@pytest.mark.asyncio
async def test_timer_extension_reopens_and_is_idempotent(tmp_path):
    path = tmp_path / 'state.db'
    await initialize_s5c_timer_state_db(path)
    validate_s5c_timer_state_db(path)
    before = path.read_bytes()
    await asyncio.gather(*(initialize_s5c_timer_state_db(path) for _ in range(2)))
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (51,)
        assert db.execute('SELECT taxonomy FROM human_memory_recovery_table_registry WHERE table_name=?',
                          (TIMER_TABLE,)).fetchone() == ('A',)
    with pytest.raises(HumanMemoryProgramEpochError):
        validate_s5c_state_db(path)


@pytest.mark.asyncio
@pytest.mark.parametrize('after_commit', [False, True])
async def test_timer_extension_interruption_preserves_publication_boundary(tmp_path, after_commit):
    path = tmp_path / 'state.db'
    await initialize_s5c_state_db(path)
    point = 's5c.timer_migration.' + ('after_commit' if after_commit else 'before_commit')
    def fault(actual):
        if actual == point:
            raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError, match='interrupted'):
        await initialize_s5c_timer_state_db(path, fault_inject=fault)
    if after_commit:
        validate_s5c_timer_state_db(path)
    else:
        validate_s5c_state_db(path)
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (TIMER_TABLE,)).fetchone() is None
    await initialize_s5c_timer_state_db(path)
    validate_s5c_timer_state_db(path)


@pytest.mark.asyncio
async def test_timer_extension_rejects_removed_fence_without_repair(tmp_path):
    path = tmp_path / 'state.db'
    await initialize_s5c_timer_state_db(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TRIGGER prospective_timer_events_no_update')
    before = path.read_bytes()
    with pytest.raises(HumanMemoryProgramEpochError):
        await initialize_s5c_timer_state_db(path)
    assert path.read_bytes() == before
