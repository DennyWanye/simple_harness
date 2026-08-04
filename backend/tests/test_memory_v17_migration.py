# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Focused coverage for the canonical state.db v17 migration closure."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.memory_v2_schema import (
    PENDING_SKILL_CANDIDATES_DDL,
    PPT_OUTLINE_HISTORY_DDL,
    SESSION_TITLES_DDL,
    ensure_memory_v2_tables,
)
from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, run_migrations
from deskpet.memory.schema import InitializeError, initialize_state_db
from deskpet.memory.session_db import SessionDB


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _index_names(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {str(row[1]) for row in conn.execute(f'PRAGMA index_list("{table}")')}


async def _build_v16_fixture(db_path: Path, migrations_dir: Path) -> None:
    migrations_dir.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("00[1-8]_*.sql")):
        shutil.copyfile(source, migrations_dir / source.name)
    await run_migrations(db_path, migrations_dir=migrations_dir)
    await ensure_memory_v2_tables(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SESSION_TITLES_DDL)
        conn.executescript(PPT_OUTLINE_HISTORY_DDL)
        conn.executescript(PENDING_SKILL_CANDIDATES_DDL)
        conn.executescript(
            """
            CREATE TABLE session_goals (
                goal_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                progress REAL NOT NULL DEFAULT 0.0,
                criteria TEXT,
                max_iterations INTEGER NOT NULL DEFAULT 10,
                iterations_used INTEGER NOT NULL DEFAULT 0,
                set_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX idx_session_goals_sid
                ON session_goals(session_id, status);
            CREATE TABLE goal_tasks (
                task_id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                depends_on TEXT NOT NULL DEFAULT '[]',
                claimed_by TEXT,
                result TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX idx_goal_tasks_goal ON goal_tasks(goal_id, status);
            INSERT INTO goal_tasks(
                task_id, goal_id, session_id, title, created_at, updated_at
            ) VALUES ('legacy-task', 'goal-1', 'session-1', 'Keep me', 1, 1);
            INSERT INTO code_todos(
                session_id, content, active_form, status, sort_order
            ) VALUES ('code-1', 'Legacy todo', 'Legacy todo', 'pending', 0);
            """
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16


@pytest.mark.asyncio
async def test_fresh_v17_has_canonical_lazy_and_ownership_schema(tmp_path: Path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 27
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
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
        }.issubset(tables)
        assert conn.execute("SELECT COUNT(*) FROM session_goals").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM goal_tasks").fetchone()[0] == 0

    assert "workflow_event_id" in _columns(db_path, "messages")
    assert {"workflow_run_id", "workflow_step_id"}.issubset(
        _columns(db_path, "code_todos")
    )
    assert {"workflow_run_id", "workflow_step_id"}.issubset(
        _columns(db_path, "goal_tasks")
    )
    assert "idx_messages_workflow_event" in _index_names(db_path, "messages")
    assert "idx_code_todos_workflow_step" in _index_names(db_path, "code_todos")
    assert "idx_goal_tasks_workflow_step" in _index_names(db_path, "goal_tasks")


@pytest.mark.asyncio
async def test_realistic_v16_lazy_fixture_upgrades_without_data_loss(tmp_path: Path):
    db_path = tmp_path / "state.db"
    await _build_v16_fixture(db_path, tmp_path / "v16-migrations")

    applied = await run_migrations(db_path)
    assert applied == [
        "009_memory_v2_v17.sql",
        "010_context_os_v18.sql",
        "011_message_projection_visibility_v19.sql",
        "012_task_conversation_scope_v20.sql",
        "013_companion_projection.sql",
        "014_context_usage_history_v22.sql",
        "015_provider_binding_lifecycle_v23.sql",
        "016_context_usage_authority_v24.sql",
        "017_provider_workload_audit_v25.sql",
        "018_message_archive_projection_v26.sql",
        "019_provider_fault_correlation_v27.sql",
    ]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 27
        assert conn.execute(
            "SELECT content FROM code_todos WHERE workflow_run_id IS NULL"
        ).fetchone() == ("Legacy todo",)
        assert conn.execute(
            "SELECT title FROM goal_tasks WHERE task_id='legacy-task'"
        ).fetchone() == ("Keep me",)


@pytest.mark.asyncio
async def test_incompatible_lazy_table_is_rebuilt_and_row_count_preserved(tmp_path: Path):
    db_path = tmp_path / "state.db"
    await _build_v16_fixture(db_path, tmp_path / "v16-migrations")
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE ppt_outline_history")
        conn.execute(
            "CREATE TABLE ppt_outline_history("
            "outline_id TEXT PRIMARY KEY, session_id TEXT, topic TEXT, "
            "created_at TEXT, slides_json TEXT, sources_count TEXT, status TEXT)"
        )
        conn.execute(
            "INSERT INTO ppt_outline_history VALUES "
            "('outline-1', 'session-1', 'Topic', 'now', '[]', '2', 'accepted')"
        )
        conn.commit()

    await run_migrations(db_path)
    with sqlite3.connect(db_path) as conn:
        info = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(ppt_outline_history)")}
        assert info["sources_count"] == "INTEGER"
        assert conn.execute("SELECT COUNT(*) FROM ppt_outline_history").fetchone()[0] == 1
        assert conn.execute(
            "SELECT sources_count FROM ppt_outline_history WHERE outline_id='outline-1'"
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_failed_v17_restores_v16_database(tmp_path: Path):
    db_path = tmp_path / "state.db"
    await _build_v16_fixture(db_path, tmp_path / "v16-migrations")
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE ppt_outline_history")
        conn.execute(
            "CREATE TABLE ppt_outline_history("
            "outline_id TEXT PRIMARY KEY, sources_count TEXT, unknown_col TEXT)"
        )
        conn.execute("INSERT INTO ppt_outline_history VALUES ('o1', '2', 'keep')")
        conn.commit()
    before = db_path.read_bytes()

    with pytest.raises(InitializeError):
        await initialize_state_db(db_path)

    assert db_path.read_bytes() == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
        assert conn.execute(
            "SELECT unknown_col FROM ppt_outline_history WHERE outline_id='o1'"
        ).fetchone() == ("keep",)


@pytest.mark.asyncio
async def test_second_start_is_idempotent_and_does_not_add_backup(tmp_path: Path):
    db_path = tmp_path / "state.db"
    await _build_v16_fixture(db_path, tmp_path / "v16-migrations")
    await initialize_state_db(db_path)
    backups_after_upgrade = sorted(tmp_path.glob("state.db.bak.*"))
    assert len(backups_after_upgrade) == 1

    await initialize_state_db(db_path)
    assert sorted(tmp_path.glob("state.db.bak.*")) == backups_after_upgrade


@pytest.mark.asyncio
async def test_workflow_message_is_idempotent_and_tombstone_fenced(tmp_path: Path):
    hook_calls: list[tuple[int, str]] = []

    async def _hook(message_id: int, content: str) -> None:
        hook_calls.append((message_id, content))

    store = SessionDB(tmp_path / "state.db", on_message_written=_hook)
    await store.initialize()
    first = await store.append_message(
        "session-1", "assistant", "done", workflow_event_id="event-1"
    )
    duplicate = await store.append_message(
        "session-1", "assistant", "done", workflow_event_id="event-1"
    )
    assert duplicate == first
    assert hook_calls == [(first, "done")]

    delivered = await store.append_message_if_epoch(
        "session-1",
        "assistant",
        "before delete",
        expected_epoch=0,
        workflow_event_id="event-2",
    )
    assert delivered is not None
    assert await store.tombstone_session("session-1", deleted_at=10.0) == 1
    blocked = await store.append_message_if_epoch(
        "session-1",
        "assistant",
        "late",
        expected_epoch=0,
        workflow_event_id="event-3",
    )
    assert blocked is None
    assert [row["content"] for row in await store.get_messages("session-1")] == [
        "done",
        "before delete",
    ]


@pytest.mark.asyncio
async def test_legacy_todo_replace_preserves_graph_owned_rows(tmp_path: Path):
    store = SessionDB(tmp_path / "state.db")
    await store.initialize()
    await store.replace_code_todos(
        "code-1",
        [{"content": "legacy-1", "activeForm": "legacy-1", "status": "pending"}],
    )
    await store.sync_workflow_code_todos(
        "code-1",
        "run-1",
        [
            {
                "workflow_step_id": "step-1",
                "content": "graph-1",
                "activeForm": "graph-1",
                "status": "in_progress",
            }
        ],
    )
    await store.replace_code_todos(
        "code-1",
        [{"content": "legacy-2", "activeForm": "legacy-2", "status": "completed"}],
    )

    rows = await store.get_code_todos("code-1")
    assert {row["content"] for row in rows} == {"legacy-2", "graph-1"}
    graph = next(row for row in rows if row["content"] == "graph-1")
    assert graph["workflow_run_id"] == "run-1"
    assert graph["workflow_step_id"] == "step-1"
