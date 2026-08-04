from __future__ import annotations

import copy
import hashlib
import json

import aiosqlite
import pytest

from deskpet.workflows.store.research_repository import (
    ResearchRepositoryError,
    ResearchWorkflowRepository,
)
from deskpet.workflows.store.run_store import WorkflowRunStore


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


async def _create_run(
    store: WorkflowRunStore,
    run_id: str,
    *,
    request_key: str | None = None,
    workflow_version: str = "v5",
) -> None:
    await store.create_run(
        request_key=request_key or f"start:{run_id}",
        session_id="session-1",
        request_id=f"request:{run_id}",
        turn_id=f"turn:{run_id}",
        workflow_name="deep_research",
        workflow_version=workflow_version,
        manifest_hash="manifest-v5",
        implementation_hash="implementation-v5",
        capability_hash="capability-v5",
        capability_snapshot={"research": True},
        state_schema_version=5,
        run_id=run_id,
        trace_id=f"trace:{run_id}",
        thread_id=f"thread:{run_id}",
    )


async def _install_head(
    path,
    run_id: str,
    *,
    head_id: str = "head-2",
    brief_id: str = "brief-1",
    status: str = "running",
    run_version: int = 7,
) -> None:
    thread_id = f"thread:{run_id}"
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """UPDATE workflow_runs SET status=?,run_version=?,head_checkpoint_ns='',
            head_checkpoint_id=?,started_at=900,updated_at=900 WHERE run_id=?""",
            (status, run_version, head_id, run_id),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
            checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at
            ) VALUES(?,?,?,?,?,'native',X'7B7D',X'7B7D','deskpet-native',1,?)""",
            (thread_id, "", brief_id, None, run_id, 901.0),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
            checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at
            ) VALUES(?,?,?,?,?,'native',X'7B7D',X'7B7D','deskpet-native',1,?)""",
            (thread_id, "", head_id, brief_id, run_id, 902.0),
        )
        for checkpoint_id, parent in ((brief_id, None), (head_id, brief_id)):
            await db.execute(
                """INSERT INTO workflow_checkpoint_owners(
                run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (run_id, thread_id, "", checkpoint_id, parent, 902.0),
            )
        await db.commit()


async def _record_final(path, run_id: str, delivery_status: str, **extra) -> None:
    payload = {"delivery_status": delivery_status, **extra}
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO workflow_events(
            event_id,event_key,run_id,seq,event_type,payload_json,created_at
            ) VALUES(?,?,?,1,'workflow.final',?,?)""",
            (
                f"final:{run_id}",
                "run:terminal",
                run_id,
                json.dumps(payload, sort_keys=True),
                999.0,
            ),
        )
        await db.commit()


async def _register_snapshot_blobs(path, manifest: dict) -> str:
    encoded = json.dumps(
        manifest, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    manifest_ref = hashlib.sha256(encoded).hexdigest()
    refs = {manifest_ref, *manifest["passage_blob_refs"]}
    async with aiosqlite.connect(path) as db:
        for ref in refs:
            await db.execute(
                """INSERT OR IGNORE INTO workflow_blobs(
                sha256,size_bytes,media_type,relative_path,created_at
                ) VALUES(?,?,'application/json',?,900)""",
                (ref, len(encoded), f"{ref[:2]}/{ref[2:]}"),
            )
        await db.commit()
    return manifest_ref


@pytest.mark.asyncio
async def test_generate_now_full_state_machine_is_cas_safe_and_restart_idempotent(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    run_store = WorkflowRunStore(path, clock=clock)
    await _create_run(run_store, "run-1")
    await _install_head(path, "run-1")
    repo = ResearchWorkflowRepository(path, clock=clock)

    opened, created = await repo.open_control(
        run_id="run-1",
        idempotency_key="click-1",
        action="generate_now",
        expected_run_version=7,
        expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head-2",
        payload={"source": "ui"},
    )
    assert created and opened["status"] == "open"
    replay, created = await repo.open_control(
        run_id="run-1",
        idempotency_key="click-1",
        action="generate_now",
        expected_run_version=7,
        expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head-2",
        payload={"source": "ui"},
    )
    assert not created and replay["command_id"] == opened["command_id"]

    accepted = await repo.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief-1"
    )
    assert accepted["status"] == "accepted"
    assert accepted["accepted_at"] == 1_000.0
    assert accepted["settle_deadline"] == 1_030.0
    assert (await repo.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief-1"
    ))["status"] == "accepted"

    observed = await repo.transition_control(
        opened["command_id"],
        expected_status="accepted",
        new_status="observed",
        checkpoint_ns="",
        checkpoint_id="head-2",
    )
    assert observed["status"] == "observed"
    restarted = ResearchWorkflowRepository(path, clock=clock)
    assert (await restarted.active_control("run-1"))["status"] == "observed"
    assert (await restarted.transition_control(
        opened["command_id"],
        expected_status="accepted",
        new_status="observed",
        checkpoint_ns="",
        checkpoint_id="head-2",
    ))["status"] == "observed"
    settled = await restarted.transition_control(
        opened["command_id"],
        expected_status="observed",
        new_status="settled",
        checkpoint_ns="",
        checkpoint_id="head-2",
        result={"route": "partial_synthesis"},
    )
    consumed = await restarted.transition_control(
        opened["command_id"],
        expected_status="settled",
        new_status="consumed",
        checkpoint_ns="",
        checkpoint_id="head-2",
    )
    assert settled["payload"]["_result"] == {"route": "partial_synthesis"}
    assert consumed["status"] == "consumed"
    assert await restarted.active_control("run-1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("stale_head", "terminal", "brief_missing"))
async def test_generate_now_rejects_stale_terminal_or_uncommitted_brief(tmp_path, failure):
    path = tmp_path / f"{failure}.db"
    clock = FakeClock()
    run_store = WorkflowRunStore(path, clock=clock)
    await _create_run(run_store, "run-1")
    await _install_head(path, "run-1")
    repo = ResearchWorkflowRepository(path, clock=clock)
    command, _ = await repo.open_control(
        run_id="run-1",
        idempotency_key=failure,
        action="generate_now",
        expected_run_version=7,
        expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head-2",
    )
    if failure == "stale_head":
        async with aiosqlite.connect(path) as db:
            await db.execute("UPDATE workflow_runs SET run_version=8 WHERE run_id='run-1'")
            await db.commit()
    elif failure == "terminal":
        async with aiosqlite.connect(path) as db:
            await db.execute("UPDATE workflow_runs SET status='completed' WHERE run_id='run-1'")
            await db.commit()
    brief_id = "missing" if failure == "brief_missing" else "brief-1"
    rejected = await repo.accept_generate_now(
        command["command_id"], brief_checkpoint_ns="", brief_checkpoint_id=brief_id
    )
    assert rejected["status"] == "rejected"
    assert rejected["payload"]["_result"]["reason"] == "run_head_terminal_or_brief_cas_failed"


@pytest.mark.asyncio
async def test_observe_requires_current_head_and_deadline_candidates_are_read_only(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    run_store = WorkflowRunStore(path, clock=clock)
    await _create_run(run_store, "run-1")
    await _install_head(path, "run-1")
    repo = ResearchWorkflowRepository(path, clock=clock)
    command, _ = await repo.open_control(
        run_id="run-1", idempotency_key="now", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head-2",
    )
    await repo.accept_generate_now(
        command["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief-1"
    )
    clock.value = 1_031.0
    candidates = await repo.expired_control_candidates()
    assert candidates["settle_deadline_exceeded"] == (command["command_id"],)
    assert (await repo.get_control(command["command_id"]))["status"] == "accepted"
    with pytest.raises(ResearchRepositoryError, match="durable run head"):
        await repo.transition_control(
            command["command_id"], expected_status="accepted", new_status="observed",
            checkpoint_ns="", checkpoint_id="stale-head",
        )


@pytest.mark.asyncio
async def test_control_rejects_parallel_active_commands_and_changed_replay(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "run-1")
    await _install_head(path, "run-1")
    repo = ResearchWorkflowRepository(path, clock=clock)
    command, _ = await repo.open_control(
        run_id="run-1", idempotency_key="first", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head-2",
    )
    with pytest.raises(ResearchRepositoryError, match="active control"):
        await repo.open_control(
            run_id="run-1", idempotency_key="second", action="generate_now",
            expected_run_version=7, expected_head_checkpoint_ns="",
            expected_head_checkpoint_id="head-2",
        )
    await repo.accept_generate_now(
        command["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief-1"
    )
    with pytest.raises(ResearchRepositoryError, match="another brief checkpoint"):
        await repo.accept_generate_now(
            command["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="head-2"
        )


@pytest.mark.asyncio
async def test_cancel_settle_is_an_idempotent_marker_on_the_single_generate_fence(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "run-1")
    await _install_head(path, "run-1")
    repo = ResearchWorkflowRepository(path, clock=clock)
    command, _ = await repo.open_control(
        run_id="run-1", idempotency_key="generate", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head-2",
    )
    await repo.accept_generate_now(
        command["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="brief-1"
    )
    projection = await repo.request_cancel_settle(
        "run-1", idempotency_key="cancel-1", expected_run_version=7
    )
    replay = await ResearchWorkflowRepository(path, clock=clock).request_cancel_settle(
        "run-1", idempotency_key="cancel-1", expected_run_version=7
    )
    assert projection["created"] is True
    assert replay["created"] is False
    assert {key: value for key, value in projection.items() if key != "created"} == {
        key: value for key, value in replay.items() if key != "created"
    }
    assert projection["action"] == "cancel_settle"
    assert projection["source_action"] == "generate_now"
    assert projection["cancel_settle"]["head_checkpoint_id"] == "head-2"
    durable = await repo.get_control(command["command_id"])
    assert durable["action"] == "generate_now"
    assert durable["payload"]["_cancel_settle"]["idempotency_key"] == "cancel-1"
    with pytest.raises(ResearchRepositoryError, match="another cancel-settle"):
        await repo.request_cancel_settle(
            "run-1", idempotency_key="cancel-2", expected_run_version=7
        )
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE workflow_runs SET status='completed' WHERE run_id='run-1'")
        await db.commit()
    with pytest.raises(ResearchRepositoryError, match="run/head/version CAS"):
        await repo.request_cancel_settle(
            "run-1", idempotency_key="cancel-1", expected_run_version=7
        )


def _manifest(run_id: str) -> dict:
    return {
        "schema_version": 1,
        "parent_run_id": run_id,
        "dimension_coverages": [{"dimension_id": "state", "status": "partial"}],
        "passage_blob_refs": ["a" * 64],
        "source_families": [{"family_id": "family-1"}],
        "query_fingerprints": ["query-1"],
        "budget_summary": {"used_ratio": 0.4},
        "created_at": "2026-07-16T00:00:00Z",
        "continue_until": "2026-08-15T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_snapshot_manifest_is_content_addressed_parent_pinned_and_idempotent(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "parent")
    repo = ResearchWorkflowRepository(path, clock=clock)
    manifest = _manifest("parent")
    manifest_ref = await _register_snapshot_blobs(path, manifest)
    expected = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    snapshot_hash, created = await repo.persist_snapshot_manifest(
        run_id="parent", operation_id="operation-parent", manifest=manifest,
        manifest_ref=manifest_ref, continue_until=2_000.0,
    )
    assert created and snapshot_hash == expected
    replay_hash, created = await repo.persist_snapshot_manifest(
        run_id="parent", operation_id="operation-parent",
        manifest={"snapshot_hash": expected, **manifest},
        manifest_ref=manifest_ref, continue_until=2_000.0,
    )
    assert replay_hash == expected and not created
    arbitrary_ref = "b" * 64
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO workflow_blobs(
            sha256,size_bytes,media_type,relative_path,created_at
            ) VALUES(?,1,'application/json','bb/arbitrary',900)""",
            (arbitrary_ref,),
        )
        await db.commit()
    with pytest.raises(ResearchRepositoryError, match="exact canonical snapshot manifest"):
        await repo.persist_snapshot_manifest(
            run_id="parent", operation_id="operation-parent", manifest=manifest,
            manifest_ref=arbitrary_ref, continue_until=2_000.0,
        )
    with pytest.raises(ResearchRepositoryError, match="not canonical"):
        await repo.persist_snapshot_manifest(
            run_id="parent", operation_id="operation-parent",
            manifest={"snapshot_hash": "0" * 64, **manifest},
            manifest_ref=manifest_ref, continue_until=2_000.0,
        )
    async with aiosqlite.connect(path) as db:
        pin = await (
            await db.execute(
                "SELECT pin_kind,expires_at FROM workflow_research_snapshot_pins WHERE snapshot_hash=?",
                (expected,),
            )
        ).fetchone()
    assert pin == ("continue_parent", 2_000.0)


@pytest.mark.asyncio
async def test_snapshot_rejects_unregistered_manifest_or_passage_blob(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "parent")
    manifest = _manifest("parent")
    encoded = json.dumps(
        manifest, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    repo = ResearchWorkflowRepository(path, clock=clock)
    with pytest.raises(ResearchRepositoryError, match="not registered"):
        await repo.persist_snapshot_manifest(
            run_id="parent", operation_id="operation-parent", manifest=manifest,
            manifest_ref=hashlib.sha256(encoded).hexdigest(), continue_until=2_000.0,
        )


@pytest.mark.asyncio
async def test_create_child_is_one_transaction_restart_idempotent_and_parent_immutable(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "parent")
    await store.bind_session_refs(
        "parent",
        (("base", "session-1", 3), ("delivery", "delivery-1", 4), ("code", "code-1", 5)),
    )
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE workflow_session_refs SET deleted_at=950 WHERE run_id='parent' AND session_kind='code'"
        )
        await db.commit()
    await _install_head(path, "parent", status="completed")
    await _record_final(path, "parent", "partial", report_ref="report:parent")
    repo = ResearchWorkflowRepository(path, clock=clock)
    await repo.ensure_root_lineage(
        run_id="parent", operation_id="operation-parent", budget_lease_id="budget-parent"
    )
    manifest = _manifest("parent")
    manifest_ref = await _register_snapshot_blobs(path, manifest)
    snapshot_hash, _ = await repo.persist_snapshot_manifest(
        run_id="parent", operation_id="operation-parent", manifest=manifest,
        manifest_ref=manifest_ref, continue_until=2_000.0,
    )
    async with aiosqlite.connect(path) as db:
        parent_before = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id='parent'")
        ).fetchone()
    parent_before = tuple(parent_before)

    kwargs = dict(
        parent_run_id="parent",
        parent_operation_id="operation-parent",
        snapshot_hash=snapshot_hash,
        child_run_id="child",
        child_operation_id="operation-child",
        child_trace_id="trace:child",
        child_thread_id="thread:child",
        child_request_key="continue:child",
        child_request_id="request:child",
        child_turn_id="turn:child",
        budget_lease_id="budget-child",
        start_payload={"only_gaps": ["state"], "snapshot_hash": snapshot_hash},
        parent_report_ref="report:parent",
    )
    lineage, created = await store.create_child_from_snapshot(**kwargs)
    assert created and lineage["parent_run_id"] == "parent"
    restarted = WorkflowRunStore(path, clock=clock)
    replay, created = await restarted.create_child_from_snapshot(**kwargs)
    assert not created and replay == lineage
    changed = copy.deepcopy(kwargs)
    changed["start_payload"] = {"only_gaps": ["other"], "snapshot_hash": snapshot_hash}
    with pytest.raises(ResearchRepositoryError, match="immutable"):
        await restarted.create_child_from_snapshot(**changed)

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        parent_after = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id='parent'")
        ).fetchone()
        child = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id='child'")
        ).fetchone()
        pin = await (
            await db.execute(
                "SELECT * FROM workflow_research_snapshot_pins WHERE run_id='child'"
            )
        ).fetchone()
        operation = await (
            await db.execute(
                "SELECT * FROM workflow_operations WHERE operation_id='operation-child:start'"
            )
        ).fetchone()
        refs = await (
            await db.execute(
                """SELECT session_kind,session_id,session_epoch,deleted_at
                FROM workflow_session_refs WHERE run_id='child' ORDER BY session_kind"""
            )
        ).fetchall()
    assert tuple(parent_after) == parent_before
    assert child["status"] == "created" and child["parent_run_id"] == "parent"
    assert pin["pin_kind"] == "continue_child" and pin["expires_at"] is None
    start_result = json.loads(operation["result_json"])
    assert start_result["payload"]["only_gaps"] == ["state"]
    assert [tuple(row) for row in refs] == [
        ("base", "session-1", 3, None),
        ("delivery", "delivery-1", 4, None),
    ]


@pytest.mark.asyncio
async def test_child_creation_failure_rolls_back_run_pin_lineage_and_payload(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "parent")
    await _create_run(store, "conflict", request_key="continue:child")
    await _install_head(path, "parent", status="completed")
    await _record_final(path, "parent", "insufficient_evidence")
    repo = ResearchWorkflowRepository(path, clock=clock)
    await repo.ensure_root_lineage(
        run_id="parent", operation_id="operation-parent", budget_lease_id="budget-parent"
    )
    manifest = _manifest("parent")
    manifest_ref = await _register_snapshot_blobs(path, manifest)
    snapshot_hash, _ = await repo.persist_snapshot_manifest(
        run_id="parent", operation_id="operation-parent", manifest=manifest,
        manifest_ref=manifest_ref, continue_until=2_000.0,
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await store.create_child_from_snapshot(
            parent_run_id="parent", parent_operation_id="operation-parent",
            snapshot_hash=snapshot_hash, child_run_id="child",
            child_operation_id="operation-child", child_trace_id="trace:child",
            child_thread_id="thread:child", child_request_key="continue:child",
            child_request_id="request:child", child_turn_id="turn:child",
            budget_lease_id="budget-child", start_payload={"snapshot_hash": snapshot_hash},
        )
    async with aiosqlite.connect(path) as db:
        counts = []
        for table in ("workflow_runs", "workflow_research_lineage", "workflow_operations"):
            row = await (
                await db.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id='child'")
            ).fetchone()
            counts.append(row[0])
        pin_count = await (
            await db.execute("SELECT COUNT(*) FROM workflow_research_snapshot_pins WHERE run_id='child'")
        ).fetchone()
    assert counts == [0, 0, 0]
    assert pin_count == (0,)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_by", ("delivery", "expiry", "pin"))
async def test_child_requires_continuable_final_unexpired_snapshot_and_parent_pin(tmp_path, blocked_by):
    path = tmp_path / f"{blocked_by}.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "parent")
    await _install_head(path, "parent", status="completed")
    await _record_final(path, "parent", "completed" if blocked_by == "delivery" else "partial")
    repo = ResearchWorkflowRepository(path, clock=clock)
    await repo.ensure_root_lineage(
        run_id="parent", operation_id="operation-parent", budget_lease_id="budget-parent"
    )
    manifest = _manifest("parent")
    manifest_ref = await _register_snapshot_blobs(path, manifest)
    snapshot_hash, _ = await repo.persist_snapshot_manifest(
        run_id="parent", operation_id="operation-parent", manifest=manifest,
        manifest_ref=manifest_ref, continue_until=2_000.0,
    )
    if blocked_by == "expiry":
        clock.value = 2_001.0
    elif blocked_by == "pin":
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "DELETE FROM workflow_research_snapshot_pins WHERE run_id='parent'"
            )
            await db.commit()
    match = "delivery is not continuable" if blocked_by == "delivery" else "missing or expired"
    with pytest.raises(ResearchRepositoryError, match=match):
        await repo.create_child_from_snapshot(
            parent_run_id="parent", parent_operation_id="operation-parent",
            snapshot_hash=snapshot_hash, child_run_id="child",
            child_operation_id="operation-child", child_trace_id="trace:child",
            child_thread_id="thread:child", child_request_key="continue:child",
            child_request_id="request:child", child_turn_id="turn:child",
            budget_lease_id="budget-child", start_payload={"snapshot_hash": snapshot_hash},
        )


@pytest.mark.asyncio
async def test_lineage_reachability_recomputes_child_ancestors_and_snapshot_pins(tmp_path):
    path = tmp_path / "workflow.db"
    clock = FakeClock()
    store = WorkflowRunStore(path, clock=clock)
    await _create_run(store, "parent")
    await _install_head(path, "parent", status="completed")
    await _record_final(path, "parent", "partial")
    repo = ResearchWorkflowRepository(path, clock=clock)
    await repo.ensure_root_lineage(
        run_id="parent", operation_id="operation-parent", budget_lease_id="budget-parent"
    )
    manifest = _manifest("parent")
    manifest_ref = await _register_snapshot_blobs(path, manifest)
    snapshot_hash, _ = await repo.persist_snapshot_manifest(
        run_id="parent", operation_id="operation-parent", manifest=manifest,
        manifest_ref=manifest_ref, continue_until=1_010.0,
    )
    await repo.create_child_from_snapshot(
        parent_run_id="parent", parent_operation_id="operation-parent",
        snapshot_hash=snapshot_hash, child_run_id="child",
        child_operation_id="operation-child", child_trace_id="trace:child",
        child_thread_id="thread:child", child_request_key="continue:child",
        child_request_id="request:child", child_turn_id="turn:child",
        budget_lease_id="budget-child", start_payload={"snapshot_hash": snapshot_hash},
    )
    clock.value = 2_000.0
    reachable = await repo.lineage_reachability()
    assert reachable.protected_run_ids >= {"parent", "child"}
    assert snapshot_hash in reachable.protected_snapshot_hashes
    assert len(reachable.protected_pin_ids) == 2

    with pytest.raises(ResearchRepositoryError, match="terminal run"):
        await repo.settle_run_snapshot_pins("child", expires_at=3_000.0)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE workflow_runs SET status='completed',ended_at=2000 WHERE run_id='child'"
        )
        await db.commit()
    assert await repo.settle_run_snapshot_pins("child", expires_at=3_000.0) == 1
    assert await repo.settle_run_snapshot_pins("child", expires_at=3_000.0) == 0

    with pytest.raises(ResearchRepositoryError, match="immutable"):
        await repo.ensure_root_lineage(
            run_id="parent", operation_id="operation-parent", budget_lease_id="different"
        )
