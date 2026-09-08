"""Explicit v55 append-only side record of assistant tool calls; v54 unchanged.

2026-09-08 HM-TO-A6 F-K1. The archived terminal observation keeps only
``{"role","content"}`` for assistant items, so a quoted history group could
not show which arguments a past tool call carried. The arguments live in
public SDK provider records; this Host table copies them at terminal
observation time, bound to the exact archived observation. Nothing here is a
grant and no envelope hash changes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import aiosqlite
from deskpet.memory import migrator, schema, schema_chain
from deskpet.memory.procedure_recovery_schema import (
    initialize_procedure_recovery_state_db, validate_procedure_recovery_state_db,
)

MIGRATION = "primary/047_primary_assistant_tool_calls_v55.sql"
SCHEMA_VERSION = 55
TABLES = ("primary_assistant_tool_calls",)


def _sql():
    return (migrator.DEFAULT_MIGRATIONS_DIR / MIGRATION).read_text(encoding="utf-8")


def validate_primary_tool_call_state_db(path, *, _expected_user_version=SCHEMA_VERSION):
    path = Path(path)
    if _expected_user_version not in schema_chain.accepted_versions(SCHEMA_VERSION):
        raise schema.HumanMemoryProgramEpochError("primary_tool_call_schema_invalid")
    validate_procedure_recovery_state_db(path, _expected_user_version=_expected_user_version)
    sql = _sql()
    try:
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(sql)
            objects = expected.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            if db.execute("SELECT schema_version,migration_sha256 FROM human_memory_migration_chain "
                    "WHERE migration_id=?", (MIGRATION,)).fetchone() != (
                        SCHEMA_VERSION, hashlib.sha256(sql.encode()).hexdigest()):
                raise ValueError("migration identity")
            if db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone() is None:
                raise ValueError("migration marker")
            for name, ddl in objects:
                if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone() != (ddl,):
                    raise ValueError("schema object")
            for table in TABLES:
                rows = db.execute(f'PRAGMA table_info("{table}")').fetchall()
                columns = [dict(cid=r[0], name=r[1], type=r[2], notnull=r[3], default=r[4], pk=r[5]) for r in rows]
                if db.execute("SELECT taxonomy,columns_json FROM human_memory_recovery_table_registry "
                        "WHERE table_name=?", (table,)).fetchone() != (
                            "A", json.dumps(columns, sort_keys=True, separators=(",", ":"))):
                    raise ValueError("recovery registration")
                for operation in ("insert", "update", "delete"):
                    name = f"hm_recovery_fence_{table}_{operation}"
                    ddl = (f'CREATE TRIGGER "{name}" BEFORE {operation.upper()} ON "{table}" '
                           "WHEN (SELECT state FROM human_memory_recovery_fence WHERE singleton=1)<>'OPEN' "
                           "BEGIN SELECT RAISE(ABORT,'human_memory_ingress_fenced'); END")
                    if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone() != (ddl,):
                        raise ValueError("recovery fence")
                if db.execute(f'PRAGMA foreign_key_check("{table}")').fetchone() is not None:
                    raise ValueError("foreign key")
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise schema.HumanMemoryProgramEpochError("primary_tool_call_schema_invalid") from error


async def initialize_primary_tool_call_state_db(path, *, fault_inject=None):
    path = Path(path)
    version = await migrator.read_user_version(path)
    if version >= SCHEMA_VERSION:
        # Already at this step or at a registered chain successor: validate that
        # exact published schema. An unregistered integer is a future/unpublished
        # database, never re-interpreted as this step.
        validator = schema_chain.domain_validator(version)
        if validator is None:
            raise schema.HumanMemoryProgramEpochError("human_memory_program_future_database_unsupported")
        validator(path)
        return
    await initialize_procedure_recovery_state_db(path)
    async with schema._human_memory_program_lock(path):
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute("PRAGMA user_version") as cursor:
                    current = (await cursor.fetchone())[0]
                if current == SCHEMA_VERSION:
                    await db.rollback()
                    validate_primary_tool_call_state_db(path)
                    return
                if current != SCHEMA_VERSION - 1:
                    raise schema.HumanMemoryProgramEpochError("primary_tool_call_schema_base_differs")
                validate_procedure_recovery_state_db(path)
                await migrator._assert_effect_closure_cutover_preconditions(db)
                async with db.execute("SELECT state FROM human_memory_recovery_fence WHERE singleton=1") as cursor:
                    if await cursor.fetchone() != ("OPEN",):
                        raise schema.HumanMemoryProgramEpochError("primary_tool_call_migration_recovery_fenced")
                sql = _sql()
                await migrator._execute_transactional_script(db, sql)
                await migrator._register_recovery_tables(db, tuple((name, "A") for name in TABLES))
                now = time.time()
                await db.execute("INSERT INTO human_memory_migration_chain VALUES (?,?,?,?)",
                    (MIGRATION, SCHEMA_VERSION, hashlib.sha256(sql.encode()).hexdigest(), now))
                await db.execute("INSERT INTO schema_migrations VALUES (?,?)", (MIGRATION, now))
                await db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                if fault_inject:
                    fault_inject("primary_tool_call.migration.before_commit")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    validate_primary_tool_call_state_db(path)
