from __future__ import annotations

import hashlib

import aiosqlite
import pytest

from deskpet.workflows.store import schema as workflow_schema
from deskpet.workflows.store import initialize_workflow_db


EXECUTION_TABLES = (
    "execution_child_commands",
    "execution_child_signal_inbox",
    "execution_continuations",
    "execution_deliveries",
    "execution_events",
    "execution_effect_links",
    "execution_effect_attempts",
    "execution_effects",
    "execution_grants",
    "execution_decisions",
    "execution_run_links",
    "execution_runs",
)


async def _downgrade_to_v4(path) -> None:
    await initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        for table in EXECUTION_TABLES:
            await db.execute(f"DROP TABLE IF EXISTS {table}")
        await db.execute("DELETE FROM workflow_schema_migrations WHERE version>=5")
        await db.execute("PRAGMA user_version=4")
        await db.commit()


async def _execution_schema(path):
    async with aiosqlite.connect(path) as db:
        return await (
            await db.execute(
                """SELECT type,name,sql FROM sqlite_master
                WHERE name LIKE 'execution_%' OR name LIKE '%execution_%'
                ORDER BY type,name"""
            )
        ).fetchall()


@pytest.mark.asyncio
async def test_fresh_and_v4_migration_produce_the_same_execution_schema(tmp_path):
    fresh = tmp_path / "fresh.db"
    migrated = tmp_path / "migrated.db"
    await initialize_workflow_db(fresh)
    await _downgrade_to_v4(migrated)

    await initialize_workflow_db(migrated)

    assert await _execution_schema(fresh) == await _execution_schema(migrated)
    async with aiosqlite.connect(migrated) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        migration = await (
            await db.execute(
                "SELECT COUNT(*) FROM workflow_schema_migrations WHERE version IN (5,6)"
            )
        ).fetchone()
    assert version == (6,)
    assert migration == (2,)


@pytest.mark.asyncio
async def test_v4_migration_interruption_rolls_back_and_can_retry(tmp_path, monkeypatch):
    path = tmp_path / "interrupted.db"
    await _downgrade_to_v4(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO workflow_runs(
            run_id,trace_id,thread_id,checkpoint_ns,session_id,request_id,turn_id,
            workflow_name,workflow_version,manifest_hash,implementation_hash,
            capability_hash,state_schema_version,status,active_nodes_json,
            created_at,updated_at
            ) VALUES('legacy','trace','thread','','session','request','turn',
            'deep_research','v7','manifest','implementation','capability',7,
            'waiting','[]',1.0,1.0)"""
        )
        await db.commit()

    original = workflow_schema._migrate_v4_to_v5

    async def interrupted(db):
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("CREATE TABLE interrupted_execution_marker(value INTEGER)")
        raise RuntimeError("injected v5 migration interruption")

    monkeypatch.setattr(workflow_schema, "_migrate_v4_to_v5", interrupted)
    with pytest.raises(RuntimeError, match="injected v5 migration interruption"):
        await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        marker = await (
            await db.execute(
                """SELECT name FROM sqlite_master
                WHERE type='table' AND name='interrupted_execution_marker'"""
            )
        ).fetchone()
        legacy = await (
            await db.execute("SELECT run_id,status FROM workflow_runs")
        ).fetchall()
    assert version == (4,)
    assert marker is None
    assert legacy == [("legacy", "waiting")]

    monkeypatch.setattr(workflow_schema, "_migrate_v4_to_v5", original)
    await initialize_workflow_db(path)
    await initialize_workflow_db(path)


@pytest.mark.asyncio
async def test_v1_to_v7_historical_rows_and_checkpoint_bytes_are_not_rewritten(tmp_path):
    path = tmp_path / "historical.db"
    await _downgrade_to_v4(path)
    async with aiosqlite.connect(path) as db:
        for number in range(1, 8):
            version = f"v{number}"
            run_id = f"historical-{version}"
            checkpoint = f"checkpoint-bytes-{version}-\x00\xff".encode("utf-8")
            metadata = f"metadata-bytes-{version}-\x00".encode("utf-8")
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,session_id,request_id,turn_id,
                workflow_name,workflow_version,manifest_hash,implementation_hash,
                capability_hash,state_schema_version,status,active_nodes_json,
                created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'waiting','[]',?,?)""",
                (
                    run_id,
                    f"trace-{version}",
                    f"thread-{version}",
                    "",
                    "historical-session",
                    f"request-{version}",
                    f"turn-{version}",
                    "deep_research",
                    version,
                    f"manifest-{version}",
                    f"implementation-{version}",
                    f"capability-{version}",
                    number,
                    float(number),
                    float(number),
                ),
            )
            await db.execute(
                """INSERT INTO workflow_checkpoints(
                thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
                checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,
                snapshot_version,created_at
                ) VALUES(?, '', ?, NULL, ?, 'fixture', ?, ?, 'native', ?, ?)""",
                (
                    f"thread-{version}",
                    f"checkpoint-{version}",
                    run_id,
                    checkpoint,
                    metadata,
                    number,
                    float(number),
                ),
            )
        await db.commit()
        before_runs = await (
            await db.execute(
                """SELECT run_id,workflow_version,manifest_hash,implementation_hash,
                capability_hash,state_schema_version,status,created_at,updated_at
                FROM workflow_runs ORDER BY run_id"""
            )
        ).fetchall()
        before_checkpoints = await (
            await db.execute(
                """SELECT run_id,checkpoint_blob,metadata_blob
                FROM workflow_checkpoints ORDER BY run_id"""
            )
        ).fetchall()
    before_digests = [
        (
            row[0],
            hashlib.sha256(bytes(row[1])).hexdigest(),
            hashlib.sha256(bytes(row[2])).hexdigest(),
        )
        for row in before_checkpoints
    ]

    await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        after_runs = await (
            await db.execute(
                """SELECT run_id,workflow_version,manifest_hash,implementation_hash,
                capability_hash,state_schema_version,status,created_at,updated_at
                FROM workflow_runs ORDER BY run_id"""
            )
        ).fetchall()
        after_checkpoints = await (
            await db.execute(
                """SELECT run_id,checkpoint_blob,metadata_blob
                FROM workflow_checkpoints ORDER BY run_id"""
            )
        ).fetchall()
        execution_count = await (
            await db.execute("SELECT COUNT(*) FROM execution_runs")
        ).fetchone()
    after_digests = [
        (
            row[0],
            hashlib.sha256(bytes(row[1])).hexdigest(),
            hashlib.sha256(bytes(row[2])).hexdigest(),
        )
        for row in after_checkpoints
    ]
    assert after_runs == before_runs
    assert after_digests == before_digests
    assert execution_count == (0,)
