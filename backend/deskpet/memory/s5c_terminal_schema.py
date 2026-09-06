"""Explicit v52 typed outbox completion; v50/v51 identities remain immutable."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import aiosqlite
from simple_harness.contracts import canonical_json

from deskpet.memory import migrator, schema
from deskpet.memory.s5c_timer_schema import (
    initialize_s5c_timer_state_db, validate_s5c_timer_state_db,
)

TERMINAL_MIGRATION = "s5c/044_prospective_terminals_v52.sql"
TERMINAL_TABLES = ("prospective_invalidation_terminals", "prospective_outbox_cursor_v52")


def _sql():
    return (migrator.DEFAULT_MIGRATIONS_DIR / TERMINAL_MIGRATION).read_text(encoding="utf-8")


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _validate_old_cursor(db, *, copied: bool):
    """Verify the entire frozen chain and its exact successor prefix, owner by owner."""
    previous = {}
    for row in db.execute("SELECT * FROM prospective_outbox_cursor ORDER BY owner_key,sequence"):
        owner, sequence, when, outbox, registration_id, prior, digest = row
        seq, after, old_hash = previous.get(owner, (0, None, "0" * 64))
        if (sequence != seq + 1 or prior != old_hash
                or (after is not None and (when, outbox) <= after)
                or digest != _hash([owner, sequence, [when, outbox], registration_id, prior])):
            raise ValueError("legacy cursor chain")
        registration = db.execute(
            "SELECT owner_key,outbox_id,phase,source_json,source_hash,authority_json,record_hash "
            "FROM prospective_scheduler_registrations WHERE record_id=?", (registration_id,),
        ).fetchone()
        if registration is None or registration[:3] != (owner, outbox, "prepared"):
            raise ValueError("legacy cursor registration")
        source, authority = json.loads(registration[3]), json.loads(registration[5])
        if (source["created_at"] != when or source["outbox_id"] != outbox
                or registration[4] != _hash(source)
                or registration[6] != _hash([owner, source, authority])):
            raise ValueError("legacy registration hash")
        if copied and db.execute(
            "SELECT * FROM prospective_outbox_cursor_v52 WHERE owner_key=? AND sequence=?",
            (owner, sequence),
        ).fetchone() != (*row, None):
            raise ValueError("legacy cursor copy differs")
        previous[owner] = (sequence, (when, outbox), digest)


def validate_s5c_terminal_state_db(path: str | Path) -> None:
    path = Path(path)
    validate_s5c_timer_state_db(path, _expected_user_version=52)
    sql = _sql()
    try:
        # Only compute the successor's schema objects. The insert/select has no
        # rows in this isolated expected schema; frozen base DDL is unmodified.
        base = (migrator.DEFAULT_MIGRATIONS_DIR /
                "s5c/042_prospective_memory_actions_v50.sql").read_text(encoding="utf-8")
        with sqlite3.connect(":memory:") as expected:
            expected.executescript(base)
            names = {r[0] for r in expected.execute("SELECT name FROM sqlite_master")}
            expected.executescript(sql)
            objects = [(name, ddl) for name, ddl in expected.execute(
                "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL") if name not in names]
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            if db.execute("SELECT schema_version,migration_sha256 FROM human_memory_migration_chain "
                    "WHERE migration_id=?", (TERMINAL_MIGRATION,)).fetchone() != (
                        52, hashlib.sha256(sql.encode()).hexdigest()):
                raise ValueError("migration identity")
            if db.execute("SELECT 1 FROM schema_migrations WHERE version=?",
                    (TERMINAL_MIGRATION,)).fetchone() is None:
                raise ValueError("migration marker")
            for name, ddl in objects:
                if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone() != (ddl,):
                    raise ValueError("schema object")
            for name in TERMINAL_TABLES:
                info = db.execute(f'PRAGMA table_info("{name}")').fetchall()
                columns = [dict(cid=r[0], name=r[1], type=r[2], notnull=r[3], default=r[4], pk=r[5]) for r in info]
                if db.execute("SELECT taxonomy,columns_json FROM human_memory_recovery_table_registry "
                        "WHERE table_name=?", (name,)).fetchone() != (
                            "A", json.dumps(columns, sort_keys=True, separators=(",", ":"))):
                    raise ValueError("recovery registration")
                for operation in ("insert", "update", "delete"):
                    trigger = f"hm_recovery_fence_{name}_{operation}"
                    ddl = (f'CREATE TRIGGER "{trigger}" BEFORE {operation.upper()} ON "{name}" '
                        "WHEN (SELECT state FROM human_memory_recovery_fence WHERE singleton=1)<>'OPEN' "
                        "BEGIN SELECT RAISE(ABORT,'human_memory_ingress_fenced'); END")
                    if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (trigger,)).fetchone() != (ddl,):
                        raise ValueError("recovery fence")
            _validate_old_cursor(db, copied=True)
            if db.execute("PRAGMA foreign_key_check(prospective_outbox_cursor_v52)").fetchone() is not None:
                raise ValueError("cursor foreign key")
    except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise schema.HumanMemoryProgramEpochError("s5c_terminal_schema_invalid") from error


async def initialize_s5c_terminal_state_db(db_path: str | Path, *, fault_inject=None) -> None:
    path = Path(db_path)
    version = await migrator.read_user_version(path)
    if version == 52:
        validate_s5c_terminal_state_db(path)
        return
    if version > 52:
        raise schema.HumanMemoryProgramEpochError("human_memory_program_future_database_unsupported")
    if path.exists():
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name IN (?,?) LIMIT 1", TERMINAL_TABLES).fetchone():
                if db.execute("PRAGMA user_version").fetchone() == (52,):
                    validate_s5c_terminal_state_db(path)
                    return
                raise schema.HumanMemoryProgramEpochError("s5c_terminal_unpublished_schema_incompatible")
    try:
        await initialize_s5c_timer_state_db(path)
    except schema.HumanMemoryProgramEpochError:
        if await migrator.read_user_version(path) != 52:
            raise
        validate_s5c_terminal_state_db(path)
        return
    async with schema._human_memory_program_lock(path):
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute("PRAGMA user_version") as cursor:
                    current = (await cursor.fetchone())[0]
                if current == 52:
                    await db.rollback()
                    validate_s5c_terminal_state_db(path)
                    return
                if current != 51:
                    raise schema.HumanMemoryProgramEpochError("s5c_terminal_base_version_differs")
                validate_s5c_timer_state_db(path)
                await migrator._assert_effect_closure_cutover_preconditions(db)
                async with db.execute("SELECT state FROM human_memory_recovery_fence WHERE singleton=1") as cursor:
                    if await cursor.fetchone() != ("OPEN",):
                        raise schema.HumanMemoryProgramEpochError("s5c_terminal_migration_recovery_fenced")
                with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as previous:
                    try:
                        _validate_old_cursor(previous, copied=False)
                    except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
                        raise schema.HumanMemoryProgramEpochError("s5c_terminal_legacy_chain_invalid") from error
                sql = _sql()
                await migrator._execute_transactional_script(db, sql)
                await migrator._register_recovery_tables(db, tuple((name, "A") for name in TERMINAL_TABLES))
                now = time.time()
                await db.execute("INSERT INTO human_memory_migration_chain VALUES (?,?,?,?)",
                    (TERMINAL_MIGRATION, 52, hashlib.sha256(sql.encode()).hexdigest(), now))
                await db.execute("INSERT INTO schema_migrations VALUES (?,?)", (TERMINAL_MIGRATION, now))
                await db.execute("PRAGMA user_version=52")
                if fault_inject:
                    fault_inject("s5c.terminal_migration.before_commit")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    if fault_inject:
        fault_inject("s5c.terminal_migration.after_commit")
    validate_s5c_terminal_state_db(path)
