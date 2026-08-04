from __future__ import annotations

import hashlib

import pytest

from deskpet.capabilities.contracts import CapabilityScope, CapabilityVersionDescriptor
from deskpet.capabilities.store import (
    CapabilityStore,
    CapabilityStoreConflict,
    CapabilityVersionRecord,
    initialize_capability_database,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _record(tmp_path) -> CapabilityVersionRecord:
    descriptor = CapabilityVersionDescriptor(
        capability_id="godot",
        display_name="Godot",
        version="1.0.0",
        kind="mcp_tool",
        source="local:fixture@1",
        description="Godot runtime",
        aliases=("game",),
        logical_tool_ids=("check",),
        provider_tool_names=("godot__check",),
        permission_categories=("filesystem_read",),
        effect_kinds=("read_only",),
        schema_hash=_hash("schema"),
        manifest_hash=_hash("manifest"),
        health="healthy",
    )
    return CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=tmp_path / "packs" / "godot" / "1.0.0",
        validation_status="healthy",
        expected_tool_fingerprints=(_hash("tool"),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=100.0,
    )


async def _ready_runtime(tmp_path):
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    record = _record(tmp_path)
    await store.record_version(record)
    runtime = await store.create_runtime_lease(
        runtime_lease_id="runtime-godot-v1",
        pack_id="godot",
        version="1.0.0",
        manifest_hash=record.descriptor.manifest_hash,
        server_id="editor",
        pid=1234,
        run_id="owner-run",
    )
    ready = await store.transition_runtime_lease(
        runtime.runtime_lease_id,
        expected_state="starting",
        target_state="ready",
        expected_generation=1,
    )
    return store, record, ready


@pytest.mark.asyncio
async def test_runtime_call_lease_pins_physical_session_and_blocks_stop(
    tmp_path,
) -> None:
    store, _record_value, runtime = await _ready_runtime(tmp_path)
    provenance = runtime.provenance_ref
    first = await store.claim_runtime_call(
        call_lease_id="call-lease-1",
        runtime_lease_id=runtime.runtime_lease_id,
        effect_id="effect-1",
    )
    assert first.session_generation == 1
    assert (await store.mark_runtime_call_running(first.call_lease_id)).state == "running"

    reconnected = await store.reconnect_runtime_lease(
        runtime.runtime_lease_id,
        expected_generation=1,
        pid=5678,
    )
    assert reconnected.session_generation == 2
    assert reconnected.provenance_ref == provenance
    second = await store.claim_runtime_call(
        call_lease_id="call-lease-2",
        runtime_lease_id=runtime.runtime_lease_id,
        effect_id="effect-2",
    )
    assert second.session_generation == 2
    persisted_first = (await store.runtime_call_leases(runtime.runtime_lease_id))[0]
    assert persisted_first.session_generation == first.session_generation == 1
    assert persisted_first.state == "running"

    draining = await store.transition_runtime_lease(
        runtime.runtime_lease_id,
        expected_state="ready",
        target_state="draining",
        expected_generation=2,
    )
    assert draining.state == "draining"
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.claim_runtime_call(
            call_lease_id="call-lease-3",
            runtime_lease_id=runtime.runtime_lease_id,
            effect_id="effect-3",
        )
    assert caught.value.code == "runtime_not_accepting_calls"
    assert not await store.runtime_can_stop(runtime.runtime_lease_id)
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.transition_runtime_lease(
            runtime.runtime_lease_id,
            expected_state="draining",
            target_state="stopped",
            expected_generation=2,
        )
    assert caught.value.code == "runtime_calls_in_flight"

    await store.settle_runtime_call(first.call_lease_id)
    await store.settle_runtime_call(second.call_lease_id)
    assert await store.runtime_can_stop(runtime.runtime_lease_id)
    stopped = await store.transition_runtime_lease(
        runtime.runtime_lease_id,
        expected_state="draining",
        target_state="stopped",
        expected_generation=2,
    )
    assert stopped.state == "stopped"


@pytest.mark.asyncio
async def test_last_run_snapshot_release_drains_unbound_shared_runtime(
    tmp_path,
) -> None:
    store, record, runtime = await _ready_runtime(tmp_path)
    binding = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id="godot",
        version="1.0.0",
        manifest_hash=record.descriptor.manifest_hash,
        expected_generation=0,
    )
    entries = await store.visible_entries(CapabilityScope(user_key="default"))
    snapshot_ref = _hash("shared-snapshot")
    await store.acquire_snapshot_lease(
        snapshot_ref=snapshot_ref,
        run_id="run-1",
        root_run_id="root-1",
        entries=entries,
    )
    await store.acquire_snapshot_lease(
        snapshot_ref=snapshot_ref,
        run_id="run-2",
        root_run_id="root-2",
        entries=entries,
    )

    await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id="godot",
        version="1.0.0",
        manifest_hash=record.descriptor.manifest_hash,
        expected_generation=binding.generation,
        enabled=False,
    )
    assert (await store.get_runtime_lease(runtime.runtime_lease_id)).state == "ready"

    assert await store.release_snapshot_lease(snapshot_ref, "run-1") == 1
    assert (await store.get_runtime_lease(runtime.runtime_lease_id)).state == "ready"
    assert await store.release_snapshot_lease(snapshot_ref, "run-2") == 1
    assert (await store.get_runtime_lease(runtime.runtime_lease_id)).state == "draining"


@pytest.mark.asyncio
async def test_runtime_and_call_claim_roll_back_with_caller_transaction(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    record = _record(tmp_path)
    await store.record_version(record)
    with pytest.raises(RuntimeError, match="crash"):
        async with store.write_transaction() as db:
            tx = store.bind(db)
            runtime = await tx.create_runtime_lease(
                runtime_lease_id="runtime-rollback",
                pack_id="godot",
                version="1.0.0",
                manifest_hash=record.descriptor.manifest_hash,
            )
            await tx.transition_runtime_lease(
                runtime.runtime_lease_id,
                expected_state="starting",
                target_state="ready",
            )
            await tx.claim_runtime_call(
                call_lease_id="call-rollback",
                runtime_lease_id=runtime.runtime_lease_id,
                effect_id="effect-rollback",
            )
            raise RuntimeError("crash")
    assert await store.get_runtime_lease("runtime-rollback") is None
