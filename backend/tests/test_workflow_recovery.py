from __future__ import annotations

import json

import pytest

from deskpet.workflows.contracts import WorkflowRunStatus
from deskpet.workflows.definition import WorkflowManifest
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.store import (
    LegacyCheckpointStore as FencedAsyncSqliteSaver,
    empty_legacy_checkpoint as empty_checkpoint,
    NativeCheckpointStore,
    WorkflowRunStore,
)


class FakeExecutable:
    def __init__(self, manifest: WorkflowManifest) -> None:
        self.manifest = manifest

    async def ainvoke(self, state, context, **kwargs):
        return state


class FakeWorkflow:
    def __init__(self, manifest: WorkflowManifest) -> None:
        self.manifest = manifest


def _manifest() -> WorkflowManifest:
    return WorkflowManifest(
        workflow_name="recovery",
        workflow_version="1",
        state_schema_version=1,
        durability="sync",
        recursion_limit=16,
        max_supersteps=8,
        definition_hash="d",
        state_hash="s",
        prompt_hash="p",
        tool_hash="t",
        policy_hash="policy",
        callable_source_hash="c",
        dependency_lock_hash="l",
        implementation_bundle_hash="i",
    )


async def _setup(tmp_path, now):
    path = tmp_path / "workflow.db"
    clock = lambda: now[0]
    store = WorkflowRunStore(path, clock=clock)
    saver = FencedAsyncSqliteSaver(path)
    registry = WorkflowRegistry()
    manifest = _manifest()
    registry.register(FakeWorkflow(manifest), executable=FakeExecutable(manifest))
    runner = WorkflowRunner(store, saver, registry, owner="recovery-runner", clock=clock)
    run_id = await runner.start(
        session_id="s",
        request_id="r",
        turn_id="t",
        workflow_name="recovery",
        workflow_version="1",
        capability_snapshot={},
    )
    return runner, store, registry, run_id


@pytest.mark.asyncio
async def test_recover_expired_fences_owner_and_marks_running_attempt_abandoned(tmp_path):
    now = [100.0]
    runner, store, _, run_id = await _setup(tmp_path, now)
    fence = await store.claim(run_id, "dead-owner", ttl_seconds=10)
    db = await store._connect()
    try:
        await db.execute(
            """INSERT INTO workflow_nodes(node_execution_id,run_id,node_id,base_checkpoint_id,
            invocation_key,latest_attempt,latest_status,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
            ("node-x", run_id, "node", "base", "key", 1, "running", now[0]),
        )
        await db.execute(
            """INSERT INTO workflow_node_attempts(node_execution_id,retry_attempt,status,started_at)
            VALUES(?,?,?,?)""",
            ("node-x", 1, "running", now[0]),
        )
        await db.commit()
    finally:
        await db.close()
    now[0] = 111.0

    records = await runner.recover_expired()

    row = await store.get_run(run_id)
    assert row is not None
    assert row["status"] == "retryable"
    assert row["lease_epoch"] == fence.lease_epoch + 1
    assert row["lease_owner"] is None
    assert any(item.reason == "lease_expired" for item in records)
    db = await store._connect()
    try:
        attempt = await (
            await db.execute(
                "SELECT status,ended_at,error_ref FROM workflow_node_attempts WHERE node_execution_id='node-x'"
            )
        ).fetchone()
    finally:
        await db.close()
    assert tuple(attempt) == ("abandoned", 111.0, "workflow_runner:lease_lost")


@pytest.mark.asyncio
async def test_recovery_blocks_when_pinned_graph_version_disappears(tmp_path):
    now = [10.0]
    runner, store, registry, run_id = await _setup(tmp_path, now)
    registry.unregister("recovery", "1")

    records = await runner.recover_expired()

    row = await store.get_run(run_id)
    assert row is not None and row["status"] == "blocked"
    error = json.loads(row["error_json"])
    assert error["reason"] == "graph_version_unavailable"
    assert records[0].action == "restore_graph_version_or_fork"


@pytest.mark.asyncio
async def test_cancel_waits_for_active_effect_then_recovery_converges(tmp_path):
    now = [10.0]
    runner, store, _, run_id = await _setup(tmp_path, now)
    db = await store._connect()
    try:
        await db.execute(
            """INSERT INTO workflow_effects(effect_id,run_id,node_execution_id,effect_fingerprint,
            effect_type,policy_json,args_hash,status,prepared_json,lease_epoch,started_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("effect", run_id, "node", "fingerprint", "file", "{}", "args", "running", "{}", 0, 10.0, 10.0),
        )
        await db.commit()
    finally:
        await db.close()

    cancelling = await runner.request_cancel(run_id)
    assert cancelling["status"] == WorkflowRunStatus.CANCELLING.value

    db = await store._connect()
    try:
        await db.execute(
            "UPDATE workflow_effects SET status='committed',ended_at=?,updated_at=? WHERE effect_id='effect'",
            (11.0, 11.0),
        )
        await db.commit()
    finally:
        await db.close()

    records = await runner.recover_expired()
    row = await store.get_run(run_id)
    assert row is not None and row["status"] == "cancelled"
    assert any(item.action == "reconcile_cancel" and item.status == "cancelled" for item in records)


def test_state_transition_table_rejects_terminal_and_illegal_edges():
    from deskpet.workflows.lease import validate_run_transition

    validate_run_transition("created", "running")
    validate_run_transition("running", "waiting")
    validate_run_transition("blocked", "retryable")
    with pytest.raises(ValueError, match="illegal"):
        validate_run_transition("completed", "running")
    with pytest.raises(ValueError, match="illegal"):
        validate_run_transition("waiting", "completed")


@pytest.mark.asyncio
async def test_recovery_rebuilds_stale_head_from_run_owned_checkpoint(tmp_path):
    now = [10.0]
    runner, store, _, run_id = await _setup(tmp_path, now)
    fence = await store.claim(run_id, "writer")
    config = {
        "configurable": {
            "thread_id": run_id,
            "checkpoint_ns": "",
            "deskpet_run_id": run_id,
            "deskpet_lease_owner": fence.owner,
            "deskpet_lease_epoch": fence.lease_epoch,
            "deskpet_run_version": fence.run_version,
        }
    }
    checkpoint = empty_checkpoint()
    await runner.saver.aput(
        config,
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {},
    )
    db = await store._connect()
    try:
        await db.execute(
            """UPDATE workflow_runs SET status='retryable',lease_owner=NULL,lease_expires_at=NULL,
            head_checkpoint_id='missing',run_version=run_version+1 WHERE run_id=?""",
            (run_id,),
        )
        await db.commit()
    finally:
        await db.close()

    records = await runner.recover_expired()

    row = await store.get_run(run_id)
    assert row is not None and row["head_checkpoint_id"] == checkpoint["id"]
    assert any(item.action == "rebuild_head_projection" for item in records)


@pytest.mark.asyncio
async def test_recovery_reports_succeeded_pending_without_replaying_it(tmp_path):
    now = [10.0]
    runner, store, _, run_id = await _setup(tmp_path, now)
    db = await store._connect()
    try:
        await db.execute(
            """INSERT INTO workflow_nodes(node_execution_id,run_id,node_id,base_checkpoint_id,
            invocation_key,latest_attempt,latest_status,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
            ("pending-node", run_id, "node", "base", "key", 1, "succeeded_pending", 10.0),
        )
        await db.execute("UPDATE workflow_runs SET status='retryable' WHERE run_id=?", (run_id,))
        await db.commit()
    finally:
        await db.close()

    records = await runner.recover_expired()

    assert any(item.action == "resume_pending_checkpoint" for item in records)
    row = await store.get_run(run_id)
    assert row is not None and row["status"] == "retryable"


@pytest.mark.asyncio
async def test_legacy_nonterminal_head_is_blocked_but_history_remains_read_only(tmp_path):
    now = [10.0]
    runner, store, _, run_id = await _setup(tmp_path, now)
    fence = await store.claim(run_id, "legacy-writer")
    checkpoint = empty_checkpoint()
    config = {
        "configurable": {
            "thread_id": run_id,
            "checkpoint_ns": "",
            "deskpet_run_id": run_id,
            "deskpet_lease_owner": fence.owner,
            "deskpet_lease_epoch": fence.lease_epoch,
            "deskpet_run_version": fence.run_version,
        }
    }
    written = await runner.saver.aput(
        config, checkpoint, {"source": "input", "step": 0, "parents": {}}, {}
    )

    blocked = await store.block_legacy_nonterminal_runs()

    assert blocked == [run_id]
    row = await store.get_run(run_id)
    assert row is not None
    assert row["status"] == "blocked"
    assert row["recovery_action"] == "read_only"
    assert row["lease_owner"] is None
    assert await store.checkpoint_engine_kind(run_id) == "langgraph-legacy"
    loaded = await runner.saver.aget_tuple(written)
    assert loaded is not None and loaded.checkpoint["id"] == checkpoint["id"]


@pytest.mark.asyncio
async def test_native_recovery_projects_durable_retry_as_next_attempt(tmp_path):
    path = tmp_path / "native-retry.db"
    store = WorkflowRunStore(path, clock=lambda: 10.0)
    run_id, _ = await store.create_run(
        request_key="native-retry",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="recovery",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="native",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
    )
    fence = await store.claim(run_id, "native-owner")
    saver = NativeCheckpointStore(path, clock=lambda: 10.0)
    task = {
        "task_id": "task-1",
        "node_id": "node-1",
        "invocation_key": "node-1:entry",
        "activation_id": "activation-1",
        "retry_attempt": 1,
    }
    genesis = await saver.ensure_genesis(fence, run_id, {"run_id": run_id}, [task])
    await saver.commit_retry(
        fence,
        genesis["checkpoint_id"],
        task,
        1,
        next_attempt_at=20.0,
        error_ref="transient",
    )

    execution = await saver.load_execution(
        run_id=run_id, thread_id=run_id, checkpoint_ns=""
    )
    recovered_task = execution.snapshot.frontier[0]
    assert recovered_task.task_id == "task-1"
    assert recovered_task.retry_attempt == 2
    assert recovered_task.next_attempt_at == 20.0
    assert execution.first_attempt_times["task-1"] == 10.0


@pytest.mark.asyncio
async def test_recovery_does_not_touch_unexpired_live_owner(tmp_path):
    now = [10.0]
    runner, store, registry, run_id = await _setup(tmp_path, now)
    fence = await store.claim(run_id, "live-owner", ttl_seconds=90)
    registry.unregister("recovery", "1")

    assert await runner.recover_expired() == []
    row = await store.get_run(run_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["lease_epoch"] == fence.lease_epoch
    assert row["lease_owner"] == "live-owner"


@pytest.mark.asyncio
async def test_corrupt_head_is_quarantined_and_blocks_without_parent_fallback(tmp_path):
    now = [10.0]
    runner, store, _, run_id = await _setup(tmp_path, now)
    fence = await store.claim(run_id, "writer")
    checkpoint = empty_checkpoint()
    config = {
        "configurable": {
            "thread_id": run_id,
            "checkpoint_ns": "",
            "deskpet_run_id": run_id,
            "deskpet_lease_owner": fence.owner,
            "deskpet_lease_epoch": fence.lease_epoch,
            "deskpet_run_version": fence.run_version,
        }
    }
    await runner.saver.aput(
        config,
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {},
    )
    db = await store._connect()
    try:
        await db.execute(
            "UPDATE workflow_checkpoints SET checkpoint_blob=? WHERE checkpoint_id=?",
            (b"\xc1", checkpoint["id"]),
        )
        await db.execute(
            """UPDATE workflow_runs SET status='retryable',lease_owner=NULL,
            lease_expires_at=NULL,run_version=run_version+1 WHERE run_id=?""",
            (run_id,),
        )
        await db.commit()
    finally:
        await db.close()

    records = await runner.recover_expired()

    row = await store.get_run(run_id)
    assert row is not None and row["status"] == "blocked"
    assert row["head_checkpoint_id"] == checkpoint["id"]
    assert any(item.reason == "checkpoint_corrupt" for item in records)
    quarantine = tmp_path / "workflow.wfq"
    assert len(list(quarantine.glob("*/*.chk"))) == 1
