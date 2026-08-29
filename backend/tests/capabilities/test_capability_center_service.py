from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from deskpet.capabilities.contracts import (
    CapabilityCatalogSnapshot,
    CapabilityDescriptor,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogStamp,
)
from deskpet.capabilities.manifest import (
    PACK_MANIFEST_NAME,
    parse_pack_manifest,
)
from deskpet.capabilities.store import (
    CapabilityStore,
    CapabilityVersionRecord,
    initialize_capability_database,
)
from deskpet.capabilities.ui_projection import project_capability_operation
from deskpet.capabilities.ui_service import CapabilityCenterService


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _store(tmp_path: Path) -> CapabilityStore:
    database = await initialize_capability_database(tmp_path / "workflow.db")
    return CapabilityStore(database)


async def _installed_snapshot(
    store: CapabilityStore, tmp_path: Path
) -> CapabilityCatalogSnapshot:
    descriptor = CapabilityVersionDescriptor(
        capability_id="photo-pack",
        display_name="Photo token=tsk_not_a_real_token",
        version="1.0.0",
        kind="pack",
        source="local:C:/packs/photo",
        description="API_KEY=super-secret",
        aliases=(),
        logical_tool_ids=("photo_rename",),
        provider_tool_names=("photo_rename",),
        permission_categories=("write_file",),
        effect_kinds=("filesystem_write",),
        schema_hash=_sha("schema"),
        manifest_hash=_sha("manifest"),
        health="healthy",
    )
    record = CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=tmp_path / "pack",
        validation_status="healthy",
        expected_tool_fingerprints=(_sha("tool"),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=1.0,
    )
    await store.record_version(record)
    binding = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id=descriptor.capability_id,
        version=descriptor.version,
        manifest_hash=descriptor.manifest_hash,
        expected_generation=0,
    )
    stamp = CatalogStamp(
        catalog_generation=1,
        registry_revision=1,
        binding_generation=1,
        skill_revision=0,
        mcp_revision=0,
    )
    from deskpet.capabilities.contracts import CapabilityDescriptor

    return CapabilityCatalogSnapshot(
        stamp=stamp,
        scope=CapabilityScope(),
        descriptors=(
            CapabilityDescriptor(
                version=descriptor,
                visible_bindings=(binding,),
                executable=True,
                installed=True,
                tool_spec_fingerprints=record.expected_tool_fingerprints,
                stamp=stamp,
            ),
        ),
        created_at=1.0,
    )


class _StaticHub:
    def __init__(self, snapshot: CapabilityCatalogSnapshot) -> None:
        self._snapshot = snapshot
        self.scopes: list[CapabilityScope] = []

    async def snapshot(self, scope: CapabilityScope) -> CapabilityCatalogSnapshot:
        self.scopes.append(scope)
        return self._snapshot


class _SlowManager:
    def __init__(self, store: CapabilityStore) -> None:
        self.store = store
        self.started = asyncio.Event()
        self.calls: list[dict[str, Any]] = []

    async def uninstall(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))
        self.started.set()
        await asyncio.Event().wait()

    async def cancel_operation(self, operation_id: str):
        operation = await self.store.get_operation(operation_id)
        if operation is not None and operation.status == "running":
            return await self.store.fail_operation(
                operation_id,
                status="cancelled",
                error={"code": "cancelled", "phase": operation.phase},
            )
        return operation


@pytest.mark.asyncio
async def test_capability_center_redacts_projection_and_only_advertises_real_action(
    tmp_path: Path,
) -> None:
    store = await _store(tmp_path)
    snapshot = await _installed_snapshot(store, tmp_path)
    service = CapabilityCenterService(
        store=store,
        manager=_SlowManager(store),  # type: ignore[arg-type]
        hub=_StaticHub(snapshot),
    )

    capabilities = await service.list_capabilities()

    assert capabilities == [
        {
            "capability_id": "photo-pack",
            "name": "Photo token=[REDACTED]",
            "description": "API_KEY=[REDACTED]",
            "categories": ["pack", "tool"],
            "version": "1.0.0",
            "source": {
                "type": "local",
                "label": "local:C:/packs/photo",
            },
            "scope": "user",
            "health": "healthy",
            "health_summary": "Executable and verified in the current catalog.",
            "installed": True,
            "available_actions": ["uninstall"],
        }
    ]


@pytest.mark.asyncio
async def test_capability_center_default_snapshot_includes_user_global_scope(
    tmp_path: Path,
) -> None:
    store = await _store(tmp_path)
    snapshot = await _installed_snapshot(store, tmp_path)
    hub = _StaticHub(snapshot)
    global_owner_key = f"user:v2:{_sha('local-user')}"
    service = CapabilityCenterService(
        store=store,
        manager=_SlowManager(store),  # type: ignore[arg-type]
        hub=hub,
        default_user_key=global_owner_key,
    )

    await service.list_capabilities()

    assert hub.scopes == [CapabilityScope(user_key=global_owner_key)]


@pytest.mark.asyncio
async def test_capability_center_projects_complete_installed_manifest(
    tmp_path: Path,
) -> None:
    store = await _store(tmp_path)
    repo_root = Path(__file__).resolve().parents[3]
    raw = json.loads(
        (
            repo_root
            / "capability-packs"
            / "godot"
            / PACK_MANIFEST_NAME
        ).read_text(encoding="utf-8")
    )
    manifest = parse_pack_manifest(raw)
    descriptor = manifest.descriptor(health="healthy")
    install_path = tmp_path / "installed" / "godot"
    install_path.mkdir(parents=True)
    (install_path / PACK_MANIFEST_NAME).write_text(
        json.dumps(raw, ensure_ascii=False),
        encoding="utf-8",
    )
    record = CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=install_path,
        validation_status="healthy",
        expected_tool_fingerprints=(_sha("godot-tool"),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=1.0,
    )
    await store.record_version(record)
    binding = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id=descriptor.capability_id,
        version=descriptor.version,
        manifest_hash=descriptor.manifest_hash,
        expected_generation=0,
    )
    stamp = CatalogStamp(1, 1, 1, 0, 0)
    snapshot = CapabilityCatalogSnapshot(
        stamp=stamp,
        scope=CapabilityScope(),
        descriptors=(
            CapabilityDescriptor(
                version=descriptor,
                visible_bindings=(binding,),
                executable=True,
                installed=True,
                tool_spec_fingerprints=record.expected_tool_fingerprints,
                stamp=stamp,
            ),
        ),
        created_at=1.0,
    )
    service = CapabilityCenterService(
        store=store,
        manager=_SlowManager(store),  # type: ignore[arg-type]
        hub=_StaticHub(snapshot),
    )

    projected = (await service.list_capabilities())[0]["manifest"]

    assert projected["compatibility"]["os"] == ["windows"]
    assert projected["entries"]["skills"] == [
        {"path": "skills/godot/SKILL.md"}
    ]
    assert {
        tool["provider_name"] for tool in projected["entries"]["tools"]
    } == {"godot__detect", "godot__project_check"}
    assert projected["permissions"] == [
        "filesystem_read",
        "filesystem_write",
        "process_execute",
    ]
    assert projected["dependencies"]["commands"] == [
        {"name": "godot", "version": ">=4.0"}
    ]
    assert len(projected["files"]) == 8
    assert projected["uninstall"] == {
        "stop_servers": True,
        "remove_environment_when_unreferenced": True,
    }


@pytest.mark.asyncio
async def test_capability_center_cancel_targets_only_its_owned_task(
    tmp_path: Path,
) -> None:
    store = await _store(tmp_path)
    snapshot = await _installed_snapshot(store, tmp_path)
    manager = _SlowManager(store)
    pushed: list[dict[str, Any]] = []

    async def notify(message):
        pushed.append(dict(message))

    service = CapabilityCenterService(
        store=store,
        manager=manager,  # type: ignore[arg-type]
        hub=_StaticHub(snapshot),
        notifier=notify,
    )
    started = await service.request_uninstall(capability_id="photo-pack")
    await asyncio.wait_for(manager.started.wait(), timeout=1.0)

    operations = await service.list_operations()
    assert operations[0]["operation_id"] == started.operation_id
    assert operations[0]["available_actions"] == ["cancel"]

    cancelled = await service.request_cancel(
        operation_id=started.operation_id
    )

    assert cancelled.status == "cancelled"
    assert len(manager.calls) == 1
    assert any(
        message.get("payload", {}).get("operation", {}).get("status")
        == "cancelled"
        for message in pushed
    )
    await service.shutdown()


@pytest.mark.asyncio
async def test_operation_projection_never_blindly_retries_unknown_publish(
    tmp_path: Path,
) -> None:
    store = await _store(tmp_path)
    operation = await store.create_operation(
        operation_id=_sha("operation"),
        idempotency_key="operation",
        kind="update",
        request={
            "source": {
                "type": "local",
                "uri": str(tmp_path),
                "revision": "local",
                "subdirectory": None,
            },
            "scope": "user",
            "scope_key": "default",
        },
        pack_id="photo-pack",
        requested_scope="user",
        requested_scope_key="default",
    )
    operation = await store.fail_operation(
        operation.operation_id,
        status="unknown",
        error={
            "code": "publish_state_unknown",
            "message": "token=tsk_should_be_hidden",
            "phase": "publish_intent",
        },
    )

    projected = project_capability_operation(
        operation,
        authorization_mode="auto",
        retryable=True,
    )

    assert projected["available_actions"] == []
    assert projected["error_message"] == "token=[REDACTED]"
    assert projected["recovery_hint"] == (
        "Reconciliation is required before another mutation."
    )
