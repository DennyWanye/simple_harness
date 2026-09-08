"""v55: the assistant tool-call side table joins the chain atomically; v54 stays byte-identical."""
import sqlite3
import pytest
from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db
from deskpet.memory.primary_tool_call_schema import (
    initialize_primary_tool_call_state_db, validate_primary_tool_call_state_db,
)
from deskpet.memory.schema import HumanMemoryProgramEpochError, dispatch_startup_epoch
from deskpet.memory.s5c_timer_schema import validate_s5c_timer_runtime_state_db
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
from tests.memory.test_s5c_terminal_schema import populated, frozen
from tests.memory.test_s5c_store import P


@pytest.mark.asyncio
async def test_v55_atomic_publication_and_every_prior_validator_accepts_it(tmp_path):
    path, _ = await populated(tmp_path)
    await initialize_procedure_recovery_state_db(path)
    before = frozen(path)
    with sqlite3.connect(path) as db:
        registry = db.execute("SELECT * FROM human_memory_recovery_table_registry ORDER BY table_name").fetchall()
        chain = db.execute("SELECT * FROM human_memory_migration_chain ORDER BY schema_version").fetchall()

    def fail(point):
        assert point == "primary_tool_call.migration.before_commit"
        raise RuntimeError("interrupt-v55")
    with pytest.raises(RuntimeError, match="interrupt-v55"):
        await initialize_primary_tool_call_state_db(path, fault_inject=fail)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (54,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='primary_assistant_tool_calls'").fetchone() is None
        assert db.execute("SELECT * FROM human_memory_migration_chain ORDER BY schema_version").fetchall() == chain
    assert frozen(path) == before

    await initialize_primary_tool_call_state_db(path)
    await initialize_primary_tool_call_state_db(path)  # idempotent
    await initialize_procedure_recovery_state_db(path)  # the v54 initializer accepts its successor
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (55,)
        assert db.execute("SELECT * FROM human_memory_recovery_table_registry "
                          "WHERE table_name <> 'primary_assistant_tool_calls' ORDER BY table_name").fetchall() == registry
        assert db.execute("SELECT taxonomy FROM human_memory_recovery_table_registry "
                          "WHERE table_name='primary_assistant_tool_calls'").fetchone() == ("A",)
        assert db.execute("SELECT migration_id,schema_version FROM human_memory_migration_chain "
                          "WHERE schema_version=55").fetchone() == ("primary/047_primary_assistant_tool_calls_v55.sql", 55)
    assert frozen(path) == before
    validate_primary_tool_call_state_db(path)
    validate_s5c_timer_runtime_state_db(path)
    ProspectiveSignalStore(path, P)
    decision = await dispatch_startup_epoch(path, approved_fresh_lane=False)
    assert decision.user_version == 55
    validate_primary_tool_call_state_db(path)


@pytest.mark.asyncio
async def test_v55_rows_are_immutable_and_fenced(tmp_path):
    path, _ = await populated(tmp_path)
    await initialize_primary_tool_call_state_db(path)
    row = ("primary-assistant-tool-calls:run:2", "run", "host", 2, "evidence", "a" * 64, "{}", "b" * 64, 1.0)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO primary_assistant_tool_calls VALUES (?,?,?,?,?,?,?,?,?)", row)
        with pytest.raises(sqlite3.IntegrityError, match="primary_assistant_tool_calls_immutable"):
            db.execute("UPDATE primary_assistant_tool_calls SET body_json='{\"x\":1}'")
        with pytest.raises(sqlite3.IntegrityError, match="primary_assistant_tool_calls_immutable"):
            db.execute("DELETE FROM primary_assistant_tool_calls")
        with pytest.raises(sqlite3.IntegrityError):  # one record per (run, ordinal)
            db.execute("INSERT INTO primary_assistant_tool_calls VALUES (?,?,?,?,?,?,?,?,?)",
                       ("other-id",) + row[1:])
        with pytest.raises(sqlite3.IntegrityError):  # ordinal 1 is always the USER item
            db.execute("INSERT INTO primary_assistant_tool_calls VALUES (?,?,?,?,?,?,?,?,?)",
                       ("primary-assistant-tool-calls:run:1", "run", "host", 1) + row[4:])
        db.commit()
        db.execute("DROP TRIGGER hm_recovery_fence_primary_assistant_tool_calls_insert")
        db.commit()
    with pytest.raises(HumanMemoryProgramEpochError):
        validate_primary_tool_call_state_db(path)


@pytest.mark.asyncio
async def test_v55_rejects_a_future_database_and_a_non_v54_base(tmp_path):
    path, _ = await populated(tmp_path)
    await initialize_primary_tool_call_state_db(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=56")
        db.commit()
    with pytest.raises(HumanMemoryProgramEpochError, match="future_database_unsupported"):
        await initialize_primary_tool_call_state_db(path)
    with pytest.raises(HumanMemoryProgramEpochError, match="future_database_unsupported"):
        await initialize_procedure_recovery_state_db(path)
