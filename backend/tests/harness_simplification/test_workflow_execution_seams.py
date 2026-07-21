from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass

import pytest

from deskpet.workflows.contracts import WorkflowContext, WorkflowRunStatus
from deskpet.workflows.definition import WorkflowManifest
from deskpet.workflows.execution_ports import WorkflowExecutionPorts
from deskpet.workflows.lease import transition_run
from deskpet.workflows.launcher import WorkflowLauncher
from deskpet.workflows.ipc import WorkflowIPCDispatcher
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunResult, WorkflowRunner
from deskpet.workflows.service import WorkflowService, WorkflowServiceError
from deskpet.workflows.store import NativeCheckpointStore, RunFence, WorkflowRunStore
from deskpet.workflows.store.checkpoint_execution import (
    SqliteCheckpointExecutionAdapter,
)
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.execution import (
    ActorContext,
    AttachmentPolicy,
    OutcomeStatus,
    RunEventCandidate,
    RunRef,
    RunStatus,
)
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.ports import DelegateRun, JoinPolicy


def _manifest(name: str, version: str) -> WorkflowManifest:
    suffix = f"{name}-{version}"
    return WorkflowManifest(
        workflow_name=name,
        workflow_version=version,
        state_schema_version=1,
        durability="sync",
        recursion_limit=32,
        max_supersteps=16,
        definition_hash=f"definition-{suffix}",
        state_hash=f"state-{suffix}",
        prompt_hash=f"prompt-{suffix}",
        tool_hash=f"tool-{suffix}",
        policy_hash=f"policy-{suffix}",
        callable_source_hash=f"callable-{suffix}",
        dependency_lock_hash=f"lock-{suffix}",
        implementation_bundle_hash=f"implementation-{suffix}",
    )


@dataclass
class _Workflow:
    manifest: WorkflowManifest


class _Executable:
    def __init__(self, manifest: WorkflowManifest) -> None:
        self.manifest = manifest

    async def ainvoke(self, state, context, **kwargs):
        return state

    async def resume(self, responses, context, **kwargs):
        return responses


class _CheckpointingExecutable(_Executable):
    def __init__(self, manifest: WorkflowManifest, saver: NativeCheckpointStore) -> None:
        super().__init__(manifest)
        self.saver = saver

    async def ainvoke(self, state, context, **kwargs):
        configurable = kwargs["configurable"]
        fence = RunFence(
            run_id=str(kwargs["run_id"]),
            owner=str(configurable["deskpet_lease_owner"]),
            lease_epoch=int(configurable["deskpet_lease_epoch"]),
            run_version=int(configurable["deskpet_run_version"]),
        )
        genesis = await self.saver.ensure_genesis(
            fence,
            str(kwargs["thread_id"]),
            state,
            [],
            operation_id=f"{kwargs['run_id']}:genesis",
        )
        await self.saver.commit_frontier(
            fence,
            str(genesis["checkpoint_id"]),
            state=state,
            frontier=[],
            step=1,
            operation_id=f"{kwargs['run_id']}:terminal",
            terminal_status="completed",
            intents=[
                {
                    "intent_id": f"{kwargs['run_id']}:run-final",
                    "event_key": "run:terminal",
                    "event_type": "workflow.final",
                    "channel": "final",
                    "payload": {"kind": "final", "status": "completed"},
                }
            ],
        )
        return state


def _stack(path, *, adapter=None):
    store = WorkflowRunStore(path)
    registry = WorkflowRegistry()
    for name, version in (
        *(("deep_research", f"v{number}") for number in range(1, 8)),
        ("ppt", "v1"), ("code", "v1"),
    ):
        manifest = _manifest(name, version)
        registry.register(_Workflow(manifest), executable=_Executable(manifest))
    checkpoint_adapter = adapter or SqliteCheckpointExecutionAdapter()
    uow = SqliteExecutionUnitOfWork(path)
    ports = WorkflowExecutionPorts(unit_of_work=uow, checkpoint=checkpoint_adapter)
    saver = NativeCheckpointStore(path, execution_adapter=checkpoint_adapter)
    runner = WorkflowRunner(
        store,
        saver,
        registry,
        owner="generic-workflow-test",
        execution_ports=ports,
    )
    service = WorkflowService(store, runner, execution_ports=ports)
    return service, runner, store, saver, ports


def _prepared(service: WorkflowService, name: str, version: str, *, suffix: str = "1"):
    return service.prepare_start(
        venue="agent-loop",
        base_session_id=f"session-{suffix}",
        delivery_session_id=f"session-{suffix}",
        request_id=f"request-{suffix}",
        turn_id=f"turn-{suffix}",
        workflow_name=name,
        workflow_version=version,
        capability_snapshot={"tools": ["read_file"]},
        start_payload={"topic": f"topic-{suffix}"},
        delivery_targets=(
            ("session_message", f"session-{suffix}"),
            ("websocket", f"session-{suffix}"),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "version"),
    (("deep_research", "v7"), ("ppt", "v1"), ("code", "v1")),
)
async def test_product_profiles_prepare_start_restart_and_cancel_without_legacy_owner_writes(
    tmp_path, name, version
):
    path = tmp_path / f"{name}.db"
    service, runner, _, _, ports = _stack(path)

    prepared = _prepared(service, name, version)
    assert not path.exists(), "prepare_start must not perform database I/O"
    spec = service.execution_spec(prepared, principal_id="principal")
    assert spec.profile_key == f"{name}/{version}"

    first = await service.start_prepared(prepared, spec, execution_ports=ports)
    restarted = await service.start_prepared(prepared, spec, execution_ports=ports)
    assert first["run_id"] == prepared.run_id == restarted["run_id"]
    assert first["created"] is True
    assert restarted["created"] is False

    cancelled = await runner.request_cancel_precreated(prepared.run_id, "test-cancel")
    assert cancelled["status"] == "cancel_requested"

    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT run_id,profile_key,status FROM execution_runs"
        ).fetchone() == (prepared.run_id, f"{name}/{version}", "cancel_requested")
        assert db.execute(
            "SELECT run_id,status FROM workflow_runs"
        ).fetchone() == (prepared.run_id, "cancel_requested")
        assert db.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_deliveries").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_decisions").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_checkpoint_adapter_uses_current_transaction_and_only_generic_owner_tables(
    tmp_path,
):
    path = tmp_path / "checkpoint-generic.db"
    service, _, store, saver, ports = _stack(path)
    prepared = _prepared(service, "deep_research", "v7")
    await service.start_prepared(
        prepared,
        service.execution_spec(prepared),
        execution_ports=ports,
    )
    fence = await store.claim(prepared.run_id, "generic-workflow-test")
    task = {
        "task_id": "task-1",
        "activation_id": "activation-1",
        "node_id": "node-1",
    }
    state = {
        "schema_version": 1,
        "workflow_name": "deep_research",
        "workflow_version": "v7",
        "thread_id": prepared.thread_id,
        "run_id": prepared.run_id,
        "session_id": prepared.identity.base_session_id,
        "values": {},
    }
    genesis = await saver.ensure_genesis(
        fence, prepared.run_id, state, [task], operation_id="generic-genesis"
    )
    interrupted = await saver.commit_interrupt(
        fence,
        genesis["checkpoint_id"],
        task,
        state=state,
        frontier=[task],
        step=0,
        interrupt_id="interrupt-1",
        kind="clarification",
        prompt={"question": "continue?"},
        operation_id="generic-interrupt",
    )
    assert interrupted is not None
    waiting_row = await store.get_run(prepared.run_id)
    assert waiting_row is not None and waiting_row["status"] == "waiting"

    db = sqlite3.connect(path)
    try:
        decision_id = str(interrupted["decision_id"])
        db.execute(
            """UPDATE execution_decisions SET status='allowed',response_schema_version=1,
            response_json='{"answer":"yes"}',resolved_at=1,decision_version=1
            WHERE decision_id=?""",
            (decision_id,),
        )
        db.execute(
            """INSERT INTO execution_effects(
            effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,args_hash,
            capability_hash,scope_hash,effect_type,status,policy_json,prepared_json,
            outcome_json,artifact_refs_json,effect_version,created_at,updated_at,ended_at
            ) VALUES('effect-1',1,?,'fingerprint-1','call-1','read_file',?,?,?,
            'read','succeeded','{}','{}','{}','[]',1,1,1,1)""",
            (prepared.run_id, "a" * 64, prepared.capability_hash, "b" * 64),
        )
        db.commit()
    finally:
        db.close()

    await transition_run(
        store,
        prepared.run_id,
        WorkflowRunStatus.RETRYABLE,
        expected_version=int(waiting_row["run_version"]),
        allowed_statuses=(WorkflowRunStatus.WAITING,),
        recovery_action="resume",
    )
    fence = await store.claim(prepared.run_id, "generic-workflow-test")

    await saver.commit_task_result(
        fence,
        genesis["checkpoint_id"],
        task,
        1,
        {"values": {"answer": "done"}},
        operation_id="generic-task-result",
    )
    completed_state = {**state, "values": {"answer": "done"}}
    committed = await saver.commit_frontier(
        fence,
        genesis["checkpoint_id"],
        state=completed_state,
        frontier=[],
        step=1,
        operation_id="generic-frontier",
        decision_ids=[{"decision_id": decision_id, "expected_version": 1}],
        effect_links=[{"effect_id": "effect-1", "node_execution_id": "node-exec-1"}],
        terminal_status="completed",
        intents=[
            {
                "intent_id": f"{prepared.run_id}:run-final",
                "event_key": "run:terminal",
                "event_type": "workflow.final",
                "channel": "final",
                "payload": {"kind": "final", "status": "completed"},
            }
        ],
    )
    assert committed["consumed_decision_ids"] == [decision_id]

    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status,terminal_event_id FROM execution_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone()[0] == "completed"
        assert db.execute(
            "SELECT consumed_checkpoint_id FROM execution_decisions WHERE decision_id=?",
            (decision_id,),
        ).fetchone()[0] == committed["checkpoint_id"]
        assert db.execute("SELECT COUNT(*) FROM execution_effect_links").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM workflow_decisions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_deliveries").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_checkpoint_effects").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_runner_run_precreated_requires_checkpoint_owned_terminal_and_restarts_cleanly(
    tmp_path,
):
    path = tmp_path / "runner-precreated.db"
    store = WorkflowRunStore(path)
    adapter = SqliteCheckpointExecutionAdapter()
    ports = WorkflowExecutionPorts(
        unit_of_work=SqliteExecutionUnitOfWork(path), checkpoint=adapter
    )
    saver = NativeCheckpointStore(path, execution_adapter=adapter)
    manifest = _manifest("code", "v1")
    executable = _CheckpointingExecutable(manifest, saver)
    registry = WorkflowRegistry()
    registry.register(_Workflow(manifest), executable=executable)
    runner = WorkflowRunner(
        store,
        saver,
        registry,
        owner="generic-runner",
        execution_ports=ports,
    )
    service = WorkflowService(store, runner, execution_ports=ports)
    prepared = _prepared(service, "code", "v1", suffix="runner")
    await service.start_prepared(
        prepared, service.execution_spec(prepared), execution_ports=ports
    )
    state = {
        "schema_version": 1,
        "workflow_name": "code",
        "workflow_version": "v1",
        "thread_id": prepared.thread_id,
        "run_id": prepared.run_id,
        "session_id": prepared.identity.base_session_id,
        "values": {},
    }

    result = await runner.run_precreated(prepared.run_id, state, WorkflowContext())
    replay = await runner.run_precreated(prepared.run_id, state, WorkflowContext())
    assert result.status is WorkflowRunStatus.COMPLETED
    assert replay.status is WorkflowRunStatus.COMPLETED

    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "completed"
        assert db.execute(
            "SELECT status FROM execution_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "completed"
        assert db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_precreated_launch_failure_finalizes_generic_and_workflow_atomically(tmp_path):
    path = tmp_path / "launch-failure.db"
    service, _, _, saver, ports = _stack(path)
    prepared = _prepared(service, "ppt", "v1", suffix="launch-failure")
    await service.start_prepared(
        prepared, service.execution_spec(prepared), execution_ports=ports
    )

    result = await saver.commit_launch_failure(
        prepared.run_id,
        error={"code": "state_factory_failed"},
        recovery_action="inspect_or_cancel",
    )
    replay = await saver.commit_launch_failure(
        prepared.run_id,
        error={"code": "state_factory_failed"},
        recovery_action="inspect_or_cancel",
    )
    assert replay == result

    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "failed"
        execution = db.execute(
            "SELECT status,terminal_event_id FROM execution_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone()
        assert execution[0] == "failed" and execution[1] == result["event_ids"][0]
        assert db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_precreated_node_failure_uses_generic_terminal_projection(tmp_path):
    path = tmp_path / "node-failure.db"
    service, _, store, saver, ports = _stack(path)
    prepared = _prepared(service, "deep_research", "v7", suffix="node-failure")
    await service.start_prepared(
        prepared, service.execution_spec(prepared), execution_ports=ports
    )
    fence = await store.claim(prepared.run_id, "generic-workflow-test")
    state = {
        "schema_version": 1,
        "workflow_name": "deep_research",
        "workflow_version": "v7",
        "thread_id": prepared.thread_id,
        "run_id": prepared.run_id,
        "session_id": prepared.identity.base_session_id,
        "values": {},
    }
    task = {"task_id": "task-fail", "activation_id": "fail", "node_id": "node-fail"}
    genesis = await saver.ensure_genesis(
        fence, prepared.run_id, state, [task], operation_id="failure-genesis"
    )
    result = await saver.commit_failure(
        fence,
        genesis["checkpoint_id"],
        task,
        error={"code": "node_failed"},
        operation_id="generic-node-failure",
    )
    assert result is not None and result["status"] == "failed"

    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status FROM execution_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "failed"
        assert db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 0
        assert db.execute(
            "SELECT status FROM execution_events WHERE event_id=?",
            (result["event_ids"][0],),
        ).fetchone()[0] == "failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_generic_final_fault_rolls_back_checkpoint_workflow_and_execution(tmp_path):
    path = tmp_path / "checkpoint-fault.db"

    def fail_at_final(stage: str) -> None:
        if stage == "generic_final.after_write":
            raise RuntimeError("injected-final-fault")

    adapter = SqliteCheckpointExecutionAdapter(fault_injector=fail_at_final)
    service, _, store, saver, ports = _stack(path, adapter=adapter)
    prepared = _prepared(service, "ppt", "v1", suffix="fault")
    await service.start_prepared(
        prepared,
        service.execution_spec(prepared),
        execution_ports=ports,
    )
    fence = await store.claim(prepared.run_id, "generic-workflow-test")
    state = {
        "schema_version": 1,
        "workflow_name": "ppt",
        "workflow_version": "v1",
        "thread_id": prepared.thread_id,
        "run_id": prepared.run_id,
        "session_id": prepared.identity.base_session_id,
        "values": {},
    }
    genesis = await saver.ensure_genesis(
        fence, prepared.run_id, state, [], operation_id="fault-genesis"
    )

    with pytest.raises(RuntimeError, match="injected-final-fault"):
        await saver.commit_frontier(
            fence,
            genesis["checkpoint_id"],
            state=state,
            frontier=[],
            step=1,
            operation_id="fault-frontier",
            terminal_status="completed",
        )

    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status,head_checkpoint_id FROM workflow_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone() == ("running", genesis["checkpoint_id"])
        execution = db.execute(
            "SELECT status,terminal_event_id FROM execution_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone()
        assert execution == ("created", None)
        assert db.execute(
            "SELECT COUNT(*) FROM execution_events WHERE event_key='run:terminal'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM workflow_operations WHERE operation_id='fault-frontier'"
        ).fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execution_row_without_adapter_fails_closed_instead_of_legacy_fallback(tmp_path):
    path = tmp_path / "fail-closed.db"
    service, _, store, _, ports = _stack(path)
    prepared = _prepared(service, "code", "v1")
    await service.start_prepared(
        prepared,
        service.execution_spec(prepared),
        execution_ports=ports,
    )
    fence = await store.claim(prepared.run_id, "generic-workflow-test")
    saver = NativeCheckpointStore(path)
    state = {
        "schema_version": 1,
        "workflow_name": "code",
        "workflow_version": "v1",
        "thread_id": prepared.thread_id,
        "run_id": prepared.run_id,
        "session_id": prepared.identity.base_session_id,
        "values": {},
    }
    genesis = await saver.ensure_genesis(
        fence, prepared.run_id, state, [], operation_id="closed-genesis"
    )
    with pytest.raises(Exception) as exc:
        await saver.commit_frontier(
            fence,
            genesis["checkpoint_id"],
            state=state,
            frontier=[],
            step=1,
            operation_id="closed-frontier",
            intents=[
                {
                    "intent_id": "progress",
                    "event_type": "workflow.progress",
                    "payload": {"kind": "progress"},
                }
            ],
        )
    assert getattr(exc.value, "code", None) == "execution_adapter_required"

    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0] == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_historical_v1_v7_without_execution_rows_remain_recoverable(tmp_path):
    path = tmp_path / "legacy-v1-v7.db"
    service, runner, store, _, _ = _stack(path)
    launcher = WorkflowLauncher(service)
    calls: list[str] = []

    async def legacy_run(run_id, state, context):
        calls.append(run_id)
        return WorkflowRunResult(run_id, WorkflowRunStatus.COMPLETED, state)

    runner.run = legacy_run
    expected: list[str] = []
    for number in range(1, 8):
        version = f"v{number}"
        prepared = _prepared(service, "deep_research", version, suffix=version)
        run_id = await runner.start(
            session_id=prepared.identity.base_session_id,
            request_id=prepared.identity.request_id,
            turn_id=prepared.identity.turn_id,
            workflow_name="deep_research",
            workflow_version=version,
            capability_snapshot=prepared.capability_snapshot,
            request_key=prepared.identity.identity_key,
            run_id=prepared.run_id,
        )
        await store.bind_session_refs(run_id, service._session_refs(prepared.identity))
        launcher.register_adapter(
            "deep_research", version,
            state_factory=lambda **kwargs: kwargs,
            context_factory=WorkflowContext,
        )
        expected.append(run_id)

    recovered = await launcher.recover_pending()
    assert set(recovered) == set(expected)
    await asyncio.gather(*tuple(launcher._tasks))
    assert set(calls) == set(recovered)
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execution_owned_accepted_run_recovers_only_through_generic_path(tmp_path):
    path = tmp_path / "accepted-before-checkpoint.db"
    service, runner, _, _, ports = _stack(path)
    prepared = _prepared(service, "code", "v1", suffix="accepted")
    await service.start_prepared(
        prepared, service.execution_spec(prepared), execution_ports=ports
    )
    launcher = WorkflowLauncher(service)
    launcher.register_adapter(
        "code", "v1", state_factory=lambda **kwargs: kwargs,
        context_factory=WorkflowContext,
    )
    generic: list[str] = []

    async def reject_legacy(*args, **kwargs):
        pytest.fail("execution-owned accepted run downgraded to legacy run")

    async def generic_run(run_id, state, context, **kwargs):
        generic.append(run_id)
        return WorkflowRunResult(run_id, WorkflowRunStatus.COMPLETED, state)

    runner.run = reject_legacy
    runner.run_precreated = generic_run
    assert await launcher.dispatch_due_work() == [prepared.run_id]
    await asyncio.gather(*tuple(launcher._tasks))
    assert generic == [prepared.run_id]
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status,head_checkpoint_id FROM workflow_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone() == ("created", None)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execution_owned_run_without_generic_ports_fails_closed(tmp_path):
    path = tmp_path / "accepted-fail-closed.db"
    service, runner, _, _, ports = _stack(path)
    prepared = _prepared(service, "code", "v1", suffix="closed")
    await service.start_prepared(
        prepared, service.execution_spec(prepared), execution_ports=ports
    )
    service.execution_ports = None
    launcher = WorkflowLauncher(service)
    runner.run = lambda *args, **kwargs: pytest.fail("legacy recovery was invoked")

    assert await launcher.dispatch_due_work() == []
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status,head_checkpoint_id FROM workflow_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone() == ("created", None)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_legacy_delivery_scan_skips_execution_row_and_continues_batch(tmp_path):
    path = tmp_path / "mixed-delivery.db"
    service, _, store, _, ports = _stack(path)
    owned = _prepared(service, "code", "v1", suffix="owned-delivery")
    await service.start_prepared(owned, service.execution_spec(owned), execution_ports=ports)
    legacy_id = await service.runner.start(
        session_id="legacy-session", request_id="legacy-request", turn_id="legacy-turn",
        workflow_name="code", workflow_version="v1",
        capability_snapshot={"_workflow_start": {"start_payload": {}}},
        request_key="legacy-delivery", run_id="legacy-delivery-run",
    )
    owned_event = await service.outbox.ensure_event(
        run_id=owned.run_id, event_key="legacy-shadow", event_type="workflow.progress",
        payload={"kind": "progress"}, deliveries=(("websocket", "owned"),),
    )
    owned_claimed = await service.outbox.ensure_event(
        run_id=owned.run_id, event_key="legacy-shadow-claimed", event_type="workflow.progress",
        payload={"kind": "progress"}, deliveries=(("websocket", "owned-claimed"),),
    )
    claimed_delivery = owned_claimed["deliveries"][0]
    await service.outbox.mutate_delivery(
        claimed_delivery["delivery_id"], action="begin",
        expected_version=claimed_delivery["version"],
    )
    owned_failed = await service.outbox.ensure_event(
        run_id=owned.run_id, event_key="legacy-shadow-failed", event_type="workflow.progress",
        payload={"kind": "progress"}, deliveries=(("websocket", "owned-failed"),),
    )
    failed_delivery = owned_failed["deliveries"][0]
    begun = await service.outbox.mutate_delivery(
        failed_delivery["delivery_id"], action="begin",
        expected_version=failed_delivery["version"],
    )
    await service.outbox.mutate_delivery(
        failed_delivery["delivery_id"], action="failed",
        expected_version=begun["delivery"]["version"], reason="fixture",
    )
    legacy_event = await service.outbox.ensure_event(
        run_id=legacy_id, event_key="legacy-real", event_type="workflow.progress",
        payload={"kind": "progress"}, deliveries=(("websocket", "legacy"),),
    )
    delivered: list[str] = []

    async def handler(event, delivery):
        delivered.append(str(event["event_id"]))

    service._delivery_handlers["websocket"] = handler
    launcher = WorkflowLauncher(service)
    assert await launcher.recover_due_deliveries(
        recover_claimed=True
    ) == [legacy_event["event_id"]]
    assert delivered == [legacy_event["event_id"]]
    assert (await service.outbox.get_delivery(
        owned_event["deliveries"][0]["delivery_id"]
    ))["status"] == "pending"
    assert (await service.outbox.get_delivery(
        claimed_delivery["delivery_id"]
    ))["status"] == "delivering"
    assert (await service.outbox.get_delivery(
        failed_delivery["delivery_id"]
    ))["status"] == "failed"
    with pytest.raises(WorkflowServiceError) as exc:
        await service.deliver_event_once(owned_event["event_id"])
    assert exc.value.code == "execution_owner"
    for operation in (service.retry_delivery, service.discard_delivery):
        with pytest.raises(WorkflowServiceError) as delivery_exc:
            await operation(
                owned_event["deliveries"][0]["delivery_id"], expected_version=0
            )
        assert delivery_exc.value.code == "execution_owner"


@pytest.mark.asyncio
async def test_ipc_cancel_routes_execution_owner_to_canonical_cancel(tmp_path):
    path = tmp_path / "ipc-cancel-owner.db"
    service, _, _, _, ports = _stack(path)
    prepared = _prepared(service, "code", "v1", suffix="ipc-cancel")
    spec = service.execution_spec(prepared)
    await service.start_prepared(prepared, spec, execution_ports=ports)

    request = {
        "type": "workflow_run_cancel", "request_id": "cancel-1",
        "payload": {"run_id": prepared.run_id, "reason": "ipc-user"},
    }
    missing = await WorkflowIPCDispatcher(service).dispatch(request)
    assert missing["error"]["code"] == "actor_required"
    for actor in (
        ActorContext(spec.context.principal_id, "other-session", 0, prepared.run_id),
        ActorContext(spec.context.principal_id, spec.context.session_id, 0, "other-root"),
    ):
        denied = await WorkflowIPCDispatcher(service, actor).dispatch(request)
        assert denied["error"]["code"] == "actor_not_authorized"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT status FROM execution_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "created"
        assert db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "created"
    actor = ActorContext(
        spec.context.principal_id,
        spec.context.session_id,
        spec.context.auth_epoch,
        spec.context.root_run_id,
    )
    response = await WorkflowIPCDispatcher(service, actor).dispatch(request)
    assert response["ok"] is True
    assert response["payload"]["status"] == "cancel_requested"
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status,cancel_reason FROM execution_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone() == ("cancel_requested", "ipc-user")
        assert db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "cancel_requested"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_execution_owned_continue_research_cannot_create_legacy_child(tmp_path):
    path = tmp_path / "owned-action.db"
    service, _, _, _, ports = _stack(path)
    prepared = _prepared(service, "deep_research", "v5", suffix="owned-action")
    await service.start_prepared(
        prepared, service.execution_spec(prepared), execution_ports=ports
    )

    with pytest.raises(WorkflowServiceError) as exc:
        await service.execute_run_action(
            prepared.run_id,
            action_id="continue_research",
            idempotency_key="continue-owned",
            expected_version=0,
        )
    assert exc.value.code == "execution_owner"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM workflow_runs WHERE parent_run_id IS NOT NULL"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_ipc_cancel_without_actor_remains_compatible_for_legacy_run(tmp_path):
    path = tmp_path / "legacy-ipc-cancel.db"
    service, _, store, _, _ = _stack(path)
    run_id, _ = await store.create_run(
        request_key="legacy-cancel",
        session_id="legacy-session",
        request_id="legacy-request",
        turn_id="legacy-turn",
        workflow_name="code",
        workflow_version="v1",
        manifest_hash="definition-code-v1",
        implementation_hash="implementation-code-v1",
        capability_hash="legacy-capabilities",
        capability_snapshot={"tools": []},
        state_schema_version=1,
    )

    response = await WorkflowIPCDispatcher(service).dispatch({
        "type": "workflow_run_cancel",
        "request_id": "legacy-cancel",
        "payload": {"run_id": run_id, "reason": "legacy-user"},
    })
    assert response["ok"] is True
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
        assert db.execute(
            "SELECT status FROM workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone()[0] in {"cancel_requested", "cancelled"}


@pytest.mark.asyncio
async def test_execution_owner_rejects_service_and_runner_legacy_resume_bypasses(tmp_path):
    path = tmp_path / "resume-owner-bypasses.db"
    service, runner, _, _, ports = _stack(path)
    prepared = _prepared(service, "code", "v1", suffix="resume-bypass")
    await service.start_prepared(
        prepared, service.execution_spec(prepared), execution_ports=ports
    )
    state = {"run_id": prepared.run_id, "values": {}}

    with pytest.raises(WorkflowServiceError) as service_error:
        await service.resume_run(prepared.run_id, {"interrupt": "approved"}, trusted=True)
    assert service_error.value.code == "execution_owner"
    with pytest.raises(RuntimeError, match="execution-owned"):
        await runner.run(prepared.run_id, state, WorkflowContext())
    with pytest.raises(RuntimeError, match="execution-owned"):
        await runner.resume(
            prepared.run_id, {"interrupt": "approved"}, WorkflowContext()
        )
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT status FROM execution_runs WHERE run_id=?", (prepared.run_id,)
        ).fetchone()[0] == "created"
        assert db.execute(
            "SELECT status,head_checkpoint_id FROM workflow_runs WHERE run_id=?",
            (prepared.run_id,),
        ).fetchone() == ("created", None)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_child_signal_ack_is_atomic_with_native_checkpoint_and_restart_replay(tmp_path):
    path = tmp_path / "workflow-child-atomic.db"
    fail_inside_commit = True

    def checkpoint_fault(stage: str) -> None:
        nonlocal fail_inside_commit
        if stage == "generic_decisions.after_write" and fail_inside_commit:
            raise RuntimeError("fault inside child checkpoint commit")

    adapter = SqliteCheckpointExecutionAdapter(fault_injector=checkpoint_fault)
    service, _, store, saver, ports = _stack(path, adapter=adapter)
    prepared = _prepared(service, "code", "v1", suffix="child-atomic")
    spec = service.execution_spec(prepared, principal_id="principal")
    await service.start_prepared(prepared, spec, execution_ports=ports)
    uow = ports.unit_of_work
    actor = ActorContext(
        principal_id=spec.context.principal_id,
        session_id=spec.context.session_id,
        auth_epoch=spec.context.auth_epoch,
        root_run_id=spec.context.root_run_id,
    )
    parent = await uow.query(RunRef(prepared.run_id, spec.context.session_id), actor)
    coordinator = ChildRunCoordinator(uow)
    command = await coordinator.submit(parent, DelegateRun(
        run_id=prepared.run_id, command_id="command-1",
        child_request={"task": "child", "driver_kind": "react"},
        route_hint="react.default", capability_subset=(),
        attachment_policy=AttachmentPolicy.ATTACHED,
        join_policy=JoinPolicy.JOIN_BEFORE_FINAL,
    ))
    leased = (await uow.lease_child_commands(
        owner="child-owner", limit=1, lease_seconds=30
    ))[0]
    scheduled = await uow.schedule_child_command(
        command.operation_id, lease_owner="child-owner",
        lease_epoch=leased.schedule_lease_epoch,
    )
    await uow.acknowledge_child_command(
        command.operation_id, lease_owner="child-owner",
        lease_epoch=scheduled.schedule_lease_epoch,
    )
    accepted_signal = (await uow.list_pending_child_signals(prepared.run_id))[0]

    state = {
        "schema_version": 1, "workflow_name": "code", "workflow_version": "v1",
        "thread_id": prepared.thread_id, "run_id": prepared.run_id,
        "session_id": prepared.identity.base_session_id, "values": {},
    }
    task_a = {
        "task_id": "task-a", "activation_id": "a", "node_id": "wait-child",
        "invocation_key": "wait-child:a",
    }
    fence = await store.claim(prepared.run_id, "generic-workflow-test")
    genesis = await saver.ensure_genesis(
        fence, prepared.run_id, state, [task_a], operation_id="child-genesis"
    )
    accepted_interrupt = await saver.commit_interrupt(
        fence, genesis["checkpoint_id"], task_a, state=state, frontier=[task_a],
        step=0, interrupt_id="accepted-interrupt", kind="workflow_hitl",
        prompt={"kind": "child_run", "command_id": "command-1"},
        operation_id="accepted-interrupt-op",
    )
    lease = await uow.claim_recovery(prepared.run_id, owner="signal-owner")
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE execution_decisions SET prompt_json=? WHERE decision_id=?",
            ('{"kind":"child_run","command_id":"wrong-command"}',
             accepted_interrupt["decision_id"]),
        )
        db.commit()
    with pytest.raises(Exception) as wrong_command:
        await uow.prepare_workflow_child_resume(
            accepted_signal.signal_id, recovery_lease=lease
        )
    assert getattr(wrong_command.value, "code", None) == "workflow_child_interrupt_not_found"
    assert (await uow.list_pending_child_signals(prepared.run_id))[0].delivered_at is None
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE execution_decisions SET prompt_json=? WHERE decision_id=?",
            ('{"kind":"child_run","command_id":"command-1"}',
             accepted_interrupt["decision_id"]),
        )
        db.commit()
    accepted_decision = await uow.prepare_workflow_child_resume(
        accepted_signal.signal_id, recovery_lease=lease
    )
    assert accepted_decision.request.nonce == "accepted-interrupt"
    waiting = await store.get_run(prepared.run_id)
    await transition_run(
        store, prepared.run_id, WorkflowRunStatus.RETRYABLE,
        expected_version=int(waiting["run_version"]),
        allowed_statuses=(WorkflowRunStatus.WAITING,), recovery_action="resume",
    )
    fence = await store.claim(prepared.run_id, "generic-workflow-test")
    task_crash_saver = NativeCheckpointStore(
        path, execution_adapter=adapter,
        fault_injector=lambda stage: (
            (_ for _ in ()).throw(RuntimeError("crash after task result commit"))
            if stage == "task_result.after_db_commit_before_return" else None
        ),
    )
    with pytest.raises(RuntimeError, match="after task result commit"):
        await task_crash_saver.commit_task_result(
            fence, genesis["checkpoint_id"], task_a, 1,
            {"values": {"accepted": True}}, operation_id="accepted-result",
            consumed_interrupt_ids=(accepted_decision.request.nonce,),
        )
    restarted_execution = await saver.load_execution(
        run_id=prepared.run_id, thread_id=prepared.thread_id, checkpoint_ns=""
    )
    assert restarted_execution.pending_consumed_interrupt_ids == ("accepted-interrupt",)
    task_b = {
        "task_id": "task-b", "activation_id": "b", "node_id": "wait-terminal",
        "invocation_key": "wait-terminal:b",
    }
    with pytest.raises(RuntimeError, match="inside child checkpoint"):
        await saver.commit_frontier(
            fence, genesis["checkpoint_id"], state={**state, "values": {"accepted": True}},
            frontier=[task_b], step=1, operation_id="accepted-frontier",
            consumed_interrupt_ids=restarted_execution.pending_consumed_interrupt_ids,
        )
    assert (await uow.list_pending_child_signals(prepared.run_id))[0].delivered_at is None
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT consumed_checkpoint_id FROM execution_decisions WHERE decision_id=?",
            (accepted_interrupt["decision_id"],),
        ).fetchone() == (None,)
    fail_inside_commit = False
    accepted_commit = await saver.commit_frontier(
        fence, genesis["checkpoint_id"], state={**state, "values": {"accepted": True}},
        frontier=[task_b], step=1, operation_id="accepted-frontier",
        consumed_interrupt_ids=restarted_execution.pending_consumed_interrupt_ids,
    )
    assert await uow.list_pending_child_signals(prepared.run_id) == ()

    terminal_interrupt = await saver.commit_interrupt(
        fence, accepted_commit["checkpoint_id"], task_b,
        state={**state, "values": {"accepted": True}}, frontier=[task_b], step=1,
        interrupt_id="terminal-interrupt", kind="workflow_hitl",
        prompt={"kind": "child_run", "command_id": "command-1"},
        operation_id="terminal-interrupt-op",
    )
    child = await uow.query(RunRef(command.child_run_id, spec.context.session_id), actor)
    await uow.finalize_child_and_enqueue_parent_signal(
        command.operation_id, expected_version=child.version,
        terminal_status=RunStatus.COMPLETED,
        event=RunEventCandidate(
            event_key="child-final", kind="run.final",
            status=OutcomeStatus.SUCCEEDED, driver_kind="react",
        ), value={"answer": 42},
    )
    terminal_signal = (await uow.list_pending_child_signals(prepared.run_id))[0]
    terminal_decision = await uow.prepare_workflow_child_resume(
        terminal_signal.signal_id, recovery_lease=lease
    )
    assert terminal_signal.signal_id != accepted_signal.signal_id
    assert terminal_decision.request.nonce == "terminal-interrupt"
    assert terminal_decision.request.nonce != accepted_decision.request.nonce
    waiting = await store.get_run(prepared.run_id)
    await transition_run(
        store, prepared.run_id, WorkflowRunStatus.RETRYABLE,
        expected_version=int(waiting["run_version"]),
        allowed_statuses=(WorkflowRunStatus.WAITING,), recovery_action="resume",
    )
    fence = await store.claim(prepared.run_id, "generic-workflow-test")
    await saver.commit_task_result(
        fence, accepted_commit["checkpoint_id"], task_b, 1,
        {"values": {"done": True}}, operation_id="terminal-result",
        consumed_interrupt_ids=(terminal_decision.request.nonce,),
    )
    restarted_terminal = await saver.load_execution(
        run_id=prepared.run_id, thread_id=prepared.thread_id, checkpoint_ns=""
    )
    after_commit_saver = NativeCheckpointStore(
        path, execution_adapter=adapter,
        fault_injector=lambda stage: (
            (_ for _ in ()).throw(RuntimeError("crash after checkpoint commit"))
            if stage == "frontier.after_db_commit_before_return" else None
        ),
    )
    with pytest.raises(RuntimeError, match="after checkpoint commit"):
        await after_commit_saver.commit_frontier(
            fence, accepted_commit["checkpoint_id"],
            state={**state, "values": {"done": True}}, frontier=[], step=2,
            operation_id="terminal-frontier", terminal_status="completed",
            consumed_interrupt_ids=restarted_terminal.pending_consumed_interrupt_ids,
        )
    replay = await saver.commit_frontier(
        fence, accepted_commit["checkpoint_id"],
        state={**state, "values": {"done": True}}, frontier=[], step=2,
        operation_id="terminal-frontier", terminal_status="completed",
        consumed_interrupt_ids=restarted_terminal.pending_consumed_interrupt_ids,
    )
    assert replay["consumed_decision_ids"] == [terminal_interrupt["decision_id"]]
    assert await uow.list_pending_child_signals(prepared.run_id) == ()
