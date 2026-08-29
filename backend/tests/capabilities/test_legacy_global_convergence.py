from __future__ import annotations

import pytest
import hashlib
import json

from deskpet.capabilities.contracts import (
    CapabilityVersionDescriptor, canonical_json, fingerprint_json,
)
from deskpet.capabilities.store import (
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStore,
    CapabilityVersionRecord,
    initialize_capability_database,
)


def _record(tmp_path, capability_id: str = "godot", derived_receipt: str | None = None):
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    descriptor = CapabilityVersionDescriptor(
        capability_id=capability_id, display_name=capability_id.title(), version="1.0.0",
        kind="function_tool", source="local:fixture@1", description="fixture",
        aliases=(), logical_tool_ids=("check",), provider_tool_names=(f"{capability_id}__check",),
        permission_categories=("filesystem_read",), effect_kinds=("read_only",),
        schema_hash=digest("schema"), manifest_hash=digest("manifest"), health="healthy",
    )
    return CapabilityVersionRecord(
        descriptor=descriptor, install_path=tmp_path / capability_id, validation_status="healthy",
        expected_tool_fingerprints=(digest("tool"),), parent_version=None,
        parent_manifest_hash=None, derived_from_receipt_ref=derived_receipt, created_at=1.0,
    )


async def _seed_legacy(
    store: CapabilityStore, tmp_path, *, project: str, suffix: str,
    content_hash: str = "c" * 64, receipt_ref: str = "receipt-same",
    status: str = "succeeded", valid_manager_receipt: bool = True,
    version_derived_receipt: str | None = None,
) -> None:
    record = _record(tmp_path, derived_receipt=version_derived_receipt)
    await store.record_version(record)
    await store.set_binding(
        scope="project", scope_key=project, pack_id="godot", version="1.0.0",
        manifest_hash=record.descriptor.manifest_hash, expected_generation=0,
        owner_key="principal",
    )
    intent_id = f"legacy-{suffix}"
    intent = CapabilitySkillInstallIntent(
        intent_id=intent_id, effect_id=f"effect-{suffix}", call_id=f"call-{suffix}",
        root_run_id=f"root-{suffix}", run_id=f"run-{suffix}", channel="settings",
        project_scope_key=project, principal_id="principal", source={"schema": "legacy-v1"},
        exact_commit="a" * 40, archive_hash="a" * 64, raw_tree_hash="b" * 64,
        member_set_stamp="d" * 64, permission_set_hash="e" * 64,
        confirmation_nonce=f"nonce-{suffix}", confirmation_version=1,
        expires_at=999.0, status="staging", state_version=1,
        settlement_ref=None, cleanup_ref=None, verification_ref=None, error=None,
        created_at=1.0, updated_at=1.0,
    )
    member = CapabilitySkillInstallMember(
        intent_id=intent_id, ordinal=0, normalized_name="godot", pack_id="godot",
        version="1.0.0", manifest_hash=record.descriptor.manifest_hash,
        content_hash=content_hash, source_digest="f" * 64, member={"name": "godot"},
    )
    await store.create_skill_install_intent(intent, (member,))
    async with store.write_transaction() as db:
        operation_id = f"operation-{suffix}"
        manager_hash = receipt_ref
        if valid_manager_receipt:
            receipt_payload = {
                "schema": "capability-batch-manager-receipt-v1",
                "operation_id": operation_id,
                "project_scope_key": project,
                "scope": "project", "scope_key": project,
                "owner_key": "principal", "committed_set_stamp": "d" * 64,
                "registry_revision": 1, "publication_state": "active",
                "members": [{
                    "pack_id": "godot", "version": "1.0.0",
                    "manifest_hash": record.descriptor.manifest_hash,
                    "content_hash": content_hash,
                    "expected_binding_generation": 0, "binding_generation": 1,
                }],
            }
            manager_hash = fingerprint_json(receipt_payload)
            await db.execute(
                """INSERT INTO capability_operations(
                   operation_id,idempotency_key,root_run_id,kind,pack_id,
                   requested_scope,requested_scope_key,phase,status,request_json,
                   error_json,started_at,updated_at,ended_at
                   ) VALUES(?,?,?,'skill_install_batch',NULL,'project',?,
                            'batch_committed','succeeded',?,NULL,1,1,1)""",
                (operation_id, f"idem-{suffix}", f"root-{suffix}", project,
                 canonical_json({"member_set_stamp": "d" * 64})),
            )
            await db.execute(
                """INSERT INTO capability_operation_members(
                   operation_id,ordinal,normalized_name,pack_id,version,manifest_hash,
                   content_hash,source_digest,committed_version,
                   committed_manifest_hash,committed_set_stamp
                   ) VALUES(?,0,'godot','godot','1.0.0',?,?,?,?,?,?)""",
                (operation_id, record.descriptor.manifest_hash, content_hash, "f" * 64,
                 "1.0.0", record.descriptor.manifest_hash, "d" * 64),
            )
            await db.execute(
                "INSERT INTO capability_skill_install_handoffs VALUES(?,?,?,?,1)",
                (intent_id, operation_id, f"confirmation-{suffix}", "d" * 64),
            )
            evidence = {**receipt_payload, "manager_receipt_hash": manager_hash,
                        "install_root": str(tmp_path / "batch")}
            await db.execute(
                """INSERT INTO capability_operation_phase_evidence
                   VALUES(?,'batch_committed',?,'committed',?,1,1)""",
                (operation_id, f"{operation_id}:batch_committed", canonical_json(evidence)),
            )
        await db.execute(
            """UPDATE capability_skill_install_intents
               SET status=?,verification_ref=?,settlement_ref=? WHERE intent_id=?""",
            (
                status,
                f"runtime-proof-{suffix}" if status == "succeeded" else None,
                manager_hash,
                intent_id,
            ),
        )


@pytest.mark.asyncio
async def test_exact_legacy_project_bindings_promote_n_to_one_and_retire_atomically(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    await _seed_legacy(store, tmp_path, project="project:a", suffix="a")
    await _seed_legacy(store, tmp_path, project="project:b", suffix="b")
    owner = "user:v2:" + "1" * 64

    result = await store.converge_legacy_project_skill_bindings(global_owner_key=owner)

    assert result[0]["outcome"] == "promoted"
    assert await store.get_binding("user", owner, "godot", owner_key=owner) is not None
    assert await store.get_binding("project", "project:a", "godot", owner_key="principal") is None
    assert await store.get_binding("project", "project:b", "godot", owner_key="principal") is None


@pytest.mark.asyncio
async def test_different_legacy_sources_record_conflict_and_preserve_bindings(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    await _seed_legacy(store, tmp_path, project="project:a", suffix="a")
    await _seed_legacy(
        store, tmp_path, project="project:b", suffix="b", content_hash="9" * 64,
    )
    owner = "user:v2:" + "2" * 64

    result = await store.converge_legacy_project_skill_bindings(global_owner_key=owner)

    assert result[0]["outcome"] == "legacy_global_conflict"
    assert await store.get_binding("user", owner, "godot", owner_key=owner) is None
    assert await store.get_binding("project", "project:a", "godot", owner_key="principal") is not None
    assert await store.get_binding("project", "project:b", "godot", owner_key="principal") is not None
    async with store.read_connection() as db:
        row = await (await db.execute(
            """SELECT outcome FROM capability_legacy_global_convergence
               WHERE global_owner_key=? AND pack_id='godot'""", (owner,)
        )).fetchone()
    assert row["outcome"] == "legacy_global_conflict"


@pytest.mark.asyncio
async def test_nonterminal_publish_settlement_is_not_manager_commit_proof(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    await _seed_legacy(
        store, tmp_path, project="project:publishing", suffix="publishing",
        status="publishing", receipt_ref="ordinary-settlement-ref",
        valid_manager_receipt=False,
    )
    owner = "user:v2:" + "3" * 64

    result = await store.converge_legacy_project_skill_bindings(global_owner_key=owner)

    assert result[0]["outcome"] == "legacy_global_conflict"
    assert await store.get_binding("user", owner, "godot", owner_key=owner) is None
    assert await store.get_binding(
        "project", "project:publishing", "godot", owner_key="principal"
    ) is not None


@pytest.mark.asyncio
async def test_plain_succeeded_receipt_string_fails_closed(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    await _seed_legacy(
        store, tmp_path, project="project:plain", suffix="plain",
        receipt_ref="not-a-manager-receipt", valid_manager_receipt=False,
    )
    owner = "user:v2:" + "4" * 64
    result = await store.converge_legacy_project_skill_bindings(global_owner_key=owner)
    assert result[0]["outcome"] == "legacy_global_conflict"


@pytest.mark.asyncio
async def test_tampered_manager_receipt_hash_fails_closed(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    await _seed_legacy(store, tmp_path, project="project:tampered", suffix="tampered")
    async with store.write_transaction() as db:
        row = await (await db.execute(
            """SELECT evidence_json FROM capability_operation_phase_evidence
               WHERE operation_id='operation-tampered' AND phase='batch_committed'"""
        )).fetchone()
        evidence = json.loads(row["evidence_json"])
        evidence["manager_receipt_hash"] = "0" * 64
        await db.execute(
            """UPDATE capability_operation_phase_evidence SET evidence_json=?
               WHERE operation_id='operation-tampered' AND phase='batch_committed'""",
            (canonical_json(evidence),),
        )
        await db.execute(
            """UPDATE capability_skill_install_intents SET settlement_ref=?
               WHERE intent_id='legacy-tampered'""", ("0" * 64,)
        )
    owner = "user:v2:" + "5" * 64
    result = await store.converge_legacy_project_skill_bindings(global_owner_key=owner)
    assert result[0]["outcome"] == "legacy_global_conflict"


@pytest.mark.asyncio
async def test_incomplete_multi_member_receipt_fails_closed(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    await _seed_legacy(store, tmp_path, project="project:multi", suffix="multi")
    extra = _record(tmp_path, "extra")
    await store.record_version(extra)
    async with store.write_transaction() as db:
        await db.execute(
            """INSERT INTO capability_skill_install_members
               VALUES('legacy-multi',1,'extra','extra','1.0.0',?,? ,?,'{}')""",
            (extra.descriptor.manifest_hash, "7" * 64, "8" * 64),
        )
        await db.execute(
            """INSERT INTO capability_operation_members(
               operation_id,ordinal,normalized_name,pack_id,version,manifest_hash,
               content_hash,source_digest,committed_version,
               committed_manifest_hash,committed_set_stamp
               ) VALUES('operation-multi',1,'extra','extra','1.0.0',?,?,?,?,?,?)""",
            (extra.descriptor.manifest_hash, "7" * 64, "8" * 64, "1.0.0",
             extra.descriptor.manifest_hash, "d" * 64),
        )
    owner = "user:v2:" + "6" * 64
    result = await store.converge_legacy_project_skill_bindings(global_owner_key=owner)
    assert result[0]["outcome"] == "legacy_global_conflict"


@pytest.mark.asyncio
async def test_existing_global_same_version_manifest_but_different_content_receipt_identity_is_rejected(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    await _seed_legacy(
        store, tmp_path, project="project:existing", suffix="existing",
        version_derived_receipt="different-global-content-receipt",
    )
    owner = "user:v2:" + "7" * 64
    record = await store.get_version("godot", "1.0.0", _record(tmp_path).descriptor.manifest_hash)
    assert record is not None
    await store.set_binding(
        scope="user", scope_key=owner, pack_id="godot", version="1.0.0",
        manifest_hash=record.descriptor.manifest_hash, expected_generation=0,
        owner_key=owner, management_policy="user_managed",
    )
    result = await store.converge_legacy_project_skill_bindings(global_owner_key=owner)
    assert result[0]["outcome"] == "legacy_global_conflict"
    assert await store.get_binding(
        "project", "project:existing", "godot", owner_key="principal"
    ) is not None
