"""Only the new 53->54 extension and the existing timer's live successor path."""
import sqlite3
import pytest
from deskpet.memory.procedure_schema import initialize_procedure_state_db
from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db, validate_procedure_recovery_state_db
from deskpet.memory.s5c_store import S5cStore
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.schema import dispatch_startup_epoch, HumanMemoryProgramEpochError
from tests.memory.test_s5c_terminal_schema import populated, frozen
from tests.memory.test_s5c_store import P


@pytest.mark.asyncio
async def test_v54_atomic_publication_preserves_original_registry_and_rejects_missing_fence(tmp_path):
    path, _ = await populated(tmp_path)
    await initialize_procedure_state_db(path)
    before = frozen(path)
    with sqlite3.connect(path) as db:
        registry = db.execute("SELECT * FROM human_memory_recovery_table_registry ORDER BY table_name").fetchall()
    def fail(point):
        raise RuntimeError("interrupt-v54")
    with pytest.raises(RuntimeError, match="interrupt-v54"):
        await initialize_procedure_recovery_state_db(path, fault_inject=fail)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (53,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='procedure_observation_attempts'").fetchone() is None
    assert frozen(path) == before
    await initialize_procedure_recovery_state_db(path)
    await dispatch_startup_epoch(path, approved_fresh_lane=False)
    await initialize_procedure_recovery_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM human_memory_recovery_table_registry WHERE table_name <> 'procedure_observation_attempts' ORDER BY table_name").fetchall() == registry
        db.execute("DROP TRIGGER hm_recovery_fence_procedure_observation_attempts_insert")
    with pytest.raises(HumanMemoryProgramEpochError):
        ProspectiveSignalStore(path, P)


@pytest.mark.asyncio
async def test_existing_timer_uses_real_v54_validators_and_cursor(tmp_path):
    from tests.memory.test_prospective_timer_races import setup
    async with setup(tmp_path) as values:
        path, memory, _, _, _, prepared, clock, _, _ = values
        await initialize_procedure_recovery_state_db(path)
        registrations, signals = S5cStore(path, P), ProspectiveSignalStore(path, P)
        assert registrations.cursor_table == "prospective_outbox_cursor_v52"
        assert await signals.get_prepared(prepared.authority.intent.signal_id) == prepared
        scheduler = ProspectiveScheduler(store=signals,
            source=PublicTimeAuthoritySource(registrations=registrations, signals=signals),
            memory=memory, clock=lambda: clock[0], lease_seconds=2)
        assert await scheduler.tick(claim_owner="v54-reopened") == 1
        assert len((await memory.read_occurrence_inbox(principal=P)).entries) == 1
        validate_procedure_recovery_state_db(path)
