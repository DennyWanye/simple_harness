# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Canonical memory-v2 schema and compatibility lazy-table helpers.

Schema v17 closes the old split between ``PRAGMA user_version`` (which
claimed v17) and tables that were created only when a feature first ran.
The formal migration uses :func:`migrate_memory_v2_v17`; the lazy ensure
functions remain for isolated stores/tests and older call sites.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Set

import aiosqlite

log = logging.getLogger(__name__)


SESSION_TITLES_DDL = """
CREATE TABLE IF NOT EXISTS session_titles (
    session_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

PPT_OUTLINE_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS ppt_outline_history (
    outline_id    TEXT PRIMARY KEY,
    session_id    TEXT,
    topic         TEXT,
    created_at    TEXT,
    slides_json   TEXT,
    sources_count INTEGER,
    status        TEXT
);
"""

PENDING_SKILL_CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS pending_skill_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    trigger_pattern TEXT,
    steps_json      TEXT    NOT NULL DEFAULT '[]',
    status          TEXT    NOT NULL DEFAULT 'pending',
    created_at      REAL    NOT NULL
);
"""

SESSION_DELIVERY_STATE_DDL = """
CREATE TABLE IF NOT EXISTS session_delivery_state (
    session_id TEXT PRIMARY KEY,
    epoch      INTEGER NOT NULL DEFAULT 0,
    deleted_at REAL,
    reason     TEXT
);
"""


_DDL = """
-- =====================================================================
-- Phase A — Evaluation
-- =====================================================================
CREATE TABLE IF NOT EXISTS memory_qa_set (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,
    query           TEXT    NOT NULL,
    expected_msg_id INTEGER NOT NULL,
    tags            TEXT,
    created_at      REAL    NOT NULL,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_qa_source ON memory_qa_set(source);

CREATE TABLE IF NOT EXISTS memory_eval_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    REAL    NOT NULL,
    finished_at   REAL,
    qa_set_size   INTEGER NOT NULL,
    metrics_json  TEXT,
    config_json   TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_run_time ON memory_eval_run(started_at);

CREATE TABLE IF NOT EXISTS memory_user_feedback (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_msg_id  INTEGER NOT NULL,
    value          INTEGER NOT NULL,
    context_query  TEXT,
    created_at     REAL    NOT NULL,
    session_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_msg ON memory_user_feedback(source_msg_id);
CREATE INDEX IF NOT EXISTS idx_feedback_time ON memory_user_feedback(created_at);

-- =====================================================================
-- Phase B — Facts
-- =====================================================================
-- 记忆系统升级 WI-M1.4 / PRD §3.1：facts 走向量召回（中文整句 LIKE
-- 子串匹配几乎不命中）。``embedding`` 存规范文本 "key: value" 的 BGE-M3
-- 向量（float32 BLOB）。facts 表小，召回走 Python brute-force cosine，
-- 不必上向量索引。facts 表此前从不被调用（死代码），DDL 直接带该列。
CREATE TABLE IF NOT EXISTS facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    category       TEXT    NOT NULL,
    subject        TEXT    NOT NULL,
    key            TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    confidence     REAL    NOT NULL DEFAULT 0.5,
    source_msg_id  INTEGER,
    created_at     REAL    NOT NULL,
    updated_at     REAL    NOT NULL,
    evidence       TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    decay_rate     REAL    NOT NULL DEFAULT 0.02,
    last_recalled  REAL,
    embedding      BLOB,
    -- Stage 2 D1：cross-key 矛盾 / memory_forget 配套列。
    -- 老库由 schema_v2_migrator.ensure_memory_v2_columns 通过 ALTER
    -- 补齐；新库一次到位避免启动后立即再 ALTER。
    superseded_by  INTEGER REFERENCES facts(id),
    forgotten_at   REAL,
    -- FP-4 Task 1：scope（user/session）+ pinned（用户主动钉住，跳过衰减）。
    -- 老库同样由 schema_v2_migrator._COLUMN_ADDS ALTER 补齐。
    scope          TEXT    DEFAULT 'user',
    pinned         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_subject_key ON facts(subject, key, is_active);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category, is_active);
CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_at);

-- =====================================================================
-- Phase C — Long-message chunks
-- =====================================================================
CREATE TABLE IF NOT EXISTS messages_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    embedding   BLOB,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_message ON messages_chunks(message_id);

-- =====================================================================
-- Phase D — Workspace memory
-- =====================================================================
CREATE TABLE IF NOT EXISTS workspace_state (
    session_id      TEXT    NOT NULL,
    path            TEXT    NOT NULL,
    last_action     TEXT    NOT NULL,
    last_action_ts  REAL    NOT NULL,
    content_hash    TEXT,
    content_summary TEXT,
    byte_size       INTEGER,
    PRIMARY KEY (session_id, path)
);
CREATE INDEX IF NOT EXISTS idx_workspace_session ON workspace_state(session_id, last_action_ts);

-- =====================================================================
-- Phase E — Skill / procedural memory
-- =====================================================================
CREATE TABLE IF NOT EXISTS skill_memory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    trigger_pattern TEXT,
    steps_json      TEXT,
    usage_count     INTEGER NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    last_used_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_skill_name ON skill_memory(name);

-- FEAT-A4 (superpowers): plan-confirm 硬门的 awaiting plan sidecar。
-- 不走 messages 表（避免 _on_message_written → VectorWorker embed + FTS5
-- 污染语义检索）。PK=session_id：plan 门 per-session 单 future，同时只一个
-- awaiting plan，upsert 天然覆盖。F5/HMR rehydration 据此重建 [执行]/[取消] 栏。
CREATE TABLE IF NOT EXISTS session_plans (
    session_id  TEXT    PRIMARY KEY,
    rationale   TEXT    NOT NULL DEFAULT '',
    steps_json  TEXT    NOT NULL DEFAULT '[]',
    awaiting    INTEGER NOT NULL DEFAULT 0,
    ts          REAL    NOT NULL
);

"""

# ─────────────────────────────────────────────────────────────────────
# goal-completion FP-1 — 目标持久化（WI-1.1，冻结 §1.3）
# ─────────────────────────────────────────────────────────────────────
# ⚠️ 故意 NOT 放进共享 `_DDL`：`ensure_memory_v2_tables` 被 facts /
# session_plans 等常态调用，若把 session_goals 塞进共享 DDL，则 goal_mode
# OFF 但 memory_v2 ON 的直接 lazy-helper 调用者意外建表。v17 正式迁移
# 会预建 schema，但 goal_mode OFF 仍不得写入业务行。独立 ensure 保留给未走
# SessionDB 初始化的隔离 store。多目标物理支持（goal_id PK），API
# 层 last-write-wins 单活跃目标；criteria 占位列 FP-3(2.3) 用。
_SESSION_GOALS_DDL = """
CREATE TABLE IF NOT EXISTS session_goals (
    goal_id         TEXT    PRIMARY KEY,
    session_id      TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active',
    progress        REAL    NOT NULL DEFAULT 0.0,
    criteria        TEXT,
    max_iterations  INTEGER NOT NULL DEFAULT 10,
    iterations_used INTEGER NOT NULL DEFAULT 0,
    set_at          REAL    NOT NULL,
    updated_at      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_goals_sid
    ON session_goals(session_id, status);
"""

# ─────────────────────────────────────────────────────────────────────
# goal-completion FP-2 — task graph（WI-1.2，冻结 §1.3）
# ─────────────────────────────────────────────────────────────────────
# ⚠️ 故意 NOT 放进共享 `_DDL`：同 session_goals 理由，隔离 store 只在
# TaskGraphStore 落库时 lazy ensure。v17 正式迁移则始终预建 schema。
_GOAL_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS goal_tasks (
    task_id     TEXT    PRIMARY KEY,
    goal_id     TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    depends_on  TEXT    NOT NULL DEFAULT '[]',
    claimed_by  TEXT,
    result      TEXT,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    workflow_run_id  TEXT,
    workflow_step_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal
    ON goal_tasks(goal_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_tasks_workflow_step
    ON goal_tasks(workflow_run_id, workflow_step_id)
    WHERE workflow_step_id IS NOT NULL;
"""


_V17_TABLES = (
    "messages",
    "code_todos",
    "memory_qa_set",
    "memory_eval_run",
    "memory_user_feedback",
    "messages_chunks",
    "workspace_state",
    "skill_memory",
    "session_plans",
    "session_goals",
    "goal_tasks",
    "session_titles",
    "ppt_outline_history",
    "pending_skill_candidates",
    "session_delivery_state",
)

# Feature-gated tables are reconciled when they already exist, but v17 must
# not create them while their feature is disabled.
_V17_OPTIONAL_TABLES = ("facts",)

# Schema v18 tables are owned exclusively by the formal 010 migration.  Keep
# them in the canonical inventory for diagnostics/tests, but deliberately do
# not add them to any lazy ensure path or the v17 reconciliation callback.
CONTEXT_OS_V18_TABLES = (
    "session_context_snapshots",
    "session_context_segments",
)
CONTEXT_OS_V18_INDEXES = ("idx_context_segments_cover",)
CANONICAL_STATE_TABLES = (*_V17_TABLES, *CONTEXT_OS_V18_TABLES)

_V17_OWNERSHIP_DDL = """
ALTER TABLE messages ADD COLUMN workflow_event_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_workflow_event
    ON messages(workflow_event_id)
    WHERE workflow_event_id IS NOT NULL;

ALTER TABLE code_todos ADD COLUMN workflow_run_id TEXT;
ALTER TABLE code_todos ADD COLUMN workflow_step_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_code_todos_workflow_step
    ON code_todos(workflow_run_id, workflow_step_id)
    WHERE workflow_step_id IS NOT NULL;
"""


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _build_canonical_v17_db() -> sqlite3.Connection:
    """Compile the canonical schema with SQLite instead of parsing SQL."""
    conn = sqlite3.connect(":memory:")
    migrations_dir = Path(__file__).parent / "migrations"
    for path in sorted(migrations_dir.glob("00[1-8]_*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.executescript(_DDL)
    conn.executescript(_SESSION_GOALS_DDL)
    conn.executescript(_GOAL_TASKS_DDL)
    conn.executescript(SESSION_TITLES_DDL)
    conn.executescript(PPT_OUTLINE_HISTORY_DDL)
    conn.executescript(PENDING_SKILL_CANDIDATES_DDL)
    conn.executescript(SESSION_DELIVERY_STATE_DDL)
    conn.executescript(_V17_OWNERSHIP_DDL)
    return conn


def _normalize_type(value: Any) -> str:
    return " ".join(str(value or "").upper().split())


def _normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split())
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    return normalized.casefold()


def _columns_compatible(actual: tuple[Any, ...], expected: tuple[Any, ...]) -> bool:
    return (
        _normalize_type(actual[2]) == _normalize_type(expected[2])
        and int(actual[3]) == int(expected[3])
        and _normalize_default(actual[4]) == _normalize_default(expected[4])
        and int(actual[5]) == int(expected[5])
    )


def _column_add_sql(table: str, column: tuple[Any, ...]) -> str:
    clause = [_quote_identifier(str(column[1])), _normalize_type(column[2])]
    if int(column[3]):
        clause.append("NOT NULL")
    if column[4] is not None:
        clause.extend(("DEFAULT", str(column[4])))
    return f"ALTER TABLE {_quote_identifier(table)} ADD COLUMN {' '.join(clause)}"


def _rewrite_create_table(sql: str, table: str, replacement: str) -> str:
    pattern = re.compile(
        rf"^(CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+)"
        rf"(?:\"{re.escape(table)}\"|{re.escape(table)})",
        re.IGNORECASE,
    )
    rewritten, count = pattern.subn(
        lambda match: match.group(1) + _quote_identifier(replacement),
        sql.strip(),
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"cannot rewrite canonical CREATE TABLE for {table}")
    return rewritten


async def _table_info(conn: aiosqlite.Connection, table: str) -> list[tuple[Any, ...]]:
    cursor = await conn.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    rows = await cursor.fetchall()
    await cursor.close()
    return [tuple(row) for row in rows]


async def _table_exists(conn: aiosqlite.Connection, table: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def _rebuild_table(
    conn: aiosqlite.Connection,
    table: str,
    create_sql: str,
    actual_columns: list[tuple[Any, ...]],
    expected_columns: list[tuple[Any, ...]],
) -> None:
    temp_table = f"{table}_v17_new"
    if await _table_exists(conn, temp_table):
        raise RuntimeError(f"reserved migration table already exists: {temp_table}")

    actual_names = {str(column[1]) for column in actual_columns}
    expected_names = {str(column[1]) for column in expected_columns}
    extra_names = actual_names - expected_names
    if extra_names:
        raise RuntimeError(
            f"cannot safely rebuild {table}; unknown columns: {sorted(extra_names)}"
        )

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
    )
    row = await cursor.fetchone()
    await cursor.close()
    before_count = int(row[0]) if row else 0

    missing_required = [
        str(column[1])
        for column in expected_columns
        if str(column[1]) not in actual_names
        and int(column[3])
        and column[4] is None
        and int(column[5]) == 0
    ]
    if before_count and missing_required:
        raise RuntimeError(
            f"cannot rebuild non-empty {table}; missing required columns: "
            f"{missing_required}"
        )

    await conn.execute(_rewrite_create_table(create_sql, table, temp_table))
    copy_names = [
        str(column[1])
        for column in expected_columns
        if str(column[1]) in actual_names
    ]
    if copy_names:
        quoted = ", ".join(_quote_identifier(name) for name in copy_names)
        await conn.execute(
            f"INSERT INTO {_quote_identifier(temp_table)} ({quoted}) "
            f"SELECT {quoted} FROM {_quote_identifier(table)}"
        )

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM {_quote_identifier(temp_table)}"
    )
    row = await cursor.fetchone()
    await cursor.close()
    after_count = int(row[0]) if row else 0
    if after_count != before_count:
        raise RuntimeError(
            f"row-count validation failed rebuilding {table}: "
            f"{before_count} != {after_count}"
        )

    await conn.execute(f"DROP TABLE {_quote_identifier(table)}")
    await conn.execute(
        f"ALTER TABLE {_quote_identifier(temp_table)} "
        f"RENAME TO {_quote_identifier(table)}"
    )


def _sync_index_signature(
    conn: sqlite3.Connection, table: str, index: str
) -> tuple[int, int, tuple[str, ...], str]:
    row = next(
        item for item in conn.execute(f"PRAGMA index_list({_quote_identifier(table)})")
        if item[1] == index
    )
    columns = tuple(
        str(item[2])
        for item in conn.execute(f"PRAGMA index_info({_quote_identifier(index)})")
    )
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)
    ).fetchone()
    sql = " ".join(str(sql_row[0] or "").casefold().split()) if sql_row else ""
    where = sql.partition(" where ")[2]
    return int(row[2]), int(row[4]), columns, where


async def _async_index_signature(
    conn: aiosqlite.Connection, table: str, index: str
) -> tuple[int, int, tuple[str, ...], str] | None:
    cursor = await conn.execute(f"PRAGMA index_list({_quote_identifier(table)})")
    rows = await cursor.fetchall()
    await cursor.close()
    row = next((item for item in rows if item[1] == index), None)
    if row is None:
        return None
    cursor = await conn.execute(f"PRAGMA index_info({_quote_identifier(index)})")
    info = await cursor.fetchall()
    await cursor.close()
    cursor = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)
    )
    sql_row = await cursor.fetchone()
    await cursor.close()
    sql = " ".join(str(sql_row[0] or "").casefold().split()) if sql_row else ""
    where = sql.partition(" where ")[2]
    return int(row[2]), int(row[4]), tuple(str(item[2]) for item in info), where


async def migrate_memory_v2_v17(conn: aiosqlite.Connection) -> None:
    """Close every state.db v17 lazy-schema divergence in one transaction.

    The caller owns ``BEGIN IMMEDIATE``/commit/rollback and writes the
    migration marker plus ``user_version`` only after this callback returns.
    """
    canonical = _build_canonical_v17_db()
    try:
        for table in (*_V17_TABLES, *_V17_OPTIONAL_TABLES):
            create_row = canonical.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if create_row is None or not create_row[0]:
                raise RuntimeError(f"canonical table missing: {table}")
            create_sql = str(create_row[0])
            expected_columns = [
                tuple(row)
                for row in canonical.execute(
                    f"PRAGMA table_info({_quote_identifier(table)})"
                )
            ]

            if not await _table_exists(conn, table):
                if table in _V17_OPTIONAL_TABLES:
                    continue
                await conn.execute(create_sql)
                continue

            actual_columns = await _table_info(conn, table)
            actual_by_name = {str(column[1]): column for column in actual_columns}
            needs_rebuild = False
            missing_additive: list[tuple[Any, ...]] = []
            for expected in expected_columns:
                name = str(expected[1])
                actual = actual_by_name.get(name)
                if actual is None:
                    if int(expected[5]) or (int(expected[3]) and expected[4] is None):
                        needs_rebuild = True
                    else:
                        missing_additive.append(expected)
                elif not _columns_compatible(actual, expected):
                    needs_rebuild = True

            if needs_rebuild:
                await _rebuild_table(
                    conn,
                    table,
                    create_sql,
                    actual_columns,
                    expected_columns,
                )
            else:
                for column in missing_additive:
                    await conn.execute(_column_add_sql(table, column))

        for table in (*_V17_TABLES, *_V17_OPTIONAL_TABLES):
            if not await _table_exists(conn, table):
                continue
            for index_name, index_sql in canonical.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            ):
                expected = _sync_index_signature(canonical, table, str(index_name))
                actual = await _async_index_signature(conn, table, str(index_name))
                if actual == expected:
                    continue
                if actual is not None:
                    await conn.execute(
                        f"DROP INDEX {_quote_identifier(str(index_name))}"
                    )
                await conn.execute(str(index_sql))
    finally:
        canonical.close()

# Cache so we don't re-run executescript every call. Keyed by absolute db path.
_ensured: Set[str] = set()
_lock = asyncio.Lock()

# Separate cache + lock for the goal-mode-gated session_goals table (see
# _SESSION_GOALS_DDL above — kept out of the shared _DDL on purpose).
_goals_ensured: Set[str] = set()
_goals_lock = asyncio.Lock()

# Separate cache + lock for goal_tasks (FP-2 WI-1.2, same flag-OFF moat).
_goal_tasks_ensured: Set[str] = set()
_goal_tasks_lock = asyncio.Lock()


async def ensure_memory_v2_tables(db_path: str | Path) -> None:
    """Idempotent: CREATE TABLE IF NOT EXISTS for every memory-v2 table.

    Safe to call concurrently and repeatedly. Caches per-path so the
    second call is a free no-op. Does NOT bump ``PRAGMA user_version``.

    Stage 2: after the CREATE TABLE pass, also runs
    :func:`schema_v2_migrator.ensure_memory_v2_columns` to additively
    ALTER in ``superseded_by`` / ``forgotten_at`` on legacy DBs (fresh
    DBs already have them via ``_DDL``). main.py reads
    :func:`schema_v2_migrator.alter_failures` to disable dependent
    feature flags on ALTER failure (R8/D17 v2).

    Failure modes:
      * CREATE TABLE error → re-raise. Callers (Phase A-E stores) should
        fail loudly when their tables can't be created.
      * Stage 2 ALTER failure → logged and recorded; not raised, so the
        rest of the app boots and the feature flag layer can decide.
    """
    key = str(Path(db_path).resolve())
    if key in _ensured:
        return
    async with _lock:
        if key in _ensured:
            return
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.executescript(_DDL)
            await conn.commit()
        # Stage 2 D1：补齐老库可能缺的列。ALTER 失败不抛，
        # main.py 据 alter_failures() 关 flag。
        try:
            from deskpet.memory.schema_v2_migrator import (
                ensure_memory_v2_columns,
            )

            await ensure_memory_v2_columns(db_path)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "memory_v2 stage2 column migration failed: %s", exc,
            )
        _ensured.add(key)
        log.debug("memory_v2 tables ensured for %s", key)


async def ensure_session_goals_table(db_path: str | Path) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for ``session_goals`` only.

    Deliberately separate from :func:`ensure_memory_v2_tables` so the goal
    table is created only when an isolated goal store actually persists.
    Normal SessionDB databases receive the same schema from migration v17;
    goal_mode OFF still writes zero rows. Does not bump user_version.
    """
    key = str(Path(db_path).resolve())
    if key in _goals_ensured:
        return
    async with _goals_lock:
        if key in _goals_ensured:
            return
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.executescript(_SESSION_GOALS_DDL)
            await conn.commit()
        _goals_ensured.add(key)
        log.debug("session_goals table ensured for %s", key)


async def ensure_goal_tasks_table(db_path: str | Path) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for ``goal_tasks`` only.

    Deliberately separate from :func:`ensure_memory_v2_tables` and
    :func:`ensure_session_goals_table` so the task-graph table is created
    only when an isolated TaskGraphStore actually persists. Normal SessionDB
    databases receive the same schema from migration v17; goal_mode OFF still
    writes zero rows. Does not bump user_version.
    """
    key = str(Path(db_path).resolve())
    if key in _goal_tasks_ensured:
        return
    async with _goal_tasks_lock:
        if key in _goal_tasks_ensured:
            return
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.executescript(_GOAL_TASKS_DDL)
            await conn.commit()
        _goal_tasks_ensured.add(key)
        log.debug("goal_tasks table ensured for %s", key)


def _reset_cache_for_tests() -> None:
    """Test helper. Clears the per-path cache so a fresh tmp_path DB
    re-runs DDL. Never call from production code.
    """
    _ensured.clear()
    _goals_ensured.clear()
    _goal_tasks_ensured.clear()
