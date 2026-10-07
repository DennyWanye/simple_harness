from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from deskpet.capabilities.contracts import CapabilityScope
from deskpet.capabilities.hub import CapabilityHub, ToolRegistryCatalogSource
from deskpet.capabilities.manager import CapabilityPackManager
from deskpet.capabilities.manifest import PackEnvironment
from deskpet.capabilities.publisher import (
    ToolRegistryCapabilityPublisher,
    capability_registry_source,
    capability_spec_version,
)
from deskpet.capabilities.source import PackSourceRequest
from deskpet.capabilities.store import (
    CapabilityStore,
    initialize_capability_database,
)
from deskpet.tools.build_identity import ExecutionBuildIdentity
from deskpet.tools.registry import PreparedToolCallStale, ToolRegistry


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_pack(
    root: Path,
    *,
    version: str,
    revision: str,
) -> PackSourceRequest:
    entry = root / "tools" / "main.py"
    schema = root / "tools" / "check.schema.json"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("print('ok')\n", encoding="utf-8")
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                },
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "id": "godot",
        "name": "Godot",
        "version": version,
        "source": {
            "type": "local",
            "uri": str(root),
            "revision": revision,
        },
        "compatibility": {
            "deskpet": ">=0.5.0",
            "os": ["windows"],
            "architectures": ["x86_64"],
            "python": ">=3.11",
        },
        "entries": {
            "skills": [],
            "tools": [
                {
                    "id": "check",
                    "provider_name": "godot__check",
                    "runtime": "deskpet-json-tool-v1",
                    "execution_profile": "native-adapter",
                    "input_views": [],
                    "entry": "tools/main.py",
                    "schema": "tools/check.schema.json",
                    "healthcheck": "check",
                }
            ],
            "mcp_servers": [],
        },
        "permissions": ["filesystem_read"],
        "effects": ["read_only"],
        "dependencies": {"python": [], "commands": []},
        "files": [
            {"path": "tools/main.py", "sha256": _hash_file(entry)},
            {
                "path": "tools/check.schema.json",
                "sha256": _hash_file(schema),
            },
        ],
        "uninstall": {
            "stop_servers": True,
            "remove_environment_when_unreferenced": True,
        },
    }
    (root / "deskpet-pack.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return PackSourceRequest("local", str(root), revision)


class _SpecFactory:
    def build_specs(
        self,
        validation,
        *,
        install_root,
        operation_id,
    ):
        del operation_id
        manifest = validation.manifest
        source = capability_registry_source(
            manifest.id,
            manifest.version,
            manifest.manifest_hash,
        )
        spec_version = capability_spec_version(
            manifest.version,
            manifest.manifest_hash,
        )
        candidate = ToolRegistry()
        for tool in manifest.tools:
            parameters = json.loads(
                (install_root / tool.schema).read_text(encoding="utf-8")
            )
            # The Run catalog only advertises tools with a durable execution
            # build identity (hub.ToolRegistryCatalogSource); the product pack
            # factory (capabilities/platform.py) always stamps one.
            handler_id = f"capability:{manifest.id}:{tool.provider_name}"
            sources_hash = hashlib.sha256(
                f"{source}:{tool.provider_name}:sources".encode()
            ).hexdigest()
            build = ExecutionBuildIdentity(
                provider="deskpet-capability-pack",
                handler_id=handler_id,
                build_digest=hashlib.sha256(
                    f"{source}:{tool.provider_name}:build".encode()
                ).hexdigest(),
                sources_manifest_hash=sources_hash,
                artifacts=(
                    (f"pack:{tool.schema}", hashlib.sha256(
                        (install_root / tool.schema).read_bytes()
                    ).hexdigest()),
                ),
            )
            candidate.register(
                tool.provider_name,
                f"capability:{manifest.id}",
                {
                    "name": tool.provider_name,
                    "description": f"{manifest.name} {tool.id}",
                    "parameters": parameters,
                },
                lambda _args, _task_id: json.dumps({"ok": True}),
                permission_category=manifest.permissions[0],
                source=source,
                spec_version=spec_version,
                runtime_provenance_ref=hashlib.sha256(
                    f"{source}:{tool.provider_name}:runtime".encode()
                ).hexdigest(),
                stable_handler_id=build.handler_id,
                execution_build_identity=build,
            )
        return candidate.catalog_snapshot().specs


class _Crash(BaseException):
    pass


async def _runtime(
    tmp_path,
    *,
    fault_injector=None,
    publish_lock=None,
):
    database = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(database, clock=lambda: 100.0)
    registry = ToolRegistry()
    environment = PackEnvironment(
        deskpet_version="0.6.0",
        os="windows",
        architecture="x86_64",
        python_version="3.11.9",
    )
    publisher = ToolRegistryCapabilityPublisher(
        registry=registry,
        spec_factory=_SpecFactory(),
        environment=environment,
    )
    manager = CapabilityPackManager(
        store=store,
        user_data_root=tmp_path / "user-data",
        publisher=publisher,
        environment=environment,
        publish_lock=publish_lock,
        fault_injector=fault_injector,
    )
    return manager, store, registry, publisher


@pytest.mark.asyncio
async def test_atomic_publisher_install_update_rollback_and_uninstall(
    tmp_path,
) -> None:
    lock = asyncio.Lock()
    manager, store, registry, _publisher = await _runtime(
        tmp_path,
        publish_lock=lock,
    )
    hub = CapabilityHub(
        store=store,
        registry_source=ToolRegistryCatalogSource(registry),
        publish_lock=lock,
    )
    v1 = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    first = await manager.install(
        v1,
        scope="user",
        scope_key="default",
        idempotency_key="install-v1",
        expected_pack_id="godot",
    )
    first_snapshot = await hub.snapshot_and_acquire_lease(
        run_id="run-v1",
        root_run_id="root-v1",
        scope=CapabilityScope(),
    )
    projected = first_snapshot.get("godot")
    assert projected is not None and projected.executable
    assert projected.version.version == "1.0.0"
    old_call = registry.prepare_call(
        "godot__check",
        {},
        "session",
        "call-v1",
        catalog_snapshot_ref=first_snapshot.snapshot_ref,
    )

    v2 = _write_pack(tmp_path / "v2", version="2.0.0", revision="r2")
    second = await manager.update(
        v2,
        scope="user",
        scope_key="default",
        idempotency_key="install-v2",
        expected_pack_id="godot",
    )
    projected = (await hub.snapshot(CapabilityScope())).get("godot")
    assert projected is not None and projected.executable
    assert projected.version.version == "2.0.0"
    assert second.tool_spec_fingerprints != first.tool_spec_fingerprints
    assert ":1.0.0:" in registry.resolve_prepared_spec(old_call).source
    assert await hub.release_lease(first_snapshot.snapshot_ref, "run-v1") == 1
    with pytest.raises(PreparedToolCallStale):
        registry.resolve_prepared_spec(old_call)

    rolled_back = await manager.rollback(
        pack_id="godot",
        target_version="1.0.0",
        target_manifest_hash=first.validation.manifest.manifest_hash,
        scope="user",
        scope_key="default",
        idempotency_key="rollback-v1",
    )
    assert rolled_back.binding.version == "1.0.0"
    assert registry.catalog_snapshot().revision == 3

    removed = await manager.uninstall(
        pack_id="godot",
        scope="user",
        scope_key="default",
        idempotency_key="uninstall-v1",
    )
    assert not removed.binding.active
    assert registry.catalog_snapshot().specs == ()
    assert (await hub.snapshot(CapabilityScope())).get("godot") is None


@pytest.mark.asyncio
async def test_atomic_publisher_crash_reconciles_new_registry_state(
    tmp_path,
) -> None:
    def crash(point: str) -> None:
        if point == "after:catalog_swapped":
            raise _Crash()

    lock = asyncio.Lock()
    manager, store, registry, _publisher = await _runtime(
        tmp_path,
        fault_injector=crash,
        publish_lock=lock,
    )
    source = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    with pytest.raises(_Crash):
        await manager.install(
            source,
            scope="user",
            scope_key="default",
            idempotency_key="crash-after-swap",
            expected_pack_id="godot",
        )
    assert registry.list_tools() == ["godot__check"]
    assert await store.get_binding("user", "default", "godot") is None

    manager._fault_injector = None
    recovered = await manager.recover()
    binding = await store.get_binding("user", "default", "godot")
    assert len(recovered) == 1
    assert recovered[0].status == "succeeded"
    assert binding is not None and binding.active


@pytest.mark.asyncio
async def test_shared_publish_lock_never_exposes_mixed_snapshot(tmp_path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pause(point: str) -> None:
        if point == "after:catalog_swapped":
            entered.set()
            await release.wait()

    lock = asyncio.Lock()
    manager, store, registry, _publisher = await _runtime(
        tmp_path,
        fault_injector=pause,
        publish_lock=lock,
    )
    hub = CapabilityHub(
        store=store,
        registry_source=ToolRegistryCatalogSource(registry),
        publish_lock=lock,
    )
    source = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    install_task = asyncio.create_task(
        manager.install(
            source,
            scope="user",
            scope_key="default",
            idempotency_key="paused-publish",
            expected_pack_id="godot",
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    snapshot_task = asyncio.create_task(hub.snapshot(CapabilityScope()))
    await asyncio.sleep(0.05)
    assert not snapshot_task.done()

    release.set()
    await asyncio.wait_for(install_task, timeout=2.0)
    snapshot = await asyncio.wait_for(snapshot_task, timeout=2.0)
    projected = snapshot.get("godot")
    assert projected is not None and projected.executable
