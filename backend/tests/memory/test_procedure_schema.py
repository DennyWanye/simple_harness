"""Only the new v53 composition and existing real timer/cursor interaction."""
import sqlite3
import pytest

from deskpet.memory.procedure_schema import initialize_procedure_state_db, validate_procedure_state_db
from deskpet.memory.s5c_terminal_schema import initialize_s5c_terminal_state_db
from deskpet.memory.s5c_store import S5cStore
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.schema import dispatch_startup_epoch, HumanMemoryProgramEpochError
from tests.memory.test_s5c_terminal_schema import populated, frozen
from tests.memory.test_s5c_store import P, registration


@pytest.mark.asyncio
async def test_v53_atomic_upgrade_reopen_and_existing_cursor_continues(tmp_path):
    path, _ = await populated(tmp_path)
    await initialize_s5c_terminal_state_db(path)
    before = frozen(path)
    def fail(point):
        if point == "procedure.migration.before_commit":
            raise RuntimeError("interrupt v53 publication")
    with pytest.raises(RuntimeError, match="interrupt v53"):
        await initialize_procedure_state_db(path, fault_inject=fail)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (52,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='procedure_uses'").fetchone() is None
    assert frozen(path) == before
    await initialize_procedure_state_db(path)
    await initialize_procedure_state_db(path)
    await initialize_s5c_terminal_state_db(path)
    await dispatch_startup_epoch(path, approved_fresh_lane=False)
    assert frozen(path) == before
    store = S5cStore(path, P)
    assert store.cursor_table == "prospective_outbox_cursor_v52"
    await store.commit_registration(*registration("after-procedure", 11.0), expected_cursor=(10.0, "outbox-1"))
    assert await S5cStore(path, P).cursor() == (11.0, "after-procedure")
    validate_procedure_state_db(path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER procedure_uses_no_update")
    with pytest.raises(HumanMemoryProgramEpochError):
        ProspectiveSignalStore(path, P)


@pytest.mark.asyncio
async def test_existing_prepared_timer_consumes_after_v53_reopen(tmp_path):
    from tests.memory.test_prospective_timer_races import setup
    async with setup(tmp_path) as values:
        path, memory, _, _, _, prepared, clock, _, _ = values
        await initialize_procedure_state_db(path)
        registrations, signals = S5cStore(path, P), ProspectiveSignalStore(path, P)
        assert await signals.get_prepared(prepared.authority.intent.signal_id) == prepared
        scheduler = ProspectiveScheduler(store=signals,
            source=PublicTimeAuthoritySource(registrations=registrations, signals=signals),
            memory=memory, clock=lambda: clock[0], lease_seconds=2)
        assert await scheduler.tick(claim_owner="v53-reopened") == 1
        assert len((await memory.read_occurrence_inbox(principal=P)).entries) == 1
        assert await scheduler.tick(claim_owner="v53-replayed") == 0
        validate_procedure_state_db(path)
