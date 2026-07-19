from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path

import pytest

from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, MigrationError, run_migrations
from deskpet.memory.session_db import SessionDB


@pytest.mark.asyncio
async def test_v19_schema_has_exact_projection_guards_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    applied = await run_migrations(db_path)

    assert applied[-1] == "011_message_projection_visibility_v19.sql"
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        assert {"projection_kind", "context_visibility"} <= columns
        indexes = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='messages'"
            )
        }
        assert {
            "idx_messages_session_visibility_time",
            "idx_messages_visibility_role_time",
            "idx_messages_visibility_salience",
        } <= indexes
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO messages(session_id,role,content,created_at,projection_kind) "
                "VALUES ('s','assistant','bad',0,'not_a_projection')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO messages(session_id,role,content,created_at,context_visibility) "
                "VALUES ('s','assistant','bad',0,'hidden')"
            )


@pytest.mark.asyncio
async def test_excluded_rows_remain_history_only_and_skip_embedding(tmp_path: Path) -> None:
    embedded: list[tuple[int, str]] = []

    async def on_written(message_id: int, content: str) -> None:
        embedded.append((message_id, content))

    store = SessionDB(tmp_path / "state.db", on_message_written=on_written)
    await store.initialize()
    await store.ensure_session("session-v19")
    conversation_id = await store.append_message(
        "session-v19", "assistant", "visibletoken"
    )
    excluded_id = await store.append_message(
        "session-v19",
        "assistant",
        "excludedtoken",
        workflow_event_id="progress-1",
        projection_kind="workflow_progress",
        context_visibility="exclude",
    )

    history = await store.get_messages("session-v19")
    recent = await store.get_recent_messages("session-v19")
    assert [row["id"] for row in history] == [conversation_id, excluded_id]
    assert history[1]["projection_kind"] == "workflow_progress"
    assert history[1]["context_visibility"] == "exclude"
    assert [row["id"] for row in recent] == [conversation_id]
    assert await store.search_fts("excludedtoken", session_id="session-v19") == []
    assert [content for _, content in embedded] == ["visibletoken"]
    await store.close()


@pytest.mark.asyncio
async def test_visibility_update_triggers_keep_fts_conversation_only(tmp_path: Path) -> None:
    store = SessionDB(tmp_path / "state.db")
    await store.initialize()
    await store.ensure_session("session-v19")
    message_id = await store.append_message(
        "session-v19", "assistant", "transitiontoken"
    )
    assert len(await store.search_fts("transitiontoken")) == 1

    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute(
            "UPDATE messages SET projection_kind='workflow_progress', "
            "context_visibility='exclude' WHERE id=?",
            (message_id,),
        )
        db.commit()
    assert await store.search_fts("transitiontoken") == []

    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute(
            "UPDATE messages SET projection_kind='assistant_message', "
            "context_visibility='conversation' WHERE id=?",
            (message_id,),
        )
        db.commit()
    assert len(await store.search_fts("transitiontoken")) == 1
    await store.close()


@pytest.mark.asyncio
async def test_workflow_event_id_rejects_projection_reclassification(tmp_path: Path) -> None:
    store = SessionDB(tmp_path / "state.db")
    await store.initialize()
    await store.ensure_session("session-v19")
    first = await store.append_message(
        "session-v19",
        "assistant",
        "progress",
        workflow_event_id="same-event",
        projection_kind="workflow_progress",
        context_visibility="exclude",
    )
    duplicate = await store.append_message(
        "session-v19",
        "assistant",
        "progress duplicate",
        workflow_event_id="same-event",
        projection_kind="workflow_progress",
        context_visibility="exclude",
    )
    assert duplicate == first

    with pytest.raises(RuntimeError, match="workflow_message_projection_conflict"):
        await store.append_message(
            "session-v19",
            "assistant",
            "now final",
            workflow_event_id="same-event",
            projection_kind="final_assistant",
            context_visibility="conversation",
        )
    await store.close()


@pytest.mark.asyncio
async def test_v19_ddl_marker_and_version_roll_back_together(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        if source.name <= "010_context_os_v18.sql":
            shutil.copyfile(source, migrations_dir / source.name)
    await run_migrations(db_path, migrations_dir=migrations_dir)

    (migrations_dir / "011_message_projection_visibility_v19.sql").write_text(
        "ALTER TABLE messages ADD COLUMN projection_kind TEXT;\n"
        "THIS IS NOT SQL;\n",
        encoding="utf-8",
    )
    with pytest.raises(MigrationError):
        await run_migrations(db_path, migrations_dir=migrations_dir)

    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 18
        columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        assert "projection_kind" not in columns
        assert db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            ("011_message_projection_visibility_v19.sql",),
        ).fetchone() is None
