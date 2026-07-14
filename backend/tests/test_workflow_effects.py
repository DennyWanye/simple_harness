from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import aiosqlite
import pytest

from deskpet.workflows import EffectKind, EffectPolicy
from deskpet.workflows.adapters import SyncHandlerTimedOut, execute_sync_handler
from deskpet.workflows.effects import (
    CheckpointEffectLink,
    EffectAction,
    EffectJournal,
    EffectStatus,
    NormalizedToolOutcome,
    PreparedTarget,
    PreparedToolCall,
    StagedFileLifecycle,
    StagingPreconditionFailed,
    TargetMode,
    TargetReservationConflict,
    effect_fingerprint,
    target_reservation_key,
)
from deskpet.workflows.store import StaleRunFence, WorkflowRunStore


async def _new_run(
    store: WorkflowRunStore,
    key: str,
    *,
    thread_id: str | None = None,
    ttl_seconds: float = 90.0,
):
    run_id, _ = await store.create_run(
        request_key=key,
        session_id="session",
        request_id=f"request-{key}",
        turn_id=f"turn-{key}",
        workflow_name="workflow",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash=f"capability-{key}",
        capability_snapshot={},
        state_schema_version=1,
        thread_id=thread_id,
    )
    return run_id, await store.claim(run_id, f"owner-{key}", ttl_seconds=ttl_seconds)


def _prepared(
    call_id: str,
    params: dict[str, object],
    *,
    targets: tuple[PreparedTarget, ...] = (),
    effect_type: str = "tool",
) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="write_file" if targets else "lookup",
        stable_call_id=call_id,
        final_params=params,
        prepared_targets=targets,
        tool_spec_version="spec-v1",
        schema_hash="schema-v1",
        permission_policy_version="permission-v1",
        effect_type=effect_type,
    )


def _policy(
    kind: EffectKind = EffectKind.DETERMINISTIC_REUSABLE,
    *,
    reusable: bool = True,
) -> EffectPolicy:
    return EffectPolicy("effect-policy", "v1", kind, reusable_across_branches=reusable)


async def _begin(journal: EffectJournal, fence, prepared: PreparedToolCall, node: str):
    return await journal.begin(
        fence,
        node_execution_id=node,
        workflow_name="workflow",
        workflow_version="1",
        node_id="node",
        logical_effect_key="logical",
        prepared=prepared,
        policy=_policy(),
    )


async def _wait_for_status(
    journal: EffectJournal,
    effect_id: str,
    expected: EffectStatus,
    *,
    attempts: int = 100,
) -> None:
    for _ in range(attempts):
        record = await journal.get(effect_id)
        if record is not None and record.status is expected:
            return
        await asyncio.sleep(0.01)
    record = await journal.get(effect_id)
    pytest.fail(f"effect stayed {record.status if record else None}, expected {expected}")


def test_prepared_call_is_canonical_and_target_identity_is_platform_aware(tmp_path):
    params = {"path": "report.txt", "options": {"encoding": "utf-8"}}
    prepared = _prepared("call-1", params)
    params["options"]["encoding"] = "utf-16"

    assert prepared.final_params["options"] == {"encoding": "utf-8"}
    with pytest.raises(TypeError):
        prepared.final_params["path"] = "other.txt"
    assert _prepared("call-1", {"path": "other.txt"}).args_hash != prepared.args_hash

    first = target_reservation_key(r"C:\Users\Desk\..\Desk\Report.docx", platform="windows")
    second = target_reservation_key("c:/users/desk/report.docx", platform="windows")
    assert first == second
    assert target_reservation_key("/tmp/Report", platform="posix") != target_reservation_key(
        "/tmp/report", platform="posix"
    )

    fingerprint = effect_fingerprint(
        workflow_name="workflow",
        workflow_version="1",
        node_id="node",
        logical_effect_key="logical",
        effect_type="tool",
        args_hash=prepared.args_hash,
        policy_version="v1",
    )
    assert len(fingerprint) == 64


@pytest.mark.asyncio
async def test_concurrent_begin_has_one_executor_and_args_change_is_new_effect(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    run_id, fence = await _new_run(store, "concurrent")
    journal = EffectJournal(path)
    prepared = _prepared("call-1", {"query": "alpha"})

    first, second = await asyncio.gather(
        _begin(journal, fence, prepared, "node-exec-a"),
        _begin(journal, fence, prepared, "node-exec-b"),
    )
    assert {first.action, second.action} == {EffectAction.EXECUTE, EffectAction.IN_FLIGHT}
    assert first.effect.effect_id == second.effect.effect_id
    assert first.effect.run_id == run_id

    changed = await _begin(
        journal, fence, _prepared("call-1", {"query": "beta"}), "node-exec-c"
    )
    assert changed.action is EffectAction.EXECUTE
    assert changed.effect.effect_id != first.effect.effect_id


@pytest.mark.asyncio
async def test_reclaimed_running_effect_requires_reconciliation(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path, clock=lambda: now[0])
    run_id, fence = await _new_run(store, "reclaim", ttl_seconds=1.0)
    journal = EffectJournal(path, clock=lambda: now[0])
    prepared = _prepared("call", {"query": "alpha"})
    first = await _begin(journal, fence, prepared, "old-node")

    now[0] = 200.0
    replacement = await store.claim(run_id, "replacement")
    resumed = await _begin(journal, replacement, prepared, "new-node")
    assert resumed.effect.effect_id == first.effect.effect_id
    assert resumed.action is EffectAction.RECONCILE
    assert resumed.effect.status is EffectStatus.UNCERTAIN


@pytest.mark.asyncio
async def test_target_reservation_rejects_concurrent_path_alias(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    _, fence = await _new_run(store, "reservation")
    journal = EffectJournal(path)
    final = tmp_path / "output.txt"
    first_target = PreparedTarget.prepare(
        final, run_id=fence.run_id, stable_call_id="call-a", mode=TargetMode.CREATE
    )
    second_target = PreparedTarget.prepare(
        final, run_id=fence.run_id, stable_call_id="call-b", mode=TargetMode.CREATE
    )
    first = _prepared("call-a", {"path": str(final)}, targets=(first_target,))
    second = _prepared("call-b", {"path": str(final)}, targets=(second_target,))

    results = await asyncio.gather(
        journal.reserve_targets(fence, first),
        journal.reserve_targets(fence, second),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, TargetReservationConflict) for result in results) == 1
    assert first_target.staging_path != second_target.staging_path


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ("released", "committed"))
async def test_target_reservation_terminal_owner_can_be_replaced(tmp_path, terminal_status):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    _, first_fence = await _new_run(store, "reservation-first")
    second_run_id, second_fence = await _new_run(store, "reservation-second")
    journal = EffectJournal(path)
    final = tmp_path / "output.txt"
    first_target = PreparedTarget.prepare(
        final,
        run_id=first_fence.run_id,
        stable_call_id="call-a",
        mode=TargetMode.CREATE,
    )
    second_target = PreparedTarget.prepare(
        final,
        run_id=second_fence.run_id,
        stable_call_id="call-b",
        mode=TargetMode.CREATE,
    )
    await journal.reserve_targets(
        first_fence,
        _prepared("call-a", {"path": str(final)}, targets=(first_target,)),
    )
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """UPDATE workflow_target_reservations
            SET status=?,lease_expires_at=NULL WHERE reservation_key=?""",
            (terminal_status, first_target.reservation_key),
        )
        await db.commit()

    await journal.reserve_targets(
        second_fence,
        _prepared("call-b", {"path": str(final)}, targets=(second_target,)),
    )

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT * FROM workflow_target_reservations WHERE reservation_key=?",
                (second_target.reservation_key,),
            )
        ).fetchone()
    assert row is not None
    assert row["run_id"] == second_run_id
    assert row["call_id"] == "call-b"
    assert row["status"] == "prepared"


def test_staged_file_rollback_and_commit_are_copy_on_write(tmp_path):
    lifecycle = StagedFileLifecycle()
    final = tmp_path / "nested" / "result.txt"
    target = PreparedTarget.prepare(
        final, run_id="run", stable_call_id="rollback", mode=TargetMode.CREATE
    )
    evidence = lifecycle.stage(target, b"draft")
    assert not final.exists()
    assert final.parent.exists()
    assert (tmp_path / "nested").exists()
    lifecycle.rollback(target, evidence)
    assert not Path(target.staging_path).exists()
    assert not final.parent.exists()

    committed_target = PreparedTarget.prepare(
        final, run_id="run", stable_call_id="commit", mode=TargetMode.CREATE
    )
    committed = lifecycle.stage(committed_target, b"committed")
    lifecycle.commit(committed_target, committed)
    assert final.read_bytes() == b"committed"
    assert lifecycle.reconcile(committed_target, committed) == "committed"

    replace_target = PreparedTarget.prepare(
        final, run_id="run", stable_call_id="replace", mode=TargetMode.REPLACE
    )
    replacement = lifecycle.stage(replace_target, b"replacement")
    assert final.read_bytes() == b"committed"
    lifecycle.rollback(replace_target, replacement)
    assert final.read_bytes() == b"committed"


@pytest.mark.parametrize(
    ("mode", "payload", "expected"),
    (
        (TargetMode.REPLACE, b"replacement", b"replacement"),
        (TargetMode.APPEND, b"+tail", b"original+tail"),
        (TargetMode.EDIT, b"edited", b"edited"),
    ),
)
def test_staged_existing_file_modes_commit_atomically(tmp_path, mode, payload, expected):
    final = tmp_path / f"{mode.value}.txt"
    final.write_bytes(b"original")
    target = PreparedTarget.prepare(
        final, run_id="run", stable_call_id=mode.value, mode=mode
    )
    lifecycle = StagedFileLifecycle()
    evidence = lifecycle.stage(target, payload)
    assert final.read_bytes() == b"original"
    lifecycle.commit(target, evidence)
    assert final.read_bytes() == expected


def test_staged_commit_rejects_target_changed_after_stage(tmp_path):
    final = tmp_path / "drift.txt"
    final.write_bytes(b"before")
    target = PreparedTarget.prepare(
        final, run_id="run", stable_call_id="edit", mode=TargetMode.EDIT
    )
    lifecycle = StagedFileLifecycle()
    evidence = lifecycle.stage(target, b"after")
    final.write_bytes(b"external change")
    with pytest.raises(StagingPreconditionFailed):
        lifecycle.commit(target, evidence)
    lifecycle.rollback(target, evidence)
    assert final.read_bytes() == b"external change"


@pytest.mark.asyncio
async def test_staged_target_lifecycle_must_commit_before_effect(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    _, fence = await _new_run(store, "staged")
    journal = EffectJournal(path)
    lifecycle = StagedFileLifecycle()
    final = tmp_path / "artifact.txt"
    target = PreparedTarget.prepare(
        final, run_id=fence.run_id, stable_call_id="call", mode=TargetMode.CREATE
    )
    prepared = _prepared("call", {"path": str(final)}, targets=(target,), effect_type="file")
    policy = EffectPolicy("staged", "v1", EffectKind.STAGED_FILE)
    begun = await journal.begin(
        fence,
        node_execution_id="node-exec",
        workflow_name="workflow",
        workflow_version="1",
        node_id="node",
        logical_effect_key="file",
        prepared=prepared,
        policy=policy,
    )
    evidence = lifecycle.stage(target, b"artifact")
    await journal.record_target_state(
        fence,
        begun.effect.effect_id,
        target.reservation_key,
        expected=("prepared",),
        status="staged",
        evidence=evidence.to_dict(),
    )
    await journal.record_target_state(
        fence,
        begun.effect.effect_id,
        target.reservation_key,
        expected=("staged",),
        status="committing",
    )
    lifecycle.commit(target, evidence)
    await journal.record_target_state(
        fence,
        begun.effect.effect_id,
        target.reservation_key,
        expected=("committing",),
        status="committed",
        evidence=evidence.to_dict(),
    )
    committed = await journal.commit(
        fence, begun.effect.effect_id, NormalizedToolOutcome.success({"path": str(final)})
    )
    assert committed.status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_checkpoint_reuse_requires_link_and_deterministic_policy(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    source_run, source_fence = await _new_run(store, "source", thread_id="thread")
    journal = EffectJournal(path)
    prepared = _prepared("call", {"query": "stable"})
    source = await _begin(journal, source_fence, prepared, "source-node")
    await journal.commit(
        source_fence, source.effect.effect_id, NormalizedToolOutcome.success({"value": 1})
    )
    link = CheckpointEffectLink("thread", "", "checkpoint-1")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
            checkpoint_type,checkpoint_blob,metadata_blob,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            ("thread", "", "checkpoint-1", None, source_run, "json", b"{}", b"{}", 1.0),
        )
        await db.commit()
    await journal.link_checkpoint(source_fence, link, source.effect.effect_id, "source-node")

    _, child_fence = await _new_run(store, "child", thread_id="thread")
    reused = await journal.begin(
        child_fence,
        node_execution_id="child-node",
        workflow_name="workflow",
        workflow_version="1",
        node_id="node",
        logical_effect_key="logical",
        prepared=prepared,
        policy=_policy(),
        reuse_checkpoint=link,
    )
    assert reused.action is EffectAction.REUSE
    assert reused.effect.effect_id == source.effect.effect_id

    not_reused = await journal.begin(
        child_fence,
        node_execution_id="child-write",
        workflow_name="workflow",
        workflow_version="1",
        node_id="node",
        logical_effect_key="logical",
        prepared=prepared,
        policy=EffectPolicy("effect-policy", "v1", EffectKind.OPAQUE_MANUAL),
        reuse_checkpoint=link,
    )
    assert not_reused.action is EffectAction.EXECUTE
    assert not_reused.effect.effect_id != source.effect.effect_id


@pytest.mark.asyncio
async def test_timeout_keeps_worker_observable_and_commits_late_result(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    _, fence = await _new_run(store, "late-success")
    journal = EffectJournal(path)
    prepared = _prepared("call", {"query": "late"})
    begun = await _begin(journal, fence, prepared, "node")
    release = threading.Event()

    def handler():
        release.wait(2)
        return {"ok": True}

    with pytest.raises(SyncHandlerTimedOut):
        await execute_sync_handler(
            handler,
            timeout_seconds=0.01,
            normalizer=lambda raw: NormalizedToolOutcome.success(raw),
            journal=journal,
            fence=fence,
            effect_id=begun.effect.effect_id,
            prepared=prepared,
        )
    assert (await journal.get(begun.effect.effect_id)).status is EffectStatus.UNCERTAIN
    release.set()
    await _wait_for_status(journal, begun.effect.effect_id, EffectStatus.COMMITTED)


@pytest.mark.asyncio
async def test_timeout_late_result_under_stale_fence_becomes_orphan(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path, clock=lambda: now[0])
    run_id, fence = await _new_run(store, "late-stale", ttl_seconds=1.0)
    journal = EffectJournal(path, clock=lambda: now[0])
    prepared = _prepared("call", {"query": "late"})
    begun = await _begin(journal, fence, prepared, "node")
    release = threading.Event()

    def handler():
        release.wait(2)
        return {"ok": True}

    with pytest.raises(SyncHandlerTimedOut):
        await execute_sync_handler(
            handler,
            timeout_seconds=0.01,
            normalizer=lambda raw: NormalizedToolOutcome.success(raw),
            journal=journal,
            fence=fence,
            effect_id=begun.effect.effect_id,
            prepared=prepared,
        )
    now[0] = 200.0
    new_fence = await store.claim(run_id, "new-owner")
    with pytest.raises(StaleRunFence):
        await journal.commit(
            fence, begun.effect.effect_id, NormalizedToolOutcome.success({"wrong": True})
        )
    release.set()
    await _wait_for_status(journal, begun.effect.effect_id, EffectStatus.LATE_ORPHAN)
    reconciled = await journal.reconcile(
        new_fence,
        begun.effect.effect_id,
        NormalizedToolOutcome.success({"ok": True}),
        evidence_verified=True,
    )
    assert reconciled.status is EffectStatus.COMMITTED
    unchanged = await journal.finalize_late(
        fence,
        effect_id=begun.effect.effect_id,
        args_hash=prepared.args_hash,
        lease_epoch=fence.lease_epoch,
        outcome=NormalizedToolOutcome.failure("late_failure", "too late"),
    )
    assert unchanged.status is EffectStatus.COMMITTED
