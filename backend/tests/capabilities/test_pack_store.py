from __future__ import annotations

import hashlib

import pytest

from deskpet.capabilities.contracts import (
    CapabilityScope,
    CapabilityVersionDescriptor,
)
from deskpet.capabilities.store import (
    CapabilitySchemaMissing,
    CapabilityStore,
    CapabilityStoreConflict,
    CapabilityVersionRecord,
    initialize_capability_database,
)
from deskpet.permissions.task_grants import ResourceSelector, TaskGrant


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _record(tmp_path, *, version: str = "1.0.0", manifest: str = "manifest"):
    descriptor = CapabilityVersionDescriptor(
        capability_id="godot",
        display_name="Godot",
        version=version,
        kind="function_tool",
        source="local:fixture@1",
        description="Godot helper",
        aliases=("game",),
        logical_tool_ids=("check",),
        provider_tool_names=("godot__check",),
        permission_categories=("filesystem_read",),
        effect_kinds=("read_only",),
        schema_hash=_hash(f"schema:{version}"),
        manifest_hash=_hash(manifest),
        health="healthy",
    )
    return CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=tmp_path / "capabilities" / "packs" / "godot" / version,
        validation_status="healthy",
        expected_tool_fingerprints=(_hash(f"tool:{version}"),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=100.0,
    )


@pytest.mark.asyncio
async def test_store_requires_execution_owned_schema(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    path.touch()
    store = CapabilityStore(path)
    with pytest.raises(CapabilitySchemaMissing) as caught:
        await store.initialize()
    assert caught.value.code == "capability_schema_missing"


@pytest.mark.asyncio
async def test_versions_are_immutable_and_binding_is_cas(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    record = _record(tmp_path)
    assert await store.record_version(record) == record
    assert await store.record_version(record) == record

    conflicting = _record(tmp_path, manifest="different")
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.record_version(conflicting)
    assert caught.value.code == "capability_version_conflict"

    binding = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id="godot",
        version="1.0.0",
        manifest_hash=record.descriptor.manifest_hash,
        expected_generation=0,
    )
    assert binding.generation == 1
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.set_binding(
            scope="user",
            scope_key="default",
            pack_id="godot",
            version="1.0.0",
            manifest_hash=record.descriptor.manifest_hash,
            expected_generation=99,
            enabled=False,
        )
    assert caught.value.code == "binding_generation_conflict"

    entries = await store.visible_entries(CapabilityScope(user_key="default"))
    assert len(entries) == 1
    assert entries[0].version.capability_id == "godot"
    assert entries[0].bindings == (binding,)


@pytest.mark.asyncio
async def test_operation_phase_intents_are_strict_and_idempotent(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    operation = await store.create_operation(
        operation_id="op-1",
        idempotency_key="request-1",
        kind="install",
        request={"source": "fixture"},
        requested_scope="user",
        requested_scope_key="default",
    )
    assert operation.phase == "planned"
    assert (await store.commit_phase("op-1", "planned")).phase == "planned"
    staged = await store.commit_phase("op-1", "staged", evidence={"path": "x"})
    assert staged.phase == "staged"
    assert (
        await store.commit_phase("op-1", "staged", evidence={"path": "x"})
    ).phase == "staged"
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.commit_phase("op-1", "environment_ready")
    assert caught.value.code == "invalid_phase_transition"


@pytest.mark.asyncio
async def test_caller_owned_transaction_rolls_back_every_domain_write(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    with pytest.raises(RuntimeError, match="crash"):
        async with store.write_transaction() as db:
            await store.bind(db).create_operation(
                operation_id="op-rollback",
                idempotency_key="rollback",
                kind="install",
                request={"source": "fixture"},
            )
            raise RuntimeError("crash")
    assert await store.get_operation("op-rollback") is None


@pytest.mark.asyncio
async def test_publish_intent_blocks_snapshot_lease_until_reconciled(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    record = _record(tmp_path)
    await store.record_version(record)
    binding = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id="godot",
        version="1.0.0",
        manifest_hash=record.descriptor.manifest_hash,
        expected_generation=0,
    )
    await store.create_operation(
        operation_id="op-1",
        idempotency_key="request-1",
        kind="install",
        request={"source": "fixture"},
        pack_id="godot",
    )
    await store.create_publish_intent(
        intent_id="intent-1",
        operation_id="op-1",
        expected_registry_revision=1,
        old_specs=(),
        new_specs=(),
        old_binding=None,
        new_binding=binding.to_dict(),
    )
    entries = await store.visible_entries(CapabilityScope(user_key="default"))
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.acquire_snapshot_lease(
            snapshot_ref=_hash("snapshot"),
            run_id="run-1",
            root_run_id="root-1",
            entries=entries,
        )
    assert caught.value.code == "publish_reconciliation_required"

    await store.advance_publish_intent(
        "intent-1", phase="catalog_swapped", status="pending"
    )
    await store.advance_publish_intent("intent-1", phase="bound", status="committed")
    await store.acquire_snapshot_lease(
        snapshot_ref=_hash("snapshot"),
        run_id="run-1",
        root_run_id="root-1",
        entries=entries,
    )
    assert await store.version_has_active_lease(
        "godot", "1.0.0", record.descriptor.manifest_hash
    )


@pytest.mark.asyncio
async def test_policy_and_task_grant_share_the_execution_database(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 101.0)
    state = await store.get_policy_state()
    assert (state.mode, state.generation) == ("manual", 0)
    auto = await store.compare_and_set_policy_mode(
        "auto", expected_generation=0
    )
    assert (auto.mode, auto.generation) == ("auto", 1)

    grant = TaskGrant(
        task_grant_id="grant-1",
        root_run_id="root-1",
        principal_id="user-1",
        resource_selectors=(
            ResourceSelector.filesystem(tmp_path / "workspace", "write"),
        ),
        permission_categories=("filesystem_write",),
        effect_kinds=("staged_file",),
        source="policy:auto",
        policy_generation=1,
        expires_at=500.0,
        version=1,
    )
    await store.put_task_grant(grant)
    assert await store.get_task_grant("grant-1") == grant
    assert await store.revoke_task_grant("grant-1")
    assert await store.get_task_grant("grant-1") is None


@pytest.mark.asyncio
async def test_builtin_install_replay_ignores_source_uri(tmp_path) -> None:
    """WBUI-DEF-S08-02 回归：builtin 包的 source.uri 是安装路径，不是操作身份。

    同一个 user-data 目录先后被两个安装路径不同的构建打开时，first-party
    install 的重放必须命中原记录（幂等键是内容哈希，与路径无关）；修复前
    request_json 逐字节参与身份比对，路径一变整个 lifespan 就抛
    operation_idempotency_conflict，后端永远起不来。
    """
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)

    def install_request(uri: str) -> dict:
        return {
            "source": {
                "type": "builtin",
                "uri": uri,
                "revision": "shipped-v1",
                "subdirectory": None,
            },
            "scope": "builtin",
            "scope_key": "builtin",
            "generated": False,
            "expected_pack_id": "skill-doc-edit",
            "parent_version": None,
            "parent_manifest_hash": None,
            "derived_from_receipt_ref": None,
        }

    first = await store.create_operation(
        operation_id="op-builtin-1",
        idempotency_key="first-party:skill-doc-edit:0.1.0:abc123",
        kind="install",
        request=install_request("/tmp/old-install-location/packs/skill-doc-edit"),
        pack_id="skill-doc-edit",
        requested_scope="builtin",
        requested_scope_key="builtin",
    )

    # 换一个安装路径重放：必须返回原记录，而不是 conflict。
    replay = await store.create_operation(
        operation_id="op-builtin-1",
        idempotency_key="first-party:skill-doc-edit:0.1.0:abc123",
        kind="install",
        request=install_request("/opt/new-install-location/packs/skill-doc-edit"),
        pack_id="skill-doc-edit",
        requested_scope="builtin",
        requested_scope_key="builtin",
    )
    assert replay.operation_id == first.operation_id
    assert replay.request == first.request  # 落库的仍是首次请求原文

    # 但 builtin 请求的其他字段变了仍然要 conflict——归一化只豁免 uri。
    mutated = install_request("/opt/new-install-location/packs/skill-doc-edit")
    mutated["generated"] = True
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.create_operation(
            operation_id="op-builtin-1",
            idempotency_key="first-party:skill-doc-edit:0.1.0:abc123",
            kind="install",
            request=mutated,
            pack_id="skill-doc-edit",
            requested_scope="builtin",
            requested_scope_key="builtin",
        )
    assert caught.value.code == "operation_idempotency_conflict"


@pytest.mark.asyncio
async def test_non_builtin_install_uri_still_part_of_identity(tmp_path) -> None:
    """git 等外部来源的 uri 就是"装的是什么"，换 uri 必须仍然 conflict。"""
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)

    def git_request(uri: str) -> dict:
        return {
            "source": {
                "type": "git",
                "uri": uri,
                "revision": "main",
                "subdirectory": None,
            },
            "scope": "user",
            "scope_key": "default",
            "generated": False,
            "expected_pack_id": "community-pack",
            "parent_version": None,
            "parent_manifest_hash": None,
            "derived_from_receipt_ref": None,
        }

    await store.create_operation(
        operation_id="op-git-1",
        idempotency_key="user-install-1",
        kind="install",
        request=git_request("https://example.com/a/pack.git"),
        pack_id="community-pack",
        requested_scope="user",
        requested_scope_key="default",
    )
    with pytest.raises(CapabilityStoreConflict) as caught:
        await store.create_operation(
            operation_id="op-git-1",
            idempotency_key="user-install-1",
            kind="install",
            request=git_request("https://example.com/b/pack.git"),
            pack_id="community-pack",
            requested_scope="user",
            requested_scope_key="default",
        )
    assert caught.value.code == "operation_idempotency_conflict"
