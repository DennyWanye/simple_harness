from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import aiosqlite
import pytest

from deskpet.agent.team.team_store import FAULT_HOOKS as TEAM_FAULT_HOOKS
from deskpet.agent.team.team_store import TeamStore
from deskpet.execution.contracts import (
    ActorContext,
    AttachmentPolicy,
    ChildCommandIntent,
    DecisionKind,
    DecisionOpen,
    DecisionSignal,
    DeliveryPolicy,
    DeliverySpec,
    GrantConsume,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    RunStatus,
    fingerprint_json,
    stable_decision_grant_id,
)
from deskpet.workflows.store.execution_uow import ATOMIC_OPERATIONS
from deskpet.workflows.store.execution_uow import FAULT_HOOKS as UOW_FAULT_HOOKS
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.harness.ports import ToolOutcomesSignal
from deskpet.harness.adapters.team import TeamChildRunReconciler
from deskpet.workflows.effects import NormalizedToolOutcome


MATRIX_PATH = Path(__file__).with_name("fault_matrix.json")
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
COUNT_KEYS = {
    "run",
    "boundary",
    "decision",
    "effect",
    "attempt",
    "child",
    "link",
    "inbox",
    "event",
    "delivery",
    "external_write",
}
TEAM_COUNT_KEYS = {"team_task", "team_command", "team_terminal_inbox"}
CAPABILITY_HASH = fingerprint_json({"tools": ["read", "write", "delegate"]})
ARGS_HASH = fingerprint_json({"path": "report.md"})
SCOPE_HASH = fingerprint_json({"root": "F:/workspace"})


def _spec(run_id: str, *, parent_run_id: str | None = None) -> RunCreate:
    root_run_id = parent_run_id or run_id
    payload = {"run_id": run_id}
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:session:request:{run_id}",
        context=RunContext(
            session_id="session",
            root_run_id=root_run_id,
            parent_run_id=parent_run_id,
            request_id=f"request:{run_id}",
            turn_id=f"turn:{run_id}",
            venue="text",
            workspace={"root": "F:/workspace"},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id=f"trace:{run_id}",
            principal_id="principal",
            auth_epoch=7,
        ),
        payload_fingerprint=fingerprint_json(payload),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react_short",
        persistence_level=PersistenceLevel.DURABLE,
    )


def _actor(root_run_id: str | None = None) -> ActorContext:
    return ActorContext(principal_id="principal", session_id="session", auth_epoch=7, root_run_id=root_run_id)


def _team_intent(operation_id, task) -> ChildCommandIntent:
    child_request = {"task": task.description, "driver_kind": "react"}
    spec = replace(
        _spec("child-run", parent_run_id="team-parent"),
        idempotency_key=operation_id,
        payload_fingerprint=fingerprint_json(child_request),
    )
    return ChildCommandIntent(
        operation_id=operation_id,
        parent_run_id="team-parent",
        command_id=operation_id,
        child_spec=spec,
        child_request=child_request,
        capability_subset=("read",),
        attachment_policy=AttachmentPolicy.DETACHED,
        capability_snapshot_ref=CAPABILITY_HASH,
    )


def _decision(run_id: str, *, permission: bool = False) -> DecisionOpen:
    common = dict(
        decision_id=f"decision:{run_id}",
        run_id=run_id,
        nonce=f"nonce:{run_id}",
        prompt_schema_version=1,
        prompt={"question": "continue?"},
    )
    if not permission:
        return DecisionOpen(kind=DecisionKind.CLARIFICATION, expires_at=None, **common)
    return DecisionOpen(
        kind=DecisionKind.PERMISSION,
        expires_at=200.0,
        call_id="call-1",
        effect_id="effect-1",
        tool_name="write_file",
        args_hash=ARGS_HASH,
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        **common,
    )


def _signal(request: DecisionOpen) -> DecisionSignal:
    return DecisionSignal(
        decision_id=request.decision_id,
        run_id=request.run_id,
        expected_session_id="session",
        nonce=request.nonce,
        expected_version=0,
        allow=True,
        response_schema_version=1,
        response={"allow": True},
        call_id=request.call_id,
        effect_id=request.effect_id,
        tool_name=request.tool_name,
        args_hash=request.args_hash,
        capability_hash=request.capability_hash,
        scope_hash=request.scope_hash,
    )


def _grant(request: DecisionOpen) -> GrantConsume:
    return GrantConsume(
        grant_id=stable_decision_grant_id(request.decision_id),
        decision_id=request.decision_id,
        decision_nonce=request.nonce,
        run_id=request.run_id,
        expected_session_id="session",
        call_id=request.call_id or "",
        effect_id=request.effect_id or "",
        tool_name=request.tool_name or "",
        args_hash=request.args_hash or "",
        capability_hash=request.capability_hash or "",
        scope_hash=request.scope_hash or "",
    )


def _waiting_event() -> RunEventCandidate:
    return RunEventCandidate(
        event_key="waiting",
        kind="run.waiting",
        status=OutcomeStatus.WAITING,
        driver_kind="react",
    )


def _terminal_event(run_id: str) -> RunEventCandidate:
    return RunEventCandidate(
        event_key=f"terminal:{run_id}",
        kind="run.completed",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload={"result": "ok"},
    )


def _delivery() -> DeliverySpec:
    return DeliverySpec(
        sink_kind="session_db",
        sink_instance="local",
        target_id="session",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )


def _goal_delivery() -> DeliverySpec:
    return DeliverySpec(
        sink_kind="goal_projection",
        sink_instance="domain",
        target_id="goal:fault-final",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )


def _child_intent() -> ChildCommandIntent:
    child_spec = _spec("child", parent_run_id="parent")
    child_request = {"run_id": "child"}
    return ChildCommandIntent(
        operation_id="operation:child",
        parent_run_id="parent",
        command_id="command:child",
        child_spec=child_spec,
        child_request=child_request,
        capability_subset=("read",),
        attachment_policy=AttachmentPolicy.ATTACHED,
        capability_snapshot_ref=CAPABILITY_HASH,
    )


async def _store(path: Path, *, hook: str | None = None) -> SqliteExecutionUnitOfWork:
    def fail(point: str) -> None:
        if point == hook:
            raise RuntimeError(f"crash:{point}")

    store = SqliteExecutionUnitOfWork(
        path, clock=lambda: 100.0, fault_injector=fail if hook else None
    )
    await store.activate_runtime()
    return store


async def _counts(path: Path) -> dict[str, int]:
    tables = {
        "run": "execution_runs",
        "boundary": "execution_continuations",
        "decision": "execution_decisions",
        "effect": "execution_effects",
        "attempt": "execution_effect_attempts",
        "child": "execution_child_commands",
        "inbox": "execution_child_signal_inbox",
        "event": "execution_events",
        "delivery": "execution_deliveries",
    }
    async with aiosqlite.connect(path) as db:
        values = {}
        for key, table in tables.items():
            row = await (await db.execute(f"SELECT COUNT(*) FROM {table}")).fetchone()
            values[key] = int(row[0])
        run_links = await (
            await db.execute("SELECT COUNT(*) FROM execution_run_links")
        ).fetchone()
        effect_links = await (
            await db.execute("SELECT COUNT(*) FROM execution_effect_links")
        ).fetchone()
        values["link"] = int(run_links[0]) + int(effect_links[0])
    values["external_write"] = 0
    return values


async def _exercise_promotion(path: Path, hook: str) -> None:
    spec = _spec("promotion")
    kwargs = dict(
        expected_run_version=0,
        expected_continuation_version=0,
        payload={"command_id": "batch-1"},
        decision=_decision("promotion"),
        waiting_event=_waiting_event(),
        deliveries=(_delivery(),),
    )
    crashing = await _store(path, hook=hook)
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.persist_react_boundary(spec, **kwargs)
    assert (await _counts(path))["run"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    await restarted.persist_react_boundary(spec, **kwargs)


async def _exercise_decision(path: Path, hook: str) -> None:
    healthy = await _store(path)
    await healthy.create(_spec("decision"))
    request = _decision("decision")
    await healthy.commit_decision(request, _actor(), expected_run_version=0)
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.commit_decision(_signal(request), _actor())
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    resolved, _ = await restarted.commit_decision(_signal(request), _actor())
    assert resolved.status.value == "allowed"


async def _exercise_decision_boundary(path: Path, hook: str) -> None:
    healthy = await _store(path)
    await healthy.create(_spec("decision-boundary"))
    permission = hook == "decision_resolve_after_grant"
    request = _decision("decision-boundary", permission=permission)
    await healthy.persist_react_boundary(
        "decision-boundary", 0, {"state": "waiting"}, request
    )
    kwargs = dict(
        expected_continuation_version=1,
        continuation_payload={"state": "resumed"},
        resumed_event=RunEventCandidate(
            event_key="decision-resumed",
            kind="run.resumed",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="react",
        ),
        deliveries=(_delivery(),),
    )
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.commit_decision(
            _signal(request), _actor(), **kwargs
        )
    unchanged = await healthy.load_continuation("decision-boundary")
    assert unchanged is not None and unchanged.payload["state"] == "waiting"
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    decision, _, continuation, event = (
        await restarted.commit_decision(
            _signal(request), _actor(), **kwargs
        )
    )
    assert decision.status.value == "allowed"
    assert continuation.payload["state"] == "resumed"
    assert event.status is OutcomeStatus.ACCEPTED


async def _exercise_grant(path: Path, hook: str) -> None:
    healthy = await _store(path)
    await healthy.create(_spec("grant"))
    request = _decision("grant", permission=True)
    await healthy.commit_decision(request, _actor(), expected_run_version=0)
    await healthy.commit_decision(_signal(request), _actor())
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.claim_tool_call(_grant(request), _actor())
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    consumed = await restarted.claim_tool_call(_grant(request), _actor())
    assert consumed.version == 1


def _effect_claim_kwargs() -> dict:
    return {
        "effect_type": "write",
        "policy": {"reconcile": True},
        "prepared": {"target": "F:/workspace/report.md"},
        "worker_owner": "effect-worker",
        "worker_epoch": 1,
    }


async def _permission_ready(path: Path) -> tuple[SqliteExecutionUnitOfWork, DecisionOpen]:
    healthy = await _store(path)
    await healthy.create(_spec("grant"))
    request = _decision("grant", permission=True)
    await healthy.commit_decision(request, _actor(), expected_run_version=0)
    await healthy.commit_decision(_signal(request), _actor())
    return healthy, request


async def _exercise_effect_claim(path: Path, hook: str) -> None:
    _, request = await _permission_ready(path)
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.claim_tool_call(
            _grant(request), _actor(request.run_id), **_effect_claim_kwargs()
        )
    before = await _counts(path)
    assert before["effect"] == before["attempt"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    claim = await restarted.claim_tool_call(
        _grant(request), _actor(request.run_id), **_effect_claim_kwargs()
    )
    replay = await restarted.claim_tool_call(
        _grant(request), _actor(request.run_id), **_effect_claim_kwargs()
    )
    assert claim.status == replay.status == "running"
    assert (claim.action, replay.action) == ("execute", "in_flight")
    assert claim.authorization is not None
    assert replay.authorization == claim.authorization


async def _exercise_effect_settle(path: Path, hook: str) -> int:
    healthy, request = await _permission_ready(path)
    await healthy.claim_tool_call(
        _grant(request), _actor(request.run_id), **_effect_claim_kwargs()
    )
    await healthy.persist_react_boundary("grant", 0, {"state": "running-effect"})
    kwargs = dict(
        expected_effect_version=0,
        attempt_no=1,
        worker_owner="effect-worker",
        worker_epoch=1,
        status="succeeded",
        outcome={"ok": True},
        receipt_ref="receipt:effect-1",
        artifact_refs=("artifact:report",),
        node_execution_id="node-1",
        checkpoint_ns="graph",
        checkpoint_id="checkpoint-1",
        expected_continuation_version=1,
        continuation_payload={"state": "effect-settled"},
        event=RunEventCandidate(
            event_key="effect-settled",
            kind="tool.result",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
        ),
        deliveries=(_delivery(),),
    )
    external_writes = 1
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.settle_effect("effect-1", **kwargs)
    before = await _counts(path)
    assert before["link"] == before["event"] == before["delivery"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    settled = await restarted.settle_effect("effect-1", **kwargs)
    replay = await restarted.settle_effect("effect-1", **kwargs)
    assert replay == settled and settled.status == "succeeded"
    return external_writes


async def _exercise_finalize(path: Path, hook: str) -> None:
    healthy = await _store(path)
    await healthy.create(_spec("final"))
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    kwargs = dict(
        expected_version=0,
        terminal_status="completed",
        event=_terminal_event("final"),
        deliveries=(_goal_delivery(),),
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.commit_run_outcome("final", **kwargs)
    before = await _counts(path)
    assert before["event"] == before["delivery"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    await restarted.commit_run_outcome("final", **kwargs)


async def _prepare_child(path: Path) -> tuple[SqliteExecutionUnitOfWork, ChildCommandIntent]:
    healthy = await _store(path)
    await healthy.create(_spec("parent"))
    return healthy, _child_intent()


async def _exercise_child_command(path: Path, hook: str) -> None:
    _, intent = await _prepare_child(path)
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.commit_child_command(intent)
    assert (await _counts(path))["child"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    await restarted.commit_child_command(intent)


async def _exercise_child_schedule(path: Path, hook: str) -> None:
    healthy, intent = await _prepare_child(path)
    await healthy.commit_child_command(intent)
    leased = (await healthy.lease_child_commands(owner="scheduler", limit=1, lease_seconds=30))[0]
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    kwargs = dict(lease_owner="scheduler", lease_epoch=leased.schedule_lease_epoch)
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.schedule_child_command(intent.operation_id, **kwargs)
    before = await _counts(path)
    assert before["run"] == 1 and before["link"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    await restarted.schedule_child_command(intent.operation_id, **kwargs)


async def _scheduled_child(path: Path) -> tuple[SqliteExecutionUnitOfWork, ChildCommandIntent]:
    healthy, intent = await _prepare_child(path)
    await healthy.commit_child_command(intent)
    leased = (await healthy.lease_child_commands(owner="scheduler", limit=1, lease_seconds=30))[0]
    await healthy.schedule_child_command(
        intent.operation_id,
        lease_owner="scheduler",
        lease_epoch=leased.schedule_lease_epoch,
    )
    await healthy.acknowledge_child_command(
        intent.operation_id,
        lease_owner="scheduler",
        lease_epoch=leased.schedule_lease_epoch,
    )
    return healthy, intent


async def _exercise_child_apply(path: Path, hook: str) -> None:
    healthy, _ = await _scheduled_child(path)
    signal = (await healthy.list_pending_child_signals("parent"))[0]
    await healthy.persist_react_boundary("parent", 0, {"state": "waiting-child"})
    kwargs = dict(
        expected_continuation_version=1,
        continuation_payload={"state": "child-accepted"},
        event=RunEventCandidate(
            event_key="child-accepted",
            kind="child.accepted",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="react",
        ),
        deliveries=(_delivery(),),
    )
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.ack_child_signal(signal.signal_id, **kwargs)
    unchanged = await healthy.load_continuation("parent")
    assert unchanged is not None and unchanged.payload["state"] == "waiting-child"
    assert (await healthy.list_pending_child_signals("parent"))[0].signal_id == signal.signal_id
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    applied = await restarted.ack_child_signal(signal.signal_id, **kwargs)
    replay = await restarted.ack_child_signal(signal.signal_id, **kwargs)
    assert replay == applied and applied[0].delivered_at is not None


async def _exercise_child_finalize(path: Path, hook: str) -> None:
    _, intent = await _scheduled_child(path)
    kwargs = dict(
        expected_version=1,
        terminal_status="completed",
        event=_terminal_event("child"),
        value={"result": "ok"},
    )
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.finalize_child_and_enqueue_parent_signal(
            intent.operation_id, **kwargs
        )
    before = await _counts(path)
    assert before["event"] == 0 and before["inbox"] == 1
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    result = await restarted.finalize_child_and_enqueue_parent_signal(
        intent.operation_id, **kwargs
    )
    replay = await restarted.finalize_child_and_enqueue_parent_signal(
        intent.operation_id, **kwargs
    )
    assert result.idempotent is False and replay.idempotent is True


CHECKPOINT_HOOKS = frozenset(
    {
        "checkpoint_consume_decisions_after_write",
        "checkpoint_open_decision_after_write",
        "checkpoint_append_event_after_write",
        "checkpoint_link_effects_after_write",
        "checkpoint_finalize_run_after_write",
    }
)


def _workflow_spec(run_id: str) -> RunCreate:
    return replace(
        _spec(run_id),
        driver_kind="workflow",
        profile_key="deep_research/v7",
    )


def _workflow_run(run_id: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "request_id": f"request:{run_id}",
        "turn_id": f"turn:{run_id}",
        "workflow_name": "deep_research",
        "workflow_version": "v7",
    }


async def _insert_checkpoint_marker(
    db: aiosqlite.Connection, *, run_id: str, checkpoint_id: str
) -> None:
    await db.execute(
        """INSERT INTO workflow_checkpoints(
        thread_id,checkpoint_ns,checkpoint_id,run_id,checkpoint_type,
        checkpoint_blob,metadata_blob,engine_kind,created_at
        ) VALUES(?,?,?,?,?,?,?,'native',100)""",
        (f"thread:{run_id}", "", checkpoint_id, run_id, "snapshot", b"state", b"{}"),
    )


async def _checkpoint_operation(
    uow: SqliteExecutionUnitOfWork,
    db: aiosqlite.Connection,
    *,
    hook: str,
    run_id: str,
) -> None:
    tx = uow.bind(db)
    run = _workflow_run(run_id)
    if hook == "checkpoint_consume_decisions_after_write":
        await tx.consume_workflow_decisions(
            run_id=run_id,
            decisions=[{"decision_id": f"decision:{run_id}", "expected_version": 1}],
            checkpoint_id=f"checkpoint:{run_id}",
            now=100.0,
        )
    elif hook == "checkpoint_open_decision_after_write":
        await tx.open_workflow_decision(
            run=run,
            interrupt_id=f"interrupt:{run_id}",
            checkpoint_id=f"checkpoint:{run_id}",
            task_id="task-1",
            kind="clarification",
            prompt={"question": "continue?"},
            expires_at=None,
            now=100.0,
        )
    elif hook == "checkpoint_append_event_after_write":
        await tx.append_workflow_event(
            run=run,
            intent={
                "intent_id": f"intent:{run_id}",
                "event_key": "workflow:progress",
                "event_type": "workflow.progress",
                "payload": {"kind": "progress", "status": "running"},
            },
            now=100.0,
        )
    elif hook == "checkpoint_link_effects_after_write":
        await tx.link_workflow_effects(
            run_id=run_id,
            checkpoint_ns="",
            checkpoint_id=f"checkpoint:{run_id}",
            links=[{"effect_id": f"effect:{run_id}", "node_execution_id": "node-1"}],
            now=100.0,
        )
    elif hook == "checkpoint_finalize_run_after_write":
        await tx.finalize_workflow_run(
            run=run,
            terminal_status="completed",
            terminal_error=None,
            recovery_action=None,
            event_ids=(),
            now=100.0,
        )
    else:  # pragma: no cover - caller is gated by CHECKPOINT_HOOKS
        raise AssertionError(hook)


async def _checkpoint_state(path: Path, run_id: str) -> tuple[int, str, int, int, int]:
    async with aiosqlite.connect(path) as db:
        checkpoint_count = int(
            (await (await db.execute("SELECT COUNT(*) FROM workflow_checkpoints")).fetchone())[0]
        )
        run = await (
            await db.execute(
                "SELECT status,durable_seq FROM execution_runs WHERE run_id=?", (run_id,)
            )
        ).fetchone()
        decision = int(
            (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM execution_decisions WHERE consumed_at IS NOT NULL"
                    )
                ).fetchone()
            )[0]
        )
        links = int(
            (await (await db.execute("SELECT COUNT(*) FROM execution_effect_links")).fetchone())[0]
        )
        return checkpoint_count, str(run[0]), int(run[1]), decision, links


async def _exercise_checkpoint_execution_tx(path: Path, hook: str) -> None:
    run_id = hook.removeprefix("checkpoint_").removesuffix("_after_write")
    healthy = await _store(path)
    await healthy.create(_workflow_spec(run_id))

    if hook == "checkpoint_consume_decisions_after_write":
        request = replace(_decision(run_id), decision_id=f"decision:{run_id}")
        await healthy.commit_decision(request, _actor(run_id), expected_run_version=0)
        async with aiosqlite.connect(path) as db:
            await db.execute(
                """UPDATE execution_decisions SET status='allowed',response_schema_version=1,
                response_json='{"answer":"yes"}',decision_version=1,resolved_at=99
                WHERE decision_id=?""",
                (request.decision_id,),
            )
            await db.commit()
    elif hook == "checkpoint_link_effects_after_write":
        async with aiosqlite.connect(path) as db:
            await db.execute(
                """INSERT INTO execution_effects(
                effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,args_hash,
                capability_hash,scope_hash,effect_type,status,policy_json,prepared_json,
                outcome_json,artifact_refs_json,effect_version,created_at,updated_at,ended_at
                ) VALUES(?,1,?,'checkpoint-effect','call-1','read_file',?,?,?,
                'read','succeeded','{}','{}','{}','[]',1,90,90,90)""",
                (f"effect:{run_id}", run_id, ARGS_HASH, CAPABILITY_HASH, SCOPE_HASH),
            )
            await db.commit()

    before_counts = await _counts(path)
    before_state = await _checkpoint_state(path, run_id)
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match=f"crash:{hook}"):
            await _insert_checkpoint_marker(
                db, run_id=run_id, checkpoint_id=f"checkpoint:{run_id}"
            )
            await _checkpoint_operation(crashing, db, hook=hook, run_id=run_id)
        await db.rollback()
    assert await _counts(path) == before_counts
    assert await _checkpoint_state(path, run_id) == before_state

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        await _insert_checkpoint_marker(
            db, run_id=run_id, checkpoint_id=f"checkpoint:{run_id}"
        )
        await _checkpoint_operation(restarted, db, hook=hook, run_id=run_id)
        await db.commit()
    state = await _checkpoint_state(path, run_id)
    assert state[0] == 1


async def _exercise_team(path: Path, hook: str) -> None:
    uow = await _store(path)
    if hook in {
        "team_saga_before_domain_ack",
        "team_saga_before_terminal_update",
    }:
        await uow.create(_spec("team-parent"))
    healthy = TeamStore(path.parent / "teams")
    await healthy.create_task("fault-team", "delegate")

    def fail(point: str) -> None:
        if point == hook:
            raise RuntimeError(f"crash:{point}")

    crashing = TeamStore(path.parent / "teams", fault_injector=fail)
    if hook == "team_saga_before_domain_ack":
        claimed = await healthy.claim_task_with_child_command(
            "fault-team", "worker-a", intent_factory=_team_intent
        )
        assert claimed is not None
        failed = TeamChildRunReconciler(crashing, uow)
        await failed.reconcile_commands_once("fault-team")
        assert any(f"crash:{hook}" in item for item in failed.last_errors)
        assert await uow.get_child_command(claimed[1].operation_id) is not None
        pending = await healthy.pending_child_commands("fault-team")
        assert len(pending) == 1
        leased = await uow.lease_child_commands(
            owner="team-fault-scheduler", limit=1, lease_seconds=30
        )
        scheduled = await uow.schedule_child_command(
            claimed[1].operation_id,
            lease_owner="team-fault-scheduler",
            lease_epoch=leased[0].schedule_lease_epoch,
        )
        await uow.acknowledge_child_command(
            claimed[1].operation_id,
            lease_owner="team-fault-scheduler",
            lease_epoch=scheduled.schedule_lease_epoch,
        )
        restarted = TeamChildRunReconciler(TeamStore(path.parent / "teams"), uow)
        await restarted.reconcile_commands_once("fault-team")
        assert restarted.last_errors == ()
        return
    if hook == "team_saga_before_terminal_update":
        claimed = await healthy.claim_task_with_child_command(
            "fault-team", "worker-a", intent_factory=_team_intent
        )
        assert claimed is not None
        reconciler = TeamChildRunReconciler(healthy, uow)
        await reconciler.reconcile_commands_once("fault-team")
        leased = await uow.lease_child_commands(
            owner="team-fault-scheduler", limit=1, lease_seconds=30
        )
        assert len(leased) == 1
        scheduled = await uow.schedule_child_command(
            claimed[1].operation_id,
            lease_owner="team-fault-scheduler",
            lease_epoch=leased[0].schedule_lease_epoch,
        )
        await uow.acknowledge_child_command(
            claimed[1].operation_id,
            lease_owner="team-fault-scheduler",
            lease_epoch=scheduled.schedule_lease_epoch,
        )
        context = scheduled.intent.child_spec.context
        child = await uow.query(
            RunRef(scheduled.child_run_id, context.session_id),
            _actor(root_run_id=context.root_run_id),
        )
        await uow.finalize_child_and_enqueue_parent_signal(
            claimed[1].operation_id,
            expected_version=child.version,
            terminal_status=RunStatus.COMPLETED,
            event=RunEventCandidate(
                event_key="team-terminal",
                kind="final",
                status=OutcomeStatus.SUCCEEDED,
                driver_kind="react",
                payload={"text": "done"},
            ),
            value={"text": "done"},
        )
        failed = TeamChildRunReconciler(crashing, uow)
        await failed.reconcile_terminals_once("fault-team")
        assert any(f"crash:{hook}" in item for item in failed.last_errors)
        task = await healthy.get_task("fault-team", claimed[0].task_id)
        assert task is not None and task.status == "claimed"
        restarted = TeamChildRunReconciler(TeamStore(path.parent / "teams"), uow)
        await restarted.reconcile_terminals_once("fault-team")
        applied = await healthy.get_task("fault-team", claimed[0].task_id)
        assert applied is not None and applied.status == "done" and applied.result == "done"
        return
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        if hook == "team_claim_before_commit":
            await crashing.claim_task("fault-team", "worker-a")
        else:
            await crashing.claim_task_with_child_command(
                "fault-team", "worker-a", intent_factory=_team_intent
            )
    pending = await healthy.list_tasks("fault-team", status="pending")
    assert len(pending) == 1
    restarted = TeamStore(path.parent / "teams")
    if hook == "team_claim_before_commit":
        claimed = await restarted.claim_task("fault-team", "worker-a")
        assert claimed is not None and claimed.status == "claimed"
    else:
        result = await restarted.claim_task_with_child_command(
            "fault-team", "worker-a", intent_factory=_team_intent
        )
        assert result is not None and result[0].status == "claimed"
        commands = await restarted.pending_child_commands("fault-team")
        assert len(commands) == 1 and commands[0].child_run_id == "child-run"


async def _team_counts(path: Path) -> dict[str, int]:
    counts = await _counts(path)
    async with aiosqlite.connect(path.parent / "teams" / "fault-team.db") as db:
        for key, table in (
            ("team_task", "tasks"),
            ("team_command", "team_childrun_commands"),
            ("team_terminal_inbox", "team_childrun_inbox"),
        ):
            row = await (await db.execute(f"SELECT COUNT(*) FROM {table}")).fetchone()
            counts[key] = int(row[0])
    return counts


def test_fault_matrix_schema_and_exported_hooks_are_exact() -> None:
    required_fields = {
        "window_id",
        "injection_hook",
        "operation_cases",
        "durable_before",
        "durable_after",
        "restart_actor",
        "idempotency_key",
        "expected_counts",
    }
    assert MATRIX
    assert all(set(row) == required_fields for row in MATRIX)
    assert len({row["window_id"] for row in MATRIX}) == len(MATRIX)
    tested = {row["injection_hook"] for row in MATRIX}
    exported = set(UOW_FAULT_HOOKS) | set(TEAM_FAULT_HOOKS)
    assert tested == exported
    assert len(MATRIX) == len(tested) == len(exported) == 39
    assert len(UOW_FAULT_HOOKS) == 34
    assert len(TEAM_FAULT_HOOKS) == 5
    assert all(COUNT_KEYS <= set(row["expected_counts"]) for row in MATRIX)
    assert all(
        TEAM_COUNT_KEYS <= set(row["expected_counts"])
        for row in MATRIX
        if row["injection_hook"] in TEAM_FAULT_HOOKS
    )
    assert all(row["restart_actor"] and row["idempotency_key"] for row in MATRIX)
    assert all(row["operation_cases"] and len(row["operation_cases"]) == len(set(row["operation_cases"])) for row in MATRIX)
    for row in MATRIX:
        hook = row["injection_hook"]
        expected_cases = (
            ["react_promotion", "start_admission", "workflow_admission_consume"]
            if hook.startswith("batch_boundary_") else
            ["decision_boundary", "resolve_admission"]
            if hook in {"decision_resolve_after_cas", "decision_resolve_after_boundary", "decision_resolve_before_commit"} else
            ["effect_claim", "claim_admission_launch"]
            if hook.startswith("effect_claim_") else
            ["terminal_delivery", "admission_launch_unknown"]
            if hook.startswith("finalize_") else ["existing"]
        )
        assert row["operation_cases"] == expected_cases
    assert len(ATOMIC_OPERATIONS) == 8
    assert len({name for name, _ in ATOMIC_OPERATIONS}) == 8
    assert all(
        callable(getattr(SqliteExecutionUnitOfWork, method_name, None))
        for _, method_names in ATOMIC_OPERATIONS
        for method_name in method_names
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("row", MATRIX, ids=lambda row: row["window_id"])
async def test_every_fault_window_rolls_back_then_restart_converges(tmp_path, row) -> None:
    hook = row["injection_hook"]
    path = tmp_path / f"{row['window_id']}.db"
    external_writes = 0
    if hook.startswith("batch_boundary_"):
        await _exercise_promotion(path, hook)
    elif hook == "decision_resolve_before_commit":
        await _exercise_decision(path, hook)
    elif hook.startswith("decision_resolve_after_"):
        await _exercise_decision_boundary(path, hook)
    elif hook == "grant_consume_before_commit":
        await _exercise_grant(path, hook)
    elif hook.startswith("effect_claim_"):
        await _exercise_effect_claim(path, hook)
    elif hook.startswith("effect_settle_"):
        external_writes = await _exercise_effect_settle(path, hook)
    elif hook.startswith("finalize_"):
        await _exercise_finalize(path, hook)
    elif hook == "child_command_before_commit":
        await _exercise_child_command(path, hook)
    elif hook.startswith("child_schedule_"):
        await _exercise_child_schedule(path, hook)
    elif hook == "child_terminal_before_commit":
        await _exercise_child_finalize(path, hook)
    elif hook == "child_signal_ack_before_commit":
        await _exercise_child_apply(path, hook)
    elif hook.startswith("child_apply_"):
        await _exercise_child_apply(path, hook)
    elif hook.startswith("child_finalize_"):
        await _exercise_child_finalize(path, hook)
    elif hook in CHECKPOINT_HOOKS:
        await _exercise_checkpoint_execution_tx(path, hook)
    elif hook in TEAM_FAULT_HOOKS:
        await _exercise_team(path, hook)
    else:  # pragma: no cover - equality gate above makes this fail closed
        raise AssertionError(f"unexercised fault hook: {hook}")

    counts = (
        await _team_counts(path)
        if hook in TEAM_FAULT_HOOKS
        else await _counts(path)
    )
    counts["external_write"] = external_writes
    assert counts == row["expected_counts"]


@pytest.mark.asyncio
async def test_unapproved_effect_claim_is_durable_and_replay_never_executes(tmp_path) -> None:
    store = await _store(tmp_path / "optional-grant.db")
    await store.create(_spec("no-approval"))
    identity = dict(
        run_id="no-approval", expected_session_id="session", call_id="call-no-approval",
        effect_id="effect-no-approval", tool_name="local-write", args_hash=ARGS_HASH,
        capability_hash=CAPABILITY_HASH, scope_hash=SCOPE_HASH,
    )
    first = await store.claim_tool_call(
        None, _actor("no-approval"), **identity, **_effect_claim_kwargs()
    )
    replay = await store.claim_tool_call(
        None, _actor("no-approval"), **identity, **_effect_claim_kwargs()
    )
    assert (first.action, replay.action) == ("execute", "in_flight")
    assert (await _counts(store.path))["effect"] == 1


@pytest.mark.asyncio
async def test_child_effect_actor_binds_to_root_tree_not_child_id(tmp_path) -> None:
    store = await _store(tmp_path / "child-effect.db")
    await store.create(_spec("parent"))
    await store.create(_spec("child-effect", parent_run_id="parent"))
    claim = await store.claim_tool_call(
        None, _actor("parent"), run_id="child-effect", expected_session_id="session",
        call_id="call-child", effect_id="effect-child", tool_name="child-write",
        args_hash=ARGS_HASH, capability_hash=CAPABILITY_HASH, scope_hash=SCOPE_HASH,
        **_effect_claim_kwargs(),
    )
    assert claim.action == "execute"


@pytest.mark.asyncio
async def test_accepted_effect_status_survives_authoritative_reuse(tmp_path) -> None:
    store = await _store(tmp_path / "accepted-effect.db")
    await store.create(_spec("accepted"))
    identity = dict(
        run_id="accepted", expected_session_id="session", call_id="call-accepted",
        effect_id="effect-accepted", tool_name="async-write", args_hash=ARGS_HASH,
        capability_hash=CAPABILITY_HASH, scope_hash=SCOPE_HASH,
    )
    claim = await store.claim_tool_call(
        None, _actor("accepted"), **identity, **_effect_claim_kwargs()
    )
    await store.persist_react_boundary("accepted", 0, {"outcomes": [None]})
    outcome = NormalizedToolOutcome.success({"status": "queued"})
    await store.settle_effect(
        "effect-accepted", expected_effect_version=claim.effect_version,
        attempt_no=claim.attempt_no, worker_owner=claim.worker_owner,
        worker_epoch=claim.worker_epoch, status="accepted", outcome=outcome.to_dict(),
        receipt_ref="receipt:accepted", artifact_refs=(),
        node_execution_id="react:accepted:0", checkpoint_ns="react",
        checkpoint_id="accepted", expected_continuation_version=1,
        continuation_payload={"outcomes": [outcome.to_dict()]},
        event=RunEventCandidate(
            event_key="effect:accepted", kind="tool.outcome",
            status=OutcomeStatus.ACCEPTED, driver_kind="react",
        ),
    )
    status, payload, _, _ = await store.read_effect_outcome(
        run_id=identity["run_id"], call_id=identity["call_id"],
        effect_id=identity["effect_id"], args_hash=identity["args_hash"],
        capability_hash=identity["capability_hash"], scope_hash=identity["scope_hash"],
    )
    restored = NormalizedToolOutcome.from_dict(payload)
    assert status == "accepted"
    assert ToolOutcomesSignal(
        "accepted", "command", (restored,), (OutcomeStatus(status),), (0,)
    ).statuses == (OutcomeStatus.ACCEPTED,)
