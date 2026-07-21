"""WI-3 backend readiness contracts for projection and per-sink delivery."""

from __future__ import annotations

import asyncio
from itertools import permutations, product
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    DeliveryClaimConflict,
    DeliveryPolicy,
    DeliverySpec,
    DeliveryStatus,
    OutcomeStatus,
    RunContext,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunIdentityConflict,
    RunStatus,
    TerminalConflict,
    fingerprint_json,
)
from deskpet.harness.projector import (
    ExecutionDeliveryDispatcher,
    SinkRegistration,
)
from deskpet.memory.session_db import SessionDB
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


CAPABILITY_HASH = "a" * 64


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _ScriptedSink:
    def __init__(self, *, failures: int = 0, bound: bool = True) -> None:
        self.failures = failures
        self.bound = bound
        self.calls: list[str] = []
        self.visible: list[str] = []

    async def is_bound(self, target_id: str) -> bool:
        del target_id
        return self.bound

    async def deliver(self, event: RunEvent, target_id: str) -> None:
        del target_id
        self.calls.append(event.event_id)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("injected sink failure")
        self.visible.append(event.event_id)


def _spec(run_id: str = "run-1") -> RunCreate:
    context = RunContext(
        session_id="session-1",
        root_run_id=run_id,
        parent_run_id=None,
        request_id=f"request-{run_id}",
        turn_id=f"turn-{run_id}",
        venue="text",
        workspace={},
        capability_hash=CAPABILITY_HASH,
        provider_plan={},
        trace_id=f"trace-{run_id}",
        principal_id="user-1",
    )
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:session-1:request-{run_id}:turn-{run_id}",
        context=context,
        payload_fingerprint=fingerprint_json({"text": run_id}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react_short",
        persistence_level="durable",
    )


def _child_spec(root_run_id: str, child_run_id: str) -> RunCreate:
    parent = _spec(root_run_id)
    context = RunContext(
        session_id=parent.context.session_id,
        root_run_id=root_run_id,
        parent_run_id=root_run_id,
        request_id=f"request-{child_run_id}",
        turn_id=f"turn-{child_run_id}",
        venue="text",
        workspace={},
        capability_hash=CAPABILITY_HASH,
        provider_plan={},
        trace_id=f"trace-{child_run_id}",
        principal_id=parent.context.principal_id,
    )
    return RunCreate(
        run_id=child_run_id,
        idempotency_key=f"delegate:{root_run_id}:command-1:react_short",
        context=context,
        payload_fingerprint=fingerprint_json({"text": child_run_id}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react_short",
        persistence_level="durable",
    )


def _terminal_candidate(*, payload: dict[str, Any] | None = None) -> RunEventCandidate:
    return RunEventCandidate(
        event_key="terminal",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload=payload or {},
    )


def _registrations(
    session_sink: object,
    ws_sink: object,
    tts_sink: object,
) -> tuple[SinkRegistration, ...]:
    return (
        SinkRegistration("session_db", "local", session_sink),
        SinkRegistration("ws", "peer", ws_sink),
        SinkRegistration("tts", "voice", tts_sink),
    )


async def _drain(worker: ExecutionDeliveryDispatcher, limit: int) -> int:
    processed = 0
    while processed < limit and await worker.run_once():
        processed += 1
    return processed


def _delivery_specs(
    *, session_id: str, session_sink_instance: str, ws_sink_instance: str,
    ws_target_id: str, tts_sink_instance: str, tts_target_id: str,
) -> tuple[DeliverySpec, ...]:
    return (
        DeliverySpec("session_db", session_sink_instance, session_id,
                     DeliveryPolicy.DURABLE_REQUIRED),
        DeliverySpec("ws", ws_sink_instance, ws_target_id,
                     DeliveryPolicy.RETRY_WHILE_BOUND),
        DeliverySpec("tts", tts_sink_instance, tts_target_id,
                     DeliveryPolicy.BEST_EFFORT),
    )


class _SessionDBSink:
    def __init__(self, session_db: SessionDB) -> None:
        self._session_db = session_db

    async def is_bound(self, target_id: str) -> bool:
        return bool(target_id)

    async def deliver(self, event: RunEvent, target_id: str) -> None:
        message = event.candidate.payload["session_message"]
        await self._session_db.append_message(
            target_id, str(message["role"]), str(message["content"]),
            workflow_event_id=event.event_id,
            projection_kind=message.get("projection_kind"),
            context_visibility=message.get("context_visibility"),
            skip_embed=bool(message.get("skip_embed", False)),
        )


class _SqliteGoalProjectionSink:
    """A separate-domain Goal sink with terminal-event idempotency."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def seed(self, goal_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                "CREATE TABLE IF NOT EXISTS goals(goal_id TEXT PRIMARY KEY,status TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS goal_terminal_events("
                "event_id TEXT PRIMARY KEY,goal_id TEXT UNIQUE NOT NULL,status TEXT NOT NULL);"
            )
            await db.execute("INSERT INTO goals VALUES(?, 'active')", (goal_id,))
            await db.commit()

    async def is_bound(self, target_id: str) -> bool:
        return bool(target_id)

    async def deliver(self, event: RunEvent, target_id: str) -> None:
        status = "done" if event.candidate.status is OutcomeStatus.SUCCEEDED else "abandoned"
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (await db.execute(
                "SELECT goal_id,status FROM goal_terminal_events WHERE event_id=?",
                (event.event_id,),
            )).fetchone()
            if existing is None:
                await db.execute(
                    "INSERT INTO goal_terminal_events VALUES(?,?,?)",
                    (event.event_id, target_id, status),
                )
                changed = await db.execute(
                    "UPDATE goals SET status=? WHERE goal_id=? AND status='active'",
                    (status, target_id),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("goal terminal conflict")
            elif existing != (target_id, status):
                raise RuntimeError("terminal event projection conflict")
            await db.commit()

    async def rows(self) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        async with aiosqlite.connect(self.path) as db:
            goals = await (await db.execute("SELECT goal_id,status FROM goals")).fetchall()
            events = await (await db.execute(
                "SELECT event_id,goal_id,status FROM goal_terminal_events"
            )).fetchall()
        return list(goals), list(events)


FAILURE_MASKS = tuple(product((False, True), repeat=3))
SINK_ORDERS = tuple(permutations(("session_db", "ws", "tts")))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_fails", "ws_fails", "tts_fails"),
    FAILURE_MASKS,
    ids=lambda value: "fail" if value else "ok",
)
async def test_every_partial_failure_subset_isolated_per_sink(
    tmp_path: Path,
    session_fails: bool,
    ws_fails: bool,
    tts_fails: bool,
) -> None:
    clock = _Clock()
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    await store.activate_runtime()
    await store.create(_spec())
    deliveries = _delivery_specs(
        session_id="session-1",
        session_sink_instance="local",
        ws_sink_instance="peer",
        ws_target_id="peer-group-1",
        tts_sink_instance="voice",
        tts_target_id="voice-connection-1",
    )
    final = await store.commit_run_outcome(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=deliveries,
    )
    session_sink = _ScriptedSink(failures=int(session_fails))
    ws_sink = _ScriptedSink(failures=int(ws_fails))
    tts_sink = _ScriptedSink(failures=int(tts_fails))
    worker = ExecutionDeliveryDispatcher(
        store,
        _registrations(session_sink, ws_sink, tts_sink),
        clock=clock,
        retry_base_seconds=1.0,
        owner_generation=1,
    )

    assert await _drain(worker, 3) == 3
    first = {
        row.sink_kind: row
        for row in await store.list_event_deliveries(final.event.event_id)
    }
    assert first["session_db"].status is (
        DeliveryStatus.FAILED if session_fails else DeliveryStatus.DELIVERED
    )
    assert first["ws"].status is (
        DeliveryStatus.FAILED if ws_fails else DeliveryStatus.DELIVERED
    )
    assert first["tts"].status is (
        DeliveryStatus.DISCARDED if tts_fails else DeliveryStatus.DELIVERED
    )
    assert first["session_db"].policy is DeliveryPolicy.DURABLE_REQUIRED
    assert first["ws"].policy is DeliveryPolicy.RETRY_WHILE_BOUND
    assert first["tts"].policy is DeliveryPolicy.BEST_EFFORT

    clock.advance(2.0)
    assert await _drain(worker, 3) == int(session_fails) + int(ws_fails)
    settled = {
        row.sink_kind: row
        for row in await store.list_event_deliveries(final.event.event_id)
    }
    assert settled["session_db"].status is DeliveryStatus.DELIVERED
    assert settled["ws"].status is DeliveryStatus.DELIVERED
    assert settled["tts"].status is (
        DeliveryStatus.DISCARDED if tts_fails else DeliveryStatus.DELIVERED
    )
    assert settled["session_db"].attempts == 1 + int(session_fails)
    assert settled["ws"].attempts == 1 + int(ws_fails)
    assert settled["tts"].attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sink_order", SINK_ORDERS, ids=lambda order: "-".join(order))
async def test_partial_failure_is_order_independent(
    tmp_path: Path,
    sink_order: tuple[str, str, str],
) -> None:
    clock = _Clock()
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    await store.activate_runtime()
    await store.create(_spec())
    final = await store.commit_run_outcome(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=_delivery_specs(
            session_id="session-1",
            session_sink_instance="local",
            ws_sink_instance="peer",
            ws_target_id="peer-1",
            tts_sink_instance="voice",
            tts_target_id="voice-1",
        ),
    )
    keys = {
        "session_db": ("session_db", "local"),
        "ws": ("ws", "peer"),
        "tts": ("tts", "voice"),
    }
    sinks = {
        kind: _ScriptedSink(failures=int(index == 1))
        for index, kind in enumerate(sink_order)
    }
    workers: dict[str, ExecutionDeliveryDispatcher] = {}
    for kind in sink_order:
        sink_kind, sink_instance = keys[kind]
        workers[kind] = ExecutionDeliveryDispatcher(
            store, (SinkRegistration(sink_kind, sink_instance, sinks[kind]),),
            clock=clock, retry_base_seconds=1.0, owner_generation=1
        )
        assert await workers[kind].run_once() is True

    failed_kind = sink_order[1]
    clock.advance(2.0)
    if failed_kind != "tts":
        assert await workers[failed_kind].run_once() is True
    rows = {
        row.sink_kind: row.status
        for row in await store.list_event_deliveries(final.event.event_id)
    }
    assert rows["session_db"] is DeliveryStatus.DELIVERED
    assert rows["ws"] is DeliveryStatus.DELIVERED
    assert rows["tts"] is (
        DeliveryStatus.DISCARDED
        if failed_kind == "tts"
        else DeliveryStatus.DELIVERED
    )


@pytest.mark.asyncio
async def test_expired_claim_is_recovered_and_stale_worker_cannot_ack(tmp_path: Path) -> None:
    clock = _Clock()
    path = tmp_path / "workflow.db"
    first_store = SqliteExecutionUnitOfWork(path, clock=clock)
    await first_store.activate_runtime()
    await first_store.create(_spec())
    delivery = DeliverySpec(
        sink_kind="session_db",
        sink_instance="local",
        target_id="session-1",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )
    final = await first_store.commit_run_outcome(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=(delivery,),
    )
    stale = await first_store.claim_delivery(owner_generation=1, claim_ttl_seconds=5.0)
    assert stale is not None and stale.delivery_version == 1

    clock.advance(6.0)
    restarted_store = SqliteExecutionUnitOfWork(path, clock=clock)
    recovered = await restarted_store.claim_delivery(owner_generation=1, claim_ttl_seconds=5.0)
    assert recovered is not None
    assert recovered.delivery_id == stale.delivery_id
    assert recovered.attempts == 2
    assert recovered.delivery_version == 2

    with pytest.raises(DeliveryClaimConflict):
        await first_store.settle_delivery(
            stale.delivery_id,
            expected_version=stale.delivery_version,
            owner_generation=1,
        )
    delivered = await restarted_store.settle_delivery(
        recovered.delivery_id,
        expected_version=recovered.delivery_version,
        owner_generation=1,
    )
    assert delivered.status is DeliveryStatus.DELIVERED


@pytest.mark.asyncio
async def test_dispatcher_generation_fence_rejects_stale_claim_and_completion(tmp_path: Path) -> None:
    clock = _Clock()
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    await store.activate_runtime()
    await store.create(_spec())
    final = await store.commit_run_outcome(
        "run-1", expected_version=0, terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(), deliveries=(DeliverySpec(
            sink_kind="session_db", sink_instance="local", target_id="session-1",
            policy=DeliveryPolicy.DURABLE_REQUIRED,
        ),),
    )
    assert await store.claim_delivery(owner_generation=2) is None
    claimed = await store.claim_delivery(owner_generation=1)
    assert claimed is not None
    with pytest.raises(DeliveryClaimConflict):
        await store.settle_delivery(
            claimed.delivery_id,
            expected_version=claimed.delivery_version,
            owner_generation=2,
        )
    row = (await store.list_event_deliveries(final.event.event_id))[0]
    assert row.status is DeliveryStatus.DELIVERING


@pytest.mark.asyncio
async def test_terminal_session_message_is_visible_once_after_crash_restart(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    workflow_path = tmp_path / "workflow.db"
    state_path = tmp_path / "state.db"
    first_store = SqliteExecutionUnitOfWork(workflow_path, clock=clock)
    await first_store.activate_runtime()
    await first_store.create(_spec())
    session_delivery = DeliverySpec(
        sink_kind="session_db",
        sink_instance="local",
        target_id="session-1",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )
    candidate = _terminal_candidate(
        payload={
            "session_message": {
                "role": "assistant",
                "content": "one visible terminal",
                "projection_kind": "final_assistant",
                "context_visibility": "conversation",
            }
        }
    )
    final = await first_store.commit_run_outcome(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=candidate,
        deliveries=(session_delivery,),
    )
    first_session_db = SessionDB(state_path)
    first_sink = _SessionDBSink(first_session_db)
    first_dispatcher = ExecutionDeliveryDispatcher(
        first_store,
        (
            SinkRegistration(
                "session_db", "local", first_sink
            ),
        ),
        owner_generation=1,
    )
    claimed = await first_store.claim_delivery(owner_generation=1, claim_ttl_seconds=5.0)
    assert claimed is not None
    await first_sink.deliver(await first_store.get_event(claimed.event_id), claimed.target_id)
    # Crash gap: the sink committed, but execution_deliveries was not acked.

    clock.advance(6.0)
    restarted_store = SqliteExecutionUnitOfWork(workflow_path, clock=clock)
    restarted_session_db = SessionDB(state_path)
    worker = ExecutionDeliveryDispatcher(
        restarted_store,
        (SinkRegistration(
            "session_db", "local", _SessionDBSink(restarted_session_db)
        ),),
        clock=clock,
        claim_ttl_seconds=5.0,
        owner_generation=1,
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    replay = await restarted_store.commit_run_outcome(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=candidate,
        deliveries=(session_delivery,),
    )
    assert replay.idempotent is True
    async with aiosqlite.connect(state_path) as db:
        messages = await (
            await db.execute(
                """SELECT content,workflow_event_id FROM messages
                WHERE workflow_event_id=?""",
                (final.event.event_id,),
            )
        ).fetchall()
    assert messages == [("one visible terminal", final.event.event_id)]
    rows = await restarted_store.list_event_deliveries(final.event.event_id)
    assert len(rows) == 1
    assert rows[0].status is DeliveryStatus.DELIVERED
    assert rows[0].attempts == 2


@pytest.mark.asyncio
async def test_root_terminal_goal_projection_is_once_across_child_race_and_restart(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    execution_path = tmp_path / "workflow.db"
    goal_path = tmp_path / "goals.db"
    store = SqliteExecutionUnitOfWork(execution_path, clock=clock)
    await store.activate_runtime()
    await store.create(_spec("root-run"))
    await store.create(_child_spec("root-run", "child-run"))
    goal = _SqliteGoalProjectionSink(goal_path)
    await goal.seed("goal-1")
    projection = DeliverySpec(
        sink_kind="goal_projection",
        sink_instance="session-goals",
        target_id="goal-1",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )

    with pytest.raises(RunIdentityConflict, match="child runs cannot project"):
        await store.commit_run_outcome(
            "child-run",
            expected_version=0,
            terminal_status=RunStatus.COMPLETED,
            event=_terminal_candidate(payload={"text": "child"}),
            deliveries=(projection,),
        )
    async with aiosqlite.connect(execution_path) as db:
        child_status = await (await db.execute(
            "SELECT status FROM execution_runs WHERE run_id='child-run'"
        )).fetchone()
    assert child_status == ("created",)

    completed = store.commit_run_outcome(
        "root-run",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(payload={"text": "winner-completed"}),
        deliveries=(projection,),
    )
    failed = store.commit_run_outcome(
        "root-run",
        expected_version=0,
        terminal_status=RunStatus.FAILED,
        event=RunEventCandidate(
            event_key="terminal",
            kind="run.final",
            status=OutcomeStatus.FAILED,
            driver_kind="react",
            payload={"text": "winner-failed"},
        ),
        deliveries=(projection,),
    )
    results = await asyncio.gather(completed, failed, return_exceptions=True)
    winner = next(item for item in results if not isinstance(item, Exception))
    assert sum(isinstance(item, TerminalConflict) for item in results) == 1
    assert len(await store.list_event_deliveries(winner.event.event_id)) == 1

    first = await store.claim_delivery(owner_generation=1, claim_ttl_seconds=5.0)
    assert first is not None
    await goal.deliver(await store.get_event(first.event_id), first.target_id)
    clock.advance(6.0)  # crash after Goal commit, before delivery completion
    restarted = SqliteExecutionUnitOfWork(execution_path, clock=clock)
    replay = ExecutionDeliveryDispatcher(
        restarted,
        (SinkRegistration("goal_projection", "session-goals", goal),),
        owner_generation=1,
        clock=clock,
        claim_ttl_seconds=5.0,
    )
    assert await replay.run_once() is True
    assert await replay.run_once() is False
    goals, events = await goal.rows()
    expected_status = "done" if winner.record.status is RunStatus.COMPLETED else "abandoned"
    assert goals == [("goal-1", expected_status)]
    assert events == [(winner.event.event_id, "goal-1", expected_status)]
    rows = await restarted.list_event_deliveries(winner.event.event_id)
    assert rows[0].status is DeliveryStatus.DELIVERED
    assert rows[0].attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_point", ("finalize_after_outbox", "finalize_before_commit"))
async def test_terminal_goal_outbox_is_atomic_across_fault_and_restart(
    tmp_path: Path, fault_point: str,
) -> None:
    path = tmp_path / "workflow.db"
    clock = _Clock()

    def crash(point: str) -> None:
        if point == fault_point:
            raise RuntimeError(f"crash:{point}")

    crashing = SqliteExecutionUnitOfWork(path, clock=clock, fault_injector=crash)
    await crashing.activate_runtime()
    await crashing.create(_spec())
    delivery = DeliverySpec(
        "goal_projection", "session-goals", "goal-1",
        DeliveryPolicy.DURABLE_REQUIRED,
    )
    with pytest.raises(RuntimeError, match=fault_point):
        await crashing.commit_run_outcome(
            "run-1", expected_version=0, terminal_status=RunStatus.COMPLETED,
            event=_terminal_candidate(), deliveries=(delivery,),
        )

    async with aiosqlite.connect(path) as db:
        event_count = await (await db.execute(
            "SELECT COUNT(*) FROM execution_events"
        )).fetchone()
        delivery_count = await (await db.execute(
            "SELECT COUNT(*) FROM execution_deliveries"
        )).fetchone()
        status = await (await db.execute(
            "SELECT status,terminal_event_id FROM execution_runs WHERE run_id='run-1'"
        )).fetchone()
    assert event_count == delivery_count == (0,)
    assert status == ("created", None)

    restarted = SqliteExecutionUnitOfWork(path, clock=clock)
    final = await restarted.commit_run_outcome(
        "run-1", expected_version=0, terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(), deliveries=(delivery,),
    )
    assert final.idempotent is False
    assert len(await restarted.list_event_deliveries(final.event.event_id)) == 1


@pytest.mark.asyncio
async def test_retry_while_bound_discards_after_peer_disconnect(tmp_path: Path) -> None:
    clock = _Clock()
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    await store.activate_runtime()
    await store.create(_spec())
    ws_delivery = DeliverySpec(
        sink_kind="ws",
        sink_instance="peer",
        target_id="peer-1",
        policy=DeliveryPolicy.RETRY_WHILE_BOUND,
    )
    final = await store.commit_run_outcome(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=(ws_delivery,),
    )
    sink = _ScriptedSink(failures=1)
    worker = ExecutionDeliveryDispatcher(
        store, (SinkRegistration("ws", "peer", sink),), clock=clock,
        retry_base_seconds=1.0, owner_generation=1,
    )
    assert await worker.run_once() is True
    sink.bound = False
    clock.advance(2.0)
    assert await worker.run_once() is True
    row = (await store.list_event_deliveries(final.event.event_id))[0]
    assert row.status is DeliveryStatus.DISCARDED
    assert row.attempts == 2


@pytest.mark.asyncio
async def test_expired_best_effort_claim_is_discarded_without_restart_replay(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    path = tmp_path / "workflow.db"
    first_store = SqliteExecutionUnitOfWork(path, clock=clock)
    await first_store.activate_runtime()
    await first_store.create(_spec())
    tts_delivery = DeliverySpec(
        sink_kind="tts",
        sink_instance="voice",
        target_id="old-voice-connection",
        policy=DeliveryPolicy.BEST_EFFORT,
    )
    final = await first_store.commit_run_outcome(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=(tts_delivery,),
    )
    claimed = await first_store.claim_delivery(owner_generation=1, claim_ttl_seconds=5.0)
    assert claimed is not None
    # Process dies after claim.  Even a mistakenly rebound target must not
    # replay the already-attempted best-effort cue after lease expiry.
    clock.advance(6.0)
    sink = _ScriptedSink(bound=True)
    restarted = SqliteExecutionUnitOfWork(path, clock=clock)
    worker = ExecutionDeliveryDispatcher(
        restarted,
        (SinkRegistration("tts", "voice", sink),),
        clock=clock,
        claim_ttl_seconds=5.0,
        owner_generation=1,
    )
    assert await worker.run_once() is False
    row = (await restarted.list_event_deliveries(final.event.event_id))[0]
    assert row.status is DeliveryStatus.DISCARDED
    assert row.last_error == "best-effort claim expired"
    assert sink.calls == []


def test_projector_is_not_registered_in_production_bootstrap() -> None:
    source = (Path(__file__).resolve().parents[3] / "backend" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "ExecutionProjector(" not in source
    assert "ExecutionDeliveryDispatcher(" not in source


def test_run_presenter_is_only_a_sink_and_cannot_create_delivery_rows() -> None:
    source = (Path(__file__).resolve().parents[3] / "backend" / "deskpet" / "agent" / "run_presenter.py").read_text(encoding="utf-8")
    assert "DeliverySpec" not in source
    assert "finalize_and_enqueue_delivery" not in source
    assert "execution_deliveries" not in source
