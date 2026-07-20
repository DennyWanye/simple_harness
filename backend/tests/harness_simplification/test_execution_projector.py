"""WI-3 backend readiness contracts for projection and per-sink delivery."""

from __future__ import annotations

import asyncio
from itertools import permutations, product
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from deskpet.execution import (
    DeliveryClaimConflict,
    DeliveryPolicy,
    DeliverySpec,
    DeliveryStatus,
    LiveCursor,
    OutcomeStatus,
    RunContext,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunStatus,
    fingerprint_json,
)
from deskpet.harness.projector import (
    DeliveryWorker,
    EventMergeCursor,
    ExecutionProjector,
    SessionDBProjectionSink,
    SinkRegistration,
    run_event_envelope,
    standard_delivery_specs,
    tool_outcome_payload,
)
from deskpet.memory.session_db import SessionDB
from deskpet.harness.tool_executor import ToolOutcome, ToolOutcomeStatus
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

    async def project(self, event: RunEvent, target_id: str) -> None:
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


def _terminal_candidate(*, payload: dict[str, Any] | None = None) -> RunEventCandidate:
    return RunEventCandidate(
        event_key="terminal",
        kind="run.final",
        status=OutcomeStatus.SUCCEEDED,
        driver_kind="react",
        payload=payload or {},
    )


def _projector(
    store: SqliteExecutionUnitOfWork,
    session_sink: object,
    ws_sink: object,
    tts_sink: object,
) -> ExecutionProjector:
    return ExecutionProjector(
        store,
        (
            SinkRegistration("session_db", "local", session_sink),
            SinkRegistration("ws", "peer", ws_sink),
            SinkRegistration("tts", "voice", tts_sink),
        ),
    )


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
    await store.create(_spec())
    deliveries = standard_delivery_specs(
        session_id="session-1",
        session_sink_instance="local",
        ws_sink_instance="peer",
        ws_target_id="peer-group-1",
        tts_sink_instance="voice",
        tts_target_id="voice-connection-1",
    )
    final = await store.finalize(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=deliveries,
    )
    session_sink = _ScriptedSink(failures=int(session_fails))
    ws_sink = _ScriptedSink(failures=int(ws_fails))
    tts_sink = _ScriptedSink(failures=int(tts_fails))
    worker = DeliveryWorker(
        store,
        _projector(store, session_sink, ws_sink, tts_sink),
        clock=clock,
        retry_base_seconds=1.0,
    )

    assert await worker.drain(max_deliveries=3) == 3
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
    assert await worker.drain(max_deliveries=3) == int(session_fails) + int(ws_fails)
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
    assert await store.required_deliveries_complete(final.event.event_id) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("sink_order", SINK_ORDERS, ids=lambda order: "-".join(order))
async def test_partial_failure_is_order_independent(
    tmp_path: Path,
    sink_order: tuple[str, str, str],
) -> None:
    clock = _Clock()
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    await store.create(_spec())
    final = await store.finalize(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=standard_delivery_specs(
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
    workers: dict[str, DeliveryWorker] = {}
    for kind in sink_order:
        sink_kind, sink_instance = keys[kind]
        projector = ExecutionProjector(
            store,
            (SinkRegistration(sink_kind, sink_instance, sinks[kind]),),
        )
        workers[kind] = DeliveryWorker(
            store, projector, clock=clock, retry_base_seconds=1.0
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
    await first_store.create(_spec())
    delivery = DeliverySpec(
        sink_kind="session_db",
        sink_instance="local",
        target_id="session-1",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )
    final = await first_store.finalize(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=(delivery,),
    )
    stale = await first_store.claim_delivery(claim_ttl_seconds=5.0)
    assert stale is not None and stale.delivery_version == 1

    clock.advance(6.0)
    restarted_store = SqliteExecutionUnitOfWork(path, clock=clock)
    recovered = await restarted_store.claim_delivery(claim_ttl_seconds=5.0)
    assert recovered is not None
    assert recovered.delivery_id == stale.delivery_id
    assert recovered.attempts == 2
    assert recovered.delivery_version == 2

    with pytest.raises(DeliveryClaimConflict):
        await first_store.complete_delivery(
            stale.delivery_id,
            expected_version=stale.delivery_version,
        )
    delivered = await restarted_store.complete_delivery(
        recovered.delivery_id,
        expected_version=recovered.delivery_version,
    )
    assert delivered.status is DeliveryStatus.DELIVERED
    assert await restarted_store.required_deliveries_complete(
        final.event.event_id
    ) is True


@pytest.mark.asyncio
async def test_terminal_session_message_is_visible_once_after_crash_restart(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    workflow_path = tmp_path / "workflow.db"
    state_path = tmp_path / "state.db"
    first_store = SqliteExecutionUnitOfWork(workflow_path, clock=clock)
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
    final = await first_store.finalize(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=candidate,
        deliveries=(session_delivery,),
    )
    first_session_db = SessionDB(state_path)
    first_projector = ExecutionProjector(
        first_store,
        (
            SinkRegistration(
                "session_db", "local", SessionDBProjectionSink(first_session_db)
            ),
        ),
    )
    claimed = await first_store.claim_delivery(claim_ttl_seconds=5.0)
    assert claimed is not None
    assert await first_projector.dispatch(claimed) is True
    # Crash gap: the sink committed, but execution_deliveries was not acked.

    clock.advance(6.0)
    restarted_store = SqliteExecutionUnitOfWork(workflow_path, clock=clock)
    restarted_session_db = SessionDB(state_path)
    restarted_projector = ExecutionProjector(
        restarted_store,
        (
            SinkRegistration(
                "session_db",
                "local",
                SessionDBProjectionSink(restarted_session_db),
            ),
        ),
    )
    worker = DeliveryWorker(
        restarted_store,
        restarted_projector,
        clock=clock,
        claim_ttl_seconds=5.0,
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is False

    replay = await restarted_store.finalize(
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
    assert await restarted_store.required_deliveries_complete(
        final.event.event_id
    ) is True


@pytest.mark.asyncio
async def test_retry_while_bound_discards_after_peer_disconnect(tmp_path: Path) -> None:
    clock = _Clock()
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    await store.create(_spec())
    ws_delivery = DeliverySpec(
        sink_kind="ws",
        sink_instance="peer",
        target_id="peer-1",
        policy=DeliveryPolicy.RETRY_WHILE_BOUND,
    )
    final = await store.finalize(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=(ws_delivery,),
    )
    sink = _ScriptedSink(failures=1)
    projector = ExecutionProjector(
        store, (SinkRegistration("ws", "peer", sink),)
    )
    worker = DeliveryWorker(store, projector, clock=clock, retry_base_seconds=1.0)
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
    await first_store.create(_spec())
    tts_delivery = DeliverySpec(
        sink_kind="tts",
        sink_instance="voice",
        target_id="old-voice-connection",
        policy=DeliveryPolicy.BEST_EFFORT,
    )
    final = await first_store.finalize(
        "run-1",
        expected_version=0,
        terminal_status=RunStatus.COMPLETED,
        event=_terminal_candidate(),
        deliveries=(tts_delivery,),
    )
    claimed = await first_store.claim_delivery(claim_ttl_seconds=5.0)
    assert claimed is not None
    # Process dies after claim.  Even a mistakenly rebound target must not
    # replay the already-attempted best-effort cue after lease expiry.
    clock.advance(6.0)
    sink = _ScriptedSink(bound=True)
    restarted = SqliteExecutionUnitOfWork(path, clock=clock)
    worker = DeliveryWorker(
        restarted,
        ExecutionProjector(
            restarted, (SinkRegistration("tts", "voice", sink),)
        ),
        clock=clock,
        claim_ttl_seconds=5.0,
    )
    assert await worker.run_once() is False
    row = (await restarted.list_event_deliveries(final.event.event_id))[0]
    assert row.status is DeliveryStatus.DISCARDED
    assert row.last_error == "best-effort claim expired"
    assert sink.calls == []


@pytest.mark.asyncio
async def test_hydrate_and_live_epochs_use_independent_order_domains(tmp_path: Path) -> None:
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await store.create(_spec())
    first = await store.append_event(
        "run-1",
        expected_version=0,
        event=RunEventCandidate(
            event_key="accepted",
            kind="run.accepted",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="react",
        ),
    )
    second = await store.append_event(
        "run-1",
        expected_version=1,
        event=RunEventCandidate(
            event_key="progress",
            kind="run.progress",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="react",
        ),
    )
    live_sink = _ScriptedSink()
    projector = ExecutionProjector(
        store, (SinkRegistration("ws", "live", live_sink),)
    )
    cursor, hydrated = await projector.hydrate(EventMergeCursor("run-1"))
    assert hydrated == (first, second)
    assert cursor.durable_seq == 2

    cursor = cursor.activate_live_epoch("epoch-a")
    live_a1 = _live_event("live-a1", "epoch-a", 1)
    cursor, visible = await projector.project_live(
        live_a1,
        cursor=cursor,
        sink_kind="ws",
        sink_instance="live",
        target_id="peer-1",
    )
    assert visible is True and cursor.live_seq == 1
    cursor = cursor.activate_live_epoch("epoch-b")
    late_a2 = _live_event("live-a2", "epoch-a", 2)
    same_cursor, visible = await projector.project_live(
        late_a2,
        cursor=cursor,
        sink_kind="ws",
        sink_instance="live",
        target_id="peer-1",
    )
    assert visible is False and same_cursor == cursor
    live_b1 = _live_event("live-b1", "epoch-b", 1)
    cursor, visible = await projector.project_live(
        live_b1,
        cursor=cursor,
        sink_kind="ws",
        sink_instance="live",
        target_id="peer-1",
    )
    assert visible is True
    duplicate_cursor, visible = await projector.project_live(
        live_b1,
        cursor=cursor,
        sink_kind="ws",
        sink_instance="live",
        target_id="peer-1",
    )
    assert visible is False and duplicate_cursor == cursor
    assert live_sink.visible == ["live-a1", "live-b1"]

    third = await store.append_event(
        "run-1",
        expected_version=2,
        event=RunEventCandidate(
            event_key="waiting",
            kind="run.waiting",
            status=OutcomeStatus.WAITING,
            driver_kind="react",
        ),
    )
    cursor, hydrated = await projector.hydrate(cursor)
    assert hydrated == (third,)
    assert cursor.durable_seq == 3
    assert cursor.live_epoch == "epoch-b" and cursor.live_seq == 1
    async with aiosqlite.connect(store.path) as db:
        count = await (await db.execute("SELECT COUNT(*) FROM execution_events")).fetchone()
    assert count == (3,), "live token projection must not write SQLite"


def _live_event(event_id: str, epoch: str, sequence: int) -> RunEvent:
    return RunEvent(
        event_id=event_id,
        run_id="run-1",
        root_run_id="run-1",
        session_id="session-1",
        durable_seq=None,
        live_cursor=LiveCursor(stream_epoch=epoch, live_seq=sequence),
        candidate=RunEventCandidate(
            event_key=event_id,
            kind="assistant.token",
            status=OutcomeStatus.SUCCEEDED,
            driver_kind="react",
            payload={"text": event_id},
        ),
        created_at=float(sequence),
    )


def test_typed_failure_is_not_reinterpreted_as_success() -> None:
    outcome = ToolOutcome(
        call_id="call-1",
        effect_id="effect-1",
        status=ToolOutcomeStatus.FAILED,
        value={"ok": False},
        error="tool_failed",
    )
    assert tool_outcome_payload(outcome)["status"] == "failed"
    event = RunEvent(
        event_id="event-failed",
        run_id="run-1",
        root_run_id="run-1",
        session_id="session-1",
        durable_seq=1,
        candidate=RunEventCandidate(
            event_key="tool-failed",
            kind="tool.outcome",
            status=OutcomeStatus.FAILED,
            driver_kind="react",
            payload={"tool_outcome": tool_outcome_payload(outcome)},
            error={"code": "tool_failed"},
        ),
        created_at=1.0,
    )
    envelope = run_event_envelope(event)
    assert envelope["status"] == "failed"
    assert envelope["payload"]["tool_outcome"]["value"] == {"ok": False}


def test_projector_is_not_registered_in_production_bootstrap() -> None:
    source = (Path(__file__).resolve().parents[3] / "backend" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "ExecutionProjector(" not in source
    assert "DeliveryWorker(" not in source
