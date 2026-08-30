# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4 state.db 启动守门：备份 → 迁移 → 失败回滚。

职责（和 migrator.py 的分工）：
  * ``migrator.py`` 只关心"把 SQL 文件按序跑起来"，不做备份恢复
  * ``schema.py``（本文件）负责"在 DB 被动过之前把 .bak 留下，失败时
    从 .bak 还原"——这是 R12 风险兜底，独立出来以便主流程（未来
    MemoryManager.initialize）走简单路径 `initialize_state_db(p)`。

Degrade contract：
  * 成功 → return（DB 就位，v9+）
  * 失败 → 从 .bak 恢复 + raise ``InitializeError``
  * 上层（MemoryManager / main.py）收到 InitializeError MUST 把 L2/L3
    关掉只跑 L1 文件记忆。这一步在 P4-S4 的 manager.py 里落实。

Ref:
  * design.md §D-MIGRATE-1 + R12
  * spec "Schema Migration v8 → v9" Scenario "Migration failure rollback"
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from deskpet.memory.migrator import (
    DEFAULT_MIGRATIONS_DIR,
    HUMAN_MEMORY_PROGRAM_MIGRATION,
    HUMAN_MEMORY_PROGRAM_SCHEMA_VERSION,
    TARGET_SCHEMA_VERSION,
    TASK_SCOPE_ARCHIVE_MIGRATION,
    TASK_SCOPE_ARCHIVE_SCHEMA_VERSION,
    TASK_SCOPE_PROVISION_MIGRATION,
    TASK_SCOPE_PROVISION_SCHEMA_VERSION,
    TASK_WORKSPACE_BINDING_MIGRATION,
    TASK_WORKSPACE_BINDING_SCHEMA_VERSION,
    backup_db,
    ensure_v9,
    read_user_version,
)
from deskpet.memory.project_session_reset import (
    finalize_legacy_session_reset,
    run_legacy_session_reset,
)
from deskpet.memory.storage import ensure_owner_only_state_db

log = logging.getLogger(__name__)


class InitializeError(RuntimeError):
    """L2 初始化失败——调用方需降级启动（无 L2/L3）。"""


class HumanMemoryProgramEpochError(InitializeError):
    """The requested database is not the fresh human-memory-v1 epoch."""

    code = "human_memory_program_legacy_database_unsupported"


_HUMAN_MEMORY_PROGRAM_LOCKS: dict[str, asyncio.Lock] = {}


def _human_memory_program_lock(db_path: Path) -> asyncio.Lock:
    return _HUMAN_MEMORY_PROGRAM_LOCKS.setdefault(
        str(db_path.resolve()), asyncio.Lock()
    )


def _migration_sha256(migration_id: str) -> str:
    migration = DEFAULT_MIGRATIONS_DIR / migration_id
    return hashlib.sha256(migration.read_bytes()).hexdigest()


def _has_bootstrap_marker(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='human_memory_program_bootstrap'"
            ).fetchone()
            if row is None:
                return False
            marker = db.execute(
                "SELECT format_epoch,origin FROM human_memory_program_bootstrap "
                "WHERE singleton=1"
            ).fetchone()
    except sqlite3.Error:
        return False
    return marker == ("human-memory-v1", "fresh-empty-database")


def _write_bootstrap_marker(db_path: Path) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS human_memory_program_bootstrap("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
            "format_epoch TEXT NOT NULL CHECK(format_epoch='human-memory-v1'),"
            "origin TEXT NOT NULL CHECK(origin='fresh-empty-database'),"
            "created_at REAL NOT NULL)"
        )
        db.execute(
            "INSERT OR IGNORE INTO human_memory_program_bootstrap("
            "singleton,format_epoch,origin,created_at) "
            "VALUES (1,'human-memory-v1','fresh-empty-database',?)",
            (time.time(),),
        )
        db.execute(
            "CREATE TRIGGER IF NOT EXISTS human_memory_bootstrap_no_update "
            "BEFORE UPDATE ON human_memory_program_bootstrap BEGIN "
            "SELECT RAISE(ABORT,'human_memory_append_only'); END"
        )
        db.execute(
            "CREATE TRIGGER IF NOT EXISTS human_memory_bootstrap_no_delete "
            "BEFORE DELETE ON human_memory_program_bootstrap BEGIN "
            "SELECT RAISE(ABORT,'human_memory_append_only'); END"
        )
        db.commit()


def _validate_human_memory_program_marker(
    db_path: Path, *, expected_user_version: int
) -> None:
    expected_sha256 = _migration_sha256(HUMAN_MEMORY_PROGRAM_MIGRATION)
    try:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT format_epoch,schema_version,migration_id,migration_sha256 "
                "FROM human_memory_program_marker WHERE singleton=1"
            ).fetchone()
            schema_marker = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (HUMAN_MEMORY_PROGRAM_MIGRATION,),
            ).fetchone()
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise HumanMemoryProgramEpochError(
            "human_memory_program_marker_invalid"
        ) from exc
    expected = (
        "human-memory-v1",
        1,
        HUMAN_MEMORY_PROGRAM_MIGRATION,
        expected_sha256,
    )
    if (
        row != expected
        or schema_marker is None
        or version != expected_user_version
    ):
        raise HumanMemoryProgramEpochError("human_memory_program_marker_invalid")


def _validate_task_scope_archive_marker(
    db_path: Path, *, expected_user_version: int
) -> None:
    expected_sha256 = _migration_sha256(TASK_SCOPE_ARCHIVE_MIGRATION)
    try:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT format_epoch,schema_version,migration_id,migration_sha256 "
                "FROM task_scope_archive_marker WHERE singleton=1"
            ).fetchone()
            schema_marker = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (TASK_SCOPE_ARCHIVE_MIGRATION,),
            ).fetchone()
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise HumanMemoryProgramEpochError("task_scope_archive_marker_invalid") from exc
    expected = (
        "human-memory-v1",
        1,
        TASK_SCOPE_ARCHIVE_MIGRATION,
        expected_sha256,
    )
    if (
        row != expected
        or schema_marker is None
        or version != expected_user_version
    ):
        raise HumanMemoryProgramEpochError("task_scope_archive_marker_invalid")


def _validate_task_scope_provision_marker(
    db_path: Path, *, expected_user_version: int
) -> None:
    expected_sha256 = _migration_sha256(TASK_SCOPE_PROVISION_MIGRATION)
    try:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT format_epoch,schema_version,migration_id,migration_sha256 "
                "FROM task_scope_provision_marker WHERE singleton=1"
            ).fetchone()
            schema_marker = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (TASK_SCOPE_PROVISION_MIGRATION,),
            ).fetchone()
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise HumanMemoryProgramEpochError("task_scope_provision_marker_invalid") from exc
    expected = (
        "human-memory-v1",
        1,
        TASK_SCOPE_PROVISION_MIGRATION,
        expected_sha256,
    )
    if (
        row != expected
        or schema_marker is None
        or version != expected_user_version
    ):
        raise HumanMemoryProgramEpochError("task_scope_provision_marker_invalid")


def _validate_task_workspace_binding_marker(db_path: Path) -> None:
    expected_sha256 = _migration_sha256(TASK_WORKSPACE_BINDING_MIGRATION)
    try:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT format_epoch,schema_version,migration_id,migration_sha256 "
                "FROM task_workspace_binding_marker WHERE singleton=1"
            ).fetchone()
            schema_marker = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (TASK_WORKSPACE_BINDING_MIGRATION,),
            ).fetchone()
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise HumanMemoryProgramEpochError(
            "task_workspace_binding_marker_invalid"
        ) from exc
    expected = (
        "human-memory-v1",
        1,
        TASK_WORKSPACE_BINDING_MIGRATION,
        expected_sha256,
    )
    if row != expected or schema_marker is None or version != TASK_WORKSPACE_BINDING_SCHEMA_VERSION:
        raise HumanMemoryProgramEpochError("task_workspace_binding_marker_invalid")


async def initialize_human_memory_program_state_db(
    db_path: str | Path,
    *,
    fault_inject: Callable[[str], None] | None = None,
) -> None:
    """Initialize or reopen the fresh-only ``human-memory-v1`` data epoch.

    Existing v34 and older databases are rejected before a migration, backup,
    reset, or delete path can touch them.  A durable bootstrap marker lets a
    genuinely fresh database resume if a base or v35 commit is interrupted.
    """

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with _human_memory_program_lock(path):
        current = await read_user_version(path)
        bootstrap = _has_bootstrap_marker(path)
        if current == TASK_WORKSPACE_BINDING_SCHEMA_VERSION:
            _validate_human_memory_program_marker(
                path, expected_user_version=TASK_WORKSPACE_BINDING_SCHEMA_VERSION
            )
            _validate_task_scope_archive_marker(
                path, expected_user_version=TASK_WORKSPACE_BINDING_SCHEMA_VERSION
            )
            _validate_task_scope_provision_marker(
                path, expected_user_version=TASK_WORKSPACE_BINDING_SCHEMA_VERSION
            )
            _validate_task_workspace_binding_marker(path)
            return
        if current > TASK_WORKSPACE_BINDING_SCHEMA_VERSION:
            raise HumanMemoryProgramEpochError(
                "human_memory_program_future_database_unsupported"
            )
        if current == HUMAN_MEMORY_PROGRAM_SCHEMA_VERSION:
            _validate_human_memory_program_marker(
                path, expected_user_version=HUMAN_MEMORY_PROGRAM_SCHEMA_VERSION
            )
        if current == TASK_SCOPE_ARCHIVE_SCHEMA_VERSION:
            _validate_human_memory_program_marker(
                path, expected_user_version=TASK_SCOPE_ARCHIVE_SCHEMA_VERSION
            )
            _validate_task_scope_archive_marker(
                path, expected_user_version=TASK_SCOPE_ARCHIVE_SCHEMA_VERSION
            )
        if current == TASK_SCOPE_PROVISION_SCHEMA_VERSION:
            _validate_human_memory_program_marker(
                path, expected_user_version=TASK_SCOPE_PROVISION_SCHEMA_VERSION
            )
            _validate_task_scope_archive_marker(
                path, expected_user_version=TASK_SCOPE_PROVISION_SCHEMA_VERSION
            )
            _validate_task_scope_provision_marker(
                path, expected_user_version=TASK_SCOPE_PROVISION_SCHEMA_VERSION
            )
        if current == 0 and not bootstrap:
            if path.exists():
                with sqlite3.connect(
                    f"file:{path.resolve()}?mode=ro", uri=True
                ) as db:
                    user_tables = db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                if user_tables:
                    raise HumanMemoryProgramEpochError(
                        HumanMemoryProgramEpochError.code
                    )
            _write_bootstrap_marker(path)
            bootstrap = True
        if not bootstrap:
            raise HumanMemoryProgramEpochError(HumanMemoryProgramEpochError.code)

        # The database was durably claimed while it was empty.  Existing base
        # migrations may now finish/replay, followed by the opt-in program steps.
        await initialize_state_db(path, fault_inject=fault_inject)
        try:
            await ensure_v9(
                path,
                fault_inject=fault_inject,
                include_human_memory_program=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise InitializeError(
                f"human memory program initialization failed: {exc}"
            ) from exc
        _validate_human_memory_program_marker(
            path, expected_user_version=TASK_WORKSPACE_BINDING_SCHEMA_VERSION
        )
        _validate_task_scope_archive_marker(
            path, expected_user_version=TASK_WORKSPACE_BINDING_SCHEMA_VERSION
        )
        _validate_task_scope_provision_marker(
            path, expected_user_version=TASK_WORKSPACE_BINDING_SCHEMA_VERSION
        )
        _validate_task_workspace_binding_marker(path)


async def initialize_state_db(
    db_path: str | Path,
    *,
    fault_inject: Callable[[str], None] | None = None,
) -> None:
    """确保 ``db_path`` 指向一个 v9+ 的 state.db。

    行为顺序（故意冗长，便于 bug 定位）：
      1. 父目录 mkdir（tmp_path 场景）
      2. 如果 db 文件已存在 → ``backup_db`` 拷一份 ``.bak.<ts>``
         * 新库（文件不存在）跳过，这种情况一定是 fresh install，
           迁移失败也没什么能"丢"，只需要上层降级。
      3. 调 ``ensure_v9(db)``
      4. 成功 → return
      5. 失败：
         * 如果之前有 .bak，把原 db 文件删掉，.bak 复制回 db_path
           * 此举确保 state.db 回到迁移前状态——即便 ``ensure_v9``
             只跑了一半导致 schema 半破也没关系
         * 无论 .bak 是否存在，最后都 raise ``InitializeError``
           * 上层据此决定进入降级模式

    Notes：
      * v33 reset 需要在 MemoryManager 打开前清理 ``messages_vec``，因此
        这里会为该一次性步骤加载 sqlite-vec；失败时 fail closed。
      * 普通迁移仍由本函数承担备份 + 回滚；v33 已提交后的跨库存储清理
        不恢复旧库，而是依照 durable phase 在下次启动继续。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_owner_only_state_db(db_path)

    # 2026-05-21 fix: only backup when a migration is actually pending.
    # Previously we backed up on every initialize(), which combined with
    # any caller that re-instantiates SessionDB per request produced
    # 400+ backup files (~20 GB) under %AppData%\deskpet\data. Now we
    # peek at PRAGMA user_version first; if the DB is already at the
    # target version, ensure_v9 will short-circuit and there's nothing
    # to roll back from — backup would just be cruft.
    need_backup = False
    if db_path.exists():
        try:
            current_version = await read_user_version(db_path)
        except Exception as exc:  # noqa: BLE001  # corrupt DB → conservative path
            # Couldn't read the version → safest to take the backup
            # before ensure_v9 touches anything.
            log.warning(
                "could not read state.db user_version (%s); will backup defensively",
                exc,
            )
            need_backup = True
        else:
            need_backup = current_version < TARGET_SCHEMA_VERSION
            if not need_backup:
                log.debug(
                    "state.db already at v%d, skipping backup",
                    current_version,
                )

    bak_path: Path | None = None
    if need_backup:
        try:
            if fault_inject:
                fault_inject("before_backup")
            bak_path = await backup_db(db_path)
            if fault_inject:
                fault_inject("after_backup")
            log.info("state.db backed up before migration: %s", bak_path)
        except OSError as exc:
            # 备份失败通常是磁盘满 / 权限问题。不强行迁移（否则迁失败就
            # 真的丢数据）——直接 raise 让上层降级。
            log.error("state.db backup failed: %s", exc)
            raise InitializeError(f"cannot backup state.db: {exc}") from exc

    try:
        applied = await ensure_v9(db_path, fault_inject=fault_inject)
        if applied:
            log.info("state.db migrations applied: %s", applied)
        else:
            log.debug("state.db migrations already up-to-date")
    except Exception as exc:  # noqa: BLE001
        # Only schema migration failures restore the old state.db.  Once v33
        # commits, external-store deletion is intentionally irreversible and
        # must resume from its durable reset phase instead of reviving an old
        # state.db beside partially-cleared stores.
        log.error("state.db migration failed: %s; attempting rollback", exc)
        if bak_path is not None and bak_path.exists():
            try:
                # 直接覆盖；WAL/SHM 副本已经 checkpoint 回主文件
                # （aiosqlite close 保证），不必单独处理。
                if db_path.exists():
                    db_path.unlink()
                shutil.copy2(bak_path, db_path)
                log.info("state.db restored from %s", bak_path)
            except OSError as restore_exc:
                # 回滚再失败就真的只能降级了——原 .bak 还在盘上，
                # 支持 bundle 可让用户手动 rename。
                log.error(
                    "state.db rollback failed: %s (manual restore may be needed from %s)",
                    restore_exc,
                    bak_path,
                )
        else:
            log.warning("state.db had no backup to restore (fresh install)")
        raise InitializeError(f"state.db initialization failed: {exc}") from exc

    try:
        await run_legacy_session_reset(
            db_path, backup_path=bak_path, fault_inject=fault_inject
        )
        # Complete the reset before MemoryManager, workflow recovery, WebSocket
        # ingress, or any Session API can open an old conversation-owned store.
        await finalize_legacy_session_reset(db_path, fault_inject=fault_inject)
        # v33 is a durable stop point because its external-store reset must
        # finish before later schema generations become writable. Continue
        # incremental migrations only after that fence is completed.
        await ensure_v9(db_path, fault_inject=fault_inject)
    except Exception as exc:  # noqa: BLE001
        log.error("legacy Session reset incomplete: %s", exc)
        raise InitializeError(f"legacy Session reset incomplete: {exc}") from exc
