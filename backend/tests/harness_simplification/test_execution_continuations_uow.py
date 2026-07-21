from __future__ import annotations

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    DecisionConflict,
    DecisionOpen,
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    RunContext,
    RunCreate,
    RunEventCandidate,
    VersionConflict,
    fingerprint_json,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork


CAPABILITY_HASH = fingerprint_json({"tools": ["read"], "scope": "workspace"})


def _spec(run_id: str) -> RunCreate:
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:session:request:{run_id}",
        context=RunContext(
            session_id="session",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=f"request:{run_id}",
            turn_id=f"turn:{run_id}",
            venue="text",
            workspace={},
            capability_hash=CAPABILITY_HASH,
            provider_plan={},
            trace_id=f"trace:{run_id}",
            principal_id="user",
        ),
        payload_fingerprint=fingerprint_json({"run_id": run_id}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react_short",
        persistence_level="durable",
    )


def _decision(run_id: str, decision_id: str = "decision-1") -> DecisionOpen:
    return DecisionOpen(
        decision_id=decision_id,
        run_id=run_id,
        nonce=f"nonce:{decision_id}",
        kind="clarification",
        prompt_schema_version=1,
        prompt={"question": "continue?"},
        expires_at=None,
    )


async def _open_store(path, *, fault_injector=None) -> SqliteExecutionUnitOfWork:
    store = SqliteExecutionUnitOfWork(
        path, clock=lambda: 100.0, fault_injector=fault_injector
    )
    await store.activate_runtime()
    return store


@pytest.mark.asyncio
async def test_save_and_load_continuation_survives_restart_with_full_json(tmp_path):
    path = tmp_path / "restart.db"
    store = await _open_store(path)
    await store.create(_spec("run-1"))
    payload = {
        "command_id": "command-1",
        "messages": [{"role": "assistant", "content": "calling tool"}],
        "calls": [{"call_id": "call-1", "args": {"path": "F:/tmp/a.txt"}}],
        "provider": {"model": "fixture", "iteration": 3},
    }

    saved = await store.persist_react_boundary("run-1", 0, payload)
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    loaded = await restarted.load_continuation("run-1")

    assert saved.version == 1
    assert loaded == saved
    assert dict(loaded.payload) == payload


@pytest.mark.asyncio
async def test_continuation_save_uses_strict_cas(tmp_path):
    path = tmp_path / "cas.db"
    store = await _open_store(path)
    await store.create(_spec("run-1"))
    first = await store.persist_react_boundary("run-1", 0, {"step": 1})
    second = await store.persist_react_boundary(
        "run-1", first.version, {"step": 2, "outcome": {"ok": True}}
    )

    with pytest.raises(VersionConflict) as stale_save:
        await store.persist_react_boundary("run-1", first.version, {"step": 99})
    assert stale_save.value.code == "continuation_replay_conflict"
    assert (await store.load_continuation("run-1")) == second

@pytest.mark.asyncio
async def test_continuation_and_pending_decision_commit_atomically(tmp_path):
    path = tmp_path / "decision.db"
    store = await _open_store(path)
    await store.create(_spec("run-1"))

    saved = await store.persist_react_boundary(
        "run-1", 0, {"command_id": "command-1"}, _decision("run-1")
    )

    assert saved.pending_decision_id == "decision-1"
    async with aiosqlite.connect(path) as db:
        decision = await (
            await db.execute(
                """SELECT run_id,status FROM execution_decisions
                WHERE decision_id='decision-1'"""
            )
        ).fetchone()
    assert decision == ("run-1", "open")


@pytest.mark.asyncio
async def test_decision_fault_rolls_back_decision_and_continuation_then_restart_recovers(
    tmp_path,
):
    path = tmp_path / "decision-crash.db"
    fired = False

    def fail_once(point: str) -> None:
        nonlocal fired
        if point == "continuation_after_decision" and not fired:
            fired = True
            raise RuntimeError("crash:continuation_after_decision")

    store = await _open_store(path, fault_injector=fail_once)
    await store.create(_spec("run-1"))
    with pytest.raises(RuntimeError, match="crash:continuation_after_decision"):
        await store.persist_react_boundary(
            "run-1", 0, {"command_id": "command-1"}, _decision("run-1")
        )

    async with aiosqlite.connect(path) as db:
        counts = await (
            await db.execute(
                """SELECT
                (SELECT COUNT(*) FROM execution_continuations),
                (SELECT COUNT(*) FROM execution_decisions)"""
            )
        ).fetchone()
    assert counts == (0, 0)

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    recovered = await restarted.persist_react_boundary(
        "run-1", 0, {"command_id": "command-1"}, _decision("run-1")
    )
    assert recovered.version == 1


@pytest.mark.asyncio
async def test_pending_decision_for_another_run_is_rejected_without_partial_write(tmp_path):
    path = tmp_path / "wrong-run.db"
    store = await _open_store(path)
    await store.create(_spec("run-1"))

    with pytest.raises(DecisionConflict) as error:
        await store.persist_react_boundary(
            "run-1", 0, {"command_id": "command-1"}, _decision("run-2")
        )
    assert error.value.code == "decision_run_mismatch"
    assert await store.load_continuation("run-1") is None


def _waiting_event() -> RunEventCandidate:
    return RunEventCandidate(
        event_key="boundary-waiting",
        kind="run.waiting",
        status=OutcomeStatus.WAITING,
        driver_kind="react",
        correlation={"command_id": "command-1"},
    )


def _delivery() -> DeliverySpec:
    return DeliverySpec(
        sink_kind="session_db",
        sink_instance="local",
        target_id="session",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )


@pytest.mark.asyncio
async def test_ephemeral_promotion_boundary_decision_event_and_delivery_are_atomic_and_idempotent(
    tmp_path,
):
    path = tmp_path / "promotion.db"
    store = await _open_store(path)
    spec = _spec("promoted-run")
    payload = {"command_id": "command-1", "calls": [{"call_id": "call-1"}]}

    created, continuation = await store.persist_react_boundary(
        spec,
        expected_run_version=4,
        expected_continuation_version=0,
        payload=payload,
        decision=_decision("promoted-run"),
        waiting_event=_waiting_event(),
        deliveries=(_delivery(),),
    )
    replay, replayed_continuation = await store.persist_react_boundary(
        spec,
        expected_run_version=4,
        expected_continuation_version=0,
        payload=payload,
        decision=_decision("promoted-run"),
        waiting_event=_waiting_event(),
        deliveries=(_delivery(),),
    )

    assert created.created is True
    assert created.record.version == 6
    assert created.record.status.value == "waiting"
    assert continuation.version == 1
    assert replay.created is False
    assert replay.record == created.record
    assert replayed_continuation == continuation
    async with aiosqlite.connect(path) as db:
        counts = await (
            await db.execute(
                """SELECT
                (SELECT COUNT(*) FROM execution_runs),
                (SELECT COUNT(*) FROM execution_continuations),
                (SELECT COUNT(*) FROM execution_decisions),
                (SELECT COUNT(*) FROM execution_events),
                (SELECT COUNT(*) FROM execution_deliveries)"""
            )
        ).fetchone()
        owner = await (
            await db.execute(
                """SELECT owner_kind,owner_generation FROM execution_runs
                WHERE run_id='promoted-run'"""
            )
        ).fetchone()
    assert counts == (1, 1, 1, 1, 1)
    assert owner == ("kernel", 1)


@pytest.mark.asyncio
async def test_existing_durable_run_persists_boundary_without_second_promotion(tmp_path):
    path = tmp_path / "already-durable.db"
    store = await _open_store(path)
    spec = _spec("durable-run")
    await store.create(spec)

    result, continuation = await store.persist_react_boundary(
        spec,
        expected_run_version=0,
        expected_continuation_version=0,
        payload={"command_id": "command-1"},
    )

    assert result.created is False
    assert result.record.version == 0
    assert continuation.version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_point",
    (
        "batch_boundary_after_promotion",
        "batch_boundary_after_continuation",
        "batch_boundary_after_waiting_event",
        "batch_boundary_before_commit",
    ),
)
async def test_promote_boundary_fault_rolls_back_every_table_and_restart_recovers(
    tmp_path, fault_point: str
):
    path = tmp_path / f"{fault_point}.db"
    fired = False

    def fail_once(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"crash:{point}")

    store = await _open_store(path, fault_injector=fail_once)
    spec = _spec("promoted-run")
    kwargs = dict(
        expected_run_version=2,
        expected_continuation_version=0,
        payload={"command_id": "command-1"},
        decision=_decision("promoted-run"),
        waiting_event=_waiting_event(),
        deliveries=(_delivery(),),
    )
    with pytest.raises(RuntimeError, match=f"crash:{fault_point}"):
        await store.persist_react_boundary(spec, **kwargs)

    async with aiosqlite.connect(path) as db:
        counts = await (
            await db.execute(
                """SELECT
                (SELECT COUNT(*) FROM execution_runs),
                (SELECT COUNT(*) FROM execution_continuations),
                (SELECT COUNT(*) FROM execution_decisions),
                (SELECT COUNT(*) FROM execution_events),
                (SELECT COUNT(*) FROM execution_deliveries)"""
            )
        ).fetchone()
    assert counts == (0, 0, 0, 0, 0)

    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    recovered, continuation = await restarted.persist_react_boundary(
        spec, **kwargs
    )
    assert recovered.created is True
    assert continuation.version == 1
