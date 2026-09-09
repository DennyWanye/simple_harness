# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit v57 widening of the Harness evidence-kind vocabulary.

2026-09-09 HM-TO-A6 incident AI.  Incident AA's bounded re-collection receipt
(v56) is imported into the canonical archive as a state.db Harness fact under
``kind='context_use_recollection'``, but that token existed in **no** copy of
the vocabulary: not in ``evidence_ingress.RESERVATION_KINDS``, not in
``protocol._EXECUTION_KINDS``, and not in the ``harness_evidence_reservations``
CHECK constraint.  The first Run whose use-authority lease entered its margin
mid-turn therefore died on ``execution_evidence_kind_rejected``.  This step
moves the SQL copy; the two Python copies move in the same commit.

Unlike every other chain step this one *rebuilds* an existing table (SQLite
cannot alter a CHECK in place), so its validator cannot replay the migration
script into an empty ``:memory:`` database to derive the expected DDL — the
script's ``INSERT … SELECT`` and ``DROP`` need the predecessor table.  It
validates the rebuilt objects directly instead: the six ``sqlite_master`` rows
the rebuild owns must exist, the table must admit exactly the published
vocabulary, and the recovery fences the ``DROP`` removed must be back.  The
column list is unchanged, so the v42 recovery registry row still describes the
table and is deliberately **not** re-registered.  v56 is otherwise unchanged.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import aiosqlite
from deskpet.memory import migrator, schema, schema_chain
from deskpet.memory.context_use_recollect_schema import (
    initialize_context_use_recollect_state_db, validate_context_use_recollect_state_db,
)

MIGRATION = "primary/049_evidence_kind_recollection_v57.sql"
SCHEMA_VERSION = 57
TABLE = "harness_evidence_reservations"

#: The SQL copy of the S4 evidence vocabulary.  Kept equal to
#: ``evidence_ingress.RESERVATION_KINDS`` and ``protocol._EXECUTION_KINDS`` by
#: ``tests/task_scope/test_protocol_evidence_kinds.py`` — a producer must never
#: reach a native Run with a kind that only one of the three copies knows.
EVIDENCE_KINDS: tuple[str, ...] = (
    "provider_invocation",
    "tool_invocation",
    "context_snapshot",
    "route_decision",
    "context_use_recollection",
    "run_terminal",
)

#: Every ``sqlite_master`` object the rebuild owns and must leave behind.
REBUILT_OBJECTS: tuple[str, ...] = (
    TABLE,
    "idx_harness_evidence_reservations_run",
    "harness_evidence_reservations_no_delete",
    "harness_evidence_reservations_guard",
    "hm_recovery_fence_harness_evidence_reservations_insert",
    "hm_recovery_fence_harness_evidence_reservations_update",
    "hm_recovery_fence_harness_evidence_reservations_delete",
)


def _sql() -> str:
    return (migrator.DEFAULT_MIGRATIONS_DIR / MIGRATION).read_text(encoding="utf-8")


def validate_evidence_kind_state_db(path, *, _expected_user_version=SCHEMA_VERSION) -> None:
    path = Path(path)
    if _expected_user_version not in schema_chain.accepted_versions(SCHEMA_VERSION):
        raise schema.HumanMemoryProgramEpochError("evidence_kind_schema_invalid")
    validate_context_use_recollect_state_db(path, _expected_user_version=_expected_user_version)
    sql = _sql()
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            if db.execute("SELECT schema_version,migration_sha256 FROM human_memory_migration_chain "
                    "WHERE migration_id=?", (MIGRATION,)).fetchone() != (
                        SCHEMA_VERSION, hashlib.sha256(sql.encode()).hexdigest()):
                raise ValueError("migration identity")
            if db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (MIGRATION,)).fetchone() is None:
                raise ValueError("migration marker")
            for name in REBUILT_OBJECTS:
                if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone() is None:
                    raise ValueError(f"schema object:{name}")
            ddl = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()[0]
            for kind in EVIDENCE_KINDS:
                if f"'{kind}'" not in ddl:
                    raise ValueError(f"evidence kind missing:{kind}")
            # The scratch table must be gone and the registry row untouched.
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                          (f"{TABLE}_v57",)).fetchone() is not None:
                raise ValueError("rebuild scratch table")
            if db.execute("SELECT count(*) FROM human_memory_recovery_table_registry "
                          "WHERE table_name=?", (TABLE,)).fetchone() != (1,):
                raise ValueError("recovery registration")
            if db.execute(f'PRAGMA foreign_key_check("{TABLE}")').fetchone() is not None:
                raise ValueError("foreign key")
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise schema.HumanMemoryProgramEpochError("evidence_kind_schema_invalid") from error


async def initialize_evidence_kind_state_db(path, *, fault_inject=None) -> None:
    path = Path(path)
    version = await migrator.read_user_version(path)
    if version >= SCHEMA_VERSION:
        validator = schema_chain.domain_validator(version)
        if validator is None:
            raise schema.HumanMemoryProgramEpochError("human_memory_program_future_database_unsupported")
        validator(path)
        return
    await initialize_context_use_recollect_state_db(path)
    async with schema._human_memory_program_lock(path):
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute("PRAGMA user_version") as cursor:
                    current = (await cursor.fetchone())[0]
                if current == SCHEMA_VERSION:
                    await db.rollback()
                    validate_evidence_kind_state_db(path)
                    return
                if current != SCHEMA_VERSION - 1:
                    raise schema.HumanMemoryProgramEpochError("evidence_kind_schema_base_differs")
                validate_context_use_recollect_state_db(path)
                await migrator._assert_effect_closure_cutover_preconditions(db)
                async with db.execute("SELECT state FROM human_memory_recovery_fence WHERE singleton=1") as cursor:
                    if await cursor.fetchone() != ("OPEN",):
                        raise schema.HumanMemoryProgramEpochError("evidence_kind_migration_recovery_fenced")
                async with db.execute(f'SELECT count(*) FROM "{TABLE}"') as cursor:
                    reserved = (await cursor.fetchone())[0]
                sql = _sql()
                await migrator._execute_transactional_script(db, sql)
                # The rebuild is a pure copy: losing a reservation would strand a
                # source_sequence and block every later run_terminal.
                async with db.execute(f'SELECT count(*) FROM "{TABLE}"') as cursor:
                    if (await cursor.fetchone())[0] != reserved:
                        raise schema.HumanMemoryProgramEpochError("evidence_kind_migration_row_loss")
                now = time.time()
                await db.execute("INSERT INTO human_memory_migration_chain VALUES (?,?,?,?)",
                    (MIGRATION, SCHEMA_VERSION, hashlib.sha256(sql.encode()).hexdigest(), now))
                await db.execute("INSERT INTO schema_migrations VALUES (?,?)", (MIGRATION, now))
                await db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                if fault_inject:
                    fault_inject("evidence_kind.migration.before_commit")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    validate_evidence_kind_state_db(path)
