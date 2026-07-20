from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from deskpet.workflows.contracts import WorkflowContext, WorkflowRunStatus
from deskpet.workflows.definition import WorkflowManifest
from deskpet.workflows.execution_ports import WorkflowExecutionPorts
from deskpet.workflows.lease import transition_run
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService
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
        ("deep_research", "v7"),
        ("ppt", "v1"),
        ("code", "v1"),
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
