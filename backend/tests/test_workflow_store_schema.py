from __future__ import annotations

import hashlib

import aiosqlite
import pytest

from deskpet.workflows.store import schema as workflow_schema
from deskpet.workflows.store import (
    BlobStore,
    StaleRunFence,
    WORKFLOW_SCHEMA_VERSION,
    WorkflowRunStore,
    initialize_workflow_db,
)


@pytest.mark.asyncio
async def test_workflow_schema_is_complete_and_idempotent(tmp_path):
    path = tmp_path / "data" / "workflow.db"
    assert await initialize_workflow_db(path) == path
    await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        row = await (await db.execute("PRAGMA user_version")).fetchone()
        assert row == (WORKFLOW_SCHEMA_VERSION,)
        rows = await (await db.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()

    names = {row[0] for row in rows}
    assert {
        "workflow_runs",
        "workflow_nodes",
        "workflow_node_attempts",
        "workflow_checkpoints",
        "workflow_pending_writes",
        "workflow_effects",
        "workflow_decisions",
        "workflow_events",
        "workflow_deliveries",
        "trace_runs",
        "trace_spans",
        "evaluations",
        "workflow_effect_budget_reservations",
        "workflow_run_control_commands",
        "workflow_research_snapshots",
        "workflow_research_snapshot_pins",
        "workflow_research_lineage",
        "workflow_research_deadlines",
        "workflow_research_resource_budgets",
        "workflow_research_resource_reservations",
        "workflow_effect_attempt_heads",
        "workflow_research_continuation_heads",
        "execution_runs",
        "execution_run_links",
        "execution_decisions",
        "execution_grants",
        "execution_effects",
        "execution_effect_attempts",
        "execution_effect_links",
        "execution_events",
        "execution_deliveries",
        "execution_continuations",
        "execution_child_commands",
        "execution_child_signal_inbox",
        "execution_runtime_state",
        "execution_legacy_drain_items",
    } <= names


async def _drop_v5_execution_schema(db) -> None:
    for table in (
        "execution_legacy_drain_items",
        "execution_runtime_state",
        "execution_child_signal_inbox",
        "execution_child_commands",
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
    ):
        await db.execute(f"DROP TABLE IF EXISTS {table}")
    await db.execute("DELETE FROM workflow_schema_migrations WHERE version>=5")


async def _downgrade_v3_fixture(path, version: int) -> None:
    await initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        await _drop_v5_execution_schema(db)
        for index in (
            "uq_workflow_effect_logical_attempt",
            "idx_workflow_effect_attempt_heads_run",
            "uq_workflow_deliveries_manifest_spec",
            "idx_workflow_deliveries_manifest_required_status",
        ):
            await db.execute(f"DROP INDEX IF EXISTS {index}")
        for table in (
            "workflow_research_continuation_heads",
            "workflow_research_resource_reservations",
            "workflow_research_resource_budgets",
            "workflow_research_deadlines",
            "workflow_effect_attempt_heads",
            "workflow_research_lineage",
            "workflow_research_snapshot_pins",
            "workflow_research_snapshots",
            "workflow_run_control_commands",
            "workflow_effect_budget_reservations",
        ):
            await db.execute(f"DROP TABLE {table}")
        await db.execute("DELETE FROM workflow_schema_migrations WHERE version=4")
        await db.execute("DELETE FROM workflow_schema_migrations WHERE version=3")
        if version == 1:
            await db.execute("DELETE FROM workflow_schema_migrations WHERE version=2")
        await db.execute(f"PRAGMA user_version={version}")
        await db.commit()


async def _as_v3_with_historical_lineage(path, workflow_version: str, *, siblings: int = 1):
    await initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=OFF")
        await _drop_v5_execution_schema(db)
        for index in (
            "uq_workflow_effect_logical_attempt",
            "idx_workflow_effect_attempt_heads_run",
            "uq_workflow_deliveries_manifest_spec",
            "idx_workflow_deliveries_manifest_required_status",
        ):
            await db.execute(f"DROP INDEX IF EXISTS {index}")
        for table in (
            "workflow_research_continuation_heads",
            "workflow_research_resource_reservations",
            "workflow_research_resource_budgets",
            "workflow_research_deadlines",
            "workflow_effect_attempt_heads",
        ):
            await db.execute(f"DROP TABLE {table}")
        await db.execute("DELETE FROM workflow_schema_migrations WHERE version=4")
        await db.execute("PRAGMA user_version=3")
        now = 100.0

        async def insert_run(run_id: str, parent: str | None = None) -> None:
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,parent_run_id,session_id,
                workflow_name,workflow_version,manifest_hash,implementation_hash,
                capability_hash,state_schema_version,status,active_nodes_json,
                created_at,updated_at,ended_at
                ) VALUES(?,?,?,?,?,?,'deep_research',?,'m','i','c',5,
                'completed','[]',?,?,?)""",
                (
                    run_id,
                    f"trace-{run_id}",
                    f"thread-{run_id}",
                    "",
                    parent,
                    f"session-{run_id}",
                    workflow_version,
                    now,
                    now,
                    now,
                ),
            )

        await insert_run("historical-root")
        await db.execute(
            """INSERT INTO workflow_research_lineage(
            operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
            parent_report_ref,budget_lease_id,created_at
            ) VALUES('op-root','historical-root',NULL,NULL,NULL,NULL,'lease-root',?)""",
            (now,),
        )
        await db.execute(
            """INSERT INTO workflow_research_snapshots(
            snapshot_hash,run_id,operation_id,schema_version,manifest_ref,created_at,expires_at
            ) VALUES('historical-snapshot','historical-root','op-root',1,'manifest',?,NULL)""",
            (now,),
        )
        for index in range(siblings):
            child = f"historical-child-{index}"
            await insert_run(child, "historical-root")
            await db.execute(
                """INSERT INTO workflow_research_lineage(
                operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
                parent_report_ref,budget_lease_id,created_at
                ) VALUES(?,?, 'historical-root','op-root','historical-snapshot',NULL,?,?)""",
                (f"op-child-{index}", child, f"lease-child-{index}", now),
            )
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("starting_version", (1, 2))
async def test_v1_and_v2_follow_the_migration_loop_to_v5(tmp_path, starting_version):
    path = tmp_path / f"workflow-v{starting_version}.db"
    await _downgrade_v3_fixture(path, starting_version)

    await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        migrations = await (
            await db.execute("SELECT version FROM workflow_schema_migrations ORDER BY version")
        ).fetchall()
        tables = await (
            await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    assert version == (7,)
    assert {row[0] for row in migrations} >= {2, 3, 4, 5, 6, 7}
    assert "workflow_effect_budget_reservations" in {row[0] for row in tables}


@pytest.mark.asyncio
async def test_v3_migration_rolls_back_cleanly_after_interrupt(tmp_path, monkeypatch):
    path = tmp_path / "workflow-interrupted.db"
    await _downgrade_v3_fixture(path, 2)
    original = workflow_schema._migrate_v2_to_v3

    async def interrupted(db):
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("CREATE TABLE interrupted_marker(value INTEGER)")
        raise RuntimeError("injected migration interruption")

    monkeypatch.setattr(workflow_schema, "_migrate_v2_to_v3", interrupted)
    with pytest.raises(RuntimeError, match="injected migration interruption"):
        await initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        marker = await (
            await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='interrupted_marker'"
            )
        ).fetchone()
    assert version == (2,)
    assert marker is None

    monkeypatch.setattr(workflow_schema, "_migrate_v2_to_v3", original)
    await initialize_workflow_db(path)
    await initialize_workflow_db(path)


@pytest.mark.asyncio
async def test_pre_v4_v6_continuation_fails_closed_without_partial_schema(tmp_path):
    path = tmp_path / "workflow-pre-v4-v6.db"
    await _as_v3_with_historical_lineage(path, "v6")

    with pytest.raises(RuntimeError, match="pre-v4 deep_research/v6 continuation"):
        await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        v4_table = await (
            await db.execute(
                """SELECT name FROM sqlite_master
                WHERE type='table' AND name='workflow_research_continuation_heads'"""
            )
        ).fetchone()
        children = await (
            await db.execute(
                "SELECT run_id FROM workflow_research_lineage WHERE parent_run_id IS NOT NULL"
            )
        ).fetchall()
    assert version == (3,)
    assert v4_table is None
    assert children == [("historical-child-0",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_version", ("v1", "v2", "v3", "v4", "v5"))
async def test_v1_to_v5_historical_lineage_migrates_without_reinterpretation(
    tmp_path, workflow_version
):
    path = tmp_path / f"workflow-{workflow_version}.db"
    await _as_v3_with_historical_lineage(
        path, workflow_version, siblings=2 if workflow_version == "v5" else 1
    )

    await initialize_workflow_db(path)

    async with aiosqlite.connect(path) as db:
        version = await (await db.execute("PRAGMA user_version")).fetchone()
        lineage = await (
            await db.execute(
                """SELECT run_id,parent_run_id FROM workflow_research_lineage
                ORDER BY run_id"""
            )
        ).fetchall()
        heads = await (
            await db.execute("SELECT 1 FROM workflow_research_continuation_heads")
        ).fetchall()
    expected_children = 2 if workflow_version == "v5" else 1
    assert version == (7,)
    assert len(lineage) == expected_children + 1
    assert sum(row[1] == "historical-root" for row in lineage) == expected_children
    assert heads == []


@pytest.mark.asyncio
async def test_future_workflow_schema_is_rejected_without_mutation(tmp_path):
    path = tmp_path / "workflow-future.db"
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE future_marker(value TEXT)")
        await db.execute("INSERT INTO future_marker VALUES('untouched')")
        await db.execute("PRAGMA user_version=99")
        await db.commit()

    with pytest.raises(RuntimeError, match="newer than supported"):
        await initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        value = await (await db.execute("SELECT value FROM future_marker")).fetchone()
        version = await (await db.execute("PRAGMA user_version")).fetchone()
    assert value == ("untouched",)
    assert version == (99,)


def test_blob_store_is_content_addressed_and_checks_integrity(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    ref = store.put(b"durable state", media_type="application/json")
    assert ref.sha256 == hashlib.sha256(b"durable state").hexdigest()
    assert store.put(b"durable state") .sha256 == ref.sha256
    assert store.get(ref) == b"durable state"

    store.path_for(ref.sha256).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        store.get(ref)


@pytest.mark.asyncio
async def test_start_is_idempotent_and_fence_rejects_stale_owner(tmp_path):
    now = [100.0]
    store = WorkflowRunStore(tmp_path / "workflow.db", clock=lambda: now[0])
    kwargs = dict(
        request_key="session:req:turn:deep-research",
        session_id="session",
        request_id="req",
        turn_id="turn",
        workflow_name="deep-research",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="caps",
        capability_snapshot={"tools": ["web_search"]},
        state_schema_version=1,
    )
    run_id, created = await store.create_run(**kwargs)
    assert created is True
    assert await store.create_run(**kwargs) == (run_id, False)

    first = await store.claim(run_id, "runner-a")
    event = await store.append_event(first, "run.started", {"status": "running"}, event_key="accepted")
    assert event["seq"] == 1
    assert await store.append_event(first, "run.started", {"status": "running"}, event_key="accepted") == event

    now[0] = 200.0
    second = await store.claim(run_id, "runner-b")
    assert second.lease_epoch > first.lease_epoch
    with pytest.raises(StaleRunFence):
        await store.append_event(first, "node.finished", {})

    assert [item["event_type"] for item in await store.events_after(run_id, 0)] == ["run.started"]


@pytest.mark.asyncio
async def test_cancel_invalidates_current_fence(tmp_path):
    store = WorkflowRunStore(tmp_path / "workflow.db")
    run_id, _ = await store.create_run(
        request_key="k",
        session_id="s",
        request_id="r",
        turn_id="t",
        workflow_name="code-complex",
        workflow_version="1",
        manifest_hash="m",
        implementation_hash="i",
        capability_hash="c",
        capability_snapshot={},
        state_schema_version=1,
    )
    fence = await store.claim(run_id, "runner")
    await store.request_cancel(run_id, "user")
    with pytest.raises(StaleRunFence):
        await store.heartbeat(fence)
