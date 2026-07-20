from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from deskpet.agent.team.team_store import FAULT_HOOKS as TEAM_FAULT_HOOKS
from deskpet.agent.team.team_store import TeamStore
from deskpet.execution import (
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
    fingerprint_json,
    stable_decision_grant_id,
)
from deskpet.workflows.store.execution_uow import FAULT_HOOKS as UOW_FAULT_HOOKS
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


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


def _actor() -> ActorContext:
    return ActorContext(principal_id="principal", session_id="session", auth_epoch=7)


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
    await store.activate_empty_runtime()
    return store


async def _counts(path: Path) -> dict[str, int]:
    tables = {
        "run": "execution_runs",
        "boundary": "execution_continuations",
        "decision": "execution_decisions",
        "effect": "execution_effects",
        "attempt": "execution_effect_attempts",
        "child": "execution_child_commands",
        "link": "execution_run_links",
        "inbox": "execution_child_signal_inbox",
        "event": "execution_events",
        "delivery": "execution_deliveries",
    }
    async with aiosqlite.connect(path) as db:
        values = {}
        for key, table in tables.items():
            row = await (await db.execute(f"SELECT COUNT(*) FROM {table}")).fetchone()
            values[key] = int(row[0])
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
        await crashing.promote_and_persist_batch_boundary(spec, **kwargs)
    assert (await _counts(path))["run"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    await restarted.promote_and_persist_batch_boundary(spec, **kwargs)


async def _exercise_decision(path: Path, hook: str) -> None:
    healthy = await _store(path)
    await healthy.create(_spec("decision"))
    request = _decision("decision")
    await healthy.open_decision(request, _actor(), expected_run_version=0)
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.resolve_decision(_signal(request), _actor())
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    resolved, _ = await restarted.resolve_decision(_signal(request), _actor())
    assert resolved.status.value == "allowed"


async def _exercise_grant(path: Path, hook: str) -> None:
    healthy = await _store(path)
    await healthy.create(_spec("grant"))
    request = _decision("grant", permission=True)
    await healthy.open_decision(request, _actor(), expected_run_version=0)
    await healthy.resolve_decision(_signal(request), _actor())
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.consume_authorization(_grant(request), _actor())
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    consumed = await restarted.consume_authorization(_grant(request), _actor())
    assert consumed.version == 1


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
        deliveries=(_delivery(),),
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.finalize("final", **kwargs)
    before = await _counts(path)
    assert before["event"] == before["delivery"] == 0
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    await restarted.finalize("final", **kwargs)


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


async def _exercise_child_terminal(path: Path, hook: str) -> None:
    healthy, intent = await _scheduled_child(path)
    await healthy.finalize(
        "child",
        expected_version=1,
        terminal_status="completed",
        event=_terminal_event("child"),
    )
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.record_child_terminal(
            intent.operation_id, terminal_status="completed", value={"result": "ok"}
        )
    assert (await _counts(path))["inbox"] == 1
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    await restarted.record_child_terminal(
        intent.operation_id, terminal_status="completed", value={"result": "ok"}
    )


async def _exercise_child_ack(path: Path, hook: str) -> None:
    healthy, _ = await _scheduled_child(path)
    signal = (await healthy.list_pending_child_signals("parent"))[0]
    crashing = SqliteExecutionUnitOfWork(
        path,
        clock=lambda: 100.0,
        fault_injector=lambda point: (_ for _ in ()).throw(RuntimeError(f"crash:{point}"))
        if point == hook
        else None,
    )
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.acknowledge_child_signal(signal.signal_id)
    assert (await healthy.list_pending_child_signals("parent"))[0].signal_id == signal.signal_id
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    acknowledged = await restarted.acknowledge_child_signal(signal.signal_id)
    assert acknowledged.delivered_at is not None


async def _exercise_team(path: Path, hook: str) -> None:
    healthy = TeamStore(path.parent / "teams")
    await healthy.create_task("fault-team", "delegate")

    def fail(point: str) -> None:
        if point == hook:
            raise RuntimeError(f"crash:{point}")

    crashing = TeamStore(path.parent / "teams", fault_injector=fail)
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await crashing.claim_task("fault-team", "worker-a")
    pending = await healthy.list_tasks("fault-team", status="pending")
    assert len(pending) == 1
    restarted = TeamStore(path.parent / "teams")
    claimed = await restarted.claim_task("fault-team", "worker-a")
    assert claimed is not None and claimed.status == "claimed"


def test_fault_matrix_schema_and_exported_hooks_are_exact() -> None:
    required_fields = {
        "window_id",
        "injection_hook",
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
    assert all(COUNT_KEYS <= set(row["expected_counts"]) for row in MATRIX)
    assert all(row["restart_actor"] and row["idempotency_key"] for row in MATRIX)


@pytest.mark.asyncio
@pytest.mark.parametrize("row", MATRIX, ids=lambda row: row["window_id"])
async def test_every_fault_window_rolls_back_then_restart_converges(tmp_path, row) -> None:
    hook = row["injection_hook"]
    path = tmp_path / f"{row['window_id']}.db"
    if hook.startswith("batch_boundary_"):
        await _exercise_promotion(path, hook)
    elif hook == "decision_resolve_before_commit":
        await _exercise_decision(path, hook)
    elif hook == "grant_consume_before_commit":
        await _exercise_grant(path, hook)
    elif hook.startswith("finalize_"):
        await _exercise_finalize(path, hook)
    elif hook == "child_command_before_commit":
        await _exercise_child_command(path, hook)
    elif hook.startswith("child_schedule_"):
        await _exercise_child_schedule(path, hook)
    elif hook == "child_terminal_before_commit":
        await _exercise_child_terminal(path, hook)
    elif hook == "child_signal_ack_before_commit":
        await _exercise_child_ack(path, hook)
    elif hook == "team_claim_before_commit":
        await _exercise_team(path, hook)
    else:  # pragma: no cover - equality gate above makes this fail closed
        raise AssertionError(f"unexercised fault hook: {hook}")

    if hook == "team_claim_before_commit":
        return
    assert await _counts(path) == row["expected_counts"]
