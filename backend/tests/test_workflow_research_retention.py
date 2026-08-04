from __future__ import annotations

import hashlib
from pathlib import Path

import aiosqlite
import pytest

from deskpet.workflows.retention import (
    BLOB_REF_STAGE,
    CHECKPOINT_STAGE,
    CONTROL_STAGE,
    DELIVERY_STAGE,
    LINEAGE_STAGE,
    ORPHAN_STAGE,
    REACHABILITY_STAGE,
    RUN_STAGE,
    SNAPSHOT_STAGE,
    TOMBSTONE_STAGE,
    RetentionPolicy,
    WorkflowRetentionManager,
)
from deskpet.workflows.store.schema import initialize_workflow_db


DAY = 24 * 60 * 60
RESEARCH_STAGE_ORDER = (
    CONTROL_STAGE,
    REACHABILITY_STAGE,
    DELIVERY_STAGE,
    TOMBSTONE_STAGE,
    CHECKPOINT_STAGE,
    BLOB_REF_STAGE,
    LINEAGE_STAGE,
    SNAPSHOT_STAGE,
    RUN_STAGE,
    ORPHAN_STAGE,
)


class FixedClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def now(self) -> float:
        return self.value


def _policy() -> RetentionPolicy:
    return RetentionPolicy.from_days(
        terminal_days=30,
        evaluation_tombstone_days=180,
        orphan_grace_hours=24,
    )


async def _db(path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def _run(
    db: aiosqlite.Connection,
    run_id: str,
    *,
    status: str,
    timestamp: float,
) -> None:
    ended_at = timestamp if status in {"completed", "failed", "cancelled"} else None
    await db.execute(
        """INSERT INTO workflow_runs(
        run_id,trace_id,thread_id,checkpoint_ns,session_id,workflow_name,
        workflow_version,manifest_hash,implementation_hash,capability_hash,
        state_schema_version,status,active_nodes_json,created_at,updated_at,ended_at
        ) VALUES(?,?,?,?,?,'deep_research','v5','manifest','implementation',
        'capability',5,?,'[]',?,?,?)""",
        (
            run_id,
            f"trace-{run_id}",
            f"thread-{run_id}",
            "",
            f"session-{run_id}",
            status,
            timestamp,
            timestamp,
            ended_at,
        ),
    )


async def _snapshot(
    db: aiosqlite.Connection,
    blob_root: Path,
    *,
    run_id: str,
    operation_id: str,
    expires_at: float,
    pin: bool = True,
    write_file: bool = True,
) -> tuple[str, Path]:
    data = f"snapshot:{run_id}:{operation_id}".encode()
    digest = hashlib.sha256(data).hexdigest()
    path = blob_root / digest[:2] / digest[2:]
    if write_file:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    await db.execute(
        """INSERT INTO workflow_blobs(
        sha256,size_bytes,media_type,relative_path,created_at
        ) VALUES(?,?,'application/json',?,?)""",
        (digest, len(data), path.relative_to(blob_root).as_posix(), expires_at - DAY),
    )
    await db.execute(
        """INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at)
        VALUES(?,'research_snapshot',?,?)""",
        (digest, digest, expires_at - DAY),
    )
    await db.execute(
        """INSERT INTO workflow_research_snapshots(
        snapshot_hash,run_id,operation_id,schema_version,manifest_ref,created_at,expires_at
        ) VALUES(?,?,?,1,?,?,?)""",
        (digest, run_id, operation_id, digest, expires_at - DAY, expires_at),
    )
    if pin:
        await db.execute(
            """INSERT INTO workflow_research_snapshot_pins(
            pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
            ) VALUES(?,?,?,'continue_parent',?,?)""",
            (f"pin-{run_id}", digest, run_id, expires_at, expires_at - DAY),
        )
    return digest, path


async def _lineage(
    db: aiosqlite.Connection,
    *,
    operation_id: str,
    run_id: str,
    timestamp: float,
    parent_run_id: str | None = None,
    parent_operation_id: str | None = None,
    snapshot_hash: str | None = None,
) -> None:
    await db.execute(
        """INSERT INTO workflow_research_lineage(
        operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
        parent_report_ref,budget_lease_id,created_at
        ) VALUES(?,?,?,?,?,NULL,?,?)""",
        (
            operation_id,
            run_id,
            parent_run_id,
            parent_operation_id,
            snapshot_hash,
            f"lease-{operation_id}",
            timestamp,
        ),
    )


@pytest.mark.asyncio
async def test_retained_child_protects_ancestor_then_child_first_gc(tmp_path: Path):
    now = 90_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    db = await _db(db_path)
    try:
        old = now - 200 * DAY
        recent = now - 2 * DAY
        await _run(db, "parent", status="completed", timestamp=old)
        await _run(db, "child", status="completed", timestamp=recent)
        snapshot_hash, blob_path = await _snapshot(
            db,
            blob_root,
            run_id="parent",
            operation_id="op-parent",
            expires_at=now - DAY,
        )
        await _lineage(db, operation_id="op-parent", run_id="parent", timestamp=old)
        await _lineage(
            db,
            operation_id="op-child",
            run_id="child",
            timestamp=recent,
            parent_run_id="parent",
            parent_operation_id="op-parent",
            snapshot_hash=snapshot_hash,
        )
        await db.commit()
    finally:
        await db.close()

    manager = WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(now)
    )
    first = await manager.cleanup()
    assert first.stage_names == RESEARCH_STAGE_ORDER
    assert "parent:research_lineage" in first.stage(DELIVERY_STAGE).protected

    db = await _db(db_path)
    try:
        assert [
            row[0]
            for row in await (
                await db.execute("SELECT run_id FROM workflow_runs ORDER BY run_id")
            ).fetchall()
        ] == ["child", "parent"]
    finally:
        await db.close()

    later = now + 200 * DAY
    second = await WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(later)
    ).cleanup(dry_run=True)
    assert second.stage(LINEAGE_STAGE).candidates[:2] == (
        "lineage:op-child",
        "lineage:op-parent",
    )
    assert second.stage(RUN_STAGE).candidates == ("run:child", "run:parent")
    assert blob_path.exists()

    await WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(later)
    ).cleanup()
    db = await _db(db_path)
    try:
        for table in (
            "workflow_runs",
            "workflow_research_lineage",
            "workflow_research_snapshot_pins",
            "workflow_research_snapshots",
            "workflow_blob_refs",
            "workflow_blobs",
        ):
            assert await (await db.execute(f"SELECT 1 FROM {table}")).fetchone() is None
    finally:
        await db.close()
    assert not blob_path.exists()


@pytest.mark.asyncio
async def test_continue_pin_expiry_and_terminal_control_cleanup_are_ordered(tmp_path: Path):
    now = 100_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    db = await _db(db_path)
    try:
        old = now - 200 * DAY
        await _run(db, "parent", status="completed", timestamp=old)
        snapshot_hash, _ = await _snapshot(
            db,
            blob_root,
            run_id="parent",
            operation_id="op-parent",
            expires_at=now + DAY,
        )
        await _lineage(db, operation_id="op-parent", run_id="parent", timestamp=old)
        await db.execute(
            """INSERT INTO workflow_run_control_commands(
            command_id,run_id,idempotency_key,action,status,payload_json,
            consumed_at,created_at,updated_at
            ) VALUES('done','parent','idem','generate_now','consumed','{}',?,?,?)""",
            (old, old, old),
        )
        await db.commit()
    finally:
        await db.close()

    manager = WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(now)
    )
    preview = await manager.cleanup(dry_run=True)
    assert preview.stage_names == RESEARCH_STAGE_ORDER
    assert preview.stage(CONTROL_STAGE).candidates == ("command:done",)
    assert f"snapshot:{snapshot_hash}" in preview.stage(REACHABILITY_STAGE).protected
    assert preview.stage(RUN_STAGE).candidates == ()

    await manager.cleanup()
    db = await _db(db_path)
    try:
        assert await (
            await db.execute("SELECT 1 FROM workflow_runs WHERE run_id='parent'")
        ).fetchone()
        assert await (
            await db.execute("SELECT 1 FROM workflow_run_control_commands")
        ).fetchone() is None
    finally:
        await db.close()

    after_expiry = now + 2 * DAY
    await WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(after_expiry)
    ).cleanup()
    db = await _db(db_path)
    try:
        assert await (
            await db.execute("SELECT 1 FROM workflow_runs WHERE run_id='parent'")
        ).fetchone() is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_expired_orphan_pin_is_recomputed_and_missing_blob_is_reported(tmp_path: Path):
    now = 110_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    db = await _db(db_path)
    try:
        recent = now - DAY
        await _run(db, "recent", status="completed", timestamp=recent)
        snapshot_hash, _ = await _snapshot(
            db,
            blob_root,
            run_id="recent",
            operation_id="op-recent",
            expires_at=now - 1,
            write_file=False,
        )
        await db.commit()
    finally:
        await db.close()

    preview = await WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(now)
    ).cleanup(dry_run=True)
    assert preview.stage(LINEAGE_STAGE).candidates == ("pin:pin-recent",)
    assert preview.stage(SNAPSHOT_STAGE).candidates == (
        f"snapshot-ref:{snapshot_hash}:{snapshot_hash}",
        f"snapshot:{snapshot_hash}",
    )
    assert f"missing-blob-file:{snapshot_hash}" in preview.stage(ORPHAN_STAGE).warnings

    applied = await WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(now)
    ).reconcile_startup()
    assert f"missing-blob-file:{snapshot_hash}" in applied.stage(ORPHAN_STAGE).warnings
    db = await _db(db_path)
    try:
        assert await (
            await db.execute("SELECT 1 FROM workflow_runs WHERE run_id='recent'")
        ).fetchone()
        assert await (
            await db.execute("SELECT 1 FROM workflow_research_snapshot_pins")
        ).fetchone() is None
        assert await (
            await db.execute("SELECT 1 FROM workflow_research_snapshots")
        ).fetchone() is None
        assert await (
            await db.execute("SELECT 1 FROM workflow_blobs")
        ).fetchone() is None
    finally:
        await db.close()
