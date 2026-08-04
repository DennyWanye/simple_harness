from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from deskpet.companion.activation import (
    ActivationDispatchError,
    ActivationDispatcher,
    PreparedCapabilityMutation,
    PreparedMutationInstance,
    RuntimeInstanceStartOutcome,
)
from deskpet.companion.authority import RevocationBarrier
from deskpet.companion.contracts import (
    CapabilityMutationReceipt,
    MutationAction,
    MutationRequest,
)
from deskpet.companion.store import CompanionStore, canonical_hash


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _prepared(*, with_runtime: bool) -> PreparedCapabilityMutation:
    instances = (
        (
            PreparedMutationInstance(
                ordinal=0,
                instance_id="runtime-1",
                adapter_id="managed-process-job-v1",
                instance_hash="instance-hash-1",
            ),
        )
        if with_runtime
        else ()
    )
    payload = {
        "schema_version": 1,
        "manager_operation_id": "manager-op-1",
        "runtime_set_ref": "runtime-set-1" if with_runtime else None,
        "runtime_set_hash": "runtime-hash-1" if with_runtime else None,
        "instances": [
            {
                "ordinal": item.ordinal,
                "instance_id": item.instance_id,
                "adapter_id": item.adapter_id,
                "instance_hash": item.instance_hash,
            }
            for item in instances
        ],
    }
    return PreparedCapabilityMutation(
        manager_operation_id="manager-op-1",
        runtime_set_ref=payload["runtime_set_ref"],
        runtime_set_hash=payload["runtime_set_hash"],
        instances=instances,
        prepared_hash=canonical_hash(payload),
    )


class _Platform:
    def __init__(
        self,
        *,
        with_runtime: bool = False,
        start_outcome: str = "started",
        healthy: bool = True,
        cleanup_ok: bool = True,
    ) -> None:
        self.prepared = _prepared(with_runtime=with_runtime)
        self.start_outcome = start_outcome
        self.healthy = healthy
        self.cleanup_ok = cleanup_ok
        self.trace: list[str] = []
        self.authorization = None

    async def prepare_mutation(
        self,
        authorization,
        *,
        persisted_operation_id,
        persisted_runtime_set_ref,
        persisted_runtime_set_hash,
    ):
        self.trace.append("prepare")
        self.authorization = authorization
        assert persisted_operation_id is None
        assert persisted_runtime_set_ref is None
        assert persisted_runtime_set_hash is None
        return self.prepared

    async def start_runtime_instance(
        self, authorization, prepared, instance
    ):
        self.trace.append("start")
        assert authorization.authorization_hash
        assert prepared is self.prepared
        assert instance.ordinal == 0
        return RuntimeInstanceStartOutcome(
            outcome=self.start_outcome,
            start_receipt_hash="start-receipt-1",
            reason_code=f"runtime_{self.start_outcome}",
        )

    async def await_runtime_instance_health(
        self, authorization, prepared, instance, start
    ):
        self.trace.append("health")
        assert start.outcome == "started"
        return self.healthy

    async def activate_mutation(self, authorization, prepared):
        self.trace.append("activate")
        return CapabilityMutationReceipt(
            activation_request_id=authorization.request_id,
            manager_operation_id=prepared.manager_operation_id,
            action=MutationAction.DISABLE,
            pack_id=authorization.pack_id,
            runtime_set_ref=prepared.runtime_set_ref,
            runtime_set_hash=prepared.runtime_set_hash,
            absence_proof_hash="absence-proof-1",
            result_hash="manager-result-1",
            reason_code="manager_committed",
        )

    async def abort_mutation(
        self, authorization, prepared, *, reason_code
    ):
        self.trace.append(f"abort:{reason_code}")
        return self.cleanup_ok


@pytest.fixture
def activation_store(tmp_path):
    clock = _Clock()
    store = CompanionStore(tmp_path / "companion.db", clock=clock)
    owner = store.create_profile(
        profile_id="alice",
        generation=1,
        identity_namespace_hash="relay:alice",
    )
    request = MutationRequest(
        request_id="disable-1",
        request_fingerprint="disable-fingerprint-1",
        action=MutationAction.DISABLE,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="personal.old-skill",
        target_expected_binding_generation=2,
        cause_ref="forget:event-1",
        reason_code="user_forget",
    )
    store.create_capability_mutation_request(owner, request)
    return store, owner, clock


@pytest.mark.asyncio
async def test_dispatcher_settles_empty_runtime_mutation_once(
    activation_store,
) -> None:
    store, owner, _clock = activation_store
    platform = _Platform()
    result = await ActivationDispatcher(
        store=store,
        platform=platform,
        barrier=RevocationBarrier(),
    ).execute(
        owner,
        request_id="disable-1",
        claim_owner="activation-worker-1",
    )

    assert result["result_hash"] == "manager-result-1"
    assert platform.trace == ["prepare", "activate"]
    with store.read() as db:
        request = db.execute(
            """SELECT status,manager_operation_id,runtime_set_ref
               FROM capability_activation_requests
               WHERE activation_request_id='disable-1'"""
        ).fetchone()
        assert tuple(request) == ("succeeded", "manager-op-1", None)
        assert db.execute(
            """SELECT count(*) FROM capability_activation_receipts
               WHERE activation_request_id='disable-1'"""
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_runtime_health_failure_aborts_without_binding_receipt(
    activation_store,
) -> None:
    store, owner, _clock = activation_store
    platform = _Platform(with_runtime=True, healthy=False)
    with pytest.raises(
        ActivationDispatchError,
        match="capability_runtime_health_failed",
    ):
        await ActivationDispatcher(
            store=store,
            platform=platform,
            barrier=RevocationBarrier(),
        ).execute(
            owner,
            request_id="disable-1",
            claim_owner="activation-worker-1",
        )

    assert platform.trace == [
        "prepare",
        "start",
        "health",
        "abort:capability_runtime_health_failed",
    ]
    with store.read() as db:
        assert db.execute(
            """SELECT status FROM capability_activation_requests
               WHERE activation_request_id='disable-1'"""
        ).fetchone()[0] == "failed"
        assert db.execute(
            "SELECT count(*) FROM capability_activation_receipts"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_unknown_start_is_not_retried_and_keeps_unknown_status(
    activation_store,
) -> None:
    store, owner, _clock = activation_store
    platform = _Platform(with_runtime=True, start_outcome="unknown")
    with pytest.raises(ActivationDispatchError, match="runtime_unknown"):
        await ActivationDispatcher(
            store=store,
            platform=platform,
            barrier=RevocationBarrier(),
        ).execute(
            owner,
            request_id="disable-1",
            claim_owner="activation-worker-1",
        )

    assert platform.trace == ["prepare", "start", "abort:runtime_unknown"]
    with store.read() as db:
        row = db.execute(
            """SELECT status,attempt FROM capability_activation_requests
               WHERE activation_request_id='disable-1'"""
        ).fetchone()
        assert tuple(row) == ("unknown", 1)


def test_expired_claim_reuses_stable_manager_idempotency_key(
    activation_store,
) -> None:
    store, owner, clock = activation_store
    first = store.claim_capability_mutation_request(
        owner,
        request_id="disable-1",
        claim_owner="worker-1",
        lease_seconds=1,
    )
    store.record_capability_mutation_prepared(
        owner,
        request_id="disable-1",
        claim_owner="worker-1",
        claim_epoch=int(first["claim_epoch"]),
        manager_operation_id="manager-op-1",
        runtime_set_ref=None,
        runtime_set_hash=None,
        reason_code="prepared",
    )
    clock.advance(2)
    recovered = store.claim_capability_mutation_request(
        owner,
        request_id="disable-1",
        claim_owner="worker-2",
        lease_seconds=10,
    )

    assert recovered["manager_idempotency_key"] == first["manager_idempotency_key"]
    assert recovered["manager_operation_id"] == "manager-op-1"
    assert recovered["claim_epoch"] == first["claim_epoch"] + 1
    assert recovered["attempt"] == 2
