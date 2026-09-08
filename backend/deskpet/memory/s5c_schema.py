# SPDX-License-Identifier: BUSL-1.1
"""Explicit isolated v50 initializer. Not called by production composition.

Use the existing transactional SQL runner/recovery registry. Keep this SQL in a
subdirectory so the ordinary v49 migration glob cannot silently activate S5c.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import aiosqlite

from deskpet.memory import migrator, schema

S5C_MIGRATION = "s5c/042_prospective_memory_actions_v50.sql"
S5C_TABLES = (
    "prospective_scheduler_registrations",
    "prospective_outbox_cursor",
    "prospective_occurrences",
    "memory_action_events",
)


def _sql() -> str:
    return (migrator.DEFAULT_MIGRATIONS_DIR / S5C_MIGRATION).read_text(encoding="utf-8")


def validate_s5c_state_db(path: Path, *, _expected_user_version: int = 50) -> None:
    """Verify the base chain, v50 checksum/DDL and registered recovery fences."""
    if _expected_user_version not in (50, 51, 52, 53, 54, 55):
        raise schema.HumanMemoryProgramEpochError("s5c_schema_invalid")
    for validator in (
        schema._validate_human_memory_program_marker,
        schema._validate_task_scope_archive_marker,
        schema._validate_task_scope_provision_marker,
        schema._validate_task_workspace_binding_marker,
        schema._validate_s4_migration_chain,
        schema._validate_recovery_marker,
        schema._validate_quiescence_marker,
        schema._validate_execution_marker,
    ):
        validator(path, expected_user_version=_expected_user_version)
    sql = _sql()
    try:
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(sql)
            objects = expected.execute(
                "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"
            ).fetchall()
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT schema_version,migration_sha256 "
                "FROM human_memory_migration_chain "
                "WHERE migration_id=?",
                (S5C_MIGRATION,),
            ).fetchone()
            if row != (50, hashlib.sha256(sql.encode()).hexdigest()):
                raise ValueError("migration hash")
            if (
                db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?", (S5C_MIGRATION,)
                ).fetchone()
                is None
            ):
                raise ValueError("migration marker")
            for name, ddl in objects:
                if db.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (name,)
                ).fetchone() != (ddl,):
                    raise ValueError("schema object")
            for name in S5C_TABLES:
                if db.execute(
                    "SELECT taxonomy FROM human_memory_recovery_table_registry "
                    "WHERE table_name=?",
                    (name,),
                ).fetchone() != ("A",):
                    raise ValueError("recovery registration")
                for operation in ("insert", "update", "delete"):
                    trigger = f"hm_recovery_fence_{name}_{operation}"
                    row = db.execute(
                        "SELECT sql FROM sqlite_master WHERE name=?", (trigger,)
                    ).fetchone()
                    expected_sql = (
                        f'CREATE TRIGGER "{trigger}" BEFORE {operation.upper()} '
                        f'ON "{name}" '
                        "WHEN (SELECT state FROM human_memory_recovery_fence "
                        "WHERE singleton=1)<>'OPEN' "
                        "BEGIN SELECT RAISE(ABORT,'human_memory_ingress_fenced'); END"
                    )
                    if row != (expected_sql,):
                        raise ValueError("recovery fence")
    except (sqlite3.Error, ValueError) as exc:
        raise schema.HumanMemoryProgramEpochError("s5c_schema_invalid") from exc


async def initialize_s5c_state_db(
    db_path: str | Path,
    *,
    fault_inject: Callable[[str], None] | None = None,
) -> None:
    path = Path(db_path)
    version = await migrator.read_user_version(path)
    if version > 50:
        raise schema.HumanMemoryProgramEpochError(
            "human_memory_program_future_database_unsupported"
        )
    # Older unpublished S5c schemas used numbers now owned by Primary.
    # Reject their actual table identity before invoking any ordinary repair path.
    if path.exists() and version != 50:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            placeholders = ",".join("?" for _ in S5C_TABLES)
            if db.execute(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ({placeholders}) LIMIT 1",
                    S5C_TABLES).fetchone() is not None:
                raise schema.HumanMemoryProgramEpochError("s5c_unpublished_schema_incompatible")
    if version < 50:
        await schema.initialize_human_memory_program_state_db(path)
    async with schema._human_memory_program_lock(path):
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute("PRAGMA user_version")
                current = (await cursor.fetchone())[0]
                await cursor.close()
                if current == 50:
                    await db.rollback()
                    validate_s5c_state_db(path)
                    return
                if current != 49:
                    raise schema.HumanMemoryProgramEpochError(
                        "s5c_base_version_differs"
                    )
                await migrator._assert_effect_closure_cutover_preconditions(db)
                fence = await db.execute(
                    "SELECT state FROM human_memory_recovery_fence WHERE singleton=1"
                )
                fence_row = await fence.fetchone()
                await fence.close()
                if fence_row != ("OPEN",):
                    raise schema.HumanMemoryProgramEpochError(
                        "s5c_migration_recovery_fenced"
                    )
                # Validate every v49 marker while holding the migration write lock.
                schema._validate_s4_migration_chain(path, expected_user_version=49)
                sql = _sql()
                await migrator._execute_transactional_script(db, sql)
                await migrator._register_recovery_tables(
                    db, tuple((name, "A") for name in S5C_TABLES)
                )
                await db.execute(
                    "INSERT INTO human_memory_migration_chain VALUES (?,?,?,?)",
                    (
                        S5C_MIGRATION,
                        50,
                        hashlib.sha256(sql.encode()).hexdigest(),
                        time.time(),
                    ),
                )
                await db.execute(
                    "INSERT INTO schema_migrations VALUES (?,?)",
                    (S5C_MIGRATION, time.time()),
                )
                await db.execute("PRAGMA user_version=50")
                if fault_inject:
                    fault_inject("s5c.migration.before_commit")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    if fault_inject:
        fault_inject("s5c.migration.after_commit")
    validate_s5c_state_db(path)
