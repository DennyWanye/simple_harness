from __future__ import annotations

import hashlib

import aiosqlite
import pytest

from deskpet.capabilities.contracts import (
    CapabilityScope,
    CapabilityVersionDescriptor,
    ProcessCatalogStamp,
    RunCatalogContentStamp,
    RunCatalogEntryIdentity,
    fingerprint_json,
)
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
        capability_id="durable-fixture",
        display_name="Durable fixture",
        version="1.0.0",
        kind="function_tool",
        source="local:durable-fixture@1",
        description="Durable projection fixture",
        aliases=(),
        logical_tool_ids=("inspect",),
        provider_tool_names=("durable_fixture__inspect",),
        permission_categories=("filesystem_read",),
        effect_kinds=("read_only",),
        schema_hash=_hash("durable-schema"),
        manifest_hash=_hash("durable-manifest"),
        health="healthy",
    )
    return CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=tmp_path / "packs" / "durable-fixture" / "1.0.0",
        validation_status="healthy",
        expected_tool_fingerprints=(_hash("durable-tool"),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=100.0,
    )


async def _projection_fixture(tmp_path):
    path = await initialize_capability_database(tmp_path / "projection.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    record = _record(tmp_path)
    await store.record_version(record)
    binding = await store.set_binding(
        owner_key="companion:alpha:1",
        management_policy="user_managed",
        scope="user",
        scope_key="default",
        pack_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        expected_generation=0,
    )
    entry = RunCatalogEntryIdentity(
        entry_kind="pack",
        descriptor_fingerprint=record.descriptor.fingerprint,
        canonical_envelope={
            "selected_binding": binding.to_dict(),
            "visible_bindings": [binding.to_dict()],
            "pack_id": record.descriptor.capability_id,
            "version": record.descriptor.version,
            "manifest_hash": record.descriptor.manifest_hash,
            "tool_spec_fingerprints": list(
                record.expected_tool_fingerprints
            ),
            "instruction_refs_hash": fingerprint_json([]),
            "workflow_refs_hash": fingerprint_json([]),
            "runtime_descriptor_hash": fingerprint_json([]),
        },
    )
    content = RunCatalogContentStamp(
        request_scope=CapabilityScope(user_key="default"),
        entries=(entry,),
    )
    process_stamp = ProcessCatalogStamp(
        process_instance_id="process-1",
        run_catalog_content_stamp=content.fingerprint,
        catalog_generation=1,
        registry_revision=2,
        skill_revision=3,
        mcp_revision=4,
    )
    tool_set = {"fingerprints": [_hash("durable-tool")]}
    kwargs = {
        "content": content,
        "process_stamp": process_stamp,
        "snapshot_ref": content.snapshot_ref,
        "request_owner_key": "companion:alpha:1",
        "catalog_generation_vector": {"companion:alpha:1": 1},
        "run_id": "run-1",
        "root_run_id": "root-1",
        "request_id": "request-1",
        "turn_id": "turn-1",
        "owner_operation_id": "run-start:run-1:1",
        "lease_owner_kind": "run_start",
        "prepared_tool_set_envelope": tool_set,
        "prepared_tool_set_hash": fingerprint_json(tool_set),
    }
    return path, store, kwargs


@pytest.mark.asyncio
async def test_c2_installs_complete_durable_projection_schema(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "schema.db")
    required = {
        "capability_snapshot_lease_intents",
        "capability_snapshot_leases",
        "capability_snapshot_lease_release_receipts",
        "capability_owner_runtime_activations",
        "capability_lease_runtime_activations",
        "capability_lease_runtime_members",
        "capability_runtime_projection_receipts",
        "capability_runtime_sets",
        "capability_runtime_prepare_intents",
    }
    async with aiosqlite.connect(path) as db:
        tables = {
            str(row[0])
            for row in await (
                await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ).fetchall()
        }
        lease_columns = {
            str(row[1])
            for row in await (
                await db.execute("PRAGMA table_info(capability_snapshot_leases)")
            ).fetchall()
        }
    assert required <= tables
    assert {
        "lease_entry_id",
        "lease_intent_id",
        "run_catalog_content_stamp",
        "entry_ordinal",
        "entry_kind",
        "descriptor_fingerprint",
        "runtime_descriptor_hash",
    } <= lease_columns


@pytest.mark.asyncio
async def test_projection_prepare_adopt_ready_release_is_one_durable_chain(
    tmp_path,
) -> None:
    _path, store, kwargs = await _projection_fixture(tmp_path)
    async with store.write_transaction() as db:
        prepared = await store.prepare_run_catalog_projection_in_tx(
            db, **kwargs
        )
        replay = await store.prepare_run_catalog_projection_in_tx(db, **kwargs)
        assert replay == prepared
        assert prepared.intent.status == "prepared"
        assert prepared.projection_receipt.status == "prepared"
        assert (
            prepared.process_catalog_stamp
            == kwargs["process_stamp"].fingerprint
        )

        adopted = await store.adopt_snapshot_lease_intent_in_tx(
            db,
            prepared.intent.lease_intent_id,
            intent_hash=prepared.intent.lease_intent_hash,
            owner_record_ref="execution-run-start:run-1",
            owner_record_hash=_hash("owner-record"),
            start_fingerprint=_hash("run-start"),
        )
        assert adopted.status == "bound"
        assert adopted.projection_receipt_id == (
            prepared.projection_receipt.projection_receipt_id
        )

    ready = await store.activate_snapshot_projection_ready(
        prepared.intent.lease_intent_id,
        intent_hash=prepared.intent.lease_intent_hash,
        start_fingerprint=_hash("run-start"),
        process_instance_id="process-1",
        process_catalog_stamp=kwargs["process_stamp"].fingerprint,
        pin_token_hash=_hash("pin-token"),
    )
    assert ready.status == "ready"

    async with store.write_transaction() as db:
        released = await store.release_snapshot_lease_intent_in_tx(
            db,
            prepared.intent.lease_intent_id,
            intent_hash=prepared.intent.lease_intent_hash,
            release_reason="terminal",
            owner_terminal_or_transition_ref="execution-terminal:run-1",
            owner_terminal_or_transition_hash=_hash("terminal"),
        )
        assert released.status == "released"
        assert released.cleanup_status == "pending"
        assert (
            await store.release_snapshot_lease_intent_in_tx(
                db,
                prepared.intent.lease_intent_id,
                intent_hash=prepared.intent.lease_intent_hash,
                release_reason="terminal",
                owner_terminal_or_transition_ref="execution-terminal:run-1",
                owner_terminal_or_transition_hash=_hash("terminal"),
            )
            == released
        )

    state = await store.read_snapshot_projection_state(
        prepared.intent.lease_intent_id
    )
    assert state.intent_status == "released"
    assert state.projection_status == "retired"
    assert state.pin_token_hash == _hash("pin-token")


@pytest.mark.asyncio
async def test_projection_prepare_rolls_back_with_caller_transaction(
    tmp_path,
) -> None:
    path, store, kwargs = await _projection_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="rollback"):
        async with store.write_transaction() as db:
            await store.prepare_run_catalog_projection_in_tx(db, **kwargs)
            raise RuntimeError("rollback")
    async with aiosqlite.connect(path) as db:
        intent_count = await (
            await db.execute(
                "SELECT COUNT(*) FROM capability_snapshot_lease_intents"
            )
        ).fetchone()
        lease_count = await (
            await db.execute("SELECT COUNT(*) FROM capability_snapshot_leases")
        ).fetchone()
    assert intent_count == (0,)
    assert lease_count == (0,)


@pytest.mark.asyncio
async def test_prepared_orphan_release_is_idempotent_and_allows_next_generation(
    tmp_path,
) -> None:
    _path, store, kwargs = await _projection_fixture(tmp_path)
    async with store.write_transaction() as db:
        prepared = await store.prepare_run_catalog_projection_in_tx(
            db, **kwargs
        )
        released = await store.release_snapshot_lease_intent_in_tx(
            db,
            prepared.intent.lease_intent_id,
            intent_hash=prepared.intent.lease_intent_hash,
            release_reason="prepared_orphan",
            owner_terminal_or_transition_ref="prepared-orphan:run-1",
            owner_terminal_or_transition_hash=_hash("prepared-orphan"),
        )
        assert released.status == "released"

        next_prepared = await store.prepare_run_catalog_projection_in_tx(
            db,
            **{
                **kwargs,
                "owner_operation_id": "refresh:run-1:2",
                "lease_owner_kind": "refresh_commit",
            },
        )
        assert next_prepared.intent.lease_generation == 2


@pytest.mark.asyncio
async def test_projection_replay_rejects_persisted_entry_drift(tmp_path) -> None:
    _path, store, kwargs = await _projection_fixture(tmp_path)
    async with store.write_transaction() as db:
        await store.prepare_run_catalog_projection_in_tx(db, **kwargs)
        await db.execute(
            """UPDATE capability_run_catalog_snapshot_entries
               SET descriptor_envelope_json='{}'"""
        )
        with pytest.raises(
            CapabilityStoreConflict, match="catalog snapshot"
        ):
            await store.prepare_run_catalog_projection_in_tx(db, **kwargs)
