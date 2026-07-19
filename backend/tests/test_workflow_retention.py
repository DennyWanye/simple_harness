from __future__ import annotations

import hashlib
import os
from pathlib import Path

import aiosqlite
import pytest

from deskpet.workflows.bootstrap import build_workflow_service
from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.retention import (
    CLEANUP_STAGE_ORDER,
    ORPHAN_STAGE,
    RESERVATION_STAGE,
    RetentionPolicy,
    WorkflowRetentionManager,
)
from deskpet.workflows.runtime_adapters import WorkflowRuntimeAdapter
from deskpet.workflows.store.schema import initialize_workflow_db
from deskpet.workflows.trace.redaction import TraceRedactor


DAY = 24 * 60 * 60


class FixedClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def now(self) -> float:
        return self.value


class SingleSampleClock(FixedClock):
    def __init__(self, value: float) -> None:
        super().__init__(value)
        self.calls = 0

    def now(self) -> float:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("startup reconciliation sampled the clock more than once")
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


async def _insert_run(
    db: aiosqlite.Connection,
    run_id: str,
    *,
    status: str,
    timestamp: float,
    implementation_hash: str | None = None,
) -> None:
    ended_at = timestamp if status in {"completed", "failed", "cancelled"} else None
    await db.execute(
        """INSERT INTO workflow_runs(
            run_id,trace_id,thread_id,checkpoint_ns,session_id,workflow_name,
            workflow_version,manifest_hash,implementation_hash,capability_hash,
            state_schema_version,status,active_nodes_json,created_at,updated_at,ended_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,'["node"]',?,?,?)""",
        (
            run_id,
            f"trace-{run_id}",
            f"thread-{run_id}",
            "",
            f"session-{run_id}",
            "deep_research",
            "v1",
            f"manifest-{run_id}",
            implementation_hash or f"bundle-{run_id}",
            f"capability-{run_id}",
            status,
            timestamp,
            timestamp,
            ended_at,
        ),
    )


async def _insert_event(
    db: aiosqlite.Connection,
    run_id: str,
    *,
    delivery_status: str = "delivered",
) -> tuple[str, str]:
    event_id = f"event-{run_id}"
    delivery_id = f"delivery-{run_id}"
    await db.execute(
        """INSERT INTO workflow_events(
            event_id,event_key,run_id,seq,event_type,payload_json,created_at
        ) VALUES(?,?,?,1,'completed','{}',1)""",
        (event_id, f"key-{run_id}", run_id),
    )
    await db.execute(
        """INSERT INTO workflow_deliveries(
            delivery_id,event_id,run_id,channel,target_id,status,created_at,updated_at,delivered_at
        ) VALUES(?,?,?,'websocket','target',?,1,1,?)""",
        (
            delivery_id,
            event_id,
            run_id,
            delivery_status,
            1 if delivery_status == "delivered" else None,
        ),
    )
    return event_id, delivery_id


async def _insert_checkpoint(
    db: aiosqlite.Connection,
    *,
    run_id: str,
    thread_id: str,
    checkpoint_id: str,
    created_at: float,
) -> None:
    await db.execute(
        """INSERT OR IGNORE INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
            checkpoint_type,checkpoint_blob,metadata_blob,created_at
        ) VALUES(?,'',?,NULL,?,'full',X'01',X'02',?)""",
        (thread_id, checkpoint_id, run_id, created_at),
    )
    await db.execute(
        """INSERT INTO workflow_checkpoint_owners(
            run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
        ) VALUES(?,?,'',?,NULL,?)""",
        (run_id, thread_id, checkpoint_id, created_at),
    )


async def _insert_blob(
    db: aiosqlite.Connection,
    blob_root: Path,
    *,
    data: bytes,
    created_at: float,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    write_file: bool = True,
) -> tuple[str, Path]:
    digest = hashlib.sha256(data).hexdigest()
    path = blob_root / digest[:2] / digest[2:]
    if write_file:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (created_at, created_at))
    await db.execute(
        """INSERT INTO workflow_blobs(
            sha256,size_bytes,media_type,relative_path,created_at
        ) VALUES(?,?,'application/octet-stream',?,?)""",
        (digest, len(data), path.relative_to(blob_root).as_posix(), created_at),
    )
    if owner_kind is not None and owner_id is not None:
        await db.execute(
            """INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at)
            VALUES(?,?,?,?)""",
            (digest, owner_kind, owner_id, created_at),
        )
    return digest, path


@pytest.mark.asyncio
async def test_cleanup_order_boundary_and_dry_run_are_clock_driven(tmp_path: Path):
    now = 20_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    db = await _db(db_path)
    try:
        boundary = now - 30 * DAY
        await _insert_run(
            db,
            "boundary",
            status="completed",
            timestamp=boundary,
            implementation_hash="immutable-v1-bundle",
        )
        event_id, delivery_id = await _insert_event(db, "boundary")
        await _insert_checkpoint(
            db,
            run_id="boundary",
            thread_id="thread-boundary",
            checkpoint_id="checkpoint-boundary",
            created_at=boundary,
        )
        digest, blob_path = await _insert_blob(
            db,
            blob_root,
            data=b"old payload",
            created_at=boundary,
            owner_kind="run",
            owner_id="boundary",
        )
        await db.commit()
    finally:
        await db.close()

    manager = WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(now)
    )
    preview = await manager.cleanup(dry_run=True)
    assert preview.stage_names == CLEANUP_STAGE_ORDER
    assert preview.stage(CLEANUP_STAGE_ORDER[0]).candidates == (
        f"delivery:{delivery_id}",
        f"event:{event_id}",
    )
    assert f"blob:{digest}" in preview.stage(ORPHAN_STAGE).candidates

    db = await _db(db_path)
    try:
        assert await (await db.execute("SELECT 1 FROM workflow_events")).fetchone()
        assert await (await db.execute("SELECT 1 FROM workflow_checkpoint_owners")).fetchone()
        assert await (await db.execute("SELECT 1 FROM workflow_blob_refs")).fetchone()
    finally:
        await db.close()
    assert blob_path.exists()

    applied = await manager.cleanup()
    assert applied.stage_names == CLEANUP_STAGE_ORDER
    db = await _db(db_path)
    try:
        run = await (
            await db.execute(
                "SELECT implementation_hash,active_nodes_json FROM workflow_runs WHERE run_id='boundary'"
            )
        ).fetchone()
        assert dict(run) == {
            "implementation_hash": "immutable-v1-bundle",
            "active_nodes_json": "[]",
        }
        for table in (
            "workflow_events",
            "workflow_deliveries",
            "workflow_checkpoint_owners",
            "workflow_checkpoints",
            "workflow_blob_refs",
            "workflow_blobs",
        ):
            assert await (await db.execute(f"SELECT 1 FROM {table}")).fetchone() is None
    finally:
        await db.close()
    assert not blob_path.exists()


@pytest.mark.asyncio
async def test_cleanup_protects_live_open_undelivered_and_retained_evaluation(tmp_path: Path):
    now = 30_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    db = await _db(db_path)
    try:
        old = now - 200 * DAY
        for run_id, status in (
            ("undelivered", "completed"),
            ("open", "failed"),
            ("live", "running"),
            ("evaluated", "completed"),
            ("expired", "cancelled"),
        ):
            await _insert_run(db, run_id, status=status, timestamp=old)
        await _insert_event(db, "undelivered", delivery_status="pending")
        await db.execute(
            """INSERT INTO workflow_decisions(
                decision_id,run_id,checkpoint_ns,checkpoint_id,kind,status,
                prompt_json,nonce,created_at
            ) VALUES('decision-open','open','','cp','approval','open','{}','nonce',?)""",
            (old,),
        )
        await db.execute(
            """INSERT INTO evaluations(
                evaluation_id,trace_id,run_id,evaluator_name,evaluator_version,
                evaluator_type,score,verdict,labels_json,evidence_refs_json,degraded,created_at
            ) VALUES('eval-current','trace-evaluated','evaluated','quality','v1',
                'deterministic',1.0,'pass','[]','[]',0,?)""",
            (now - DAY,),
        )
        await _insert_event(db, "evaluated")
        await _insert_event(db, "expired")
        await db.commit()
    finally:
        await db.close()

    report = await WorkflowRetentionManager(
        db_path, tmp_path / "blobs", policy=_policy(), clock=FixedClock(now)
    ).cleanup()
    protected = report.stage(CLEANUP_STAGE_ORDER[0]).protected
    assert "undelivered:undelivered_delivery" in protected
    assert "open:active_decision" in protected

    db = await _db(db_path)
    try:
        rows = await (
            await db.execute("SELECT run_id FROM workflow_runs ORDER BY run_id")
        ).fetchall()
        assert [row["run_id"] for row in rows] == ["evaluated", "live", "open", "undelivered"]
        assert await (
            await db.execute("SELECT 1 FROM evaluations WHERE evaluation_id='eval-current'")
        ).fetchone()
        assert await (
            await db.execute("SELECT 1 FROM workflow_events WHERE run_id='undelivered'")
        ).fetchone()
        assert await (
            await db.execute("SELECT 1 FROM workflow_decisions WHERE run_id='open'")
        ).fetchone()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_reachability_keeps_checkpoint_owned_by_live_run(tmp_path: Path):
    now = 40_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    db = await _db(db_path)
    try:
        old = now - 40 * DAY
        await _insert_run(db, "old", status="completed", timestamp=old)
        await _insert_run(db, "live", status="waiting", timestamp=old)
        await _insert_checkpoint(
            db,
            run_id="old",
            thread_id="shared-thread",
            checkpoint_id="shared",
            created_at=old,
        )
        await _insert_checkpoint(
            db,
            run_id="live",
            thread_id="shared-thread",
            checkpoint_id="shared",
            created_at=old,
        )
        await _insert_checkpoint(
            db,
            run_id="old",
            thread_id="old-thread",
            checkpoint_id="old-only",
            created_at=old,
        )
        await db.commit()
    finally:
        await db.close()

    await WorkflowRetentionManager(
        db_path, tmp_path / "blobs", policy=_policy(), clock=FixedClock(now)
    ).cleanup()
    db = await _db(db_path)
    try:
        checkpoints = await (
            await db.execute("SELECT checkpoint_id FROM workflow_checkpoints ORDER BY checkpoint_id")
        ).fetchall()
        assert [row["checkpoint_id"] for row in checkpoints] == ["shared"]
        owners = await (
            await db.execute("SELECT run_id,checkpoint_id FROM workflow_checkpoint_owners")
        ).fetchall()
        assert [tuple(row) for row in owners] == [("live", "shared")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_referenced_evaluation_pins_expired_tombstone(tmp_path: Path):
    now = 50_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    db = await _db(db_path)
    try:
        old = now - 200 * DAY
        await _insert_run(db, "evaluated", status="completed", timestamp=old)
        await db.execute(
            "INSERT INTO eval_datasets VALUES('dataset','name','v1','{}',?)",
            (old,),
        )
        await db.execute(
            "INSERT INTO eval_examples VALUES('example','dataset','{}','{}','{}',?)",
            (old,),
        )
        await db.execute(
            "INSERT INTO eval_experiments VALUES('experiment','dataset','key','{}','completed',?,?)",
            (old, old),
        )
        await db.execute(
            """INSERT INTO evaluations(
                evaluation_id,trace_id,run_id,evaluator_name,evaluator_version,
                evaluator_type,score,verdict,labels_json,evidence_refs_json,degraded,created_at
            ) VALUES('evaluation','trace-evaluated','evaluated','quality','v1',
                'deterministic',1.0,'pass','[]','[]',0,?)""",
            (old,),
        )
        await db.execute(
            """INSERT INTO eval_results(
                experiment_id,example_id,run_id,evaluation_id,status,score,latency_ms,
                error_taxonomy,result_json
            ) VALUES('experiment','example','evaluated','evaluation','pass',1.0,1.0,NULL,'{}')"""
        )
        await db.commit()
    finally:
        await db.close()

    await WorkflowRetentionManager(
        db_path, tmp_path / "blobs", policy=_policy(), clock=FixedClock(now)
    ).cleanup()
    db = await _db(db_path)
    try:
        assert await (
            await db.execute("SELECT 1 FROM workflow_runs WHERE run_id='evaluated'")
        ).fetchone()
        assert await (
            await db.execute("SELECT 1 FROM evaluations WHERE evaluation_id='evaluation'")
        ).fetchone()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_startup_reconciles_expired_reservations_and_blob_files(tmp_path: Path):
    now = 60_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    db = await _db(db_path)
    try:
        await _insert_run(db, "live", status="running", timestamp=now - DAY)
        for key, status, expires_at in (
            ("expired", "prepared", now),
            ("fresh", "claimed", now + 1),
            ("committed", "committed", now - 1),
        ):
            await db.execute(
                """INSERT INTO workflow_target_reservations(
                    reservation_key,display_path,run_id,call_id,status,lease_expires_at,updated_at
                ) VALUES(?,?,'live','call',?,?,?)""",
                (key, key, status, expires_at, now),
            )
        missing_digest, _ = await _insert_blob(
            db,
            blob_root,
            data=b"missing",
            created_at=now - 2 * DAY,
            owner_kind="run",
            owner_id="live",
            write_file=False,
        )
        await db.commit()
    finally:
        await db.close()

    old_temp = blob_root / ".write.1.tmp"
    old_temp.parent.mkdir(parents=True, exist_ok=True)
    old_temp.write_bytes(b"old")
    os.utime(old_temp, (now - 2 * DAY, now - 2 * DAY))
    fresh_temp = blob_root / ".write.2.tmp"
    fresh_temp.write_bytes(b"fresh")
    os.utime(fresh_temp, (now, now))

    manager = WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=FixedClock(now)
    )
    preview = await manager.reconcile_startup(dry_run=True)
    assert preview.stage_names == (RESERVATION_STAGE, *CLEANUP_STAGE_ORDER)
    assert preview.stage(RESERVATION_STAGE).candidates == ("reservation:expired",)
    assert old_temp.exists()

    report = await manager.reconcile_startup()
    assert not old_temp.exists()
    assert fresh_temp.exists()
    assert f"missing-blob-file:{missing_digest}" in report.stage(ORPHAN_STAGE).warnings
    db = await _db(db_path)
    try:
        rows = await (
            await db.execute(
                "SELECT reservation_key FROM workflow_target_reservations ORDER BY reservation_key"
            )
        ).fetchall()
        assert [row["reservation_key"] for row in rows] == ["committed", "fresh"]
        assert await (
            await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (missing_digest,))
        ).fetchone()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_startup_collects_dangling_known_refs_but_preserves_unknown_owners(tmp_path: Path):
    now = 65_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    db = await _db(db_path)
    try:
        dangling_digest, dangling_path = await _insert_blob(
            db,
            blob_root,
            data=b"dangling-event",
            created_at=now - 2 * DAY,
            owner_kind="event",
            owner_id="event-removed-before-crash",
        )
        unknown_digest, unknown_path = await _insert_blob(
            db,
            blob_root,
            data=b"future-owner-kind",
            created_at=now - 2 * DAY,
            owner_kind="future_owner",
            owner_id="owner-1",
        )
        await db.commit()
    finally:
        await db.close()

    clock = SingleSampleClock(now)
    report = await WorkflowRetentionManager(
        db_path, blob_root, policy=_policy(), clock=clock
    ).reconcile_startup()
    assert clock.calls == 1
    assert any(
        item.endswith(f":{dangling_digest}")
        for item in report.stage(CLEANUP_STAGE_ORDER[3]).candidates
    )
    assert not dangling_path.exists()
    assert unknown_path.exists()

    db = await _db(db_path)
    try:
        assert await (
            await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (dangling_digest,))
        ).fetchone() is None
        assert await (
            await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (unknown_digest,))
        ).fetchone()
        assert await (
            await db.execute(
                "SELECT 1 FROM workflow_blob_refs WHERE sha256=?", (unknown_digest,)
            )
        ).fetchone()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bootstrap_runs_optional_startup_reconciliation(tmp_path: Path):
    now = 70_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "data" / "workflow.db")
    db = await _db(db_path)
    try:
        await _insert_run(db, "live", status="running", timestamp=now - DAY)
        await db.execute(
            """INSERT INTO workflow_target_reservations(
                reservation_key,display_path,run_id,call_id,status,lease_expires_at,updated_at
            ) VALUES('expired','path','live','call','prepared',?,?)""",
            (now, now),
        )
        await db.commit()
    finally:
        await db.close()

    service = await build_workflow_service(
        tmp_path,
        retention_policy=_policy(),
        retention_clock=FixedClock(now),
        activate=False,
    )
    service.runtime_adapters.register(
        WorkflowRuntimeAdapter(
            "deep_research",
            "v1",
            lambda **_values: {},
            lambda *_args, **_kwargs: WorkflowContext(ports={}),
        )
    )
    await service.activate_runtime(
        required_runtime_identities=(("deep_research", "v1"),)
    )
    assert service.retention_diagnostics.stage_names == (
        RESERVATION_STAGE,
        *CLEANUP_STAGE_ORDER,
    )
    db = await _db(db_path)
    try:
        assert await (
            await db.execute("SELECT 1 FROM workflow_target_reservations")
        ).fetchone() is None
    finally:
        await db.close()


def test_redaction_resource_and_malformed_fallback_are_fail_closed(tmp_path: Path):
    resource = Path(__file__).parents[2] / "resources" / "diagnostic-redaction.json"
    redactor = TraceRedactor.from_file(str(resource))
    assert redactor.redact({"authorization": "Bearer secret-value", "safe": "ok"}) == {
        "authorization": "[REDACTED]",
        "safe": "ok",
    }

    malformed = tmp_path / "diagnostic-redaction.json"
    malformed.write_text('{"version": 1, "sensitive_value_patterns": ["["]}', encoding="utf-8")
    assert TraceRedactor.from_file(str(malformed)).redact("ordinary diagnostic") == "[REDACTED]"

    empty = tmp_path / "empty-redaction.json"
    empty.write_text(
        '{"version": 1, "sensitive_keys": [], "sensitive_value_patterns": []}',
        encoding="utf-8",
    )
    assert TraceRedactor.from_file(str(empty)).redact("ordinary diagnostic") == "[REDACTED]"

    wrong_root = tmp_path / "list-redaction.json"
    wrong_root.write_text("[]", encoding="utf-8")
    assert TraceRedactor.from_file(str(wrong_root)).redact("ordinary diagnostic") == "[REDACTED]"
