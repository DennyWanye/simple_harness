from __future__ import annotations

import hashlib

import aiosqlite
import pytest

from deskpet.capabilities.contracts import (
    EMPTY_OWNER_BINDING_SET_STAMP,
    LEGACY_LOCAL_OWNER_KEY,
    CapabilityScope,
    CapabilityVersionDescriptor,
    OwnerScopeKey,
    ProcessCatalogStamp,
    RunCatalogContentStamp,
    RunCatalogEntryIdentity,
    canonical_json,
)
from deskpet.capabilities.store import (
    CAPABILITY_SCHEMA_VERSION,
    CapabilityStore,
    CapabilityStoreConflict,
    CapabilityVersionRecord,
    initialize_capability_database,
    install_capability_schema,
    migrate_capability_schema_v1_to_v2,
)
from deskpet.workflows.store import (
    WORKFLOW_SCHEMA_VERSION,
    initialize_workflow_db,
)
from deskpet.workflows.store import schema as workflow_schema


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _record(tmp_path) -> CapabilityVersionRecord:
    descriptor = CapabilityVersionDescriptor(
        capability_id="owner-fixture",
        display_name="Owner fixture",
        version="1.0.0",
        kind="function_tool",
        source="local:owner-fixture@1",
        description="Owner isolation fixture",
        aliases=(),
        logical_tool_ids=("inspect",),
        provider_tool_names=("owner_fixture__inspect",),
        permission_categories=("filesystem_read",),
        effect_kinds=("read_only",),
        schema_hash=_hash("owner-fixture-schema"),
        manifest_hash=_hash("owner-fixture-manifest"),
        health="healthy",
    )
    return CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=tmp_path / "packs" / "owner-fixture" / "1.0.0",
        validation_status="healthy",
        expected_tool_fingerprints=(_hash("owner-fixture-tool"),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=100.0,
    )


async def _insert_v1_version_and_binding(
    db: aiosqlite.Connection,
    record: CapabilityVersionRecord,
) -> None:
    descriptor = record.descriptor
    await db.execute(
        """INSERT INTO capability_versions(
            pack_id,version,manifest_hash,descriptor_json,source_json,
            install_path,validation_status,expected_tool_fingerprints_json,
            parent_version,parent_manifest_hash,derived_from_receipt_ref,
            created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            descriptor.capability_id,
            descriptor.version,
            descriptor.manifest_hash,
            canonical_json(descriptor.to_dict()),
            canonical_json({"source": descriptor.source}),
            str(record.install_path.resolve(strict=False)),
            record.validation_status,
            canonical_json(list(record.expected_tool_fingerprints)),
            None,
            None,
            None,
            record.created_at,
        ),
    )
    await db.execute(
        """INSERT INTO capability_bindings(
            binding_id,scope,scope_key,pack_id,active_version,
            active_manifest_hash,generation,enabled,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            "legacy-binding-id",
            "user",
            "default",
            descriptor.capability_id,
            descriptor.version,
            descriptor.manifest_hash,
            7,
            1,
            101.0,
        ),
    )


@pytest.mark.asyncio
async def test_capability_v1_to_v2_migration_preserves_binding_and_repeats(
    tmp_path,
) -> None:
    path = tmp_path / "capability-v1.db"
    record = _record(tmp_path)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await install_capability_schema(db)
        await _insert_v1_version_and_binding(db, record)
        await db.execute(
            """INSERT INTO capability_snapshot_leases(
                snapshot_ref,run_id,root_run_id,pack_id,version,
                manifest_hash,tool_spec_fingerprints_json,acquired_at,released_at
            ) VALUES(?,?,?,?,?,?,?,?,NULL)""",
            (
                _hash("legacy-snapshot"),
                "legacy-run",
                "legacy-root",
                record.descriptor.capability_id,
                record.descriptor.version,
                record.descriptor.manifest_hash,
                canonical_json(list(record.expected_tool_fingerprints)),
                100.0,
            ),
        )
        await db.commit()

        await db.execute("BEGIN IMMEDIATE")
        await migrate_capability_schema_v1_to_v2(db)
        await migrate_capability_schema_v1_to_v2(db)
        await db.commit()

        state = await (
            await db.execute(
                "SELECT schema_version FROM capability_schema_state"
            )
        ).fetchone()
        binding = await (
            await db.execute(
                """SELECT binding_id,owner_key,scope,scope_key,generation,
                          enabled,management_policy,management_generation
                   FROM capability_bindings"""
            )
        ).fetchone()
        detail = await (
            await db.execute(
                """SELECT row_state,version,owner_catalog_generation
                   FROM capability_owner_detail_versions"""
            )
        ).fetchone()
        errors = await (await db.execute("PRAGMA foreign_key_check")).fetchall()
        migrated_lease = await (
            await db.execute(
                """SELECT lease_intent_id,entry_ordinal,entry_kind,
                          pack_id,version,manifest_hash,released_at
                   FROM capability_snapshot_leases"""
            )
        ).fetchone()
        migrated_intent = await (
            await db.execute(
                """SELECT status,last_error
                   FROM capability_snapshot_lease_intents"""
            )
        ).fetchone()

    assert state == (2,)
    assert binding == (
        "legacy-binding-id",
        LEGACY_LOCAL_OWNER_KEY,
        "user",
        "default",
        7,
        1,
        "legacy_import",
        0,
    )
    assert detail == ("existing", 1, 1)
    assert migrated_lease is not None
    assert migrated_lease[1:] == (
        0,
        "pack",
        record.descriptor.capability_id,
        record.descriptor.version,
        record.descriptor.manifest_hash,
        None,
    )
    assert migrated_intent == ("conflict", "legacy_owner_record_unproven")
    assert errors == []


@pytest.mark.asyncio
async def test_workflow_fresh_schema_installs_current_capability_schema_idempotently(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    await initialize_workflow_db(path)
    await initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        workflow_version = await (
            await db.execute("PRAGMA user_version")
        ).fetchone()
        capability_version = await (
            await db.execute(
                "SELECT schema_version FROM capability_schema_state"
            )
        ).fetchone()
        columns = await (
            await db.execute("PRAGMA table_info(capability_bindings)")
        ).fetchall()
    assert workflow_version == (WORKFLOW_SCHEMA_VERSION,)
    # Fresh installs land on the current capability schema (v4 since
    # a1e5d96b4); the v2 owner columns must still be present.
    assert capability_version == (CAPABILITY_SCHEMA_VERSION,)
    assert {
        "owner_key",
        "management_policy",
        "management_generation",
    } <= {str(row[1]) for row in columns}


@pytest.mark.asyncio
async def test_workflow_v17_migrates_existing_capability_v1_atomically(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "workflow-existing.db"
    record = _record(tmp_path)
    monkeypatch.setattr(workflow_schema, "WORKFLOW_SCHEMA_VERSION", 17)
    await workflow_schema.initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        await _insert_v1_version_and_binding(db, record)
        await db.commit()

    monkeypatch.setattr(
        workflow_schema, "WORKFLOW_SCHEMA_VERSION", WORKFLOW_SCHEMA_VERSION
    )
    await workflow_schema.initialize_workflow_db(path)
    await workflow_schema.initialize_workflow_db(path)
    async with aiosqlite.connect(path) as db:
        workflow_version = await (
            await db.execute("PRAGMA user_version")
        ).fetchone()
        binding = await (
            await db.execute(
                """SELECT binding_id,owner_key,generation,management_policy
                   FROM capability_bindings"""
            )
        ).fetchone()
        migration = await (
            await db.execute(
                """SELECT COUNT(*) FROM workflow_schema_migrations
                   WHERE version=18"""
            )
        ).fetchone()
    assert workflow_version == (WORKFLOW_SCHEMA_VERSION,)
    assert binding == (
        "legacy-binding-id",
        LEGACY_LOCAL_OWNER_KEY,
        7,
        "legacy_import",
    )
    assert migration == (1,)


@pytest.mark.asyncio
async def test_owner_scoped_bindings_are_sql_isolated(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "owner.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    record = _record(tmp_path)
    await store.record_version(record)
    bindings = []
    for owner_key in ("companion:alpha:1", "companion:beta:1"):
        bindings.append(
            await store.set_binding(
                owner_key=owner_key,
                management_policy="user_managed",
                scope="user",
                scope_key="default",
                pack_id=record.descriptor.capability_id,
                version=record.descriptor.version,
                manifest_hash=record.descriptor.manifest_hash,
                expected_generation=0,
            )
        )

    assert bindings[0].binding_id != bindings[1].binding_id
    assert (
        await store.get_binding(
            "user",
            "default",
            record.descriptor.capability_id,
            owner_key="companion:alpha:1",
        )
        == bindings[0]
    )
    assert (
        await store.get_binding(
            "user",
            "default",
            record.descriptor.capability_id,
        )
        is None
    )
    alpha_entries = await store.visible_entries(
        CapabilityScope(user_key="default"),
        owner_key="companion:alpha:1",
    )
    beta_entries = await store.visible_entries(
        CapabilityScope(user_key="default"),
        owner_key="companion:beta:1",
    )
    assert alpha_entries[0].bindings == (bindings[0],)
    assert beta_entries[0].bindings == (bindings[1],)


@pytest.mark.asyncio
async def test_detail_tokens_preserve_missing_deleted_and_aba_versions(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "detail.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    record = _record(tmp_path)
    await store.record_version(record)
    key = OwnerScopeKey("companion:alpha:1", "user", "default")

    missing = (await store.read_detail_token_vector((key,))).items[0]
    assert (missing.exists, missing.version) == (False, 0)
    assert (
        missing.committed_owner_binding_set_stamp
        == EMPTY_OWNER_BINDING_SET_STAMP
    )
    ensured = await store.ensure_owner_detail_key(key)
    assert (ensured.exists, ensured.version) == (True, 0)

    first = await store.set_binding(
        owner_key=key.owner_key,
        management_policy="user_managed",
        scope=key.scope,
        scope_key=key.scope_key,
        pack_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        expected_generation=0,
    )
    created = (await store.read_detail_token_vector((key,))).items[0]
    assert (created.exists, created.version, created.owner_catalog_generation) == (
        True,
        1,
        1,
    )

    assert await store.delete_binding(
        owner_key=key.owner_key,
        scope=key.scope,
        scope_key=key.scope_key,
        pack_id=record.descriptor.capability_id,
        expected_generation=first.generation,
    )
    deleted = (await store.read_detail_token_vector((key,))).items[0]
    assert (deleted.exists, deleted.version, deleted.owner_catalog_generation) == (
        False,
        2,
        2,
    )

    await store.set_binding(
        owner_key=key.owner_key,
        management_policy="user_managed",
        scope=key.scope,
        scope_key=key.scope_key,
        pack_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        expected_generation=0,
    )
    recreated = (await store.read_detail_snapshot((key,))).tokens.items[0]
    assert (recreated.exists, recreated.version, recreated.owner_catalog_generation) == (
        True,
        3,
        3,
    )
    assert recreated.fingerprint not in {
        created.fingerprint,
        deleted.fingerprint,
    }


@pytest.mark.asyncio
async def test_run_catalog_stamp_and_lease_intent_are_durable_and_idempotent(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "catalog.db")
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
        },
    )
    stamp = RunCatalogContentStamp(
        request_scope=CapabilityScope(user_key="default"),
        entries=(entry,),
    )
    same_stamp = RunCatalogContentStamp(
        request_scope=CapabilityScope(user_key="default"),
        entries=(entry,),
    )
    assert same_stamp.fingerprint == stamp.fingerprint
    assert (
        ProcessCatalogStamp(
            process_instance_id="process-a",
            run_catalog_content_stamp=stamp.fingerprint,
            catalog_generation=1,
            registry_revision=2,
            skill_revision=3,
            mcp_revision=4,
        ).fingerprint
        != ProcessCatalogStamp(
            process_instance_id="process-b",
            run_catalog_content_stamp=stamp.fingerprint,
            catalog_generation=1,
            registry_revision=2,
            skill_revision=3,
            mcp_revision=4,
        ).fingerprint
    )

    assert (
        await store.put_run_catalog_snapshot(
            stamp,
            request_owner_key="companion:alpha:1",
            catalog_generation_vector={"companion:alpha:1": 1},
        )
        == stamp.fingerprint
    )
    assert (
        await store.put_run_catalog_snapshot(
            stamp,
            request_owner_key="companion:alpha:1",
            catalog_generation_vector={"companion:alpha:1": 1},
        )
        == stamp.fingerprint
    )

    prepare_kwargs = {
        "snapshot_ref": stamp.fingerprint,
        "snapshot_ref_schema": "run_catalog_v2",
        "run_id": "run-1",
        "root_run_id": "root-1",
        "request_id": "request-1",
        "turn_id": "turn-1",
        "owner_operation_id": "run-start:1",
        "lease_owner_kind": "run_start",
        "run_catalog_content_stamp": stamp.fingerprint,
        "entry_set_hash": stamp.entry_set_hash,
        "expected_entry_count": 1,
    }
    prepared = await store.prepare_snapshot_lease_intent(**prepare_kwargs)
    assert await store.prepare_snapshot_lease_intent(**prepare_kwargs) == prepared
    with pytest.raises(CapabilityStoreConflict):
        await store.prepare_snapshot_lease_intent(
            **{**prepare_kwargs, "entry_set_hash": _hash("different")}
        )

    bind_kwargs = {
        "owner_record_ref": "execution-run-start:run-1",
        "owner_record_hash": _hash("owner-record"),
        "start_fingerprint": _hash("run-start"),
    }
    bound = await store.bind_snapshot_lease_intent(
        prepared.lease_intent_id, **bind_kwargs
    )
    assert bound.status == "bound"
    assert (
        await store.bind_snapshot_lease_intent(
            prepared.lease_intent_id, **bind_kwargs
        )
        == bound
    )
    released = await store.release_snapshot_lease_intent(
        prepared.lease_intent_id
    )
    assert released.status == "released"
    assert (
        await store.release_snapshot_lease_intent(
            prepared.lease_intent_id
        )
        == released
    )
    next_intent = await store.prepare_snapshot_lease_intent(
        **{
            **prepare_kwargs,
            "owner_operation_id": "refresh-commit:2",
            "lease_owner_kind": "refresh_commit",
        }
    )
    assert next_intent.lease_generation == 2
