# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4 memory DB migration runner (clean-room, 不共享 P3 代码).

Scope: 负责 ``backend/deskpet/memory/migrations/*.sql`` 的发现、执行、幂等
判定、失败回滚前置（backup）。P3 的 ``backend/memory/migrator.py`` 风格
接近，但两者 **不共享代码**——P3 服务老 memory.db，P4 服务 state.db，生命
周期各自独立。

Conventions（和 P3 对齐以免认知负担）：
  * 每条迁移是 ``<NNN>_<name>.sql`` 单文件，按文件名字典序执行
  * ``schema_migrations(version TEXT PRIMARY KEY, applied_at REAL)``
    表记录已应用的迁移，重跑时 skip
  * ``PRAGMA user_version`` 作为"总体 schema 版本"冗余指针——migration
    文件内部自己 set，便于 ensure_v9 快速判断"库已就位"

Design notes：
  * **Clean-room 起步不经历 v1..v8**。首版 DeskPet 直接 v9，所以 001_p4_initial_v9.sql
    是唯一迁移，内部 ``PRAGMA user_version=9``。未来 v10 再加 002_*.sql 即可。
  * **失败回滚策略在 schema.py 的 initialize_state_db() 里**——本模块只抛
    ``MigrationError``，由调用方决定是不是从 .bak 恢复。

Ref:
  * openspec/changes/p4-poseidon-agent-harness/design.md §D-MIGRATE-1 + R12
  * openspec/changes/p4-poseidon-agent-harness/specs/memory-system/spec.md
    Requirement "Schema Migration v8 → v9"
"""
from __future__ import annotations

import logging
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import aiosqlite

log = logging.getLogger(__name__)

# schema_migrations DDL —— 独立于 P3 的常量。命名一致是故意的：未来如果
# cutover 把 P3 memory 合并进来，schema 语义不变，省一次迁移。
_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at REAL NOT NULL
)
"""

# 默认 migration 目录 —— 和本文件同级的 migrations/。
DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# v9 是 P4 的起手目标版本。spec "Schema Migration v8 → v9" 定义。
TARGET_SCHEMA_VERSION = 34
_V17_MIGRATION = "009_memory_v2_v17.sql"
_V17_SCHEMA_VERSION = 17
_V18_MIGRATION = "010_context_os_v18.sql"
_V18_SCHEMA_VERSION = 18
_V19_MIGRATION = "011_message_projection_visibility_v19.sql"
_V19_SCHEMA_VERSION = 19
_V20_MIGRATION = "012_task_conversation_scope_v20.sql"
_V20_SCHEMA_VERSION = 20
_COMPANION_PROJECTION_MIGRATION = "013_companion_projection.sql"
_COMPANION_PROJECTION_SCHEMA_VERSION = 21
_CONTEXT_USAGE_HISTORY_MIGRATION = "014_context_usage_history_v22.sql"
_CONTEXT_USAGE_HISTORY_SCHEMA_VERSION = 22
_PROVIDER_BINDING_LIFECYCLE_MIGRATION = "015_provider_binding_lifecycle_v23.sql"
_PROVIDER_BINDING_LIFECYCLE_SCHEMA_VERSION = 23
_CONTEXT_USAGE_AUTHORITY_MIGRATION = "016_context_usage_authority_v24.sql"
_CONTEXT_USAGE_AUTHORITY_SCHEMA_VERSION = 24
_PROVIDER_WORKLOAD_AUDIT_MIGRATION = "017_provider_workload_audit_v25.sql"
_PROVIDER_WORKLOAD_AUDIT_SCHEMA_VERSION = 25
_MESSAGE_ARCHIVE_PROJECTION_MIGRATION = "018_message_archive_projection_v26.sql"
_MESSAGE_ARCHIVE_PROJECTION_SCHEMA_VERSION = 26
_PROVIDER_FAULT_CORRELATION_MIGRATION = "019_provider_fault_correlation_v27.sql"
_PROVIDER_FAULT_CORRELATION_SCHEMA_VERSION = 27
_SDK_CONTEXT_AUTHORITY_MIGRATION = "020_sdk_context_authority_v28.sql"
_SDK_CONTEXT_AUTHORITY_SCHEMA_VERSION = 28
_SDK_PROVIDER_PROJECTION_SEQUENCE_MIGRATION = (
    "021_sdk_provider_projection_sequence_v29.sql"
)
_SDK_PROVIDER_PROJECTION_SEQUENCE_SCHEMA_VERSION = 29
_AGENT_RUNTIME_MEMORY_MIGRATION = "022_agent_runtime_memory_v30.sql"
_AGENT_RUNTIME_MEMORY_SCHEMA_VERSION = 30
_OFFICIAL_MEMORY_INTEGRATION_MIGRATION = "023_official_memory_integration_v31.sql"
_OFFICIAL_MEMORY_INTEGRATION_SCHEMA_VERSION = 31
_PROJECT_SCOPED_SESSIONS_MIGRATION = "024_project_scoped_sessions_v32.sql"
_PROJECT_SCOPED_SESSIONS_SCHEMA_VERSION = 32
_LEGACY_SESSION_RESET_MIGRATION = "025_legacy_session_reset_v33.sql"
_LEGACY_SESSION_RESET_SCHEMA_VERSION = 33
_AUTOMATIC_SESSION_WORKSPACE_MIGRATION = "026_automatic_session_workspace_v34.sql"
_AUTOMATIC_SESSION_WORKSPACE_SCHEMA_VERSION = 34
HUMAN_MEMORY_PROGRAM_MIGRATION = "027_human_memory_program_v35.sql"
HUMAN_MEMORY_PROGRAM_SCHEMA_VERSION = 35
TASK_SCOPE_ARCHIVE_MIGRATION = "028_task_scope_archive_v36.sql"
TASK_SCOPE_ARCHIVE_SCHEMA_VERSION = 36
HUMAN_MEMORY_PROGRAM_MIGRATIONS = frozenset(
    {HUMAN_MEMORY_PROGRAM_MIGRATION, TASK_SCOPE_ARCHIVE_MIGRATION}
)

# From v23 onward every registered SQL step is executed with its DDL,
# schema marker, and user_version in one runner-owned transaction.  Migration
# files must not carry transaction control of their own.
MIGRATION_STEPS: dict[str, int] = {
    _PROVIDER_BINDING_LIFECYCLE_MIGRATION: _PROVIDER_BINDING_LIFECYCLE_SCHEMA_VERSION,
    _CONTEXT_USAGE_AUTHORITY_MIGRATION: _CONTEXT_USAGE_AUTHORITY_SCHEMA_VERSION,
    _PROVIDER_WORKLOAD_AUDIT_MIGRATION: _PROVIDER_WORKLOAD_AUDIT_SCHEMA_VERSION,
    _MESSAGE_ARCHIVE_PROJECTION_MIGRATION: _MESSAGE_ARCHIVE_PROJECTION_SCHEMA_VERSION,
    _PROVIDER_FAULT_CORRELATION_MIGRATION: _PROVIDER_FAULT_CORRELATION_SCHEMA_VERSION,
    _SDK_CONTEXT_AUTHORITY_MIGRATION: _SDK_CONTEXT_AUTHORITY_SCHEMA_VERSION,
    _SDK_PROVIDER_PROJECTION_SEQUENCE_MIGRATION: (
        _SDK_PROVIDER_PROJECTION_SEQUENCE_SCHEMA_VERSION
    ),
    _AGENT_RUNTIME_MEMORY_MIGRATION: _AGENT_RUNTIME_MEMORY_SCHEMA_VERSION,
    _OFFICIAL_MEMORY_INTEGRATION_MIGRATION: _OFFICIAL_MEMORY_INTEGRATION_SCHEMA_VERSION,
    _PROJECT_SCOPED_SESSIONS_MIGRATION: _PROJECT_SCOPED_SESSIONS_SCHEMA_VERSION,
    _LEGACY_SESSION_RESET_MIGRATION: _LEGACY_SESSION_RESET_SCHEMA_VERSION,
    _AUTOMATIC_SESSION_WORKSPACE_MIGRATION: _AUTOMATIC_SESSION_WORKSPACE_SCHEMA_VERSION,
    HUMAN_MEMORY_PROGRAM_MIGRATION: HUMAN_MEMORY_PROGRAM_SCHEMA_VERSION,
    TASK_SCOPE_ARCHIVE_MIGRATION: TASK_SCOPE_ARCHIVE_SCHEMA_VERSION,
}


async def _execute_transactional_script(
    db: aiosqlite.Connection,
    sql: str,
) -> None:
    """Execute a simple migration script without ``executescript`` commits.

    ``sqlite3.executescript`` commits an open transaction before running the
    script.  V18 needs its DDL, marker, and ``user_version`` to share one
    transaction, so statements are split with SQLite's own completeness
    parser and executed one by one.
    """

    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if not sqlite3.complete_statement(pending):
            continue
        statement = pending.strip()
        pending = ""
        if statement:
            await db.execute(statement)
    if pending.strip():
        raise sqlite3.OperationalError("incomplete migration statement")


def _contains_forbidden_transaction_control(sql: str) -> bool:
    """Inspect complete statements without mistaking trigger bodies for BEGIN.

    A trigger's ``BEGIN ... END`` is part of a single ``CREATE TRIGGER``
    statement and is safe inside the runner-owned transaction.
    """

    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if not sqlite3.complete_statement(pending):
            continue
        statement = re.sub(r"\A(?:\s*--[^\n]*(?:\n|\Z))*", "", pending).lstrip().lower()
        pending = ""
        if re.match(r"(?:begin|commit|rollback)\b|pragma\s+user_version\b", statement):
            return True
    return bool(pending.strip())


class MigrationError(RuntimeError):
    """Raised when a migration step fails.

    调用方（schema.initialize_state_db）捕获它来触发 .bak 回滚。它本身
    不做任何状态回滚 —— DB 可能处于"部分执行"状态，只能靠外部备份恢复。
    """


def _discover(migrations_dir: Path) -> list[Path]:
    """返回 ``*.sql`` 文件列表，按文件名字典序（NNN_ 前缀保序）。

    目录不存在直接返回空——调用方负责处理"库已 vX 但没迁移文件"的场景。
    """
    if not migrations_dir.exists():
        return []
    return sorted(p for p in migrations_dir.iterdir() if p.suffix == ".sql")


def _safe_timestamp() -> str:
    """返回文件名安全的 ISO8601 时间戳（``:`` 替换成 ``-``）。

    用于 ``.bak.<ts>`` 后缀 —— Windows 文件名不能包含 ``:``。
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # "2026-04-24T12:34:56+00:00" → "2026-04-24T12-34-56"
    return now.isoformat().replace(":", "-").split("+")[0]


#: Cap on the number of `state.db.bak.*` files retained after each
#: successful backup. 2026-05-21 incident: a hot loop calling
#: `initialize_state_db` produced 400+ backups (~20 GB) under
#: `%AppData%\deskpet\data` before anyone noticed. We now prune
#: oldest files past this threshold so the worst case is bounded
#: at MAX_BACKUPS * sizeof(state.db) — ~210 MB at v9 size.
MAX_BACKUPS = 3


def _prune_old_backups(db_path: Path, keep: int = MAX_BACKUPS) -> int:
    """Delete `state.db.bak.*` siblings of `db_path` past the newest `keep`.

    Pure utility: no DB access, just `Path.glob`. Returns the count
    deleted so the caller can log a single "pruned N stale backups"
    line at DEBUG level. Failures (locked file, permission denied)
    are swallowed because pruning is best-effort — we never want a
    retention pass to crash the migration path.

    Ordering: sort by **filename** descending rather than by mtime.
    The filename embeds a UTC ISO8601 timestamp (see
    ``_safe_timestamp``) which is lexicographically sortable. Sorting
    by mtime would be wrong because backup metadata may preserve source
    timestamps. The embedded timestamp makes filename ordering unambiguous.
    """
    parent = db_path.parent
    name = db_path.name
    candidates = sorted(
        parent.glob(f"{name}.bak.*"),
        key=lambda p: p.name,
        reverse=True,
    )
    pruned = 0
    for old in candidates[keep:]:
        try:
            old.unlink()
            old.with_name(f"{old.name}.manifest.json").unlink(missing_ok=True)
            pruned += 1
        except OSError:
            # Locked / disappeared mid-iteration — fine, try next.
            continue
    return pruned


async def backup_db(db_path: str | Path) -> Path:
    """复制 ``db_path`` 到 ``<db_path>.bak.<timestamp>`` 并返回备份路径。

    使用 SQLite online backup API 生成一致快照。不存在原库时返回 None？**不**
    ——本函数约定只在"库已存在要迁移"时调用，调用方先检查 exists 再调。

    失败（权限、磁盘满）直接 raise OSError，由上层处理。

    2026-05-21: After creating the new backup we prune older ones past
    ``MAX_BACKUPS``. This is intentionally **inside** ``backup_db`` not
    in the caller — anyone who calls this helper inherits the retention
    policy automatically, including future migration paths we don't
    yet have.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"cannot backup: {db_path} does not exist")
    bak_path = db_path.with_name(f"{db_path.name}.bak.{_safe_timestamp()}")
    manifest_path = bak_path.with_name(f"{bak_path.name}.manifest.json")

    # A state database may have committed pages only in WAL even when the main
    # process is between requests.  Make that state explicit, fail if another
    # writer prevents a complete checkpoint, then use SQLite's online backup
    # API rather than copying the main file by assumption.
    source = sqlite3.connect(db_path)
    destination: sqlite3.Connection | None = None
    try:
        checkpoint = source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise OSError(f"state.db WAL checkpoint remained busy: {checkpoint}")
        destination = sqlite3.connect(bak_path)
        source.backup(destination)
        destination.commit()
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        destination.execute("PRAGMA journal_mode=DELETE")
    finally:
        if destination is not None:
            destination.close()
        source.close()

    with sqlite3.connect(f"file:{bak_path}?mode=ro", uri=True) as check_db:
        quick_check = check_db.execute("PRAGMA quick_check").fetchone()
        user_version = int(check_db.execute("PRAGMA user_version").fetchone()[0])
    if quick_check is None or str(quick_check[0]).lower() != "ok":
        bak_path.unlink(missing_ok=True)
        raise OSError(f"state.db backup quick_check failed: {quick_check}")
    digest = hashlib.sha256(bak_path.read_bytes()).hexdigest()
    manifest = {
        "format": "simple_harness.state-db-backup.v1",
        "source_name": db_path.name,
        "backup_name": bak_path.name,
        "sha256": digest,
        "size_bytes": bak_path.stat().st_size,
        "user_version": user_version,
    }
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_manifest, manifest_path)
    pruned = _prune_old_backups(db_path)
    if pruned:
        log.debug("pruned %d stale state.db backups", pruned)
    return bak_path


async def read_user_version(db_path: str | Path) -> int:
    """Return ``PRAGMA user_version`` for ``db_path``, or 0 if the DB
    doesn't exist yet.

    Extracted as a public helper (was inlined inside ``ensure_v9``) so
    ``initialize_state_db`` can use it to decide whether to do the
    expensive backup-then-migrate dance at all. When the DB is already
    at the target version, both backup and migration are no-ops; we
    skip both to keep the on-disk backup count bounded.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return 0
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def run_migrations(
    db_path: str | Path,
    migrations_dir: Path | None = None,
    *,
    fault_inject: Callable[[str], None] | None = None,
    include_human_memory_program: bool = False,
) -> list[str]:
    """顺序执行所有未应用的迁移文件，返回本次应用的版本列表。

    行为：
      1. 确保父目录存在（tmp_path 场景常见）
      2. 打开 aiosqlite 连接
      3. 建 ``schema_migrations`` 表（幂等）
      4. 读取已应用版本集合
      5. 对每个未应用文件：普通迁移执行 ``executescript``；v17 执行
         Python introspection callback，并在同一事务内写 marker/user_version
      6. 任何 SQL 异常 → 抛 ``MigrationError``，当前 commit 不落盘，但
         **之前成功的迁移已落盘**。调用方用 backup 兜底完整 DB 回滚。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migrations_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    files = _discover(migrations_dir)
    if not include_human_memory_program:
        # v35 starts a new, fresh-only data epoch.  Ordinary state.db startup
        # must never turn an existing v34 database into that epoch merely
        # because a new SQL file is present on disk.
        files = [p for p in files if p.name not in HUMAN_MEMORY_PROGRAM_MIGRATIONS]

    applied_now: list[str] = []
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_MIGRATIONS_TABLE_SQL)
        await db.commit()
        cursor = await db.execute("SELECT version FROM schema_migrations")
        already = {row[0] for row in await cursor.fetchall()}
        await cursor.close()

        for path in files:
            version = path.name  # e.g. "001_p4_initial_v9.sql"
            if version in already:
                continue
            sql = path.read_text(encoding="utf-8")
            if version == _V17_MIGRATION:
                try:
                    from deskpet.memory.memory_v2_schema import (
                        migrate_memory_v2_v17,
                    )

                    await db.execute("BEGIN IMMEDIATE")
                    await migrate_memory_v2_v17(db)
                    await db.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (version, time.time()),
                    )
                    await db.execute(f"PRAGMA user_version={_V17_SCHEMA_VERSION}")
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    log.error(
                        "migration %s failed: %s (db=%s)",
                        version,
                        exc,
                        db_path,
                    )
                    raise MigrationError(
                        f"migration {version} failed: {exc}"
                    ) from exc
                applied_now.append(version)
                continue
            if version == _V18_MIGRATION:
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    await _execute_transactional_script(db, sql)
                    await db.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (version, time.time()),
                    )
                    await db.execute(f"PRAGMA user_version={_V18_SCHEMA_VERSION}")
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    log.error(
                        "migration %s failed: %s (db=%s)",
                        version,
                        exc,
                        db_path,
                    )
                    raise MigrationError(
                        f"migration {version} failed: {exc}"
                    ) from exc
                applied_now.append(version)
                continue
            if version == _V19_MIGRATION:
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    await _execute_transactional_script(db, sql)
                    await db.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (version, time.time()),
                    )
                    await db.execute(f"PRAGMA user_version={_V19_SCHEMA_VERSION}")
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    log.error(
                        "migration %s failed: %s (db=%s)",
                        version,
                        exc,
                        db_path,
                    )
                    raise MigrationError(
                        f"migration {version} failed: {exc}"
                    ) from exc
                applied_now.append(version)
                continue
            if version == _COMPANION_PROJECTION_MIGRATION:
                try:
                    from deskpet.companion.companion_message_projection import (
                        migrate_companion_message_projection,
                    )

                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("BEGIN IMMEDIATE")
                    await migrate_companion_message_projection(db)
                    await db.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (version, time.time()),
                    )
                    await db.execute(
                        f"PRAGMA user_version={_COMPANION_PROJECTION_SCHEMA_VERSION}"
                    )
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    log.error(
                        "migration %s failed: %s (db=%s)",
                        version,
                        exc,
                        db_path,
                    )
                    raise MigrationError(
                        f"migration {version} failed: {exc}"
                    ) from exc
                applied_now.append(version)
                continue
            if version == _CONTEXT_USAGE_HISTORY_MIGRATION:
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    await _execute_transactional_script(db, sql)
                    await db.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (version, time.time()),
                    )
                    await db.execute(
                        f"PRAGMA user_version={_CONTEXT_USAGE_HISTORY_SCHEMA_VERSION}"
                    )
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    log.error(
                        "migration %s failed: %s (db=%s)",
                        version,
                        exc,
                        db_path,
                    )
                    raise MigrationError(
                        f"migration {version} failed: {exc}"
                    ) from exc
                applied_now.append(version)
                continue
            if version in MIGRATION_STEPS:
                if _contains_forbidden_transaction_control(sql):
                    raise MigrationError(
                        f"migration {version} contains forbidden transaction control"
                    )
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    await _execute_transactional_script(db, sql)
                    if version == HUMAN_MEMORY_PROGRAM_MIGRATION:
                        migration_sha256 = hashlib.sha256(
                            sql.encode("utf-8")
                        ).hexdigest()
                        await db.execute(
                            "INSERT INTO human_memory_program_marker("
                            "singleton,format_epoch,schema_version,migration_id,"
                            "migration_sha256,initialized_at) VALUES "
                            "(1,'human-memory-v1',1,?,?,?)",
                            (version, migration_sha256, time.time()),
                        )
                    if version == TASK_SCOPE_ARCHIVE_MIGRATION:
                        migration_sha256 = hashlib.sha256(
                            sql.encode("utf-8")
                        ).hexdigest()
                        await db.execute(
                            "INSERT INTO task_scope_archive_marker("
                            "singleton,format_epoch,schema_version,migration_id,"
                            "migration_sha256,initialized_at) VALUES "
                            "(1,'human-memory-v1',1,?,?,?)",
                            (version, migration_sha256, time.time()),
                        )
                    await db.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (version, time.time()),
                    )
                    await db.execute(
                        f"PRAGMA user_version={MIGRATION_STEPS[version]}"
                    )
                    if version == _PROJECT_SCOPED_SESSIONS_MIGRATION and fault_inject:
                        fault_inject("before_ddl_commit")
                    if version == _LEGACY_SESSION_RESET_MIGRATION and fault_inject:
                        fault_inject("before_legacy_reset_commit")
                    if version == HUMAN_MEMORY_PROGRAM_MIGRATION and fault_inject:
                        fault_inject("before_human_memory_program_commit")
                    if version == TASK_SCOPE_ARCHIVE_MIGRATION and fault_inject:
                        fault_inject("before_task_scope_archive_commit")
                    await db.commit()
                    if version == _PROJECT_SCOPED_SESSIONS_MIGRATION and fault_inject:
                        fault_inject("after_ddl_commit")
                    if version == _LEGACY_SESSION_RESET_MIGRATION and fault_inject:
                        fault_inject("after_legacy_reset_commit")
                    if version == HUMAN_MEMORY_PROGRAM_MIGRATION and fault_inject:
                        fault_inject("after_human_memory_program_commit")
                    if version == TASK_SCOPE_ARCHIVE_MIGRATION and fault_inject:
                        fault_inject("after_task_scope_archive_commit")
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    log.error(
                        "migration %s failed: %s (db=%s)",
                        version,
                        exc,
                        db_path,
                    )
                    raise MigrationError(
                        f"migration {version} failed: {exc}"
                    ) from exc
                applied_now.append(version)
                continue
            try:
                # executescript 跑多语句；隐式 BEGIN/COMMIT 由 aiosqlite
                # 的 isolation 控制，这里显式再 commit 一次保险。
                await db.executescript(sql)
                await db.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, time.time()),
                )
                await db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                log.error(
                    "migration %s failed: %s (db=%s)",
                    version,
                    exc,
                    db_path,
                )
                raise MigrationError(
                    f"migration {version} failed: {exc}"
                ) from exc
            applied_now.append(version)

        # Re-applying an older migration during a repair can lower
        # user_version even though a newer marker is already durable. Restore
        # only to the highest marker actually present; V17 must not claim V18
        # before the V18 DDL transaction commits.
        durable_markers = already | set(applied_now)
        if _PROVIDER_BINDING_LIFECYCLE_MIGRATION in durable_markers:
            # Recovery tooling can recreate an older table after the v23
            # marker exists. Repair the marker's physical invariant without
            # claiming that a new migration was applied.
            column_cursor = await db.execute("PRAGMA table_info(code_session_provider)")
            columns = {str(row[1]) for row in await column_cursor.fetchall()}
            await column_cursor.close()
            required_columns = {
                "provider_incarnation_id": "TEXT",
                "provider_config_revision": "INTEGER",
                "binding_epoch": "INTEGER NOT NULL DEFAULT 0",
            }
            missing = [name for name in required_columns if name not in columns]
            if missing:
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    for name in missing:
                        await db.execute(
                            f"ALTER TABLE code_session_provider ADD COLUMN "
                            f"{name} {required_columns[name]}"
                        )
                    await db.execute(
                        "CREATE TABLE IF NOT EXISTS provider_binding_reconcile_marker ("
                        "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                        "registry_digest TEXT NOT NULL, completed_at REAL NOT NULL)"
                    )
                    await db.execute(
                        f"PRAGMA user_version={_PROVIDER_BINDING_LIFECYCLE_SCHEMA_VERSION}"
                    )
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    raise MigrationError(
                        "provider binding lifecycle repair failed"
                    ) from exc
        durable_version = (
            TASK_SCOPE_ARCHIVE_SCHEMA_VERSION
            if TASK_SCOPE_ARCHIVE_MIGRATION in durable_markers
            else HUMAN_MEMORY_PROGRAM_SCHEMA_VERSION
            if HUMAN_MEMORY_PROGRAM_MIGRATION in durable_markers
            else _AUTOMATIC_SESSION_WORKSPACE_SCHEMA_VERSION
            if _AUTOMATIC_SESSION_WORKSPACE_MIGRATION in durable_markers
            else _LEGACY_SESSION_RESET_SCHEMA_VERSION
            if _LEGACY_SESSION_RESET_MIGRATION in durable_markers
            else _PROJECT_SCOPED_SESSIONS_SCHEMA_VERSION
            if _PROJECT_SCOPED_SESSIONS_MIGRATION in durable_markers
            else _OFFICIAL_MEMORY_INTEGRATION_SCHEMA_VERSION
            if _OFFICIAL_MEMORY_INTEGRATION_MIGRATION in durable_markers
            else _AGENT_RUNTIME_MEMORY_SCHEMA_VERSION
            if _AGENT_RUNTIME_MEMORY_MIGRATION in durable_markers
            else _SDK_PROVIDER_PROJECTION_SEQUENCE_SCHEMA_VERSION
            if _SDK_PROVIDER_PROJECTION_SEQUENCE_MIGRATION in durable_markers
            else _SDK_CONTEXT_AUTHORITY_SCHEMA_VERSION
            if _SDK_CONTEXT_AUTHORITY_MIGRATION in durable_markers
            else _PROVIDER_FAULT_CORRELATION_SCHEMA_VERSION
            if _PROVIDER_FAULT_CORRELATION_MIGRATION in durable_markers
            else _MESSAGE_ARCHIVE_PROJECTION_SCHEMA_VERSION
            if _MESSAGE_ARCHIVE_PROJECTION_MIGRATION in durable_markers
            else _PROVIDER_WORKLOAD_AUDIT_SCHEMA_VERSION
            if _PROVIDER_WORKLOAD_AUDIT_MIGRATION in durable_markers
            else _CONTEXT_USAGE_AUTHORITY_SCHEMA_VERSION
            if _CONTEXT_USAGE_AUTHORITY_MIGRATION in durable_markers
            else _PROVIDER_BINDING_LIFECYCLE_SCHEMA_VERSION
            if _PROVIDER_BINDING_LIFECYCLE_MIGRATION in durable_markers
            else _CONTEXT_USAGE_HISTORY_SCHEMA_VERSION
            if _CONTEXT_USAGE_HISTORY_MIGRATION in durable_markers
            else _COMPANION_PROJECTION_SCHEMA_VERSION
            if _COMPANION_PROJECTION_MIGRATION in durable_markers
            else _V20_SCHEMA_VERSION
            if _V20_MIGRATION in durable_markers
            else _V19_SCHEMA_VERSION
            if _V19_MIGRATION in durable_markers
            else _V18_SCHEMA_VERSION
            if _V18_MIGRATION in durable_markers
            else _V17_SCHEMA_VERSION
            if _V17_MIGRATION in durable_markers
            else None
        )
        if durable_version is not None:
            cursor = await db.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            await cursor.close()
            if row is None or int(row[0]) < durable_version:
                await db.execute(f"PRAGMA user_version={durable_version}")
                await db.commit()

    return applied_now


async def ensure_v9(
    db_path: str | Path,
    migrations_dir: Path | None = None,
    *,
    fault_inject: Callable[[str], None] | None = None,
    include_human_memory_program: bool = False,
) -> list[str]:
    """启动守门：保证 ``db_path`` 的 schema 至少在 v9。

    流程：
      * 读 ``PRAGMA user_version``
      * ``version == 0`` → 全新库，跑所有 migrations
      * ``0 < version < 9`` → 老 P3 库（理论上 DeskPet 不会遇到；但 spec
        写了 "v8→v9" 所以代码要 defensive）。log warning 然后仍跑增量，
        ``schema_migrations`` 里没记录的文件会被补上。
      * ``version >= 9`` → 正常路径。仍跑 ``run_migrations`` 来确保
        ``schema_migrations`` 和 user_version 一致（比如手动加的 002_*.sql）。
      * 返回本次 applied 的版本列表（可能为空 = 幂等场景）。

    失败：任何子步骤抛 ``MigrationError`` 或 ``sqlite3.Error`` 上抛。
    """
    db_path = Path(db_path)
    # 先单独 open 读 user_version（run_migrations 会自己再 open，
    # 故意分两次以便精确 log）。
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        await cursor.close()
    current = int(row[0]) if row else 0

    if current == 0:
        log.info("state.db fresh (user_version=0), running all migrations")
    elif current < TARGET_SCHEMA_VERSION:
        log.warning(
            "state.db user_version=%d < %d; DeskPet clean-room has no real v1..v8 "
            "history — treating as incremental upgrade and will re-run unapplied SQL",
            current,
            TARGET_SCHEMA_VERSION,
        )
    else:
        log.info(
            "state.db user_version=%d >= %d, applying any pending incremental migrations",
            current,
            TARGET_SCHEMA_VERSION,
        )

    return await run_migrations(
        db_path,
        migrations_dir=migrations_dir,
        fault_inject=fault_inject,
        include_human_memory_program=include_human_memory_program,
    )
