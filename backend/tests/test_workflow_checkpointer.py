from __future__ import annotations

import sqlite3

import pytest

from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.store import (
    NATIVE_ENGINE_KIND,
    LegacyCheckpointStore as FencedAsyncSqliteSaver,
    NativeCheckpointError,
    NativeCheckpointStore,
    RegisteredBlobStore,
    StaleRunFence,
    WorkflowRunStore,
    initialize_workflow_db,
    empty_legacy_checkpoint as empty_checkpoint,
)


async def _run_and_config(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    run_id, _ = await store.create_run(
        request_key="request",
        session_id="session",
        request_id="req",
        turn_id="turn",
        workflow_name="test",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
    )
    fence = await store.claim(run_id, "runner")
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
    return path, store, run_id, fence, config


@pytest.mark.asyncio
async def test_fenced_saver_round_trip_and_pending_writes(tmp_path):
    path, _, _, _, config = await _run_and_config(tmp_path)
    saver = FencedAsyncSqliteSaver(path)
    checkpoint = empty_checkpoint()
    written = await saver.aput(config, checkpoint, {"source": "input", "step": 0, "parents": {}}, {})
    await saver.aput_writes(written, [("value", {"ok": True})], "task-1", "root")

    loaded = await saver.aget_tuple(written)
    assert loaded is not None
    assert loaded.checkpoint["id"] == checkpoint["id"]
    assert loaded.pending_writes == [("task-1", "value", {"ok": True})]


@pytest.mark.asyncio
async def test_fenced_saver_rejects_commit_after_cancel(tmp_path):
    path, store, run_id, _, config = await _run_and_config(tmp_path)
    saver = FencedAsyncSqliteSaver(path)
    await store.request_cancel(run_id, "user")
    with pytest.raises(StaleRunFence):
        await saver.aput(config, empty_checkpoint(), {"source": "input", "step": 0, "parents": {}}, {})


@pytest.mark.asyncio
async def test_v1_schema_migrates_native_columns_without_decoding_legacy_blob(tmp_path):
    path = tmp_path / "workflow.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        PRAGMA user_version=1;
        CREATE TABLE workflow_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
        CREATE TABLE workflow_runs(run_id TEXT PRIMARY KEY);
        CREATE TABLE workflow_checkpoints(
            thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,parent_checkpoint_id TEXT,
            run_id TEXT,checkpoint_type TEXT,checkpoint_blob BLOB,metadata_blob BLOB,
            created_at REAL,PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id));
        CREATE TABLE workflow_pending_writes(
            thread_id TEXT,checkpoint_ns TEXT,base_checkpoint_id TEXT,task_id TEXT,
            write_index INTEGER,channel TEXT,value_type TEXT,value_blob BLOB,task_path TEXT,
            PRIMARY KEY(thread_id,checkpoint_ns,base_checkpoint_id,task_id,write_index));
        CREATE TABLE workflow_node_attempts(
            node_execution_id TEXT,retry_attempt INTEGER,task_id TEXT,task_path TEXT,
            status TEXT,started_at REAL,ended_at REAL,error_ref TEXT,
            PRIMARY KEY(node_execution_id,retry_attempt));
        CREATE TABLE workflow_decisions(
            decision_id TEXT PRIMARY KEY,run_id TEXT,node_execution_id TEXT,interrupt_id TEXT,
            checkpoint_ns TEXT,checkpoint_id TEXT,kind TEXT,status TEXT,prompt_json TEXT,
            response_json TEXT,nonce TEXT,decision_version INTEGER,expires_at REAL,
            created_at REAL,resolved_at REAL);
        INSERT INTO workflow_checkpoints VALUES(
            'thread','','legacy',NULL,'run','msgpack',X'80',X'80',1);
        """
    )
    db.commit()
    db.close()

    await initialize_workflow_db(path)

    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    checkpoint = db.execute(
        "SELECT checkpoint_blob,engine_kind,snapshot_version FROM workflow_checkpoints"
    ).fetchone()
    assert checkpoint == (b"\x80", "langgraph-legacy", None)
    assert {
        "write_kind", "payload_json", "node_execution_id"
    }.issubset({row[1] for row in db.execute("PRAGMA table_info(workflow_pending_writes)")})
    assert {
        "consumed_at", "consumed_checkpoint_id"
    }.issubset({row[1] for row in db.execute("PRAGMA table_info(workflow_decisions)")})
    assert "request_hash" in {
        row[1] for row in db.execute("PRAGMA table_info(workflow_operations)")
    }
    db.close()


@pytest.mark.asyncio
async def test_native_checkpoint_transaction_and_operation_replay(tmp_path):
    path, store, run_id, fence, _ = await _run_and_config(tmp_path)
    await store.bind_session_refs(run_id, [("delivery", "session", 0)])
    saver = NativeCheckpointStore(path)
    task = {"task_id": "task-1", "activation_id": "activation-1", "node_id": "node-1"}
    state = {
        "schema_version": 1,
        "workflow_name": "test",
        "workflow_version": "1",
        "thread_id": run_id,
        "run_id": run_id,
        "session_id": "session",
        "values": {},
    }
    genesis = await saver.ensure_genesis(
        fence, run_id, state, [task], operation_id="operation-genesis"
    )
    duplicate_genesis = await saver.ensure_genesis(
        fence, run_id, state, [task], operation_id="operation-genesis"
    )
    assert duplicate_genesis == genesis

    await saver.commit_task_result(
        fence,
        genesis["checkpoint_id"],
        task,
        1,
        {"values": {"answer": 42}},
        operation_id="operation-task",
    )
    db = sqlite3.connect(path)
    pending = db.execute(
        "SELECT channel,value_type,value_blob,write_kind,payload_json "
        "FROM workflow_pending_writes"
    ).fetchone()
    assert pending[0:2] == ("__native__:state_patch", "json")
    assert pending[2].decode("utf-8") == pending[4]
    assert pending[3] == "state_patch"
    db.close()

    next_state = {**state, "values": {"answer": 42}}
    committed = await saver.commit_frontier(
        fence,
        genesis["checkpoint_id"],
        state=next_state,
        frontier=[],
        step=1,
        operation_id="operation-frontier",
        intents=[
            {
                "intent_id": "answer-ready",
                "event_type": "workflow.progress",
                "payload": {"answer": 42},
            }
        ],
    )
    duplicate = await saver.commit_frontier(
        fence,
        genesis["checkpoint_id"],
        state=next_state,
        frontier=[],
        step=1,
        operation_id="operation-frontier",
        intents=[
            {
                "intent_id": "answer-ready",
                "event_type": "workflow.progress",
                "payload": {"answer": 42},
            }
        ],
    )
    assert duplicate == committed
    assert (await saver.load_head(run_id))["state"] == next_state

    db = sqlite3.connect(path)
    assert db.execute("SELECT COUNT(*) FROM workflow_checkpoints").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM workflow_pending_writes").fetchone()[0] == 0
    assert db.execute("SELECT status FROM workflow_node_attempts").fetchone()[0] == "succeeded"
    assert db.execute("SELECT event_key FROM workflow_events").fetchone()[0] == "intent:answer-ready"
    assert db.execute("SELECT status FROM workflow_deliveries").fetchone()[0] == "pending"
    db.close()

    with pytest.raises(NativeCheckpointError) as conflict:
        await saver.commit_frontier(
            fence,
            genesis["checkpoint_id"],
            state={**next_state, "values": {"answer": 99}},
            frontier=[],
            step=1,
            operation_id="operation-frontier",
        )
    assert conflict.value.code == "operation_identity_conflict"


@pytest.mark.asyncio
async def test_blob_owner_converts_staging_to_pending_to_checkpoint_atomically(tmp_path):
    path, store, run_id, fence, _ = await _run_and_config(tmp_path)
    saver = NativeCheckpointStore(path)
    task = {"task_id": "task-blob", "activation_id": "activation", "node_id": "fetch_b0"}
    state = {
        "schema_version": 2, "workflow_name": "deep_research", "workflow_version": "v2",
        "thread_id": run_id, "run_id": run_id, "session_id": "session", "values": {}, "blob_refs": [],
    }
    genesis = await saver.ensure_genesis(fence, run_id, state, [task], operation_id="blob-genesis")
    registered = RegisteredBlobStore(tmp_path / "blobs", path)
    identity = NodeExecutionIdentity("deep_research", "v2", run_id, run_id, genesis["checkpoint_id"], "", "task-blob", "fetch_b0", 1)
    ref = await registered.put(b"large durable evidence", identity, media_type="text/plain")
    await saver.commit_task_result(
        fence, genesis["checkpoint_id"], task, 1,
        {"blob_refs": [{"id": ref.sha256, "sha256": ref.sha256}]},
        operation_id="blob-task", blob_refs=[ref.sha256],
    )
    db = sqlite3.connect(path)
    try:
        owners = db.execute("SELECT owner_kind FROM workflow_blob_refs WHERE sha256=? ORDER BY owner_kind", (ref.sha256,)).fetchall()
    finally:
        db.close()
    assert owners == [("pending_task",)]
    next_state = {**state, "blob_refs": [{"id": ref.sha256, "sha256": ref.sha256}]}
    committed = await saver.commit_frontier(
        fence, genesis["checkpoint_id"], state=next_state, frontier=[], step=1,
        operation_id="blob-frontier", blob_refs=[ref.sha256],
    )
    db = sqlite3.connect(path)
    try:
        owner = db.execute("SELECT owner_kind,owner_id FROM workflow_blob_refs WHERE sha256=?", (ref.sha256,)).fetchone()
    finally:
        db.close()
    assert owner == ("checkpoint", committed["checkpoint_id"])


@pytest.mark.asyncio
async def test_terminal_checkpoint_run_status_and_outbox_commit_atomically(tmp_path):
    path, store, run_id, fence, _ = await _run_and_config(tmp_path)
    await store.bind_session_refs(run_id, [("delivery", "session", 0)])
    crashed = False

    async def inject(stage: str) -> None:
        nonlocal crashed
        if stage == "frontier.after_db_commit_before_return" and not crashed:
            crashed = True
            raise RuntimeError("simulated process loss after commit")

    saver = NativeCheckpointStore(path, fault_injector=inject)
    task = {"task_id": "task-terminal", "activation_id": "activation-terminal", "node_id": "terminal"}
    state = {
        "schema_version": 1,
        "workflow_name": "test",
        "workflow_version": "1",
        "thread_id": run_id,
        "run_id": run_id,
        "session_id": "session",
        "values": {},
    }
    genesis = await saver.ensure_genesis(
        fence, run_id, state, [task], operation_id="terminal-genesis"
    )
    await saver.commit_task_result(
        fence,
        genesis["checkpoint_id"],
        task,
        1,
        {"values": {"done": True}},
        operation_id="terminal-task",
    )
    terminal_state = {**state, "values": {"done": True}}
    intents = [
        {
            "intent_id": "terminal-business",
            "event_key": "terminal:terminal-business",
            "event_type": "workflow.final_assistant",
            "payload": {"kind": "final_assistant", "status": "completed"},
        },
        {
            "intent_id": f"{run_id}:run-terminal",
            "event_key": "run:terminal",
            "event_type": "workflow.final",
            "channel": "final",
            "payload": {"kind": "final", "status": "completed"},
        },
    ]

    with pytest.raises(RuntimeError, match="process loss"):
        await saver.commit_frontier(
            fence,
            genesis["checkpoint_id"],
            state=terminal_state,
            frontier=[],
            step=1,
            operation_id="terminal-frontier",
            intents=intents,
            terminal_status="completed",
        )

    run = await store.get_run(run_id)
    assert run is not None
    assert run["status"] == "completed"
    assert run["ended_at"] is not None
    assert run["lease_owner"] is None
    db = sqlite3.connect(path)
    assert db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM workflow_deliveries").fetchone()[0] == 5
    assert db.execute("SELECT COUNT(*) FROM workflow_pending_writes").fetchone()[0] == 0
    db.close()

    replay = await saver.commit_frontier(
        fence,
        genesis["checkpoint_id"],
        state=terminal_state,
        frontier=[],
        step=1,
        operation_id="terminal-frontier",
        intents=intents,
        terminal_status="completed",
    )
    assert len(replay["event_ids"]) == 2


@pytest.mark.asyncio
async def test_interrupt_atomically_materializes_recoverable_decision_event(tmp_path):
    path, store, run_id, fence, _ = await _run_and_config(tmp_path)
    await store.bind_session_refs(run_id, [("delivery", "session", 0)])
    saver = NativeCheckpointStore(path)
    task = {"task_id": "task-decision", "activation_id": "activation-decision", "node_id": "wait"}
    state = {
        "schema_version": 1,
        "workflow_name": "test",
        "workflow_version": "1",
        "thread_id": run_id,
        "run_id": run_id,
        "session_id": "session",
        "values": {},
    }
    genesis = await saver.ensure_genesis(
        fence, run_id, state, [task], operation_id="decision-genesis"
    )
    result = await saver.commit_interrupt(
        fence,
        genesis["checkpoint_id"],
        task,
        interrupt_id="outline-interrupt",
        kind="ppt_outline",
        prompt={
            "kind": "ppt_outline",
            "outline_id": f"workflow:{run_id}:0",
            "topic": "Native",
            "outline_markdown": "# Slide 1",
        },
        operation_id="decision-interrupt",
    )

    assert result is not None
    assert len(result["event_ids"]) == 1
    run = await store.get_run(run_id)
    assert run is not None and run["status"] == "waiting"
    db = sqlite3.connect(path)
    event = db.execute(
        "SELECT event_type,payload_json FROM workflow_events WHERE event_id=?",
        (result["event_ids"][0],),
    ).fetchone()
    assert event[0] == "workflow.decision"
    assert '"decision_kind":"ppt_outline"' in event[1]
    deliveries = db.execute(
        "SELECT channel,status FROM workflow_deliveries WHERE event_id=? ORDER BY channel",
        (result["event_ids"][0],),
    ).fetchall()
    assert deliveries == [("session_message", "pending"), ("websocket", "pending")]
    db.close()


@pytest.mark.asyncio
async def test_launch_failure_atomically_finalizes_run_and_outbox(tmp_path):
    path, store, run_id, _, _ = await _run_and_config(tmp_path)
    # _run_and_config claims the run; create a fresh unclaimed run for this preflight path.
    run_id, _ = await store.create_run(
        request_key="launch-failure-request",
        session_id="session",
        request_id="req-launch",
        turn_id="turn-launch",
        workflow_name="test",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability-launch",
        capability_snapshot={},
        state_schema_version=1,
    )
    await store.bind_session_refs(run_id, [("delivery", "session", 0)])
    saver = NativeCheckpointStore(path)

    result = await saver.commit_launch_failure(
        run_id,
        error={"code": "launcher_error", "type": "ValueError"},
    )
    replay = await saver.commit_launch_failure(
        run_id,
        error={"code": "launcher_error", "type": "ValueError"},
    )

    assert replay == result
    run = await store.get_run(run_id)
    assert run is not None and run["status"] == "failed"
    assert run["ended_at"] is not None
    db = sqlite3.connect(path)
    assert db.execute(
        "SELECT COUNT(*) FROM workflow_events WHERE run_id=? AND event_key='run:terminal'",
        (run_id,),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM workflow_deliveries WHERE run_id=? AND status='pending'",
        (run_id,),
    ).fetchone()[0] == 3
    db.close()
