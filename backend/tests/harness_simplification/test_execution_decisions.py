from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import aiosqlite
import pytest

from deskpet.execution import (
    ActorContext,
    AuthorizationError,
    DecisionConflict,
    DecisionKind,
    DecisionOpen,
    DecisionSignal,
    DecisionStatus,
    GrantConsume,
    GrantConsumeConflict,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    VersionConflict,
    fingerprint_json,
    root_idempotency_key,
    stable_decision_grant_id,
)
from deskpet.harness import DecisionStore, DecisionWakeupCache
from deskpet.harness.effects import SqliteExecutionEffectJournal
from deskpet.harness.tool_executor import (
    PreparedExecutionCall,
    ToolOutcome,
    ToolOutcomeStatus,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.store import SqliteExecutionUnitOfWork


CAPABILITY_HASH = fingerprint_json({"tools": ["write"], "root": "workspace"})
ARGS_HASH = fingerprint_json({"path": "report.md", "content": "ready"})
SCOPE_HASH = fingerprint_json({"root": "F:/workspace", "write": True})
OTHER_HASH = fingerprint_json({"different": True})


def _run(run_id: str = "run-decision") -> RunCreate:
    context = RunContext(
        session_id="session-a",
        root_run_id=run_id,
        parent_run_id=None,
        request_id=f"request-{run_id}",
        turn_id=f"turn-{run_id}",
        venue="text",
        workspace={"root": "F:/workspace"},
        capability_hash=CAPABILITY_HASH,
        provider_plan={"model": "fixture"},
        trace_id=f"trace-{run_id}",
        principal_id="principal-a",
        auth_epoch=7,
    )
    return RunCreate(
        run_id=run_id,
        idempotency_key=root_idempotency_key(
            context.session_id, context.request_id, context.turn_id
        ),
        context=context,
        payload_fingerprint=fingerprint_json({"prompt": "write report"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="chat",
        persistence_level=PersistenceLevel.DURABLE,
    )


def _actor(
    *, session_id: str = "session-a", principal_id: str = "principal-a"
) -> ActorContext:
    return ActorContext(
        principal_id=principal_id,
        session_id=session_id,
        auth_epoch=7,
    )


def _permission(*, expires_at: float = 200.0) -> DecisionOpen:
    return DecisionOpen(
        decision_id="decision-permission",
        run_id="run-decision",
        nonce="nonce-permission-1",
        kind=DecisionKind.PERMISSION,
        prompt_schema_version=1,
        prompt={"question": "Allow write?"},
        expires_at=expires_at,
        call_id="call-1",
        effect_id="effect-1",
        tool_name="file_write",
        args_hash=ARGS_HASH,
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
    )


def _signal(
    request: DecisionOpen,
    *,
    allow: bool = True,
    expected_version: int = 0,
) -> DecisionSignal:
    return DecisionSignal(
        decision_id=request.decision_id,
        run_id=request.run_id,
        expected_session_id="session-a",
        nonce=request.nonce,
        expected_version=expected_version,
        allow=allow,
        response_schema_version=1,
        response={"allow": allow},
        domain_kind=request.domain_kind,
        domain_id=request.domain_id,
        call_id=request.call_id,
        effect_id=request.effect_id,
        tool_name=request.tool_name,
        args_hash=request.args_hash,
        capability_hash=request.capability_hash,
        scope_hash=request.scope_hash,
    )


def _consume(request: DecisionOpen) -> GrantConsume:
    return GrantConsume(
        grant_id=stable_decision_grant_id(request.decision_id),
        decision_id=request.decision_id,
        decision_nonce=request.nonce,
        run_id=request.run_id,
        expected_session_id="session-a",
        call_id=request.call_id or "",
        effect_id=request.effect_id or "",
        tool_name=request.tool_name or "",
        args_hash=request.args_hash or "",
        capability_hash=request.capability_hash or "",
        scope_hash=request.scope_hash or "",
    )


@pytest.mark.asyncio
async def test_permission_survives_restart_and_grant_is_consumed_once(tmp_path):
    path = tmp_path / "workflow.db"
    clock = [100.0]
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: clock[0])
    await uow.create(_run())
    request = _permission()
    first = DecisionStore(uow, DecisionWakeupCache())

    opened = await first.open(request, _actor(), expected_run_version=0)
    assert opened.status is DecisionStatus.OPEN
    assert opened.decision_version == 0

    # A fresh adapter/cache represents a process restart.  The response is
    # resolved solely from the durable decision row.
    restarted_uow = SqliteExecutionUnitOfWork(path, clock=lambda: clock[0])
    restarted = DecisionStore(restarted_uow, DecisionWakeupCache())
    resolved, grant = await restarted.resolve(_signal(request), _actor())
    assert resolved.status is DecisionStatus.ALLOWED
    assert resolved.decision_version == 1
    assert grant is not None
    assert grant.grant_id == stable_decision_grant_id(request.decision_id)
    assert grant.version == 0

    consumed = await restarted.consume(_consume(request), _actor())
    assert consumed.version == 1
    with pytest.raises(GrantConsumeConflict):
        await restarted.consume(_consume(request), _actor())
    with pytest.raises(DecisionConflict):
        await restarted.resolve(_signal(request), _actor())

    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT status,grant_version,consumed_at FROM execution_grants"
            )
        ).fetchone()
    assert row[0:2] == ("consumed", 1)
    assert row[2] is not None


@pytest.mark.asyncio
async def test_effect_prepare_rolls_back_grant_and_attempt_together(tmp_path):
    path = tmp_path / "workflow.db"
    clock = [100.0]
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: clock[0])
    await uow.create(_run())
    store = DecisionStore(uow, DecisionWakeupCache())
    request = _permission()
    await store.open(request, _actor(), expected_run_version=0)
    _, authorization = await store.resolve(_signal(request), _actor())
    assert authorization is not None

    call = PreparedExecutionCall(
        tool_name="file_write",
        model_args={"path": "report.md", "content": "ready"},
        call_id="call-1",
        effect_id="effect-1",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        requires_authorization=True,
        recoverable_effect=True,
    )
    assert call.args_hash == ARGS_HASH
    context = ToolExecutionContext(
        scope_id="scope-a",
        session_id="session-a",
        request_id="request-run-decision",
        root_run_id="run-decision",
        turn_id="turn-run-decision",
        capability_hash=CAPABILITY_HASH,
        scope_hash=SCOPE_HASH,
        run_id="run-decision",
        call_id="call-1",
        effect_id="effect-1",
        trace_id="trace-run-decision",
    )

    def crash(point: str) -> None:
        if point == "effect_prepare_before_commit":
            raise RuntimeError("simulated process loss")

    failing = SqliteExecutionEffectJournal(
        path,
        clock=lambda: clock[0],
        fault_injector=crash,
    )
    with pytest.raises(RuntimeError, match="simulated process loss"):
        await failing.prepare_effect(call, context, authorization)

    async with aiosqlite.connect(path) as db:
        grant = await (
            await db.execute(
                "SELECT status,grant_version FROM execution_grants WHERE grant_id=?",
                (authorization.grant_id,),
            )
        ).fetchone()
        effect_count = await (
            await db.execute(
                "SELECT COUNT(*) FROM execution_effects WHERE effect_id=?",
                (call.effect_id,),
            )
        ).fetchone()
    assert grant == ("issued", 0)
    assert effect_count == (0,)

    healthy = SqliteExecutionEffectJournal(path, clock=lambda: clock[0])
    await healthy.prepare_effect(call, context, authorization)
    in_flight = await healthy.get_outcome(call.effect_id)
    assert in_flight is not None
    assert in_flight.status is ToolOutcomeStatus.UNKNOWN
    assert in_flight.reconciliation == "required"
    replayed_in_flight = await healthy.prepare_effect(call, context, authorization)
    assert replayed_in_flight == in_flight
    async with aiosqlite.connect(path) as db:
        grant = await (
            await db.execute(
                "SELECT status,grant_version FROM execution_grants WHERE grant_id=?",
                (authorization.grant_id,),
            )
        ).fetchone()
        effect = await (
            await db.execute(
                "SELECT status FROM execution_effects WHERE effect_id=?",
                (call.effect_id,),
            )
        ).fetchone()
        attempt_count = await (
            await db.execute(
                "SELECT COUNT(*) FROM execution_effect_attempts WHERE effect_id=?",
                (call.effect_id,),
            )
        ).fetchone()
    assert grant == ("consumed", 1)
    assert effect == ("running",)
    assert attempt_count == (1,)

    succeeded = ToolOutcome(
        call.call_id,
        call.effect_id,
        ToolOutcomeStatus.SUCCEEDED,
        value={"written": True},
    )
    await healthy.finalize_effect(call, context, succeeded, late=False)
    replayed_success = await healthy.prepare_effect(call, context, authorization)
    assert replayed_success == succeeded


@pytest.mark.asyncio
async def test_open_is_idempotent_but_nonce_or_intent_collision_fails_closed(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: 100.0)
    await uow.create(_run())
    store = DecisionStore(uow, DecisionWakeupCache())
    request = _permission()

    first = await store.open(request, _actor(), expected_run_version=0)
    replay = await store.open(request, _actor(), expected_run_version=0)
    assert replay == first

    with pytest.raises(DecisionConflict) as nonce_collision:
        await store.open(
            replace(request, decision_id="decision-other"),
            _actor(),
            expected_run_version=1,
        )
    assert nonce_collision.value.code == "decision_identity_conflict"

    with pytest.raises(DecisionConflict) as intent_collision:
        await store.open(
            replace(request, scope_hash=OTHER_HASH),
            _actor(),
            expected_run_version=1,
        )
    assert intent_collision.value.code == "decision_identity_conflict"


@pytest.mark.asyncio
async def test_open_resolve_and_consume_each_have_a_single_cas_winner(tmp_path):
    open_path = tmp_path / "open.db"
    open_uow = SqliteExecutionUnitOfWork(open_path, clock=lambda: 100.0)
    await open_uow.create(_run())
    open_store = DecisionStore(open_uow, DecisionWakeupCache())
    first = DecisionOpen(
        decision_id="decision-a",
        run_id="run-decision",
        nonce="nonce-a",
        kind=DecisionKind.PLAN,
        prompt_schema_version=1,
        prompt={"value": "a"},
        expires_at=200.0,
    )
    second = replace(
        first,
        decision_id="decision-b",
        nonce="nonce-b",
        prompt={"value": "b"},
    )
    opened = await asyncio.gather(
        open_store.open(first, _actor(), expected_run_version=0),
        open_store.open(second, _actor(), expected_run_version=0),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in opened) == 1
    assert sum(isinstance(item, VersionConflict) for item in opened) == 1

    path = tmp_path / "resolve-consume.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.create(_run())
    request = _permission()
    store = DecisionStore(uow, DecisionWakeupCache())
    await store.open(request, _actor(), expected_run_version=0)
    resolved = await asyncio.gather(
        store.resolve(_signal(request), _actor()),
        store.resolve(_signal(request), _actor()),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in resolved) == 1
    assert sum(isinstance(item, DecisionConflict) for item in resolved) == 1
    consumed = await asyncio.gather(
        store.consume(_consume(request), _actor()),
        store.consume(_consume(request), _actor()),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in consumed) == 1
    assert sum(isinstance(item, GrantConsumeConflict) for item in consumed) == 1

    async with aiosqlite.connect(path) as db:
        rows = await (
            await db.execute(
                "SELECT status,grant_version FROM execution_grants WHERE decision_id=?",
                (request.decision_id,),
            )
        ).fetchall()
    assert rows == [("consumed", 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("nonce", "wrong-nonce"),
        ("run_id", "wrong-run"),
        ("call_id", "wrong-call"),
        ("effect_id", "wrong-effect"),
        ("tool_name", "wrong-tool"),
        ("args_hash", OTHER_HASH),
        ("capability_hash", OTHER_HASH),
        ("scope_hash", OTHER_HASH),
    ),
)
async def test_signal_rejects_every_frozen_binding_mismatch(tmp_path, field, value):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: 100.0)
    await uow.create(_run())
    request = _permission()
    store = DecisionStore(uow, DecisionWakeupCache())
    await store.open(request, _actor(), expected_run_version=0)

    with pytest.raises(DecisionConflict) as error:
        await store.resolve(replace(_signal(request), **{field: value}), _actor())
    assert error.value.code == "decision_binding_mismatch"

    current = await store.get(
        request.decision_id,
        ref=RunRef(request.run_id, "session-a"),
        actor=_actor(),
    )
    assert current.status is DecisionStatus.OPEN
    assert current.decision_version == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("decision_nonce", "wrong-nonce"),
        ("run_id", "wrong-run"),
        ("call_id", "wrong-call"),
        ("effect_id", "wrong-effect"),
        ("tool_name", "wrong-tool"),
        ("args_hash", OTHER_HASH),
        ("capability_hash", OTHER_HASH),
        ("scope_hash", OTHER_HASH),
    ),
)
async def test_grant_consume_rejects_every_binding_mismatch(tmp_path, field, value):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: 100.0)
    await uow.create(_run())
    request = _permission()
    store = DecisionStore(uow, DecisionWakeupCache())
    await store.open(request, _actor(), expected_run_version=0)
    await store.resolve(_signal(request), _actor())

    with pytest.raises(GrantConsumeConflict) as error:
        await store.consume(replace(_consume(request), **{field: value}), _actor())
    assert error.value.code == "grant_binding_mismatch"

    consumed = await store.consume(_consume(request), _actor())
    assert consumed.version == 1


@pytest.mark.asyncio
async def test_wrong_session_principal_nonce_and_hash_leave_decision_open(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: 100.0)
    await uow.create(_run())
    request = _permission()
    store = DecisionStore(uow, DecisionWakeupCache())
    await store.open(request, _actor(), expected_run_version=0)
    signal = _signal(request)

    with pytest.raises(AuthorizationError):
        await store.resolve(
            replace(signal, expected_session_id="session-b"),
            _actor(session_id="session-b"),
        )
    with pytest.raises(AuthorizationError):
        await store.resolve(signal, _actor(principal_id="principal-b"))
    with pytest.raises(DecisionConflict):
        await store.resolve(replace(signal, nonce="wrong-nonce"), _actor())
    with pytest.raises(DecisionConflict):
        await store.resolve(replace(signal, scope_hash=OTHER_HASH), _actor())

    current = await store.get(
        request.decision_id,
        ref=RunRef(request.run_id, "session-a"),
        actor=_actor(),
    )
    assert current.status is DecisionStatus.OPEN
    assert current.decision_version == 0


@pytest.mark.asyncio
async def test_expired_signal_and_grant_are_persistently_rejected(tmp_path):
    path = tmp_path / "workflow.db"
    clock = [100.0]
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: clock[0])
    await uow.create(_run("run-expired-decision"))
    expired_request = replace(
        _permission(expires_at=105.0),
        decision_id="decision-expired",
        run_id="run-expired-decision",
        nonce="nonce-expired",
    )
    store = DecisionStore(uow, DecisionWakeupCache())
    await store.open(expired_request, _actor(), expected_run_version=0)
    clock[0] = 106.0
    with pytest.raises(DecisionConflict) as error:
        await store.resolve(_signal(expired_request), _actor())
    assert error.value.code == "decision_expired"

    restarted = DecisionStore(
        SqliteExecutionUnitOfWork(path, clock=lambda: clock[0]),
        DecisionWakeupCache(),
    )
    persisted = await restarted.get(
        expired_request.decision_id,
        ref=RunRef(expired_request.run_id, "session-a"),
        actor=_actor(),
    )
    assert persisted.status is DecisionStatus.EXPIRED
    assert persisted.decision_version == 1

    await uow.create(_run("run-expired-grant"))
    grant_request = replace(
        _permission(expires_at=115.0),
        decision_id="decision-grant-expiry",
        run_id="run-expired-grant",
        nonce="nonce-grant-expiry",
    )
    clock[0] = 110.0
    await store.open(grant_request, _actor(), expected_run_version=0)
    await store.resolve(_signal(grant_request), _actor())
    clock[0] = 116.0
    with pytest.raises(GrantConsumeConflict) as grant_error:
        await store.consume(_consume(grant_request), _actor())
    assert grant_error.value.code == "grant_expired"

    async with aiosqlite.connect(path) as db:
        status = await (
            await db.execute(
                "SELECT status,grant_version FROM execution_grants WHERE decision_id=?",
                (grant_request.decision_id,),
            )
        ).fetchone()
    assert status == ("expired", 1)


@pytest.mark.asyncio
async def test_denial_never_issues_permission_grant(tmp_path):
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.create(_run())
    request = _permission()
    store = DecisionStore(uow, DecisionWakeupCache())
    await store.open(request, _actor(), expected_run_version=0)

    decision, grant = await store.resolve(_signal(request, allow=False), _actor())
    assert decision.status is DecisionStatus.DENIED
    assert grant is None
    async with aiosqlite.connect(path) as db:
        count = await (await db.execute("SELECT COUNT(*) FROM execution_grants")).fetchone()
    assert count == (0,)


@pytest.mark.asyncio
async def test_cancel_while_waiting_expires_row_wakes_handle_and_rejects_late_signal(
    tmp_path,
):
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.create(_run())
    wakeups = DecisionWakeupCache()
    store = DecisionStore(uow, wakeups)
    request = DecisionOpen(
        decision_id="decision-plan",
        run_id="run-decision",
        nonce="nonce-plan",
        kind=DecisionKind.PLAN,
        prompt_schema_version=1,
        prompt={"plan": ["inspect", "edit"]},
        expires_at=200.0,
    )
    await store.open(request, _actor(), expected_run_version=0)
    waiter = asyncio.create_task(
        store.wait(
            request.decision_id,
            ref=RunRef(request.run_id, "session-a"),
            actor=_actor(),
        )
    )
    for _ in range(100):
        if wakeups.waiter_count == 1:
            break
        await asyncio.sleep(0.01)
    assert wakeups.waiter_count == 1

    cancelled_run = await uow.request_cancel(
        request.run_id,
        expected_version=1,
        reason="user",
        event=RunEventCandidate(
            event_key="cancel-while-waiting",
            kind="run.cancel_requested",
            status=OutcomeStatus.CANCEL_REQUESTED,
            driver_kind="react",
            payload={"reason": "user"},
        ),
    )
    cancelled = await store.cancel_waiting(
        RunRef(request.run_id, "session-a"),
        _actor(),
        expected_run_version=cancelled_run.version,
    )
    assert [item.decision_id for item in cancelled] == [request.decision_id]
    observed = await asyncio.wait_for(waiter, timeout=1.0)
    assert observed.status is DecisionStatus.EXPIRED
    assert wakeups.waiter_count == 0

    late = replace(_signal(request), expected_version=1)
    with pytest.raises(DecisionConflict) as error:
        await store.resolve(late, _actor())
    assert error.value.code == "run_not_signalable"


@pytest.mark.asyncio
async def test_store_miss_after_restart_reads_resolved_decision_without_waiter(tmp_path):
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    await uow.create(_run())
    request = DecisionOpen(
        decision_id="decision-clarify",
        run_id="run-decision",
        nonce="nonce-clarify",
        kind=DecisionKind.CLARIFICATION,
        prompt_schema_version=1,
        prompt={"question": "Which format?"},
        expires_at=200.0,
    )
    first = DecisionStore(uow, DecisionWakeupCache())
    await first.open(request, _actor(), expected_run_version=0)
    await first.resolve(_signal(request), _actor())

    wakeups = DecisionWakeupCache()
    restarted = DecisionStore(
        SqliteExecutionUnitOfWork(path, clock=lambda: 100.0), wakeups
    )
    observed = await restarted.wait(
        request.decision_id,
        ref=RunRef(request.run_id, "session-a"),
        actor=_actor(),
    )
    assert observed.status is DecisionStatus.ALLOWED
    assert wakeups.waiter_count == 0


def test_decision_readiness_is_not_registered_in_production_bootstrap():
    main_source = (Path(__file__).resolve().parents[2] / "main.py").read_text(
        encoding="utf-8"
    )
    assert "DecisionStore(" not in main_source
    assert "DecisionWakeupCache(" not in main_source
