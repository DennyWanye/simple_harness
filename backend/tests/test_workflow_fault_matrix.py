from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass

import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.workflows import EffectKind, EffectPolicy
from deskpet.workflows.contracts import WorkflowContext, WorkflowRunStatus
from deskpet.workflows.definition import WorkflowManifest
from deskpet.workflows.effects import (
    EffectAction,
    EffectJournal,
    EffectStatus,
    NormalizedToolOutcome,
    PreparedToolCall,
)
from deskpet.workflows.outbox import WorkflowOutbox
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import (
    LegacyCheckpointStore as FencedAsyncSqliteSaver,
    empty_legacy_checkpoint as empty_checkpoint,
    NativeCheckpointStore,
    StaleRunFence,
    WorkflowRunStore,
)


class InjectedFault(RuntimeError):
    pass


class _FaultingConnection:
    """Inject once after a SQL statement or after a durable commit."""

    def __init__(self, connection, *, sql_marker: str | None = None, after_commit=False):
        self._connection = connection
        self._sql_marker = sql_marker
        self._after_commit = after_commit
        self._fired = False

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    async def execute(self, sql: str, parameters=None):
        if parameters is None:
            cursor = await self._connection.execute(sql)
        else:
            cursor = await self._connection.execute(sql, parameters)
        if self._sql_marker and self._sql_marker in " ".join(sql.split()) and not self._fired:
            self._fired = True
            raise InjectedFault(f"after SQL: {self._sql_marker}")
        return cursor

    async def commit(self) -> None:
        await self._connection.commit()
        if self._after_commit and not self._fired:
            self._fired = True
            raise InjectedFault("after durable commit")


async def _claimed_run(path, key: str = "run"):
    store = WorkflowRunStore(path)
    run_id, _ = await store.create_run(
        request_key=key,
        session_id="session",
        request_id=f"request-{key}",
        turn_id=f"turn-{key}",
        workflow_name="fault-workflow",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash=f"capability-{key}",
        capability_snapshot={},
        state_schema_version=1,
    )
    fence = await store.claim(run_id, f"owner-{key}")
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
    return store, run_id, fence, config


async def _checkpoint_counts(store: WorkflowRunStore, run_id: str) -> tuple[int, int]:
    db = await store._connect()
    try:
        checkpoint = await (
            await db.execute(
                "SELECT COUNT(*) FROM workflow_checkpoints WHERE run_id=?", (run_id,)
            )
        ).fetchone()
        owner = await (
            await db.execute(
                "SELECT COUNT(*) FROM workflow_checkpoint_owners WHERE run_id=?",
                (run_id,),
            )
        ).fetchone()
        return int(checkpoint[0]), int(owner[0])
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
async def test_checkpoint_fault_before_and_after_commit_converges(tmp_path, stage):
    path = tmp_path / "workflow.db"
    store, run_id, _, config = await _claimed_run(path, stage)
    saver = FencedAsyncSqliteSaver(path)
    checkpoint = empty_checkpoint()
    original_connect = saver._connect

    async def faulting_connect():
        connection = await original_connect()
        return _FaultingConnection(
            connection,
            sql_marker=(
                "INSERT INTO workflow_checkpoints" if stage == "before_commit" else None
            ),
            after_commit=stage == "after_commit",
        )

    saver._connect = faulting_connect
    with pytest.raises(InjectedFault):
        await saver.aput(
            config,
            checkpoint,
            {"source": "input", "step": 0, "parents": {}},
            {},
        )
    saver._connect = original_connect

    row = await store.get_run(run_id)
    assert row is not None
    expected_count = 0 if stage == "before_commit" else 1
    assert await _checkpoint_counts(store, run_id) == (expected_count, expected_count)
    assert row["head_checkpoint_id"] == (
        None if stage == "before_commit" else checkpoint["id"]
    )

    written = await saver.aput(
        config,
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {},
    )
    loaded = await saver.aget_tuple(written)
    assert loaded is not None and loaded.checkpoint["id"] == checkpoint["id"]
    assert await _checkpoint_counts(store, run_id) == (1, 1)


@pytest.mark.asyncio
async def test_pending_writes_roll_back_as_a_unit_and_retry_without_duplicates(tmp_path):
    path = tmp_path / "workflow.db"
    _, _, _, config = await _claimed_run(path, "pending")
    saver = FencedAsyncSqliteSaver(path)
    checkpoint = empty_checkpoint()
    written = await saver.aput(
        config,
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {},
    )
    original_connect = saver._connect

    async def faulting_connect():
        return _FaultingConnection(
            await original_connect(), sql_marker="INTO workflow_pending_writes"
        )

    saver._connect = faulting_connect
    writes = [("first", {"value": 1}), ("second", {"value": 2})]
    with pytest.raises(InjectedFault):
        await saver.aput_writes(written, writes, "task-1", "root")
    saver._connect = original_connect

    rolled_back = await saver.aget_tuple(written)
    assert rolled_back is not None and rolled_back.pending_writes == []

    await saver.aput_writes(written, writes, "task-1", "root")
    await saver.aput_writes(written, writes, "task-1", "root")
    recovered = await saver.aget_tuple(written)
    assert recovered is not None
    assert recovered.pending_writes == [
        ("task-1", "first", {"value": 1}),
        ("task-1", "second", {"value": 2}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "fault_stage"),
    [
        ("genesis", "genesis.after_db_commit_before_return"),
        ("task", "task_result.after_db_commit_before_return"),
        ("frontier", "frontier.after_db_commit_before_return"),
        ("retry", "retry.after_db_commit_before_return"),
        ("interrupt", "interrupt.after_db_commit_before_return"),
        ("failure", "failure.after_db_commit_before_return"),
        ("engine_failure", "engine_failure.after_db_commit_before_return"),
    ],
)
async def test_native_commit_after_durable_commit_replays_operation(tmp_path, kind, fault_stage):
    path = tmp_path / f"{kind}.db"
    store, run_id, fence, _ = await _claimed_run(path, f"native-{kind}")
    task = {"task_id": "task-1", "activation_id": "activation-1", "node_id": "node-1"}
    state = {"run_id": run_id, "values": {}}
    fired = False

    def inject(stage):
        nonlocal fired
        if stage == fault_stage and not fired:
            fired = True
            raise InjectedFault(stage)

    faulting = NativeCheckpointStore(path, fault_injector=inject)
    clean = NativeCheckpointStore(path)
    if kind == "genesis":
        async def invoke(saver):
            return await saver.ensure_genesis(
                fence, run_id, state, [task], operation_id="stable-operation"
            )
    else:
        genesis = await clean.ensure_genesis(
            fence, run_id, state, [task], operation_id="genesis"
        )
        head = genesis["checkpoint_id"]
        if kind == "task":
            async def invoke(saver):
                return await saver.commit_task_result(
                    fence, head, task, 1, {"values": {"done": True}},
                    operation_id="stable-operation",
                )
        elif kind == "frontier":
            await clean.commit_task_result(
                fence, head, task, 1, {"values": {"done": True}},
                operation_id="frontier-task-result",
            )
            async def invoke(saver):
                return await saver.commit_frontier(
                    fence, head, state={"run_id": run_id, "values": {"done": True}},
                    frontier=[], step=1, operation_id="stable-operation",
                )
        elif kind == "retry":
            async def invoke(saver):
                return await saver.commit_retry(
                    fence, head, task, 1, next_attempt_at=123.0,
                    error_ref="retryable", operation_id="stable-operation",
                )
        elif kind == "interrupt":
            async def invoke(saver):
                return await saver.commit_interrupt(
                    fence, head, task, state=state, frontier=[task], step=1,
                    interrupt_id="confirm", prompt={"question": "continue?"},
                    operation_id="stable-operation",
                )
        elif kind == "failure":
            async def invoke(saver):
                return await saver.commit_failure(
                    fence, head, task, error={"code": "node_failed"},
                    operation_id="stable-operation",
                )
        else:
            async def invoke(saver):
                return await saver.commit_engine_failure(
                    fence, head, [], error={"code": "invalid_frontier"},
                    operation_id="stable-operation",
                )

    with pytest.raises(InjectedFault, match=fault_stage):
        await invoke(faulting)
    recovered = await invoke(clean)
    duplicate = await invoke(clean)
    assert recovered == duplicate

    db = await store._connect()
    try:
        row = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM workflow_operations WHERE operation_id=?",
                ("stable-operation",),
            )
        ).fetchone()
        assert int(row["n"]) == 1
    finally:
        await db.close()


def _manifest() -> WorkflowManifest:
    return WorkflowManifest(
        workflow_name="fault-workflow",
        workflow_version="1",
        state_schema_version=1,
        durability="sync",
        recursion_limit=16,
        max_supersteps=8,
        definition_hash="definition",
        state_hash="state",
        prompt_hash="prompt",
        tool_hash="tool",
        policy_hash="policy",
        callable_source_hash="callable",
        dependency_lock_hash="lock",
        implementation_bundle_hash="implementation",
    )


@dataclass
class _Workflow:
    manifest: WorkflowManifest


class _LateResultExecutable:
    def __init__(self) -> None:
        self.manifest = _manifest()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, state, context, **kwargs):
        self.started.set()
        await self.release.wait()
        return {"late": True}


@pytest.mark.asyncio
async def test_cancelled_run_rejects_late_result_instead_of_advancing(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    saver = FencedAsyncSqliteSaver(path)
    registry = WorkflowRegistry()
    executable = _LateResultExecutable()
    registry.register(_Workflow(executable.manifest), executable=executable)
    runner = WorkflowRunner(store, saver, registry, owner="late-result-runner")
    run_id = await runner.start(
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="fault-workflow",
        workflow_version="1",
        capability_snapshot={},
    )

    running = asyncio.create_task(runner.run(run_id, {"input": True}, WorkflowContext()))
    await executable.started.wait()
    cancelled_version = await store.request_cancel(run_id, "user")
    executable.release.set()
    late_result = await running

    row = await store.get_run(run_id)
    assert row is not None
    assert cancelled_version == row["run_version"]
    assert late_result.status is WorkflowRunStatus.CANCEL_REQUESTED
    assert row["status"] == WorkflowRunStatus.CANCEL_REQUESTED.value
    assert row["head_checkpoint_id"] is None

    converged = await runner.request_cancel(run_id, "user")
    assert converged["status"] == WorkflowRunStatus.CANCELLED.value


def _prepared_call() -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="lookup",
        stable_call_id="call-1",
        final_params={"query": "durability"},
        tool_spec_version="spec-v1",
        schema_hash="schema-v1",
        permission_policy_version="permission-v1",
        effect_type="tool",
    )


@pytest.mark.asyncio
async def test_effect_commit_is_idempotent_and_stale_writer_cannot_replace_outcome(tmp_path):
    path = tmp_path / "workflow.db"
    store, run_id, fence, _ = await _claimed_run(path, "effect")
    journal = EffectJournal(path)
    begun = await journal.begin(
        fence,
        node_execution_id="node-execution",
        workflow_name="fault-workflow",
        workflow_version="1",
        node_id="node",
        logical_effect_key="lookup",
        prepared=_prepared_call(),
        policy=EffectPolicy(
            "effect-policy",
            "v1",
            EffectKind.DETERMINISTIC_REUSABLE,
            reusable_across_branches=True,
        ),
    )
    assert begun.action is EffectAction.EXECUTE
    committed = await journal.commit(
        fence,
        begun.effect.effect_id,
        NormalizedToolOutcome.success({"answer": "first"}),
    )
    duplicate = await journal.commit(
        fence,
        begun.effect.effect_id,
        NormalizedToolOutcome.success({"answer": "different"}),
    )
    assert committed.status is EffectStatus.COMMITTED
    assert duplicate == committed
    assert duplicate.outcome is not None
    assert dict(duplicate.outcome.value) == {"answer": "first"}

    await store.request_cancel(run_id, "user")
    with pytest.raises(StaleRunFence):
        await journal.commit(
            fence,
            begun.effect.effect_id,
            NormalizedToolOutcome.success({"answer": "late"}),
        )
    persisted = await journal.get(begun.effect.effect_id)
    assert persisted == committed


@pytest.mark.asyncio
async def test_session_tombstone_fences_late_delivery_and_outbox_retries_once(tmp_path):
    workflow_path = tmp_path / "workflow.db"
    # Production startup completes the one-time state reset before workflow
    # storage opens.  Creating the run first would correctly classify it as
    # pre-upgrade data and delete it.
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    store, run_id, _, _ = await _claimed_run(workflow_path, "delivery")
    await store.bind_session_refs(run_id, (("delivery", "session-late", 0),))
    outbox = WorkflowOutbox(store)
    event = await outbox.ensure_event(
        run_id=run_id,
        event_key="run:final",
        event_type="workflow.final",
        payload={"text": "late result"},
        deliveries=(("session_message", "session-late"),),
    )
    duplicate_event = await outbox.ensure_event(
        run_id=run_id,
        event_key="run:final",
        event_type="workflow.final",
        payload={"text": "late result"},
        deliveries=(("session_message", "session-late"),),
    )
    assert duplicate_event["event_id"] == event["event_id"]
    assert duplicate_event["seq"] == event["seq"] == 1
    assert duplicate_event["deliveries"] == event["deliveries"]
    attempts: list[int | None] = []

    async def deliver(envelope, delivery):
        result = await session_db.append_message_if_epoch(
            delivery["target_id"],
            "assistant",
            envelope["payload"]["text"],
            expected_epoch=0,
            workflow_event_id=envelope["event_id"],
        )
        attempts.append(result)

    service = WorkflowService(
        store,
        runner=object(),
        delivery_handlers={"session_message": deliver},
    )
    assert await session_db.tombstone_session("session-late", deleted_at=10.0) == 1

    first = await service.deliver_event_once(event["event_id"])
    second = await service.deliver_event_once(event["event_id"])

    assert attempts == [None]
    assert first["deliveries"][0]["status"] == "delivered"
    assert second["deliveries"][0]["status"] == "delivered"
    assert await session_db.get_messages("session-late") == []
    state = await session_db.get_session_delivery_state("session-late")
    assert state["epoch"] == 1 and state["deleted_at"] == 10.0


class _CheckpointingExecutable:
    def __init__(self, saver: FencedAsyncSqliteSaver) -> None:
        self.manifest = _manifest()
        self.saver = saver

    async def ainvoke(self, state, context, **kwargs):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = copy.deepcopy(state)
        checkpoint["channel_versions"] = {key: 1 for key in state}
        checkpoint["updated_channels"] = sorted(state)
        config = {
            "configurable": {
                **kwargs["configurable"],
                "thread_id": kwargs["thread_id"],
                "checkpoint_ns": kwargs["checkpoint_ns"],
                "deskpet_run_id": kwargs["run_id"],
            }
        }
        await self.saver.aput(
            config,
            checkpoint,
            {"source": "input", "step": 0, "parents": {}, "deskpet_node_id": "root"},
            {},
        )
        return state


@pytest.mark.asyncio
async def test_fork_changes_only_child_and_leaves_source_run_and_history_unchanged(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    saver = FencedAsyncSqliteSaver(path)
    registry = WorkflowRegistry()
    executable = _CheckpointingExecutable(saver)
    registry.register(_Workflow(executable.manifest), executable=executable)
    runner = WorkflowRunner(store, saver, registry, owner="fork-fault-runner")
    source_id = await runner.start(
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="fault-workflow",
        workflow_version="1",
        capability_snapshot={},
    )
    result = await runner.run(source_id, {"value": "source"}, WorkflowContext())
    assert result.status is WorkflowRunStatus.COMPLETED
    source_before = await store.get_run(source_id)
    history_before = await runner.get_state_history(source_id)
    assert source_before is not None and len(history_before) == 1

    child = await runner.fork_checkpoint(
        run_id=source_id,
        checkpoint_id=history_before[0]["checkpoint_id"],
        expected_version=source_before["run_version"],
        state_patch={"value": "child"},
    )

    source_after = await store.get_run(source_id)
    history_after = await runner.get_state_history(source_id)
    child_history = await runner.get_state_history(child["run_id"])
    assert source_after == source_before
    assert history_after == history_before
    assert history_after[0]["state"]["channel_values"]["value"] == "source"
    assert child["run_id"] != source_id
    assert child_history[0]["state"]["channel_values"]["value"] == "child"
