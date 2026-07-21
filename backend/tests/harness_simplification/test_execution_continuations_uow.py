from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    ActorContext,
    AdmissionBoundary,
    AdmissionLaunchUnknownFence,
    AdmissionPhase,
    AdmissionSpec,
    DecisionConflict,
    DecisionOpen,
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    ProviderLaunchSnapshot,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    RunStatus,
    DecisionSignal,
    VersionConflict,
    WorkflowRunSeed,
    fingerprint_json,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork


CAPABILITY_HASH = fingerprint_json({"tools": ["read"], "scope": "workspace"})
START_ADMISSION_HOOKS = (
    "batch_boundary_after_promotion", "batch_boundary_after_continuation",
    "batch_boundary_after_waiting_event", "batch_boundary_before_commit",
)
RESOLVE_ADMISSION_HOOKS = (
    "decision_resolve_after_cas", "decision_resolve_after_boundary",
    "decision_resolve_before_commit",
)
CLAIM_ADMISSION_HOOKS = (
    "effect_claim_after_grant", "effect_claim_after_effect", "effect_claim_before_commit",
)
LAUNCH_UNKNOWN_HOOKS = ("finalize_after_outbox", "finalize_before_commit")


def _spec(run_id: str, driver_kind: str = "react", profile_key: str = "react_short") -> RunCreate:
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
        driver_kind=driver_kind,
        profile_key=profile_key,
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


def _admission(run_id: str, driver_kind: str = "react", profile_key: str = "react_short") -> tuple[AdmissionSpec, AdmissionBoundary]:
    admission = AdmissionSpec(
        kind="plan", prompt_schema_version=1, response_schema_version=1,
        prompt={"question": "run this plan?"}, presentation={"steps": ["one"]},
        expires_at=None,
    )
    boundary = AdmissionBoundary(
        run_id=run_id, decision_id=f"decision:{run_id}", nonce=f"nonce:{run_id}",
        launch_operation_id=f"launch:{run_id}", driver_kind=driver_kind,
        profile_key=profile_key, admission=admission, phase="pending",
        boundary_version=1, canonical_messages=({"role": "user", "content": "do it"},),
        request_payload={"text": "do it"},
        provider_snapshot=ProviderLaunchSnapshot("provider", "adapter", "v1", False, None),
        capability_snapshot={"tools": ["read"]},
    )
    return admission, boundary


def _admission_waiting(driver_kind: str = "react") -> RunEventCandidate:
    return RunEventCandidate(
        event_key="admission:waiting", kind="admission.waiting",
        status="waiting", driver_kind=driver_kind,
    )


def _admission_signal(run_id: str, allow: bool, response=None) -> DecisionSignal:
    return DecisionSignal(
        decision_id=f"decision:{run_id}", run_id=run_id,
        expected_session_id="session", nonce=f"nonce:{run_id}",
        expected_version=0, allow=allow, response_schema_version=1,
        response=response or {"resolution": "accepted" if allow else "rejected"},
    )


@pytest.mark.asyncio
async def test_admission_launched_without_continuation_footprint_finalizes_unknown(tmp_path):
    path, run_id = tmp_path / "admission.db", "admitted-run"
    store = await _open_store(path)
    admission, boundary = _admission(run_id)
    started = await store.start_admission(_spec(run_id), admission, boundary, _admission_waiting())
    replay = await store.start_admission(_spec(run_id), admission, boundary, _admission_waiting())
    assert started == replay == boundary

    actor, ref = ActorContext("user", "session", 0), RunRef(run_id, "session")
    signal = _admission_signal(run_id, True)
    resolved = await store.resolve_admission(ref, actor, signal, expected_boundary_version=1)
    duplicate = await store.resolve_admission(ref, actor, signal, expected_boundary_version=1)
    assert resolved.boundary.phase is AdmissionPhase.ACCEPTED_START_PENDING
    assert duplicate.duplicate is True
    with pytest.raises(DecisionConflict, match="typed launch claim"):
        await store.persist_react_boundary(run_id, 2, {"command_id": "unfenced"})

    lease = await store.recovery_scope(run_id, owner="admission-test")
    claimed = await store.claim_admission_launch(lease, expected_boundary_version=2)
    duplicate_claim = await store.claim_admission_launch(lease, expected_boundary_version=2)
    assert claimed.boundary.phase is AdmissionPhase.LAUNCH_CLAIMED
    assert duplicate_claim.duplicate is True

    launched = await store.persist_react_boundary(
        run_id, 3, {"command_id": "provider-emission"},
        recovery_lease=lease, admission_launch=claimed,
    )
    launched_boundary = AdmissionBoundary.from_dict(launched.payload["_admission"])
    assert (launched.version, launched_boundary.phase, launched_boundary.consumed) == (
        4, AdmissionPhase.LAUNCHED, True,
    )
    progressed_replay = await store.resolve_admission(
        ref, actor, signal, expected_boundary_version=4,
        terminal_deliveries=(_delivery(),))
    assert progressed_replay.duplicate is True

    final = RunEventCandidate(
        event_key="run:final", kind="run.final", status="failed", driver_kind="react",
        payload={"error_code": "launch_outcome_unknown", "retry_safe": False,
                 "launch_operation_id": boundary.launch_operation_id},
    )
    result = await store.commit_run_outcome(
        run_id, expected_version=2, terminal_status=RunStatus.FAILED, event=final,
        recovery_lease=lease,
        admission_failure=AdmissionLaunchUnknownFence(
            run_id, boundary.decision_id, boundary.launch_operation_id, 4,
        ),
    )
    persisted = await store.load_continuation(run_id)
    assert result.record.status is RunStatus.FAILED
    assert AdmissionBoundary.from_dict(persisted.payload["_admission"]).phase is AdmissionPhase.LAUNCH_UNKNOWN


@pytest.mark.asyncio
async def test_admission_rejection_atomically_consumes_and_terminalizes(tmp_path):
    path, run_id = tmp_path / "admission-reject.db", "rejected-run"
    store = await _open_store(path)
    admission, boundary = _admission(run_id)
    await store.start_admission(_spec(run_id), admission, boundary, _admission_waiting())
    result = await store.resolve_admission(
        RunRef(run_id, "session"), ActorContext("user", "session", 0),
        _admission_signal(run_id, False), expected_boundary_version=1,
    )
    record = await store.query(RunRef(run_id, "session"), ActorContext("user", "session", 0))
    assert result.boundary.phase is AdmissionPhase.REJECTED
    assert result.boundary.consumed is True
    assert record.status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_post_accept_cancel_and_expiry_replays_are_idempotent(tmp_path):
    actor = ActorContext("user", "session", 0)

    cancel_store = await _open_store(tmp_path / "cancel-replay.db")
    admission, boundary = _admission("cancel-replay")
    ref = RunRef(boundary.run_id, "session")
    await cancel_store.start_admission(
        _spec(boundary.run_id), admission, boundary, _admission_waiting())
    await cancel_store.resolve_admission(
        ref, actor, _admission_signal(boundary.run_id, True),
        expected_boundary_version=1)
    cancel = DecisionSignal(
        decision_id=boundary.decision_id, run_id=boundary.run_id,
        expected_session_id="session", nonce=boundary.nonce,
        expected_version=1, allow=False, response_schema_version=1,
        response={"resolution": "cancelled"})
    first = await cancel_store.resolve_admission(
        ref, actor, cancel, expected_boundary_version=2)
    replay = await cancel_store.resolve_admission(
        ref, actor, cancel, expected_boundary_version=2)
    assert first.boundary.phase is AdmissionPhase.CANCELLED
    assert replay.duplicate is True and replay.boundary == first.boundary
    with pytest.raises(DecisionConflict, match="another resolution already won"):
        await cancel_store.resolve_admission(
            ref, actor,
            DecisionSignal(
                decision_id=boundary.decision_id, run_id=boundary.run_id,
                expected_session_id="session", nonce=boundary.nonce,
                expected_version=1, allow=False, response_schema_version=1,
                response={"resolution": "cancelled", "reason": "changed"}),
            expected_boundary_version=2)

    now = [100.0]
    expired_store = SqliteExecutionUnitOfWork(
        tmp_path / "expired-replay.db", clock=lambda: now[0])
    await expired_store.activate_runtime()
    admission, boundary = _admission("expired-replay")
    admission = AdmissionSpec(
        kind="plan", prompt_schema_version=1, response_schema_version=1,
        prompt={"question": "run this plan?"}, presentation={"steps": ["one"]},
        expires_at=101.0)
    value = boundary.to_dict()
    value["admission"] = admission.to_dict()
    boundary = AdmissionBoundary.from_dict(value)
    ref = RunRef(boundary.run_id, "session")
    await expired_store.start_admission(
        _spec(boundary.run_id), admission, boundary, _admission_waiting())
    now[0] = 102.0
    signal = _admission_signal(boundary.run_id, True)
    first = await expired_store.resolve_admission(
        ref, actor, signal, expected_boundary_version=1,
        terminal_deliveries=(_delivery(),))
    replay = await expired_store.resolve_admission(
        ref, actor, signal, expected_boundary_version=1,
        terminal_deliveries=(_delivery(),))
    assert first.boundary.phase is AdmissionPhase.EXPIRED
    assert replay.duplicate is True and replay.boundary == first.boundary
    assert await expired_store.list_event_deliveries(first.event.event_id)
    with pytest.raises(DecisionConflict, match="another resolution already won"):
        await expired_store.resolve_admission(
            ref, actor,
            DecisionSignal(
                decision_id=boundary.decision_id, run_id=boundary.run_id,
                expected_session_id="session", nonce=boundary.nonce,
                expected_version=0, allow=False, response_schema_version=1,
                response=dict(signal.response)),
            expected_boundary_version=1,
            terminal_deliveries=(_delivery(),))


@pytest.mark.asyncio
async def test_cancel_vs_launch_claim_has_exactly_one_winner(tmp_path):
    path, run_id = tmp_path / "claim-cancel-race.db", "claim-cancel-race"
    store = await _open_store(path)
    admission, boundary = _admission(run_id)
    actor, ref = ActorContext("user", "session", 0), RunRef(run_id, "session")
    await store.start_admission(
        _spec(run_id), admission, boundary, _admission_waiting())
    await store.resolve_admission(
        ref, actor, _admission_signal(run_id, True), expected_boundary_version=1)
    lease = await store.recovery_scope(run_id, owner="race")
    cancel = DecisionSignal(
        decision_id=boundary.decision_id, run_id=run_id,
        expected_session_id="session", nonce=boundary.nonce,
        expected_version=1, allow=False, response_schema_version=1,
        response={"resolution": "cancelled"})
    results = await asyncio.gather(
        store.resolve_admission(ref, actor, cancel, expected_boundary_version=2),
        store.claim_admission_launch(lease, expected_boundary_version=2),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    current = AdmissionBoundary.from_dict(
        (await store.load_continuation(run_id)).payload["_admission"])
    assert current.phase in {AdmissionPhase.CANCELLED, AdmissionPhase.LAUNCH_CLAIMED}


def _fail_once(target: str):
    fired = False
    def inject(point: str) -> None:
        nonlocal fired
        if point == target and not fired:
            fired = True
            raise RuntimeError(f"crash:{point}")
    return inject


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", START_ADMISSION_HOOKS)
async def test_start_admission_fault_cases_restart_atomically(tmp_path, hook):
    path, run_id = tmp_path / f"{hook}.db", "fault-admission"
    store = await _open_store(path, fault_injector=_fail_once(hook))
    admission, boundary = _admission(run_id)
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await store.start_admission(_spec(run_id), admission, boundary, _admission_waiting())
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    assert await restarted.start_admission(
        _spec(run_id), admission, boundary, _admission_waiting()
    ) == boundary


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", RESOLVE_ADMISSION_HOOKS)
async def test_resolve_admission_fault_cases_restart_atomically(tmp_path, hook):
    path, run_id = tmp_path / f"{hook}.db", "fault-resolve"
    admission, boundary = _admission(run_id)
    setup = await _open_store(path)
    await setup.start_admission(_spec(run_id), admission, boundary, _admission_waiting())
    signal = _admission_signal(run_id, True)
    failing = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0, fault_injector=_fail_once(hook))
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await failing.resolve_admission(
            RunRef(run_id, "session"), ActorContext("user", "session", 0), signal,
            expected_boundary_version=1,
        )
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    resolved = await restarted.resolve_admission(
        RunRef(run_id, "session"), ActorContext("user", "session", 0), signal,
        expected_boundary_version=1,
    )
    assert resolved.boundary.phase is AdmissionPhase.ACCEPTED_START_PENDING


async def _accepted_claim(path, run_id: str, *, driver_kind="react", profile_key="react_short"):
    store = await _open_store(path)
    admission, boundary = _admission(run_id, driver_kind, profile_key)
    await store.start_admission(
        _spec(run_id, driver_kind, profile_key), admission, boundary,
        _admission_waiting(driver_kind),
    )
    await store.resolve_admission(
        RunRef(run_id, "session"), ActorContext("user", "session", 0),
        _admission_signal(run_id, True), expected_boundary_version=1,
    )
    lease = await store.recovery_scope(run_id, owner="fault-worker")
    return store, boundary, lease


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", CLAIM_ADMISSION_HOOKS)
async def test_claim_admission_fault_cases_restart_atomically(tmp_path, hook):
    path, run_id = tmp_path / f"{hook}.db", "fault-claim"
    _, _, lease = await _accepted_claim(path, run_id)
    failing = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0, fault_injector=_fail_once(hook))
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await failing.claim_admission_launch(lease, expected_boundary_version=2)
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    claim = await restarted.claim_admission_launch(lease, expected_boundary_version=2)
    assert claim.boundary.phase is AdmissionPhase.LAUNCH_CLAIMED


def _launch_unknown_event(boundary: AdmissionBoundary) -> RunEventCandidate:
    return RunEventCandidate(
        event_key="run:final", kind="run.final", status="failed", driver_kind="react",
        payload={"error_code": "launch_outcome_unknown", "retry_safe": False,
                 "launch_operation_id": boundary.launch_operation_id},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", LAUNCH_UNKNOWN_HOOKS)
async def test_launch_unknown_fault_cases_restart_atomically(tmp_path, hook):
    path, run_id = tmp_path / f"{hook}.db", "fault-unknown"
    store, boundary, lease = await _accepted_claim(path, run_id)
    claim = await store.claim_admission_launch(lease, expected_boundary_version=2)
    continuation = await store.persist_react_boundary(
        run_id, 3, {"command_id": "provider-emission"},
        recovery_lease=lease, admission_launch=claim,
    )
    launched = AdmissionBoundary.from_dict(continuation.payload["_admission"])
    fence = AdmissionLaunchUnknownFence(
        run_id, boundary.decision_id, boundary.launch_operation_id, launched.boundary_version,
    )
    failing = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0, fault_injector=_fail_once(hook))
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await failing.commit_run_outcome(
            run_id, expected_version=2, terminal_status="failed",
            event=_launch_unknown_event(boundary), recovery_lease=lease,
            admission_failure=fence,
        )
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    result = await restarted.commit_run_outcome(
        run_id, expected_version=2, terminal_status="failed",
        event=_launch_unknown_event(boundary), recovery_lease=lease,
        admission_failure=fence,
    )
    assert result.record.status is RunStatus.FAILED


def _workflow_seed(run_id: str) -> WorkflowRunSeed:
    snapshot = {"tools": ["read"], "scope": "workspace"}
    return WorkflowRunSeed(
        request_key=f"request:{run_id}", workflow_name="code", workflow_version="v1",
        manifest_hash="manifest", implementation_hash="implementation",
        capability_hash=fingerprint_json(snapshot), capability_snapshot=snapshot,
        state_schema_version=1, trace_id=f"trace:{run_id}", thread_id=f"thread:{run_id}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", START_ADMISSION_HOOKS)
async def test_workflow_admission_consume_fault_cases_restart_atomically(tmp_path, hook):
    path, run_id = tmp_path / f"workflow-{hook}.db", "fault-workflow"
    _, _, lease = await _accepted_claim(
        path, run_id, driver_kind="workflow", profile_key="code/v1",
    )
    base = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0)
    claim = await base.claim_admission_launch(lease, expected_boundary_version=2)
    spec, seed = _spec(run_id, "workflow", "code/v1"), _workflow_seed(run_id)
    accepted = RunEventCandidate(
        event_key="workflow:accepted", kind="workflow.accepted",
        status="accepted", driver_kind="workflow",
    )
    failing = SqliteExecutionUnitOfWork(path, clock=lambda: 100.0, fault_injector=_fail_once(hook))
    with pytest.raises(RuntimeError, match=f"crash:{hook}"):
        await failing.start_workflow(
            spec, seed, accepted_event=accepted, admission_launch=claim,
        )
    restarted = SqliteExecutionUnitOfWork(path, clock=lambda: 101.0)
    result = await restarted.start_workflow(
        spec, seed, accepted_event=accepted, admission_launch=claim,
    )
    replay = await restarted.start_workflow(
        spec, seed, accepted_event=accepted, admission_launch=claim,
    )
    assert (
        result.execution_created, result.workflow_created,
        result.admission_consumed, result.start_claimed,
    ) == (False, True, True, True)
    assert (
        replay.execution_created, replay.workflow_created,
        replay.admission_consumed, replay.start_claimed,
    ) == (False, False, False, False)
