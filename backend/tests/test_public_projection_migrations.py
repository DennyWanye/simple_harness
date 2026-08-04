from __future__ import annotations

import sqlite3

import aiosqlite
import pytest

from deskpet.memory.migrator import run_migrations
from deskpet.memory.summarizer import _commit_summary_txn
from deskpet.workflows.store.schema import (
    _migrate_v28_to_v29_public_run_projection,
)


@pytest.mark.asyncio
async def test_message_archive_v26_copies_projection_identity(tmp_path):
    path = tmp_path / "state.db"
    await run_migrations(path)
    with sqlite3.connect(path) as db:
        cursor = db.execute(
            """INSERT INTO messages(
            session_id,role,content,created_at,workflow_event_id,projection_kind,
            context_visibility,root_run_id,task_scope_id,projection_event_id,
            projection_owner_kind,projection_owner_id,projection_owner_generation,
            projection_epoch,projection_route_version,projection_payload_hash)
            VALUES('s','assistant','hello',1,'wf-1','workflow_progress',
            'conversation','root-1','scope-1','projection-1','companion_profile','root-1',
            2,3,4,?)""",
            ("a" * 64,),
        )
        message_id = int(cursor.lastrowid)
        db.commit()
    await _commit_summary_txn(path, "s", [message_id], "summary", 2.0)
    with sqlite3.connect(path) as db:
        row = db.execute(
            """SELECT workflow_event_id,projection_kind,root_run_id,task_scope_id,
            projection_event_id,projection_owner_kind,projection_owner_id,
            projection_owner_generation,projection_epoch,projection_route_version,
            projection_payload_hash FROM messages_archive WHERE id=?""",
            (message_id,),
        ).fetchone()
    assert row == (
        "wf-1",
        "workflow_progress",
        "root-1",
        "scope-1",
        "projection-1",
        "companion_profile",
        "root-1",
        2,
        3,
        4,
        "a" * 64,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_point",
    ["after_ddl", "after_marker", "before_user_version", "after_user_version"],
)
async def test_workflow_v29_ddl_marker_and_version_roll_back_together(
    tmp_path, fault_point
):
    path = tmp_path / f"workflow-{fault_point}.db"
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """CREATE TABLE workflow_schema_migrations(
            version INTEGER PRIMARY KEY,applied_at REAL NOT NULL);
            CREATE TABLE execution_runs(run_id TEXT PRIMARY KEY);
            CREATE TABLE execution_events(event_id TEXT PRIMARY KEY);
            CREATE TABLE execution_effects(effect_id TEXT PRIMARY KEY);
            INSERT INTO workflow_schema_migrations VALUES(28,1);
            PRAGMA user_version=28;"""
        )

        def fail(point: str) -> None:
            if point == fault_point:
                raise RuntimeError(point)

        with pytest.raises(RuntimeError, match=fault_point):
            await _migrate_v28_to_v29_public_run_projection(
                db, fault_injector=fail
            )
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        marker = await (
            await db.execute(
                "SELECT 1 FROM workflow_schema_migrations WHERE version=29"
            )
        ).fetchone()
        table = await (
            await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='execution_tool_public_projections'"
            )
        ).fetchone()
        assert version == (28,)
        assert marker is None
        assert table is None

        await _migrate_v28_to_v29_public_run_projection(db)
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        assert version == (29,)
        await db.execute("INSERT INTO execution_runs(run_id) VALUES('root-1')")
        await db.commit()
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO execution_run_block_signals(
            signal_id,root_run_id,schema_version,reason_code,evidence_refs_json,
            producer,created_event_id,payload_hash,created_at)
            VALUES('signal-1','root-1',1,'workspace_unavailable','[]',
            'test','future-event',?,1)""",
            ("a" * 64,),
        )
        await db.execute(
            "INSERT INTO execution_events(event_id) VALUES('future-event')"
        )
        await db.commit()
