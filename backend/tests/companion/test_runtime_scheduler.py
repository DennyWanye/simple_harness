from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from deskpet.companion.contracts import OwnerRef
from deskpet.companion.identity_gate import FrozenOwnerIdentity, IdentityReadyGate
from deskpet.companion.runtime import (
    CompanionJobResult,
    CompanionRuntime,
    CompanionRuntimePolicy,
    ForegroundActivityGate,
)
from deskpet.companion.store import CompanionStore
from deskpet.execution.contracts import RunContext, RunCreate, fingerprint_json
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.elapsed = 0.0

    def now_utc(self) -> datetime:
        return self.value

    def monotonic(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds=seconds)
        await asyncio.sleep(0)

    def store_now(self) -> datetime:
        return self.value

    def advance(self, *, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)
        self.elapsed += seconds


class ExecutionState:
    def __init__(self) -> None:
        self.records: list[object] = []
        self.fail = False

    async def list_recoverable(self, **_kwargs):
        if self.fail:
            raise RuntimeError("execution db unavailable")
        return tuple(self.records)


def run_record(root_run_id: str, *, venue: str = "text") -> object:
    context = SimpleNamespace(root_run_id=root_run_id, venue=venue)
    return SimpleNamespace(spec=SimpleNamespace(context=context))


@pytest.fixture
def runtime_env(tmp_path):
    clock = MutableClock(datetime(2026, 7, 25, 4, 0, tzinfo=UTC))
    store = CompanionStore(tmp_path / "companion.db", clock=clock.store_now)
    owner = store.create_profile(
        profile_id="alice",
        generation=1,
        identity_namespace_hash="identity",
    )
    identity = IdentityReadyGate()
    identity.bind(
        FrozenOwnerIdentity(
            owner=owner,
            owner_key="companion:alice:1",
            binding_epoch=1,
        )
    )
    execution = ExecutionState()
    foreground = ForegroundActivityGate(execution, clock=clock)
    return clock, store, owner, identity, execution, foreground


async def wait_until(predicate, *, turns: int = 100) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


@pytest.mark.asyncio
async def test_foreground_gate_recomputes_durable_state_and_fails_closed(
    runtime_env,
) -> None:
    clock, _, _, _, execution, foreground = runtime_env
    execution.records = [
        run_record("root-1"),
        run_record("root-1"),
        run_record("background-1", venue="background"),
    ]
    busy = await foreground.snapshot()
    assert busy.busy is True
    assert busy.active_runs == 1

    execution.records = []
    clock.advance(seconds=12)
    idle = await foreground.snapshot()
    assert idle.busy is False
    assert idle.idle_for_seconds == 12

    execution.fail = True
    unavailable = await foreground.snapshot()
    assert unavailable.busy is True
    assert unavailable.reason_code == "execution_state_unavailable"


@pytest.mark.asyncio
async def test_foreground_busy_rebuilds_from_persisted_execution_run(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    first = SqliteExecutionUnitOfWork(path)
    await first.activate_runtime()
    capability_hash = "a" * 64
    context = RunContext(
        session_id="session",
        root_run_id="foreground-root",
        parent_run_id=None,
        request_id="request",
        turn_id="turn",
        venue="text",
        workspace={},
        capability_hash=capability_hash,
        provider_plan={},
        trace_id="trace",
        principal_id="user",
    )
    await first.create(
        RunCreate(
            run_id="foreground-root",
            idempotency_key="root:session:request:turn",
            context=context,
            payload_fingerprint=fingerprint_json({"text": "hello"}),
            capability_fingerprint=capability_hash,
            driver_kind="react",
            profile_key="react_short",
            persistence_level="durable",
        )
    )
    restarted = SqliteExecutionUnitOfWork(path)
    gate = ForegroundActivityGate(restarted)
    snapshot = await gate.snapshot()
    assert snapshot.busy is True
    assert snapshot.active_runs == 1
    assert snapshot.reason_code == "foreground_run_active"


@pytest.mark.asyncio
async def test_runtime_has_one_scheduler_and_bounded_children(runtime_env) -> None:
    clock, store, owner, identity, _, foreground = runtime_env
    for number in (1, 2):
        store.enqueue_job(
            owner,
            job_id=f"job-{number}",
            kind="reflection",
            dedupe_key=f"signal-{number}",
            payload={"requires_idle": True},
            budget_reserved_tokens=10,
            budget_reserved_ms=1000,
        )
    started: list[str] = []
    release = asyncio.Event()

    async def handler(_owner, claim):
        started.append(claim.item_id)
        await release.wait()
        return CompanionJobResult(result_hash=f"hash:{claim.item_id}")

    runtime = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=foreground,
        handler=handler,
        clock=clock,
        policy=CompanionRuntimePolicy(
            poll_interval_seconds=0.01,
            idle_window_seconds=0,
            max_children=1,
        ),
        authority_active=lambda: True,
    )
    assert await runtime.start(owner) is True
    assert await runtime.start(owner) is True
    await wait_until(lambda: started == ["job-1"])
    assert runtime.diagnostics()["scheduler_count"] == 1
    assert runtime.diagnostics()["child_count"] == 1
    release.set()
    await wait_until(lambda: len(started) == 2)
    await runtime.close()
    assert runtime.diagnostics()["scheduler_count"] == 0
    assert runtime.diagnostics()["child_count"] == 0
    assert store.get_job(owner, job_id="job-1")["status"] == "succeeded"
    assert store.get_job(owner, job_id="job-2")["status"] == "succeeded"


@pytest.mark.asyncio
async def test_scheduled_work_hook_runs_before_foreground_idle_gate(
    runtime_env,
) -> None:
    clock, store, owner, identity, execution, foreground = runtime_env
    execution.records = [run_record("foreground-root")]
    calls: list[OwnerRef] = []

    async def scheduled_work(current_owner: OwnerRef) -> None:
        calls.append(current_owner)

    runtime = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=foreground,
        handler=lambda _owner, _claim: CompanionJobResult(),
        clock=clock,
        policy=CompanionRuntimePolicy(
            poll_interval_seconds=0.01,
            idle_window_seconds=30,
        ),
        authority_active=lambda: True,
        scheduled_work_hook=scheduled_work,
    )
    assert await runtime.start(owner) is True
    await wait_until(lambda: bool(calls))
    await runtime.close()
    assert calls[0] == owner


@pytest.mark.asyncio
async def test_failed_model_run_persists_reason_and_replans_fresh_attempt(
    runtime_env,
) -> None:
    clock, store, owner, identity, _, foreground = runtime_env
    store.enqueue_job(
        owner,
        job_id="reflection-replan",
        kind="reflection",
        dedupe_key="reflection-replan",
        payload={"text": "reflect", "requires_idle": False},
        budget_reserved_tokens=10,
        budget_reserved_ms=1000,
    )
    attempts: list[int] = []

    async def handler(_owner, claim):
        attempts.append(claim.attempt)
        if claim.attempt == 1:
            return CompanionJobResult(
                status="failed",
                result_ref="run:first",
                result_hash="a" * 64,
                reason_code="background_run_failed",
                failure_context={
                    "execution_run_id": "run:first",
                    "terminal_error": {
                        "code": "provider_failed",
                        "message": "bad response",
                    },
                },
            )
        assert claim.payload["replan_failure"]["attempt"] == 1
        assert (
            claim.payload["replan_failure"]["failure"]["terminal_error"][
                "code"
            ]
            == "provider_failed"
        )
        return CompanionJobResult(
            result_ref="run:second",
            result_hash="b" * 64,
        )

    runtime = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=foreground,
        handler=handler,
        clock=clock,
        policy=CompanionRuntimePolicy(
            poll_interval_seconds=0.01,
            retry_delay_seconds=0,
            idle_window_seconds=0,
            max_retries=3,
        ),
        authority_active=lambda: True,
    )
    await runtime.start(owner)
    await wait_until(
        lambda: store.get_job(owner, job_id="reflection-replan")["status"]
        == "succeeded"
    )
    await runtime.close()
    row = store.get_job(owner, job_id="reflection-replan")
    payload = json.loads(row["payload_json"])
    assert attempts == [1, 2]
    assert row["attempt"] == 2
    assert row["result_ref"] == "run:second"
    assert len(payload["failure_history"]) == 1


@pytest.mark.asyncio
async def test_runtime_exception_persists_reason_and_replans_without_clock_advance(
    runtime_env,
) -> None:
    clock, store, owner, identity, _, foreground = runtime_env

    async def frozen_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    clock.sleep = frozen_sleep
    store.enqueue_job(
        owner,
        job_id="evaluation-timeout-replan",
        kind="evaluation",
        dedupe_key="evaluation-timeout-replan",
        payload={
            "text": "evaluate",
            "purpose": "evaluation",
            "requires_idle": False,
        },
        budget_reserved_tokens=10,
        budget_reserved_ms=1000,
    )
    attempts: list[int] = []

    async def handler(_owner, claim):
        attempts.append(claim.attempt)
        if claim.attempt == 1:
            raise TimeoutError
        failure = claim.payload["replan_failure"]
        assert failure["attempt"] == 1
        assert failure["failure"] == {
            "error_message": "TimeoutError",
            "error_type": "TimeoutError",
            "job_kind": "evaluation",
            "reason_code": "background_run_exception",
        }
        return CompanionJobResult(
            result_ref="run:recovered",
            result_hash="c" * 64,
        )

    runtime = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=foreground,
        handler=handler,
        clock=clock,
        policy=CompanionRuntimePolicy(
            poll_interval_seconds=0.01,
            retry_delay_seconds=5,
            idle_window_seconds=0,
            max_retries=3,
            claim_kinds=("evaluation",),
            retryable_kinds=("evaluation",),
        ),
        authority_active=lambda: True,
    )
    await runtime.start(owner)
    await wait_until(
        lambda: store.get_job(
            owner,
            job_id="evaluation-timeout-replan",
        )["status"]
        == "succeeded",
        turns=1000,
    )
    await runtime.close()
    row = store.get_job(owner, job_id="evaluation-timeout-replan")
    payload = json.loads(row["payload_json"])
    assert attempts == [1, 2]
    assert row["attempt"] == 2
    assert row["result_ref"] == "run:recovered"
    assert len(payload["failure_history"]) == 1


@pytest.mark.asyncio
async def test_dormant_authority_quiet_hours_and_budget_gate(runtime_env) -> None:
    clock, store, owner, identity, _, foreground = runtime_env
    calls = 0

    async def handler(_owner, _claim):
        nonlocal calls
        calls += 1
        return CompanionJobResult()

    dormant = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=foreground,
        handler=handler,
        clock=clock,
    )
    assert await dormant.start(owner) is False
    assert dormant.diagnostics()["scheduler_count"] == 0
    await dormant.close()

    clock.value = datetime(2026, 7, 25, 15, 0, tzinfo=UTC)  # 23:00 Shanghai
    store.enqueue_job(
        owner,
        job_id="quiet",
        kind="reflection",
        dedupe_key="quiet",
        payload={"requires_idle": False},
        budget_reserved_tokens=1,
        budget_reserved_ms=1,
    )
    runtime = CompanionRuntime(
        store=store,
        identity_gate=identity,
        foreground_gate=foreground,
        handler=handler,
        clock=clock,
        policy=CompanionRuntimePolicy(
            poll_interval_seconds=0.01,
            idle_window_seconds=0,
            max_job_tokens=50,
            daily_token_budget=50,
        ),
        authority_active=lambda: True,
    )
    await runtime.start(owner)
    await wait_until(
        lambda: store.get_job(owner, job_id="quiet")["reason_code"]
        == "job_deferred_quiet_hours"
    )
    assert calls == 0
    await runtime.close()


def test_job_claim_budget_is_atomic_and_retry_time_is_strict(runtime_env) -> None:
    clock, store, owner, *_ = runtime_env
    for number in (1, 2):
        store.enqueue_job(
            owner,
            job_id=f"budget-{number}",
            kind="reflection",
            dedupe_key=f"budget-{number}",
            payload={},
            budget_reserved_tokens=60,
            budget_reserved_ms=10,
        )
    window = "2026-07-25T00:00:00.000Z"
    first = store.claim_next_job_with_budget(
        owner,
        claim_owner="worker",
        lease_seconds=30,
        budget_window_start=window,
        token_budget=100,
        time_budget_ms=100,
        max_attempts=3,
        max_job_tokens=100,
        max_job_ms=100,
    )
    assert first is not None
    assert (
        store.claim_next_job_with_budget(
            owner,
            claim_owner="worker",
            lease_seconds=30,
            budget_window_start=window,
            token_budget=100,
            time_budget_ms=100,
            max_attempts=3,
            max_job_tokens=100,
            max_job_ms=100,
        )
        is None
    )
    with pytest.raises(ValueError, match="timezone_aware"):
        store.defer_job(
            owner,
            job_id=first.item_id,
            claim_owner=first.claim_owner,
            claim_epoch=first.claim_epoch,
            retry_at=datetime(2026, 7, 25, 5, 0),
            reason_code="test",
        )
    store.settle_job(
        owner,
        job_id=first.item_id,
        claim_owner=first.claim_owner,
        claim_epoch=first.claim_epoch,
        status="succeeded",
        result_ref=None,
        result_hash="budget-result",
        reason_code="budget_actual_settled",
        budget_actual_tokens=20,
        budget_actual_ms=10,
    )
    # A terminal job consumes its actual usage, not its former reservation.
    # The second 60-token job therefore fits in the same 100-token window.
    assert (
        store.claim_next_job_with_budget(
            owner,
            claim_owner="worker",
            lease_seconds=30,
            budget_window_start=window,
            token_budget=100,
            time_budget_ms=100,
            max_attempts=3,
            max_job_tokens=100,
            max_job_ms=100,
        )
        is not None
    )


def test_recover_expired_leases_retries_safe_job_and_expires_reminder(
    runtime_env,
) -> None:
    clock, store, owner, *_ = runtime_env
    for job_id, kind in (("reflection", "reflection"), ("reminder", "reminder")):
        store.enqueue_job(
            owner,
            job_id=job_id,
            kind=kind,
            dedupe_key=job_id,
            payload={},
        )
        assert store.claim_job(
            owner, claim_owner="crashed", lease_seconds=5
        ) is not None
    clock.advance(seconds=6)
    recovered = store.recover_expired_job_leases(
        owner,
        max_attempts=3,
        retryable_kinds=("reflection",),
        expire_kinds=("reminder",),
    )
    assert recovered == {"recovered": 1, "expired": 1, "failed": 0}
    assert store.get_job(owner, job_id="reflection")["status"] == "queued"
    assert store.get_job(owner, job_id="reminder")["status"] == "expired"


def test_recovery_does_not_blindly_retry_delegated_or_external_jobs(
    runtime_env,
) -> None:
    clock, store, owner, *_ = runtime_env
    for job_id in ("delegated_task", "external_action"):
        store.enqueue_job(
            owner,
            job_id=job_id,
            kind=job_id,
            dedupe_key=job_id,
            payload={},
        )
        assert store.claim_job(
            owner, claim_owner="crashed", lease_seconds=5
        ) is not None
    clock.advance(seconds=6)
    recovered = store.recover_expired_job_leases(
        owner,
        max_attempts=3,
        retryable_kinds=("reflection",),
        expire_kinds=("reminder",),
    )
    assert recovered == {"recovered": 0, "expired": 0, "failed": 2}
    assert store.get_job(owner, job_id="delegated_task")["status"] == "failed"
    assert store.get_job(owner, job_id="external_action")["status"] == "failed"
