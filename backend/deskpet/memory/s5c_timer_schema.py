"""Explicit schema51 extension for the durable time-signal journal.

The frozen schema50 SQL and default initializer retain their meaning. This
extension is installed only by the scheduler composition, never by a store read.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import aiosqlite

from deskpet.memory import migrator, schema
from deskpet.memory.s5c_schema import initialize_s5c_state_db, validate_s5c_state_db

TIMER_MIGRATION = "s5c/043_prospective_time_signals_v51.sql"
TIMER_TABLE = "prospective_timer_events"


def _sql():
    return (migrator.DEFAULT_MIGRATIONS_DIR / TIMER_MIGRATION).read_text(encoding="utf-8")


def _version(path):
    with sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def validate_s5c_timer_state_db(path: str | Path, *, _expected_user_version: int = 51) -> None:
    path = Path(path)
    if _expected_user_version not in (51, 52, 53, 54):
        raise schema.HumanMemoryProgramEpochError("s5c_timer_schema_invalid")
    validate_s5c_state_db(path, _expected_user_version=_expected_user_version)
    sql = _sql()
    try:
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(sql)
            objects = expected.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            if db.execute("SELECT schema_version,migration_sha256 FROM human_memory_migration_chain "
                    "WHERE migration_id=?", (TIMER_MIGRATION,)).fetchone() != (51, hashlib.sha256(sql.encode()).hexdigest()):
                raise ValueError("migration identity")
            if db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (TIMER_MIGRATION,)).fetchone() is None:
                raise ValueError("migration marker")
            for name, ddl in objects:
                if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone() != (ddl,):
                    raise ValueError("schema object")
            if db.execute("SELECT taxonomy FROM human_memory_recovery_table_registry WHERE table_name=?",
                    (TIMER_TABLE,)).fetchone() != ("A",):
                raise ValueError("recovery registration")
            for operation in ("insert", "update", "delete"):
                trigger = f"hm_recovery_fence_{TIMER_TABLE}_{operation}"
                ddl = (f'CREATE TRIGGER "{trigger}" BEFORE {operation.upper()} ON "{TIMER_TABLE}" '
                    "WHEN (SELECT state FROM human_memory_recovery_fence WHERE singleton=1)<>'OPEN' "
                    "BEGIN SELECT RAISE(ABORT,'human_memory_ingress_fenced'); END")
                if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (trigger,)).fetchone() != (ddl,):
                    raise ValueError("recovery fence")
    except (sqlite3.Error, ValueError) as error:
        raise schema.HumanMemoryProgramEpochError("s5c_timer_schema_invalid") from error


def validate_s5c_domain_state_db(path: str | Path) -> None:
    """Domain stores accept exactly the known base or validated timer extension."""
    path = Path(path)
    if _version(path) == 54:
        from deskpet.memory.procedure_recovery_schema import validate_procedure_recovery_state_db
        validate_procedure_recovery_state_db(path)
    elif _version(path) == 53:
        from deskpet.memory.procedure_schema import validate_procedure_state_db
        validate_procedure_state_db(path)
    elif _version(path) == 52:
        from deskpet.memory.s5c_terminal_schema import validate_s5c_terminal_state_db
        validate_s5c_terminal_state_db(path)
    elif _version(path) == 51:
        validate_s5c_timer_state_db(path)
    else:
        validate_s5c_state_db(path)


def validate_s5c_timer_runtime_state_db(path: str | Path) -> None:
    """A timer requires the real time journal, including in the typed successor."""
    if _version(path) not in (51, 52, 53, 54):
        raise schema.HumanMemoryProgramEpochError("s5c_timer_schema_required")
    validate_s5c_domain_state_db(path)


async def initialize_s5c_timer_state_db(db_path: str | Path, *, fault_inject=None) -> None:
    path = Path(db_path)
    version = await migrator.read_user_version(path)
    if version == 51:
        validate_s5c_timer_state_db(path)
        return
    if version > 51:
        raise schema.HumanMemoryProgramEpochError("human_memory_program_future_database_unsupported")
    if path.exists():
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TIMER_TABLE,)).fetchone() is not None:
                if db.execute("PRAGMA user_version").fetchone() == (51,):
                    validate_s5c_timer_state_db(path)
                    return
                raise schema.HumanMemoryProgramEpochError("s5c_timer_unpublished_schema_incompatible")
    try:
        await initialize_s5c_state_db(path)
    except schema.HumanMemoryProgramEpochError:
        # Another initializer can publish 51 after our initial read. Accept
        # only its fully validated successor; never reinterpret another error.
        if await migrator.read_user_version(path) != 51:
            raise
        validate_s5c_timer_state_db(path)
        return
    async with schema._human_memory_program_lock(path):
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute("PRAGMA user_version") as cursor:
                    current = (await cursor.fetchone())[0]
                if current == 51:
                    await db.rollback()
                    validate_s5c_timer_state_db(path)
                    return
                if current != 50:
                    raise schema.HumanMemoryProgramEpochError("s5c_timer_base_version_differs")
                validate_s5c_state_db(path)
                await migrator._assert_effect_closure_cutover_preconditions(db)
                async with db.execute("SELECT state FROM human_memory_recovery_fence WHERE singleton=1") as cursor:
                    fence = await cursor.fetchone()
                if fence != ("OPEN",):
                    raise schema.HumanMemoryProgramEpochError("s5c_timer_migration_recovery_fenced")
                sql = _sql()
                await migrator._execute_transactional_script(db, sql)
                await migrator._register_recovery_tables(db, ((TIMER_TABLE, "A"),))
                now = time.time()
                await db.execute("INSERT INTO human_memory_migration_chain VALUES (?,?,?,?)",
                    (TIMER_MIGRATION, 51, hashlib.sha256(sql.encode()).hexdigest(), now))
                await db.execute("INSERT INTO schema_migrations VALUES (?,?)", (TIMER_MIGRATION, now))
                await db.execute("PRAGMA user_version=51")
                if fault_inject:
                    fault_inject("s5c.timer_migration.before_commit")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    if fault_inject:
        fault_inject("s5c.timer_migration.after_commit")
    validate_s5c_timer_state_db(path)
