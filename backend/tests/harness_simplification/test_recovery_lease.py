from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    ActorContext,
    GrantConsume,
    OutcomeStatus,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunStatus,
    fingerprint_json,
)
from deskpet.workflows.store import (
    SqliteExecutionUnitOfWork,
    StaleRecoveryLease,
    WorkflowRunStore,
)


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


async def _create_workflow(store: WorkflowRunStore, run_id: str) -> None:
    await store.create_run(
        request_key=f"request:{run_id}",
        session_id="session",
        request_id=f"request:{run_id}",
        turn_id=f"turn:{run_id}",
        workflow_name="test",
        workflow_version="1",
        manifest_hash="m" * 64,
        implementation_hash="i" * 64,
        capability_hash=CAPABILITY_HASH,
        capability_snapshot={},
        state_schema_version=1,
        run_id=run_id,
    )


@pytest.mark.asyncio
async def test_recovery_lease_claim_renew_release_and_expired_takeover(tmp_path):
    now = [100.0]
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: now[0])
    await store.create(_spec("run-lease"))

    first = await store.claim_recovery("run-lease", owner="worker-a", lease_seconds=10)
    assert (first.owner, first.epoch, first.expires_at) == ("worker-a", 1, 110.0)
    with pytest.raises(StaleRecoveryLease, match="another owner"):
        await store.claim_recovery("run-lease", owner="worker-b", lease_seconds=10)

    now[0] = 105.0
    renewed = await store.renew_recovery(first, lease_seconds=20)
    assert (renewed.epoch, renewed.expires_at) == (1, 125.0)
    await store.assert_recovery_fence(renewed)

    now[0] = 126.0
    second = await store.claim_recovery("run-lease", owner="worker-b", lease_seconds=10)
    assert (second.owner, second.epoch, second.expires_at) == ("worker-b", 2, 136.0)
    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await store.assert_recovery_fence(renewed)
    assert await store.release_recovery(renewed) is False
    assert await store.release_recovery(second) is True


@pytest.mark.asyncio
async def test_stale_recovery_epoch_cannot_append_event(tmp_path):
    now = [100.0]
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: now[0])
    created = await store.create(_spec("run-write-fence"))
    stale = await store.claim_recovery(
        "run-write-fence", owner="worker-a", lease_seconds=5
    )
    now[0] = 106.0
    current = await store.claim_recovery(
        "run-write-fence", owner="worker-b", lease_seconds=10
    )

    event = RunEventCandidate(
        event_key="recovered",
        kind="recovered",
        status=OutcomeStatus.ACCEPTED,
        driver_kind="react",
    )
    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await store.append_event(
            "run-write-fence",
            expected_version=created.record.version,
            event=event,
            recovery_lease=stale,
        )

    stored = await store.append_event(
        "run-write-fence",
        expected_version=created.record.version,
        event=event,
        recovery_lease=current,
    )
    assert stored.candidate.kind == "recovered"


@pytest.mark.asyncio
async def test_same_owner_concurrent_claim_reuses_one_epoch(tmp_path):
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: 100.0)
    await store.create(_spec("run-same-owner"))

    leases = await asyncio.gather(
        *(store.claim_recovery("run-same-owner", owner="worker-a") for _ in range(12))
    )

    assert {(lease.owner, lease.epoch) for lease in leases} == {("worker-a", 1)}


@pytest.mark.asyncio
async def test_stale_recovery_cannot_write_continuation_or_terminal(tmp_path):
    now = [100.0]
    store = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=lambda: now[0])
    created = await store.create(_spec("run-stale-writes"))
    stale = await store.claim_recovery("run-stale-writes", owner="worker-a", lease_seconds=5)
    now[0] = 106.0
    await store.claim_recovery("run-stale-writes", owner="worker-b", lease_seconds=10)

    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await store.save_continuation(
            "run-stale-writes", 0, {"step": 1}, recovery_lease=stale
        )
    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await store.finalize_and_enqueue_delivery(
            "run-stale-writes",
            expected_version=created.record.version,
            terminal_status=RunStatus.COMPLETED,
            event=RunEventCandidate(
                event_key="terminal",
                kind="run.final",
                status=OutcomeStatus.SUCCEEDED,
                driver_kind="react",
            ),
            recovery_lease=stale,
        )

    assert await store.load_continuation("run-stale-writes") is None
    assert await store.list_events("run-stale-writes") == ()


@pytest.mark.asyncio
async def test_workflow_handoff_is_atomic_and_same_owner_idempotent(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: now[0])
    workflows = WorkflowRunStore(path, clock=lambda: now[0])
    await store.create(_spec("run-workflow"))
    await _create_workflow(workflows, "run-workflow")
    stale = await store.claim_recovery("run-workflow", owner="worker-a", lease_seconds=5)

    first = await store.claim_workflow_recovery_handoff(
        stale, workflow_owner="workflow-worker"
    )
    repeated = await store.claim_workflow_recovery_handoff(
        stale, workflow_owner="workflow-worker"
    )
    assert (first.lease_epoch, first.run_version) == (
        repeated.lease_epoch,
        repeated.run_version,
    )

    now[0] = 106.0
    await store.claim_recovery("run-workflow", owner="worker-b", lease_seconds=10)
    before = await workflows.get_run("run-workflow")
    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await store.claim_workflow_recovery_handoff(
            stale, workflow_owner="stale-workflow-worker"
        )
    after = await workflows.get_run("run-workflow")
    assert after == before


@pytest.mark.asyncio
async def test_stale_recovery_cannot_claim_effect_before_external_execution(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    store = SqliteExecutionUnitOfWork(path, clock=lambda: now[0])
    await store.create(_spec("run-stale-effect"))
    stale = await store.claim_recovery("run-stale-effect", owner="worker-a", lease_seconds=5)
    now[0] = 106.0
    await store.claim_recovery("run-stale-effect", owner="worker-b", lease_seconds=10)
    request = GrantConsume(
        grant_id="grant", decision_id="decision", decision_nonce="nonce",
        run_id="run-stale-effect", expected_session_id="session", call_id="call",
        effect_id="effect", tool_name="write", args_hash="a" * 64,
        capability_hash=CAPABILITY_HASH, scope_hash="d" * 64,
    )
    actor = ActorContext("user", "session", 0)

    with pytest.raises(StaleRecoveryLease, match="lost its lease"):
        await store.consume_grant_and_claim_effect(
            request, actor, effect_type="tool", policy={}, prepared={},
            worker_owner="worker-a", worker_epoch=1, recovery_lease=stale,
        )
    async with aiosqlite.connect(path) as db:
        count = await (await db.execute("SELECT COUNT(*) FROM execution_effects")).fetchone()
    assert count == (0,)
