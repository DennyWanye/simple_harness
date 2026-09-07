"""Exercise the real pre-opener and subsequent generic opener on scheduler DBs."""
import sqlite3

import pytest

from deskpet.memory import schema
from deskpet.memory.s5c_schema import initialize_s5c_state_db
from deskpet.memory.s5c_timer_schema import initialize_s5c_timer_state_db
from deskpet.memory.s5c_terminal_schema import initialize_s5c_terminal_state_db
from deskpet.memory.s5c_store import S5cStore
from tests.memory.test_s5c_store import P, registration


def corrupt_immutable_row(db, table, statement, parameters=()):
    # Emulate on-disk corruption, preserving the original protection DDL so
    # this control reaches the content validator rather than failing on writes.
    triggers = db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
        (table,),
    ).fetchall()
    for name, _ in triggers:
        db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    db.execute(statement, parameters)
    for _, ddl in triggers:
        db.execute(ddl)


async def populated(path, version):
    await initialize_s5c_state_db(path)
    await S5cStore(path, P).commit_registration(*registration(), expected_cursor=None)
    if version >= 51:
        await initialize_s5c_timer_state_db(path)
    if version >= 52:
        await initialize_s5c_terminal_state_db(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [50, 51, 52])
async def test_composed_restart_preserves_real_registration_and_schema(tmp_path, version):
    path = tmp_path / "state.db"
    await populated(path, version)
    before = path.read_bytes()
    for _ in range(2):
        decision = await schema.dispatch_startup_epoch(path, approved_fresh_lane=False)
        assert decision.epoch is schema.StartupEpoch.HUMAN_RESUME
        assert decision.composition_mode is schema.StartupCompositionMode.HUMAN
        assert decision.user_version == version
        await schema.initialize_state_db(path)
        await schema.initialize_human_memory_program_state_db(path)
        assert await S5cStore(path, P).cursor() == (10.0, "outbox-1")
        assert len(await S5cStore(path, P).pending_registrations()) == 1
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["marker", "ddl", "fence", "bootstrap"])
async def test_composed_restart_refuses_invalid_extension_without_repair(tmp_path, damage):
    path = tmp_path / "state.db"
    await populated(path, 52)
    with sqlite3.connect(path) as db:
        if damage == "marker":
            corrupt_immutable_row(db, "human_memory_migration_chain",
                "UPDATE human_memory_migration_chain SET migration_sha256=? "
                "WHERE schema_version=52", ("a" * 64,))
        elif damage == "ddl":
            db.execute("DROP TRIGGER s5c_cursor_v50_sealed")
        elif damage == "fence":
            db.execute("DROP TRIGGER hm_recovery_fence_prospective_outbox_cursor_v52_insert")
        else:
            corrupt_immutable_row(db, "human_memory_program_bootstrap",
                "DELETE FROM human_memory_program_bootstrap WHERE singleton=1")
    before = path.read_bytes()
    with pytest.raises(schema.HumanMemoryProgramEpochError):
        await schema.dispatch_startup_epoch(path, approved_fresh_lane=False)
    with pytest.raises(schema.HumanMemoryProgramEpochError):
        await schema.initialize_state_db(path)
    with pytest.raises(schema.HumanMemoryProgramEpochError):
        await schema.initialize_human_memory_program_state_db(path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak*"))


@pytest.mark.asyncio
async def test_unknown_successor_is_rejected_even_with_valid_older_markers(tmp_path):
    path = tmp_path / "state.db"
    await populated(path, 52)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=53")
    before = path.read_bytes()
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="future"):
        await schema.dispatch_startup_epoch(path, approved_fresh_lane=False)
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="future"):
        await schema.initialize_state_db(path)
    with pytest.raises(schema.HumanMemoryProgramEpochError, match="future"):
        await schema.initialize_human_memory_program_state_db(path)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_fresh_composition_retains_base_until_scheduler_installs_extension(tmp_path):
    path = tmp_path / "state.db"
    decision = await schema.dispatch_startup_epoch(path, approved_fresh_lane=True)
    assert decision.epoch is schema.StartupEpoch.FRESH
    await schema.initialize_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (49,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='prospective_occurrences'").fetchone() is None
