"""Real v46 -> v47 migration, atomicity, reopen and future-version rejection."""
import hashlib
import shutil
import sqlite3
import pytest
from deskpet.memory import migrator, schema


async def v46(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    directory = tmp_path / 'v46-migrations'
    directory.mkdir()
    for source in migrator.DEFAULT_MIGRATIONS_DIR.glob('*.sql'):
        if source.name != migrator.PRIMARY_EFFECT_SOURCES_MIGRATION:
            shutil.copy2(source, directory / source.name)
    with monkeypatch.context() as patch:
        patch.setattr(migrator, 'DEFAULT_MIGRATIONS_DIR', directory)
        patch.setattr(migrator, 'HUMAN_MEMORY_TARGET_SCHEMA_VERSION', 46)
        patch.setattr(schema, 'HUMAN_MEMORY_TARGET_SCHEMA_VERSION', 46)
        await schema.initialize_human_memory_program_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 46
        old = list(db.execute('SELECT * FROM human_memory_recovery_transitions'))
    return path, old


@pytest.mark.asyncio
async def test_v46_upgrade_reopen_keeps_old_facts_and_registers_only_source_index(tmp_path, monkeypatch):
    assert migrator.HUMAN_MEMORY_TARGET_SCHEMA_VERSION == schema.HUMAN_MEMORY_TARGET_SCHEMA_VERSION == 47
    path, old = await v46(tmp_path, monkeypatch)
    await schema.initialize_human_memory_program_state_db(path)
    await schema.initialize_human_memory_program_state_db(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 47
        assert list(db.execute('SELECT * FROM human_memory_recovery_transitions')) == old
        assert db.execute('SELECT count(*) FROM primary_effect_identities').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM schema_migrations WHERE version=?', (migrator.PRIMARY_EFFECT_SOURCES_MIGRATION,)).fetchone()[0] == 1
        assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('prospective_occurrences','memory_action_events')").fetchall()


@pytest.mark.asyncio
async def test_source_migration_failure_rolls_back_ddl_chain_and_version(tmp_path, monkeypatch):
    path, old = await v46(tmp_path, monkeypatch)
    def fail(point):
        if point == 'before_039_primary_effect_sources_v47_commit':
            raise RuntimeError('injected source migration interruption')
    with pytest.raises(migrator.MigrationError):
        await migrator.run_migrations(path, include_human_memory_program=True, fault_inject=fail)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 46
        assert list(db.execute('SELECT * FROM human_memory_recovery_transitions')) == old
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='primary_effect_identities'").fetchall()
        assert not db.execute('SELECT * FROM human_memory_migration_chain WHERE migration_id=?', (migrator.PRIMARY_EFFECT_SOURCES_MIGRATION,)).fetchall()
    await schema.initialize_human_memory_program_state_db(path)


@pytest.mark.asyncio
async def test_future48_rejected_before_repair_without_changing_bytes(tmp_path):
    path = tmp_path / 'future.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=48')
        db.execute('CREATE TABLE future_fact(value TEXT)')
        db.execute("INSERT INTO future_fact VALUES ('preserve')")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(schema.HumanMemoryProgramEpochError, match='future_database_unsupported'):
        await schema.initialize_human_memory_program_state_db(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['primary_effect_identities', 'primary_effect_identities_no_delete', 'hm_recovery_fence_primary_effect_identities_insert'])
async def test_reopen_rejects_corrupt_actual_schema_despite_valid_marker(tmp_path, missing):
    path = tmp_path / 'corrupt.db'
    await schema.initialize_human_memory_program_state_db(path)
    with sqlite3.connect(path) as db:
        # Explicit schema-corruption negative, never used as a legacy fixture.
        kind = 'TABLE' if missing == 'primary_effect_identities' else 'TRIGGER'
        db.execute(f'DROP {kind} {missing}')
    with pytest.raises(schema.HumanMemoryProgramEpochError, match='primary_effect_index_schema_invalid'):
        await schema.initialize_human_memory_program_state_db(path)
