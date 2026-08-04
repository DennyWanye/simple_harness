from __future__ import annotations

import hashlib
import time

import pytest

from deskpet.capabilities.runtime_ledger import (
    CapabilityStoreRuntimeSetLedger,
)
from deskpet.capabilities.runtime_prepare import (
    CapabilityRuntimeSetCoordinator,
    PreparedRuntimeInstanceSpec,
    RuntimeHealthOutcome,
    RuntimeLaunchAuthorization,
    RuntimeStartedAck,
)
from deskpet.capabilities.store import CapabilityStore
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class _Adapter:
    adapter_id = "fixture"
    adapter_fingerprint = "f" * 64

    async def start_prepared(self, spec, authorization):
        return RuntimeStartedAck(
            operation_id=authorization.operation_id,
            runtime_set_hash=authorization.runtime_set_hash,
            entry_id=spec.entry_id,
            runtime_instance_id=authorization.runtime_instance_id,
            adapter_identity="fixture-process-v1",
            start_identity={
                "job_identity": "job-1",
                "pid": 101,
                "process_create_time": 123.5,
                "command_line_hash": "c" * 64,
            },
            acknowledged_at=time.time(),
        )

    async def await_health(self, spec, ack):
        return RuntimeHealthOutcome(
            operation_id=ack.operation_id,
            entry_id=spec.entry_id,
            healthy=True,
            outcome_hash=hashlib.sha256(spec.entry_id.encode()).hexdigest(),
            detail={"healthy": True},
        )

    async def abort(self, spec, start):
        return None


class _Projection:
    def __init__(self, ledger):
        self._ledger = ledger

    async def activate_prepared_projection(self, prepared_set):
        await self._ledger.mark_set_activated(
            prepared_set.operation_id,
            prepared_set.runtime_set_hash,
        )
        return {"activated": True}


class _UnhealthyAdapter(_Adapter):
    async def await_health(self, spec, ack):
        return RuntimeHealthOutcome(
            operation_id=ack.operation_id,
            entry_id=spec.entry_id,
            healthy=False,
            outcome_hash=hashlib.sha256(b"unhealthy").hexdigest(),
            detail={"healthy": False},
        )


def _spec() -> PreparedRuntimeInstanceSpec:
    return PreparedRuntimeInstanceSpec(
        entry_id="entry-1",
        runtime_kind="local_runtime",
        adapter_id="fixture",
        adapter_fingerprint="f" * 64,
        start_envelope={
            "argv": ["python", "worker.py"],
            "env_scope": {"profile": "owner-a"},
            "workdir": ".",
        },
        health_envelope={"kind": "ping"},
        ordinal=0,
    )


@pytest.mark.asyncio
async def test_store_ledger_recovers_start_and_activates_atomically(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    store = CapabilityStore(uow, clock=time.time)
    await store.initialize()
    ledger = CapabilityStoreRuntimeSetLedger(store)
    adapter = _Adapter()
    coordinator = CapabilityRuntimeSetCoordinator(
        ledger=ledger,
        adapters={"fixture": adapter},
        projection=_Projection(ledger),
    )
    activation, prepared = (
        await coordinator.prepare_owner_runtime_activation(
            owner_key="owner-a",
            scope="user",
            scope_key="profile-a",
            owner_binding_set_stamp="a" * 64,
            startup_or_profile_epoch=3,
            operation_id="operation-1",
            instances=(_spec(),),
            launch_revocation_epoch=7,
        )
    )
    authorization = RuntimeLaunchAuthorization.issue(
        prepared, prepared.instances[0]
    )
    started = await coordinator.start_prepared_runtime_instance(
        prepared, prepared.instances[0], authorization
    )

    recovered_ledger = CapabilityStoreRuntimeSetLedger(store)
    recovered = CapabilityRuntimeSetCoordinator(
        ledger=recovered_ledger,
        adapters={"fixture": adapter},
        projection=_Projection(recovered_ledger),
    )
    recovered_start = (
        await recovered_ledger.get_runtime_instance_start_result(
            prepared, prepared.instances[0]
        )
    )
    assert recovered_start == started

    health = await recovered.await_prepared_runtime_instance_health(
        prepared, prepared.instances[0]
    )
    assert health.healthy is True
    assert await recovered.activate_prepared_set(prepared) == {
        "activated": True
    }

    record = await store.get_runtime_set(prepared.operation_id)
    assert record is not None
    assert record.status == "activated"
    assert record.instances[0].status == "activated"
    async with store.read_connection() as db:
        row = await (
            await db.execute(
                """SELECT status
                   FROM capability_owner_runtime_activations
                   WHERE owner_activation_id=?""",
                (activation.owner_activation_id,),
            )
        ).fetchone()
    assert row["status"] == "published"
    assert (
        await recovered_ledger.next_owner_runtime_generation(
            "owner-a", "user", "profile-a"
        )
        == 2
    )
    await uow.close()


@pytest.mark.asyncio
async def test_store_ledger_rejects_conflicting_launch_claim(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    store = CapabilityStore(uow)
    await store.initialize()
    ledger = CapabilityStoreRuntimeSetLedger(store)
    coordinator = CapabilityRuntimeSetCoordinator(
        ledger=ledger,
        adapters={"fixture": _Adapter()},
        projection=_Projection(ledger),
    )
    _, prepared = await coordinator.prepare_owner_runtime_activation(
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_binding_set_stamp="a" * 64,
        startup_or_profile_epoch=3,
        operation_id="operation-1",
        instances=(_spec(),),
        launch_revocation_epoch=7,
    )
    instance = prepared.instances[0]
    first = RuntimeLaunchAuthorization.issue(prepared, instance)
    second = RuntimeLaunchAuthorization.issue(prepared, instance)
    await ledger.claim_runtime_instance(prepared, instance, first)

    with pytest.raises(RuntimeError, match="runtime_launch_claim_conflict"):
        await ledger.claim_runtime_instance(prepared, instance, second)
    await uow.close()


@pytest.mark.asyncio
async def test_store_ledger_abort_retires_set_and_owner_activation(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    store = CapabilityStore(uow)
    await store.initialize()
    ledger = CapabilityStoreRuntimeSetLedger(store)
    coordinator = CapabilityRuntimeSetCoordinator(
        ledger=ledger,
        adapters={"fixture": _Adapter()},
        projection=_Projection(ledger),
    )
    activation, prepared = (
        await coordinator.prepare_owner_runtime_activation(
            owner_key="owner-a",
            scope="user",
            scope_key="profile-a",
            owner_binding_set_stamp="a" * 64,
            startup_or_profile_epoch=3,
            operation_id="operation-abort",
            instances=(_spec(),),
            launch_revocation_epoch=7,
        )
    )

    await coordinator.abort_prepared_set(
        prepared, reason_code="startup_cancelled"
    )

    record = await store.get_runtime_set(prepared.operation_id)
    assert record is not None
    assert record.status == "aborted"
    assert record.last_error == "startup_cancelled"
    async with store.read_connection() as db:
        row = await (
            await db.execute(
                """SELECT status,last_error
                   FROM capability_owner_runtime_activations
                   WHERE owner_activation_id=?""",
                (activation.owner_activation_id,),
            )
        ).fetchone()
    assert (row["status"], row["last_error"]) == (
        "aborted",
        "startup_cancelled",
    )
    await uow.close()


@pytest.mark.asyncio
async def test_store_ledger_persists_failed_health_until_exact_abort(
    tmp_path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    store = CapabilityStore(uow)
    await store.initialize()
    ledger = CapabilityStoreRuntimeSetLedger(store)
    adapter = _UnhealthyAdapter()
    coordinator = CapabilityRuntimeSetCoordinator(
        ledger=ledger,
        adapters={"fixture": adapter},
        projection=_Projection(ledger),
    )
    _, prepared = await coordinator.prepare_owner_runtime_activation(
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_binding_set_stamp="a" * 64,
        startup_or_profile_epoch=3,
        operation_id="operation-unhealthy",
        instances=(_spec(),),
        launch_revocation_epoch=7,
    )
    instance = prepared.instances[0]
    await coordinator.start_prepared_runtime_instance(
        prepared,
        instance,
        RuntimeLaunchAuthorization.issue(prepared, instance),
    )

    outcome = await coordinator.await_prepared_runtime_instance_health(
        prepared, instance
    )
    assert outcome.healthy is False
    record = await store.get_runtime_set(prepared.operation_id)
    assert record is not None
    assert record.status == "cleanup_required"
    with pytest.raises(RuntimeError, match="runtime_set_not_healthy"):
        await ledger.assert_set_health_passed(
            prepared.operation_id, prepared.runtime_set_hash
        )

    await coordinator.abort_prepared_set(
        prepared, reason_code="health_failed"
    )
    record = await store.get_runtime_set(prepared.operation_id)
    assert record is not None
    assert record.status == "aborted"
    await uow.close()
