from __future__ import annotations

import hashlib

import aiosqlite
import pytest

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
    } <= names


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
