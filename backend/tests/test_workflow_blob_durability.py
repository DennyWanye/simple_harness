from __future__ import annotations

import sqlite3

import pytest

from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.store import RegisteredBlobStore


@pytest.mark.asyncio
async def test_registered_blob_put_adds_run_staging_owner_and_validates_get(tmp_path):
    database = tmp_path / "workflow.db"
    store = RegisteredBlobStore(tmp_path / "blobs", database)
    identity = NodeExecutionIdentity(
        "deep_research", "v2", "thread", "run-1", "checkpoint", "", "task", "fetch_b0", 1
    )
    ref = await store.put(b"durable evidence", identity, media_type="text/plain")
    assert await store.get(ref) == b"durable evidence"
    db = sqlite3.connect(database)
    try:
        owner = db.execute(
            "SELECT owner_kind,owner_id FROM workflow_blob_refs WHERE sha256=?", (ref.sha256,)
        ).fetchone()
    finally:
        db.close()
    assert owner == ("run_staging", "run-1")


@pytest.mark.asyncio
async def test_registered_blob_get_rejects_missing_registry(tmp_path):
    store = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    with pytest.raises(FileNotFoundError, match="not registered"):
        await store.get("0" * 64)
