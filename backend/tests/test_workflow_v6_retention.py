from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import aiosqlite
import pytest

from deskpet.workflows.retention import RetentionPolicy, WorkflowRetentionManager
from deskpet.workflows.store.schema import initialize_workflow_db


DAY = 24 * 60 * 60
FAULT_STAGES = (
    "retention.v6.after_checkpoint_data",
    "retention.v6.after_heads",
    "retention.v6.after_pins",
    "retention.v6.after_lineage",
    "retention.v6.after_operations",
    "retention.v6.after_start_requests",
    "retention.v6.after_session_refs",
    "retention.v6.after_snapshots",
    "retention.v6.after_blobs",
    "retention.v6.after_children",
    "retention.v6.after_root",
)


class Clock:
    def __init__(self, now: float) -> None:
        self.value = now

    def now(self) -> float:
        return self.value


def policy() -> RetentionPolicy:
    return RetentionPolicy.from_days(
        terminal_days=30,
        evaluation_tombstone_days=180,
        orphan_grace_hours=24,
    )


async def _connect(path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def _blob(
    db: aiosqlite.Connection, root: Path, payload: bytes, now: float
) -> tuple[str, Path]:
    digest = hashlib.sha256(payload).hexdigest()
    path = root / digest[:2] / digest[2:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    await db.execute(
        """INSERT OR IGNORE INTO workflow_blobs(
        sha256,size_bytes,media_type,relative_path,created_at
        ) VALUES(?,?,'application/json',?,?)""",
        (digest, len(payload), path.relative_to(root).as_posix(), now - 300 * DAY),
    )
    return digest, path


async def _run(
    db: aiosqlite.Connection,
    run_id: str,
    now: float,
    *,
    parent_run_id: str | None = None,
    version: str = "v6",
    ended_at: float | None = None,
) -> None:
    ended = now - 300 * DAY if ended_at is None else ended_at
    await db.execute(
        """INSERT INTO workflow_runs(
        run_id,trace_id,thread_id,checkpoint_ns,parent_run_id,session_id,
        workflow_name,workflow_version,manifest_hash,implementation_hash,
        capability_hash,state_schema_version,status,active_nodes_json,
        created_at,updated_at,ended_at
        ) VALUES(?,?,?,?,?,?,'deep_research',?,'m','i','c',6,'completed','[]',?,?,?)""",
        (
            run_id,
            f"trace-{run_id}",
            f"thread-{run_id}",
            "",
            parent_run_id,
            f"session-{run_id}",
            version,
            ended,
            ended,
            ended,
        ),
    )


async def _component(path: Path, root: Path, now: float) -> dict[str, Path]:
    db = await _connect(path)
    paths: dict[str, Path] = {}
    try:
        await _run(db, "root", now)
        await _run(db, "child", now, parent_run_id="root")
        await _run(db, "grand", now, parent_run_id="child")
        await db.execute(
            """INSERT INTO workflow_research_lineage(
            operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
            parent_report_ref,budget_lease_id,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            ("op-root", "root", None, None, None, None, "lease-root", now),
        )
        snapshot_hashes: dict[str, str] = {}
        policy_keys = (
            "compiler", "route", "extraction", "llm_extract", "llm_repair",
            "llm_inference", "admission", "inference", "assessment", "claim", "quality",
        )
        for owner in ("root", "child"):
            closure_digest, closure_path = await _blob(
                db, root, f"closure:{owner}".encode(), now
            )
            closure_ref = f"sha256:{closure_digest}"
            snapshot_base = {
                "schema_version": 1,
                "run_id": owner,
                "workflow_name": "deep_research",
                "workflow_version": "v6",
                "spec_ref": closure_ref,
                "spec_hash": "a" * 64,
                "fact_batch_refs": [closure_ref],
                "evidence_head_hash": "b" * 64,
                "assessment_ref": closure_ref,
                "assessment_hash": "c" * 64,
                "assessment_input_hash": "d" * 64,
                "claim_batch_ref": closure_ref,
                "provenance_refs": [closure_ref],
                "policy_refs": {key: closure_ref for key in policy_keys},
                "closure_refs": [closure_ref],
            }
            semantic = json.dumps(
                snapshot_base, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            snapshot_hash = hashlib.sha256(semantic.encode()).hexdigest()
            snapshot_hashes[owner] = snapshot_hash
            snapshot_value = {
                **snapshot_base,
                "snapshot_id": "rcs_" + snapshot_hash[:24],
                "snapshot_hash": snapshot_hash,
            }
            digest, blob_path = await _blob(
                db,
                root,
                json.dumps(
                    snapshot_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode(),
                now,
            )
            paths[f"snap-{owner}"] = blob_path
            paths[f"closure-{owner}"] = closure_path
            await db.execute(
                """INSERT INTO workflow_research_snapshots(
                snapshot_hash,run_id,operation_id,schema_version,manifest_ref,
                created_at,expires_at) VALUES(?,?,?,1,?,?,?)""",
                (
                    snapshot_hash,
                    owner,
                    f"op-{owner}",
                    f"sha256:{digest}",
                    now,
                    now - DAY,
                ),
            )
            await db.executemany(
                """INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at)
                VALUES(?,'research_snapshot',?,?)""",
                (
                    (digest, snapshot_hash, now),
                    (closure_digest, snapshot_hash, now),
                ),
            )
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,'continue_parent',?,?)""",
                (f"pin-{owner}", snapshot_hash, owner, now - DAY, now),
            )
        await db.execute(
            """INSERT INTO workflow_research_lineage(
            operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
            parent_report_ref,budget_lease_id,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "op-child",
                "child",
                "root",
                "op-root",
                snapshot_hashes["root"],
                None,
                "lease-child",
                now,
            ),
        )
        await db.execute(
            """INSERT INTO workflow_research_lineage(
            operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
            parent_report_ref,budget_lease_id,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "op-grand",
                "grand",
                "child",
                "op-child",
                snapshot_hashes["child"],
                None,
                "lease-grand",
                now,
            ),
        )
        spec_digest, spec_path = await _blob(db, root, b'{"spec":"shared"}', now)
        paths["spec"] = spec_path
        await db.executemany(
            """INSERT INTO workflow_research_continuation_heads(
            parent_run_id,child_run_id,parent_operation_id,child_operation_id,
            source_snapshot_hash,spec_hash,spec_blob_digest,evidence_head_hash,
            policy_version,claimed_at
            ) VALUES(?,?,?,?,?,?,?,?,1,?)""",
            (
                ("root", "child", "op-root", "op-child", snapshot_hashes["root"], "a" * 64, spec_digest, "b" * 64, now),
                ("child", "grand", "op-child", "op-grand", snapshot_hashes["child"], "a" * 64, spec_digest, "c" * 64, now),
            ),
        )
        for run_id in ("root", "child", "grand"):
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,
                capability_hash,run_id,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (f"req-{run_id}", f"s-{run_id}", f"r-{run_id}", f"t-{run_id}", "deep_research", "c", run_id, now),
            )
            await db.execute(
                "INSERT INTO workflow_session_refs(run_id,session_kind,session_id) VALUES(?,'delivery',?)",
                (run_id, f"s-{run_id}"),
            )
            await db.execute(
                """INSERT INTO workflow_operations(
                operation_id,run_id,operation_kind,request_hash,result_json,created_at
                ) VALUES(?,?,'continue','h','{}',?)""",
                (f"audit-{run_id}", run_id, now),
            )

        checkpoint_payload, checkpoint_path = await _blob(db, root, b"checkpoint", now)
        paths["checkpoint"] = checkpoint_path
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
            checkpoint_type,checkpoint_blob,metadata_blob,created_at
            ) VALUES('thread-root','','cp',NULL,'root','regular',X'00',X'00',?)""",
            (now,),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_owners(
            run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
            ) VALUES('root','thread-root','','cp',NULL,?)""",
            (now,),
        )
        await db.execute(
            """INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at)
            VALUES(?,'checkpoint','cp',?)""",
            (checkpoint_payload, now),
        )
        await db.execute(
            """INSERT INTO workflow_pending_writes(
            thread_id,checkpoint_ns,base_checkpoint_id,task_id,write_index,
            channel,value_type,value_blob
            ) VALUES('thread-root','','cp','task',0,'value','bytes',X'00')"""
        )
        await db.execute(
            """INSERT INTO workflow_nodes(
            node_execution_id,run_id,node_id,base_checkpoint_id,invocation_key,
            latest_attempt,latest_status,updated_at
            ) VALUES('node-root','root','research','cp','invoke',1,'completed',?)""",
            (now,),
        )
        await db.execute(
            """INSERT INTO workflow_effects(
            effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,
            policy_json,args_hash,status,prepared_json,outcome_json,
            artifact_refs_json,lease_epoch,started_at,updated_at,ended_at
            ) VALUES('effect-root','root','node-root','fingerprint','research',
            '{}','args','completed','{}','{}','[]',0,?,?,?)""",
            (now, now, now),
        )
        await db.execute(
            "INSERT INTO workflow_node_effects(node_execution_id,effect_id) VALUES('node-root','effect-root')"
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_effects(
            thread_id,checkpoint_ns,checkpoint_id,effect_id,node_execution_id
            ) VALUES('thread-root','','cp','effect-root','node-root')"""
        )
        effect_digest, effect_path = await _blob(db, root, b"effect outcome", now)
        paths["effect"] = effect_path
        await db.execute(
            """INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at)
            VALUES(?,'effect','effect-root',?)""",
            (effect_digest, now),
        )
        await db.commit()
    finally:
        await db.close()
    return paths


async def _counts(path: Path) -> dict[str, int]:
    db = await _connect(path)
    tables = (
        "workflow_runs",
        "workflow_research_continuation_heads",
        "workflow_research_lineage",
        "workflow_research_snapshot_pins",
        "workflow_research_snapshots",
        "workflow_start_requests",
        "workflow_session_refs",
        "workflow_operations",
        "workflow_checkpoints",
        "workflow_checkpoint_owners",
        "workflow_pending_writes",
        "workflow_nodes",
        "workflow_effects",
        "workflow_node_effects",
        "workflow_checkpoint_effects",
        "workflow_blob_refs",
        "workflow_blobs",
    )
    try:
        return {
            table: int((await (await db.execute(f"SELECT count(*) FROM {table}")).fetchone())[0])
            for table in tables
        }
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_v6_parent_child_grandchild_component_is_deleted_without_residue(tmp_path: Path):
    now = 100_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    paths = await _component(db_path, blob_root, now)

    report = await WorkflowRetentionManager(
        db_path, blob_root, policy=policy(), clock=Clock(now)
    ).cleanup()

    assert {"run:root", "run:child", "run:grand"} <= set(
        report.stage("unreachable_research_runs").candidates
    )
    assert all(value == 0 for value in (await _counts(db_path)).values())
    assert all(not path.exists() for path in paths.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_stage", FAULT_STAGES)
async def test_v6_component_faults_rollback_every_write(tmp_path: Path, fault_stage: str):
    now = 110_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    paths = await _component(db_path, blob_root, now)
    before = await _counts(db_path)

    def inject(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError, match="retention.v6"):
        await WorkflowRetentionManager(
            db_path,
            blob_root,
            policy=policy(),
            clock=Clock(now),
            fault_injector=inject,
        ).cleanup()

    assert await _counts(db_path) == before
    assert all(path.exists() for path in paths.values())


@pytest.mark.asyncio
async def test_v6_missing_snapshot_closure_fails_closed_as_a_component(tmp_path: Path):
    now = 120_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    paths = await _component(db_path, blob_root, now)
    paths["snap-root"].unlink()

    report = await WorkflowRetentionManager(
        db_path, blob_root, policy=policy(), clock=Clock(now)
    ).cleanup()

    counts = await _counts(db_path)
    assert counts["workflow_runs"] == 3
    assert any("closure_invalid" in item for item in report.stage("unreachable_research_runs").protected)


@pytest.mark.asyncio
async def test_v6_live_parent_protects_descendants_and_checkpoint_is_readable(tmp_path: Path):
    now = 130_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    paths = await _component(db_path, blob_root, now)
    db = await _connect(db_path)
    try:
        await db.execute(
            "UPDATE workflow_runs SET status='running',ended_at=NULL WHERE run_id='root'"
        )
        await db.commit()
    finally:
        await db.close()

    await WorkflowRetentionManager(
        db_path, blob_root, policy=policy(), clock=Clock(now)
    ).cleanup()

    counts = await _counts(db_path)
    assert counts["workflow_runs"] == 3
    assert counts["workflow_checkpoints"] == 1
    assert paths["checkpoint"].read_bytes() == b"checkpoint"


@pytest.mark.asyncio
async def test_v6_component_deletion_keeps_blob_shared_by_external_run(tmp_path: Path):
    now = 140_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    await _component(db_path, blob_root, now)
    db = await _connect(db_path)
    try:
        await _run(db, "outside", now, version="v5", ended_at=now)
        await db.execute(
            "UPDATE workflow_runs SET status='running',ended_at=NULL WHERE run_id='outside'"
        )
        digest, shared_path = await _blob(db, blob_root, b"shared arbitrary blob", now)
        await db.executemany(
            """INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at)
            VALUES(?,'run_staging',?,?)""",
            ((digest, "root", now), (digest, "outside", now)),
        )
        await db.commit()
    finally:
        await db.close()

    await WorkflowRetentionManager(
        db_path, blob_root, policy=policy(), clock=Clock(now)
    ).cleanup()

    db = await _connect(db_path)
    try:
        runs = await (await db.execute("SELECT run_id FROM workflow_runs")).fetchall()
        refs = await (
            await db.execute(
                "SELECT owner_id FROM workflow_blob_refs WHERE sha256=? ORDER BY owner_id",
                (digest,),
            )
        ).fetchall()
    finally:
        await db.close()
    assert [str(row[0]) for row in runs] == ["outside"]
    assert [str(row[0]) for row in refs] == ["outside"]
    assert shared_path.read_bytes() == b"shared arbitrary blob"


@pytest.mark.asyncio
async def test_v6_external_shared_checkpoint_owner_protects_component(tmp_path: Path):
    now = 150_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    await _component(db_path, blob_root, now)
    db = await _connect(db_path)
    try:
        await _run(db, "outside", now, version="v5", ended_at=now)
        await db.execute(
            "UPDATE workflow_runs SET status='running',ended_at=NULL WHERE run_id='outside'"
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_owners(
            run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
            ) VALUES('outside','thread-root','','cp',NULL,?)""",
            (now,),
        )
        await db.commit()
    finally:
        await db.close()

    report = await WorkflowRetentionManager(
        db_path, blob_root, policy=policy(), clock=Clock(now)
    ).cleanup()

    assert (await _counts(db_path))["workflow_runs"] == 4
    assert any(
        "external_blob_owner" in item
        for item in report.stage("unreachable_research_runs").protected
    )


@pytest.mark.asyncio
async def test_v6_post_commit_unlink_failure_is_recovered_by_startup_orphan_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 160_000_000.0
    db_path = await initialize_workflow_db(tmp_path / "workflow.db")
    blob_root = tmp_path / "blobs"
    paths = await _component(db_path, blob_root, now)
    failed_unlink = paths["checkpoint"]
    old_mtime = now - 300 * DAY
    os.utime(failed_unlink, (old_mtime, old_mtime))

    # An external live run owns the same blob as the component.  Component GC
    # must remove only the dead owner's ref and leave the registered bytes for
    # the surviving owner through both cleanup passes.
    db = await _connect(db_path)
    try:
        await _run(db, "outside", now, version="v5", ended_at=now)
        await db.execute(
            "UPDATE workflow_runs SET status='running',ended_at=NULL WHERE run_id='outside'"
        )
        shared_digest, shared_path = await _blob(
            db, blob_root, b"post-commit shared blob", now
        )
        await db.executemany(
            """INSERT INTO workflow_blob_refs(sha256,owner_kind,owner_id,created_at)
            VALUES(?,'run_staging',?,?)""",
            (
                (shared_digest, "root", now),
                (shared_digest, "outside", now),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    real_unlink = Path.unlink

    def fail_selected_unlink(path: Path, *args, **kwargs) -> None:
        if path.resolve() == failed_unlink.resolve():
            raise OSError("injected post-commit unlink failure")
        real_unlink(path, *args, **kwargs)

    # Keep failing this one file for the complete first cleanup.  This covers
    # both the immediate post-commit unlink and the same-process orphan retry,
    # making the next startup pass the recovery owner.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_selected_unlink)
        first = await WorkflowRetentionManager(
            db_path, blob_root, policy=policy(), clock=Clock(now)
        ).cleanup()

    assert failed_unlink.exists()
    assert any(
        warning.startswith("unlink-failed:")
        for warning in (
            *first.stage("unreachable_research_runs").warnings,
            *first.stage("orphan_grace").warnings,
        )
    )

    # The filesystem failure happens after the component transaction commits:
    # every component row is gone atomically even though one unregistered file
    # remains.  The external run/ref/blob are still intact.
    db = await _connect(db_path)
    try:
        runs = await (
            await db.execute("SELECT run_id FROM workflow_runs ORDER BY run_id")
        ).fetchall()
        for table in (
            "workflow_research_continuation_heads",
            "workflow_research_lineage",
            "workflow_research_snapshot_pins",
            "workflow_research_snapshots",
            "workflow_checkpoints",
            "workflow_checkpoint_owners",
        ):
            assert int(
                (await (await db.execute(f"SELECT count(*) FROM {table}")).fetchone())[0]
            ) == 0
        shared_refs = await (
            await db.execute(
                "SELECT owner_id FROM workflow_blob_refs WHERE sha256=? ORDER BY owner_id",
                (shared_digest,),
            )
        ).fetchall()
        shared_row = await (
            await db.execute(
                "SELECT relative_path FROM workflow_blobs WHERE sha256=?",
                (shared_digest,),
            )
        ).fetchone()
    finally:
        await db.close()
    assert [str(row[0]) for row in runs] == ["outside"]
    assert [str(row[0]) for row in shared_refs] == ["outside"]
    assert shared_row is not None
    assert shared_path.read_bytes() == b"post-commit shared blob"

    # A fresh startup cleanup has no in-memory deletion plan.  It discovers
    # the old, unregistered file from disk and removes it as an orphan, without
    # disturbing the still-registered shared blob.
    second = await WorkflowRetentionManager(
        db_path, blob_root, policy=policy(), clock=Clock(now)
    ).reconcile_startup()

    assert not failed_unlink.exists()
    assert any(
        item.startswith("file:")
        for item in second.stage("orphan_grace").candidates
    )
    assert second.stage("orphan_grace").applied >= 1
    assert shared_path.read_bytes() == b"post-commit shared blob"
    db = await _connect(db_path)
    try:
        assert await (
            await db.execute(
                "SELECT 1 FROM workflow_blobs WHERE sha256=?", (shared_digest,)
            )
        ).fetchone() is not None
        assert await (
            await db.execute(
                """SELECT 1 FROM workflow_blob_refs
                WHERE sha256=? AND owner_kind='run_staging' AND owner_id='outside'""",
                (shared_digest,),
            )
        ).fetchone() is not None
    finally:
        await db.close()
