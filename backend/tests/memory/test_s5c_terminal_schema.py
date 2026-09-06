"""v52 preserves real v50 registration chains and seals the superseded writer."""
import sqlite3

import pytest

from deskpet.memory.schema import HumanMemoryProgramEpochError
from deskpet.memory.s5c_store import S5cStore
from deskpet.memory.s5c_timer_schema import initialize_s5c_timer_state_db, validate_s5c_timer_state_db
from deskpet.memory.s5c_terminal_schema import (
    TERMINAL_TABLES, initialize_s5c_terminal_state_db, validate_s5c_terminal_state_db,
)
from tests.memory.test_s5c_store import P, registration


async def populated(tmp_path):
    path = tmp_path / "state.db"
    await initialize_s5c_timer_state_db(path)
    store = S5cStore(path, P)
    await store.commit_registration(*registration(), expected_cursor=None)
    return path, store


def frozen(path):
    with sqlite3.connect(path) as db:
        return (
            db.execute("SELECT * FROM prospective_outbox_cursor ORDER BY owner_key,sequence").fetchall(),
            db.execute("SELECT * FROM prospective_scheduler_registrations ORDER BY record_id").fetchall(),
            db.execute("SELECT * FROM human_memory_recovery_table_registry "
                       "WHERE table_name='prospective_outbox_cursor'").fetchall(),
        )


@pytest.mark.asyncio
async def test_typed_cursor_preserves_nonempty_old_chain_and_continues_after_reopen(tmp_path):
    path, old_store = await populated(tmp_path)
    before = frozen(path)
    await initialize_s5c_terminal_state_db(path)
    validate_s5c_terminal_state_db(path)
    assert frozen(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM prospective_outbox_cursor_v52").fetchall() == [(*before[0][0], None)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # The pre-migration writer is not allowed to append into a hidden old queue.
    with pytest.raises(sqlite3.IntegrityError, match="successor_required"):
        await old_store.commit_registration(*registration("outbox-2", 11.0), expected_cursor=(10.0, "outbox-1"))
    reopened = S5cStore(path, P)
    await reopened.commit_registration(*registration("outbox-2", 11.0), expected_cursor=(10.0, "outbox-1"))
    assert await reopened.cursor() == (11.0, "outbox-2")
    assert len(await reopened.pending_registrations()) == 2
    assert (await reopened.registration("outbox-1")).entry.outbox_id == "outbox-1"
    validate_s5c_terminal_state_db(path)
    raw = path.read_bytes()
    await initialize_s5c_terminal_state_db(path)
    assert path.read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("after_commit", [False, True])
async def test_typed_cursor_publication_is_atomic_with_nonempty_history(tmp_path, after_commit):
    path, _ = await populated(tmp_path)
    before = frozen(path)
    point = "s5c.terminal_migration." + ("after_commit" if after_commit else "before_commit")
    def fault(actual):
        if actual == point:
            raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        await initialize_s5c_terminal_state_db(path, fault_inject=fault)
    assert frozen(path) == before
    if after_commit:
        validate_s5c_terminal_state_db(path)
    else:
        validate_s5c_timer_state_db(path)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE name IN (?,?)", TERMINAL_TABLES).fetchall() == []
    await initialize_s5c_terminal_state_db(path)
    validate_s5c_terminal_state_db(path)


@pytest.mark.asyncio
async def test_typed_cursor_refuses_corrupt_legacy_chain_without_publishing(tmp_path):
    path, _ = await populated(tmp_path)
    with sqlite3.connect(path) as db:
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='s5c_cursor_no_update'").fetchone()[0]
        db.execute("DROP TRIGGER s5c_cursor_no_update")
        db.execute("UPDATE prospective_outbox_cursor SET cursor_hash=?", ("b" * 64,))
        db.execute(trigger)
    before = path.read_bytes()
    with pytest.raises(HumanMemoryProgramEpochError, match="legacy_chain_invalid"):
        await initialize_s5c_terminal_state_db(path)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_typed_cursor_missing_target_and_missing_fence_are_rejected(tmp_path):
    path, _ = await populated(tmp_path)
    await initialize_s5c_terminal_state_db(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        # Neither target is not a completion; no fake terminal is inserted.
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO prospective_outbox_cursor_v52 VALUES (?,?,?,?,?,?,?,?)",
                       ("owner", 1, 1.0, "outbox", None, "0"*64, "a"*64, None))
        db.execute("DROP TRIGGER s5c_cursor_v50_sealed")
    before = path.read_bytes()
    with pytest.raises(HumanMemoryProgramEpochError, match="s5c_terminal_schema_invalid"):
        await initialize_s5c_terminal_state_db(path)
    assert path.read_bytes() == before
