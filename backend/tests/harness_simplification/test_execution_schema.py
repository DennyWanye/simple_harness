from __future__ import annotations

import hashlib

import aiosqlite
import pytest

from deskpet.workflows.store import schema as workflow_schema
from deskpet.workflows.store import initialize_workflow_db


EXECUTION_TABLES = (
    "execution_legacy_drain_items",
    "execution_runtime_state",
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


async def _create_v6_database(path) -> None:
    original = workflow_schema.WORKFLOW_SCHEMA_VERSION
    workflow_schema.WORKFLOW_SCHEMA_VERSION = 6
    try:
        await initialize_workflow_db(path)
    finally:
        workflow_schema.WORKFLOW_SCHEMA_VERSION = original


async def _insert_execution_run(db, run_id: str) -> None:
    digest = "a" * 64
    await db.execute(
        """INSERT INTO execution_runs(
        run_id,schema_version,idempotency_key,session_id,root_run_id,parent_run_id,
        request_id,turn_id,venue,workspace_json,capability_hash,provider_plan_json,
        trace_id,principal_id,auth_epoch,payload_fingerprint,capability_fingerprint,
        driver_kind,profile_key,persistence_level,status,created_at,updated_at
        ) VALUES(?,1,?,?,?,NULL,?,?,'chat','{}',?,'{}',?,?,0,?,?,
        'react','chat.default','durable','running',1.0,1.0)""",
        (
            run_id,
            f"idem-{run_id}",
            "session",
            run_id,
            f"request-{run_id}",
            f"turn-{run_id}",
            digest,
            f"trace-{run_id}",
            "principal",
            digest,
            digest,
        ),
    )


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
                "SELECT COUNT(*) FROM workflow_schema_migrations WHERE version IN (5,6,7)"
            )
        ).fetchone()
    assert version == (7,)
    assert migration == (3,)


@pytest.mark.asyncio
async def test_fresh_v7_has_dormant_legacy_runtime_state_and_owner_constraints(tmp_path):
    path = tmp_path / "fresh-v7.db"
    await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        state = await (
            await db.execute(
                """SELECT singleton_id,generation,phase,drain_manifest_hash,
                drain_count,activated_at FROM execution_runtime_state"""
            )
        ).fetchall()
        columns = {
            row[1]: (row[3], row[4])
            for row in await (await db.execute("PRAGMA table_info(execution_runs)")).fetchall()
        }
        tables = {
            row[0]
            for row in await (
                await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        await _insert_execution_run(db, "legacy-default")
        owner = await (
            await db.execute(
                "SELECT owner_kind,owner_generation FROM execution_runs WHERE run_id=?",
                ("legacy-default",),
            )
        ).fetchone()
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "UPDATE execution_runs SET owner_kind='kernel' WHERE run_id=?",
                ("legacy-default",),
            )
        await db.execute(
            """UPDATE execution_runs
            SET owner_kind='kernel',owner_generation=1 WHERE run_id=?""",
            ("legacy-default",),
        )
        kernel_owner = await (
            await db.execute(
                "SELECT owner_kind,owner_generation FROM execution_runs WHERE run_id=?",
                ("legacy-default",),
            )
        ).fetchone()
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                """INSERT INTO execution_runtime_state(
                singleton_id,generation,phase,drain_count,created_at,updated_at
                ) VALUES(2,0,'legacy',0,1.0,1.0)"""
            )

    assert state == [(1, 0, "legacy", None, 0, None)]
    assert columns["owner_kind"] == (1, "'legacy'")
    assert columns["owner_generation"] == (1, "0")
    assert owner == ("legacy", 0)
    assert kernel_owner == ("kernel", 1)
    assert {"execution_runtime_state", "execution_legacy_drain_items"} <= tables


@pytest.mark.asyncio
async def test_v6_to_v7_backfills_historical_execution_owner_without_rewriting_blobs(tmp_path):
    path = tmp_path / "historical-v6.db"
    await _create_v6_database(path)
    checkpoint = b"historical-checkpoint-\x00\xff"
    metadata = b"historical-metadata-\x00"
    async with aiosqlite.connect(path) as db:
        await _insert_execution_run(db, "historical-execution")
        await db.execute(
            """INSERT INTO workflow_runs(
            run_id,trace_id,thread_id,checkpoint_ns,session_id,workflow_name,
            workflow_version,manifest_hash,implementation_hash,capability_hash,
            state_schema_version,status,active_nodes_json,created_at,updated_at
            ) VALUES('historical-workflow','trace-workflow','thread-workflow','',
            'session','deep_research','v6','manifest','implementation','capability',
            6,'waiting','[]',1.0,1.0)"""
        )
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
            checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,
            snapshot_version,created_at
            ) VALUES('thread-workflow','','checkpoint',NULL,'historical-workflow',
            'fixture',?,?,'native',6,1.0)""",
            (checkpoint, metadata),
        )
        await db.commit()
        before = hashlib.sha256(checkpoint + metadata).hexdigest()

    await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        owner = await (
            await db.execute(
                """SELECT owner_kind,owner_generation FROM execution_runs
                WHERE run_id='historical-execution'"""
            )
        ).fetchone()
        blobs = await (
            await db.execute(
                """SELECT checkpoint_blob,metadata_blob FROM workflow_checkpoints
                WHERE run_id='historical-workflow'"""
            )
        ).fetchone()

    assert version == (7,)
    assert owner == ("legacy", 0)
    assert hashlib.sha256(bytes(blobs[0]) + bytes(blobs[1])).hexdigest() == before


@pytest.mark.asyncio
async def test_v7_drain_manifest_schema_enforces_identity_state_and_fence(tmp_path):
    path = tmp_path / "drain-schema.db"
    await initialize_workflow_db(path)
    values = (
        "drain-1", 1, "workflow_run", "run-1", "pending", 1.0, 1.0
    )
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO execution_legacy_drain_items(
            drain_item_id,manifest_generation,source_kind,source_run_id,status,
            created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?)""",
            values,
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                """INSERT INTO execution_legacy_drain_items(
                drain_item_id,manifest_generation,source_kind,source_run_id,status,
                created_at,updated_at
                ) VALUES('drain-duplicate',1,'workflow_run','run-1','pending',1.0,1.0)"""
            )
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                """INSERT INTO execution_legacy_drain_items(
                drain_item_id,manifest_generation,source_kind,source_run_id,status,
                lease_owner,lease_epoch,created_at,updated_at
                ) VALUES('drain-bad-fence',1,'execution_run','run-2','leased',
                'worker',1,1.0,1.0)"""
            )
        await db.execute(
            """UPDATE execution_legacy_drain_items
            SET status='leased',lease_owner='worker',lease_epoch=1,
                lease_expires_at=10.0,updated_at=2.0
            WHERE drain_item_id='drain-1'"""
        )
        row = await (
            await db.execute(
                """SELECT status,lease_owner,lease_epoch,lease_expires_at
                FROM execution_legacy_drain_items WHERE drain_item_id='drain-1'"""
            )
        ).fetchone()
    assert row == ("leased", "worker", 1, 10.0)


@pytest.mark.asyncio
async def test_v7_migration_interrupt_rolls_back_and_retries(tmp_path, monkeypatch):
    path = tmp_path / "interrupted-v7.db"
    await _create_v6_database(path)
    async with aiosqlite.connect(path) as db:
        await _insert_execution_run(db, "historical")
        await db.commit()

    original = workflow_schema._migrate_v6_to_v7

    async def interrupted(db):
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "ALTER TABLE execution_runs ADD COLUMN owner_kind TEXT NOT NULL DEFAULT 'legacy'"
        )
        await db.execute("CREATE TABLE interrupted_v7_marker(value INTEGER)")
        raise RuntimeError("injected v7 migration interruption")

    monkeypatch.setattr(workflow_schema, "_migrate_v6_to_v7", interrupted)
    with pytest.raises(RuntimeError, match="injected v7 migration interruption"):
        await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        columns = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(execution_runs)")).fetchall()
        }
        marker = await (
            await db.execute(
                """SELECT name FROM sqlite_master
                WHERE type='table' AND name='interrupted_v7_marker'"""
            )
        ).fetchone()
    assert version == (6,)
    assert "owner_kind" not in columns
    assert marker is None

    monkeypatch.setattr(workflow_schema, "_migrate_v6_to_v7", original)
    await initialize_workflow_db(path)
    await initialize_workflow_db(path)


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
