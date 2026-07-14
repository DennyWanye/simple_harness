from __future__ import annotations

import asyncio
import inspect
import sqlite3

import pytest
import pytest_asyncio

from deskpet.memory.context_snapshot_store import (
    ContextSnapshotStore,
    SnapshotCommitCancelled,
    SnapshotConflictError,
    SnapshotDataError,
    SnapshotMissingError,
    SnapshotStoreDisabled,
    await_snapshot_commit_ack,
)
from deskpet.memory.migrator import run_migrations


def _projection(*, objective: str = "Ship", pending: list[dict] | None = None):
    return {
        "session_id": "s1",
        "task_scope_id": "goal:g1",
        "source_revisions": {"goal:g1": {"revision": "1"}},
        "objective": objective,
        "decisions": [],
        "completed": [],
        "pending": pending or [{"fact_id": "t1", "status": "pending"}],
        "artifacts": [],
        "blockers": [],
        "narrative_summary": "",
    }


def _tool_summary(**overrides):
    value = {
        "direct_names": ["read_file"],
        "activated_names": [],
        "schema_hashes": {"read_file": "abc"},
        "selection_reasons": {"read_file": "policy direct"},
        "schema_tokens": 42,
        "adapter_state": "canonical",
        "persisted_tool_scope_revision": 3,
        "registry_revision": 9,
        "policy_fingerprint": "policy-1",
        "schema_fingerprint": "schema-1",
    }
    value.update(overrides)
    return value


@pytest_asyncio.fixture
async def store(tmp_path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    return ContextSnapshotStore(db_path)


@pytest.mark.asyncio
async def test_create_get_and_keyword_only_row_revision(store):
    signature = inspect.signature(store.persist_projection_with_tool_context_cas)
    assert signature.parameters["expected_row_revision"].kind is inspect.Parameter.KEYWORD_ONLY

    receipt = await store.persist_projection_with_tool_context_cas(
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    loaded = await store.get("s1", "goal:g1")

    assert receipt.previous_row_revision == 0
    assert receipt.new_handle.row_revision == 1
    assert receipt.persisted_tool_scope_revision == 3
    assert loaded is not None
    assert loaded.objective == "Ship"
    assert loaded.handle == receipt.new_handle


@pytest.mark.asyncio
async def test_stale_cas_conflicts_and_missing_tool_update_is_typed(store):
    await store.persist_projection_with_tool_context_cas(
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    with pytest.raises(SnapshotConflictError):
        await store.compare_and_swap(_projection(objective="late"), expected_row_revision=0)
    with pytest.raises(SnapshotMissingError):
        await store.update_tool_context_cas(
            "s1",
            "goal:missing",
            expected_row_revision=1,
            prepared_toolset_summary=_tool_summary(),
        )


@pytest.mark.asyncio
async def test_same_hash_does_not_increment_revision(store):
    first = await store.persist_projection_with_tool_context_cas(
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    second = await store.persist_projection_with_tool_context_cas(
        _projection(),
        expected_row_revision=first.new_handle.row_revision,
        prepared_toolset_summary=_tool_summary(),
    )
    assert second.changed is False
    assert second.new_handle.row_revision == 1


@pytest.mark.asyncio
async def test_tool_update_preserves_task_fields_and_drops_sensitive_keys(store):
    first = await store.persist_projection_with_tool_context_cas(
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    update = await store.update_tool_context_cas(
        "s1",
        "goal:g1",
        expected_row_revision=first.new_handle.row_revision,
        prepared_toolset_summary={
            "provider_adapter_id": "anthropic",
            "provider_adapter_version": "1",
            "wire_payload_hash": "wire-1",
            "wire_tokens": 51,
            "attempt_id": "attempt-1",
            "adapter_state": "prepared",
            "full_schema": {"secret": "must-not-persist"},
            "credential": "must-not-persist",
        },
    )
    loaded = await store.get("s1", "goal:g1")

    assert update.new_handle.row_revision == 2
    assert update.persisted_tool_scope_revision == 3
    assert loaded is not None
    assert loaded.objective == "Ship"
    assert loaded.prepared_toolset_summary["direct_names"] == ["read_file"]
    assert loaded.prepared_toolset_summary["provider_adapter_id"] == "anthropic"
    assert "full_schema" not in loaded.prepared_toolset_summary
    assert "credential" not in loaded.prepared_toolset_summary


@pytest.mark.asyncio
async def test_flush_cycle_is_idempotent(store):
    first = await store.flush_once(
        "cycle-1",
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    second = await store.flush_once(
        "cycle-1",
        _projection(objective="must not overwrite"),
        expected_row_revision=first.new_handle.row_revision,
        prepared_toolset_summary=_tool_summary(),
    )
    loaded = await store.get("s1", "goal:g1")

    assert second.idempotent is True
    assert second.new_handle.row_revision == 1
    assert loaded is not None and loaded.objective == "Ship"


@pytest.mark.asyncio
async def test_new_flush_cycle_advances_watermark_when_projection_is_unchanged(store):
    first = await store.flush_once(
        "cycle-1",
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    second = await store.flush_once(
        "cycle-2",
        _projection(),
        expected_row_revision=first.new_handle.row_revision,
        prepared_toolset_summary=_tool_summary(),
    )
    loaded = await store.get("s1", "goal:g1")

    assert second.changed is True
    assert second.idempotent is False
    assert second.new_handle.row_revision == 2
    assert loaded is not None
    assert loaded.last_compaction_cycle_id == "cycle-2"


@pytest.mark.asyncio
async def test_old_cycle_is_exactly_once_after_newer_cycle_commits(store):
    first = await store.flush_once(
        "cycle-a",
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    second = await store.flush_once(
        "cycle-b",
        _projection(),
        expected_row_revision=first.new_handle.row_revision,
        prepared_toolset_summary=_tool_summary(),
    )
    replay = await store.flush_once(
        "cycle-a",
        _projection(objective="stale replay must not win"),
        expected_row_revision=second.new_handle.row_revision,
        prepared_toolset_summary=_tool_summary(),
    )
    loaded = await store.get("s1", "goal:g1")

    assert replay.idempotent is True
    assert replay.changed is False
    assert replay.new_handle.row_revision == 2
    assert loaded is not None
    assert loaded.objective == "Ship"
    assert loaded.last_compaction_cycle_id == "cycle-b"
    assert "_compaction_cycle_ids" not in loaded.prepared_toolset_summary


@pytest.mark.asyncio
async def test_tool_context_update_preserves_old_cycle_exactly_once_ledger(store):
    first = await store.flush_once(
        "cycle-a",
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    second = await store.flush_once(
        "cycle-b",
        _projection(),
        expected_row_revision=first.new_handle.row_revision,
        prepared_toolset_summary=_tool_summary(),
    )
    tool_update = await store.update_tool_context_cas(
        "s1",
        "goal:g1",
        expected_row_revision=second.new_handle.row_revision,
        prepared_toolset_summary={
            "attempt_id": "attempt-after-compaction",
            "adapter_state": "prepared",
        },
    )
    replay = await store.flush_once(
        "cycle-a",
        _projection(objective="stale replay after tool CAS"),
        expected_row_revision=tool_update.new_handle.row_revision,
        prepared_toolset_summary=_tool_summary(),
    )
    loaded = await store.get("s1", "goal:g1")

    assert replay.idempotent is True
    assert replay.changed is False
    assert replay.new_handle.row_revision == tool_update.new_handle.row_revision
    assert loaded is not None
    assert loaded.objective == "Ship"
    assert loaded.last_compaction_cycle_id == "cycle-b"
    assert loaded.prepared_toolset_summary["attempt_id"] == (
        "attempt-after-compaction"
    )


@pytest.mark.asyncio
async def test_session_delete_only_removes_session_scoped_snapshot(store):
    session_projection = dict(_projection())
    session_projection["task_scope_id"] = "session:s1"
    await store.persist_projection_with_tool_context_cas(
        session_projection,
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    await store.persist_projection_with_tool_context_cas(
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )

    assert await store.delete_session_scope("s1") == 1
    assert await store.get("s1", "session:s1") is None
    assert await store.get("s1", "goal:g1") is not None


@pytest.mark.asyncio
async def test_corrupt_json_safe_fails(store):
    first = await store.persist_projection_with_tool_context_cas(
        _projection(),
        expected_row_revision=0,
        prepared_toolset_summary=_tool_summary(),
    )
    with sqlite3.connect(store._db_path) as conn:  # test-only corruption fixture
        conn.execute(
            "UPDATE session_context_snapshots SET pending_json='not-json' "
            "WHERE session_id='s1' AND task_scope_id='goal:g1'"
        )
        conn.commit()
    events = []
    store._diagnostic_sink = lambda event, payload: events.append((event, payload))
    assert await store.get("s1", "goal:g1") is None
    assert events[0][0] == "context_snapshot_corrupt"


@pytest.mark.asyncio
async def test_commit_ack_settles_committed_write_before_propagating_cancel(tmp_path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hook(stage: str):
        if stage == "after_commit":
            entered.set()
            await release.wait()

    store = ContextSnapshotStore(db_path, fault_hook=hook)
    write_task = asyncio.create_task(
        store.persist_projection_with_tool_context_cas(
            _projection(),
            expected_row_revision=0,
            prepared_toolset_summary=_tool_summary(),
        )
    )
    waiter = asyncio.create_task(await_snapshot_commit_ack(write_task))
    await entered.wait()
    waiter.cancel()
    release.set()

    with pytest.raises(SnapshotCommitCancelled) as raised:
        await waiter
    assert raised.value.receipt is not None
    assert raised.value.receipt.new_handle.row_revision == 1
    assert await store.get("s1", "goal:g1") is not None


@pytest.mark.asyncio
async def test_commit_ack_reports_rollback_before_propagating_cancel(tmp_path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hook(stage: str):
        if stage == "before_commit":
            entered.set()
            await release.wait()
            raise RuntimeError("injected db failure")

    store = ContextSnapshotStore(db_path, fault_hook=hook)
    write_task = asyncio.create_task(
        store.persist_projection_with_tool_context_cas(
            _projection(),
            expected_row_revision=0,
            prepared_toolset_summary=_tool_summary(),
        )
    )
    waiter = asyncio.create_task(await_snapshot_commit_ack(write_task))
    await entered.wait()
    waiter.cancel()
    release.set()

    with pytest.raises(SnapshotCommitCancelled) as raised:
        await waiter
    assert raised.value.receipt is None
    assert isinstance(raised.value.write_error, RuntimeError)
    assert await store.get("s1", "goal:g1") is None


@pytest.mark.asyncio
async def test_commit_ack_does_not_lose_outcome_on_repeated_cancel():
    release = asyncio.Event()

    async def write():
        await release.wait()
        from deskpet.memory.context_snapshot_store import (
            ContextSnapshotHandle,
            SnapshotWriteReceipt,
        )

        return SnapshotWriteReceipt(
            previous_row_revision=0,
            new_handle=ContextSnapshotHandle("s1", "goal:g1", 1, "hash"),
        )

    write_task = asyncio.create_task(write())
    waiter = asyncio.create_task(await_snapshot_commit_ack(write_task))
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.sleep(0)
    waiter.cancel()
    release.set()

    with pytest.raises(SnapshotCommitCancelled) as raised:
        await waiter
    assert raised.value.receipt is not None
    assert raised.value.cancellation_count >= 1


def test_prepared_summary_rejects_noncanonical_numbers():
    from deskpet.memory.context_snapshot_store import sanitize_prepared_toolset_summary

    with pytest.raises(SnapshotDataError):
        sanitize_prepared_toolset_summary({"schema_tokens": -1})


@pytest.mark.asyncio
async def test_disabled_store_does_not_read_or_write(tmp_path):
    db_path = tmp_path / "not-created.db"
    disabled = ContextSnapshotStore(db_path, enabled=False)
    assert await disabled.get("s1", "session:s1") is None
    with pytest.raises(SnapshotStoreDisabled):
        await disabled.compare_and_swap(_projection(), expected_row_revision=0)
    assert not db_path.exists()
