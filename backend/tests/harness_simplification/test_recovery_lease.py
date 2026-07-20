from __future__ import annotations

import pytest

from deskpet.execution import (
    OutcomeStatus,
    RunContext,
    RunCreate,
    RunEventCandidate,
    fingerprint_json,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork, StaleRecoveryLease


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

