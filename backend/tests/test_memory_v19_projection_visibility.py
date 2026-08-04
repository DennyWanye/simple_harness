from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from deskpet.memory.chunker import MessageChunker
from deskpet.memory.enhanced_retriever import EnhancedRetriever
from deskpet.memory.eval.qaset import QASetBuilder
from deskpet.memory.retriever import Hit
from deskpet.memory.reflection import ReflectionWorker
from deskpet.memory.retriever import Retriever
from deskpet.memory.migrator import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationError,
    run_migrations,
)
from deskpet.memory.session_db import SessionDB
from deskpet.memory.summarizer import _find_candidate_sessions


class _VisibleChunkEmbedder:
    def is_mock(self) -> bool:
        return False

    async def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)


class _EmptyBaseRetriever:
    policy = SimpleNamespace(top_k=10)

    async def recall(
        self,
        _query: str,
        top_k: int | None = None,
        **_kwargs: object,
    ) -> list[Hit]:
        return []


@pytest.mark.asyncio
async def test_v19_schema_has_exact_projection_guards_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    applied = await run_migrations(db_path)

    assert applied[-1] == "019_provider_fault_correlation_v27.sql"
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 27
        columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        assert {
            "projection_kind",
            "context_visibility",
            "root_run_id",
            "task_scope_id",
        } <= columns
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
            "idx_messages_root_time",
            "idx_messages_task_scope_time",
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


@pytest.mark.asyncio
async def test_task12_excluded_rows_are_absent_from_all_memory_source_queries(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    store = SessionDB(db_path)
    await store.initialize()
    await store.ensure_session("visible-session")
    await store.ensure_session("excluded-session")
    visible_id = await store.append_message(
        "visible-session",
        "user",
        "visible source content long enough for qa selection",
    )
    excluded_id = await store.append_message(
        "excluded-session",
        "assistant",
        "excluded private notification long enough for qa selection",
        workflow_event_id="excluded-all-memory",
        projection_kind="workflow_progress",
    )

    retriever = Retriever(store, object())  # private SQL-source checks do not embed
    assert [item[0] for item in await retriever._recency_recall(20)] == [visible_id]
    assert [item[0] for item in await retriever._salience_recall(20)] == [visible_id]
    assert set(
        (await retriever._fetch_message_meta([visible_id, excluded_id])).keys()
    ) == {visible_id}

    chunker = MessageChunker(db_path)
    assert await chunker.chunk_message(
        message_id=excluded_id,
        content="must never become a chunk",
    ) == []
    assert await chunker.chunk_message(
        message_id=visible_id,
        content="visible chunk",
    )

    async def _unused_llm(_prompt: str) -> str:
        return ""

    qa_builder = QASetBuilder(db_path, _unused_llm, rng_seed=1)
    qa_sources = await qa_builder._pick_sources(
        20, include_archive=False, min_content_len=1
    )
    assert {int(item["id"]) for item in qa_sources} == {visible_id}

    reflection = ReflectionWorker(
        db_path,
        facts_store=object(),  # type: ignore[arg-type]
        llm_call=_unused_llm,
    )
    reflection_rows = await reflection._fetch_recent_turns(0)
    assert [row["content"] for row in reflection_rows] == [
        "visible source content long enough for qa selection"
    ]

    candidates = await _find_candidate_sessions(
        db_path,
        cutoff_ts=10**12,
        min_messages=1,
        max_count=20,
    )
    assert candidates == ["visible-session"]
    await store.close()


@pytest.mark.asyncio
async def test_enhanced_retriever_filters_preexisting_excluded_chunks(
    tmp_path: Path,
) -> None:
    """Even stale chunks created before v19 must not leak into enhanced recall."""

    db_path = tmp_path / "state.db"
    store = SessionDB(db_path)
    await store.initialize()
    await store.ensure_session("session-v19")
    visible_id = await store.append_message(
        "session-v19", "assistant", "visible enhanced chunk"
    )
    excluded_id = await store.append_message(
        "session-v19",
        "assistant",
        "excluded enhanced chunk",
        workflow_event_id="excluded-enhanced",
        projection_kind="workflow_progress",
        context_visibility="exclude",
    )
    embedder = _VisibleChunkEmbedder()
    chunker = MessageChunker(db_path, embedder=embedder)
    await chunker.chunk_message(
        message_id=visible_id,
        content="visible enhanced chunk",
    )

    # Simulate a pre-v19/stale writer that left a chunk behind before the
    # visibility guard existed. The production join must still filter it.
    with sqlite3.connect(db_path) as db:
        db.execute(
            """INSERT INTO messages_chunks(
                 message_id,chunk_index,text,embedding,created_at
               ) VALUES (?,?,?,?,?)""",
            (
                excluded_id,
                0,
                "excluded enhanced chunk",
                np.asarray([1.0, 0.0], dtype=np.float32).tobytes(),
                0.0,
            ),
        )
        db.commit()

    retriever = EnhancedRetriever(
        _EmptyBaseRetriever(),  # type: ignore[arg-type]
        embedder=embedder,
        chunk_store=chunker,
    )
    hits = await retriever._collect_chunk_hits("query", top_k=10)
    assert [hit.message_id for hit in hits] == [visible_id]
    await store.close()
