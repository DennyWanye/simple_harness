from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from deskpet.capabilities.brokered_planner import BrokeredEffectPlanner
from deskpet.capabilities.contracts import CapabilityScope
from deskpet.capabilities.input_views import InputViewResolver
from deskpet.capabilities.local_runtime import LocalToolRuntime
from deskpet.capabilities.manager import CapabilityManagerError
from deskpet.capabilities.manifest import (
    PackEnvironment,
    PackManifestError,
    load_and_validate_pack,
)
from deskpet.capabilities.platform import (
    CapabilityPlatform,
    LegacyPluginCatalogSource,
    LocalCapabilityToolSpecFactory,
    MCPManagerRevisionSource,
)
from deskpet.capabilities.store import (
    CapabilityStore,
    initialize_capability_database,
)
from deskpet.capabilities.source import PackSourceRequest
from deskpet.capabilities.tool_proxy import LocalToolProxy
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import (
    ToolRegistry,
    legacy_tool_spec_fingerprint_pre_authority_v2,
    legacy_tool_spec_fingerprint_v1,
    tool_spec_fingerprint,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GODOT_PACK = REPOSITORY_ROOT / "capability-packs" / "godot"
TEST_ENVIRONMENT = PackEnvironment(
    deskpet_version="0.6.0-beta.9",
    os="windows",
    architecture="x86_64",
    python_version="3.11.9",
)


def _copy_pack(source: Path, destination: Path) -> Path:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    return destination


def _replace_declared_hash(pack_root: Path, relative: str) -> None:
    manifest_path = pack_root / "deskpet-pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256((pack_root / relative).read_bytes()).hexdigest()
    for item in manifest["files"]:
        if item["path"] == relative:
            item["sha256"] = digest
            break
    else:  # pragma: no cover - fixture invariant
        raise AssertionError(f"manifest does not declare {relative}")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


async def _platform(
    tmp_path: Path,
    *,
    pack_root: Path,
    runtime: LocalToolRuntime | None = None,
) -> tuple[CapabilityPlatform, CapabilityStore, ToolRegistry]:
    # Keep immutable pack paths below the legacy Windows MAX_PATH boundary.
    short_root = (
        tmp_path.parent
        / f"cp-{hashlib.sha256(tmp_path.name.encode()).hexdigest()[:10]}"
    )
    short_root.mkdir(parents=True, exist_ok=True)
    database = await initialize_capability_database(short_root / "workflow.db")
    store = CapabilityStore(database)
    registry = ToolRegistry()
    platform = CapabilityPlatform(
        registry=registry,
        store=store,
        user_data_root=short_root / "u",
        environment=TEST_ENVIRONMENT,
        runtime=runtime,
        first_party_pack_roots=(pack_root,),
        register_control_surface=False,
        command_finder=lambda _name: None,
    )
    return platform, store, registry


def _context(workspace: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id="root-run",
        turn_id="turn",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash="capability-hash",
        scope_hash="scope-hash",
        run_id="run",
        call_id="provider-call",
        effect_id="effect",
        trace_id="trace",
    )


@pytest.mark.asyncio
async def test_platform_installs_missing_godot_detector_idempotently_and_invokes_it(
    tmp_path: Path,
) -> None:
    platform, store, registry = await _platform(
        tmp_path, pack_root=GODOT_PACK
    )

    first = await platform.initialize()
    revision = registry.catalog_snapshot().revision
    second = await platform.initialize()

    assert first is second
    assert len(first.first_party_installs) == 1
    assert registry.catalog_snapshot().revision == revision
    binding = await store.get_binding("builtin", "builtin", "godot")
    assert binding is not None and binding.active
    projected = (await platform.hub.snapshot(CapabilityScope())).get("godot")
    assert projected is not None and projected.executable

    environment_evidence = await store.get_phase_evidence(
        first.first_party_installs[0].operation.operation_id,
        "environment_ready",
    )
    assert environment_evidence is not None
    command = environment_evidence["command_dependencies"][0]
    assert command["name"] == "godot"
    assert command["status"] == "missing_nonblocking_detector"
    assert environment_evidence["healthchecks"][0]["status"] == "success"

    fake_godot = tmp_path / "fake-godot"
    fake_godot.write_text(
        "#!/bin/sh\necho 4.3.stable.official.77dcf97d8\n",
        encoding="utf-8",
    )
    fake_godot.chmod(0o755)
    spec = next(
        item
        for item in registry.catalog_snapshot().specs
        if item.name == "godot__detect"
    )
    assert spec.execution_build_identity is not None
    assert (
        spec.execution_build_identity.handler_id
        == "capability.godot.detect.v1"
    )
    assert (
        spec.execution_build_identity.provider
        == "deskpet-capability-pack"
    )
    context = _context(tmp_path)
    prepared = registry.prepare_call(
        "godot__detect",
        {"preferred_executable": str(fake_godot)},
        context.session_id,
        context.call_id,
        execution_context=context,
    )
    assert [
        selector.kind for selector in prepared.resource_selectors
    ] == ["process_executable"]
    assert (
        prepared.resource_selectors[0].canonical_value
        == str(fake_godot.resolve())
    )
    assert spec.context_handler is not None
    outcome = await spec.context_handler(
        {"preferred_executable": str(fake_godot)},
        context,
    )
    payload = outcome.to_dict()
    assert payload["state"] == "success"
    detected = payload["value"]["value"]
    assert detected["found"] is True
    assert detected["compatible"] is True
    assert detected["version"] == "4.3.0"
    assert detected["executable"] == str(fake_godot.resolve())
    assert await platform.shutdown() == ()


@pytest.mark.asyncio
async def test_platform_migrates_exact_legacy_tool_fingerprints_on_rehydrate(
    tmp_path: Path,
) -> None:
    platform, store, registry = await _platform(
        tmp_path,
        pack_root=GODOT_PACK,
    )
    await platform.initialize()
    specs = tuple(
        spec
        for spec in registry.catalog_snapshot().specs
        if spec.source.startswith("capability:godot:")
    )
    record = (await store.list_active_versions())[0]
    current = tuple(tool_spec_fingerprint(spec) for spec in specs)
    legacy = tuple(legacy_tool_spec_fingerprint_v1(spec) for spec in specs)
    assert current == record.expected_tool_fingerprints
    assert legacy != current
    assert await store.migrate_version_tool_fingerprint_schema(
        pack_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        expected_fingerprints=current,
        replacement_fingerprints=legacy,
    )
    await platform.shutdown()

    restarted_registry = ToolRegistry()
    restarted = CapabilityPlatform(
        registry=restarted_registry,
        store=store,
        user_data_root=platform.manager.layout.root.parent,
        environment=TEST_ENVIRONMENT,
        first_party_pack_roots=(GODOT_PACK,),
        register_control_surface=False,
        command_finder=lambda _name: None,
    )
    initialized = await restarted.initialize()

    assert len(initialized.rehydrated_publications) == 1
    migrated = (await store.list_active_versions())[0]
    assert migrated.expected_tool_fingerprints == current
    assert {
        spec.name for spec in restarted_registry.catalog_snapshot().specs
    } == {"godot__detect", "godot__project_check"}
    assert await restarted.shutdown() == ()


@pytest.mark.asyncio
async def test_platform_migrates_exact_pre_authority_fingerprints_on_rehydrate(
    tmp_path: Path,
) -> None:
    platform, store, registry = await _platform(
        tmp_path,
        pack_root=GODOT_PACK,
    )
    await platform.initialize()
    specs = tuple(
        spec
        for spec in registry.catalog_snapshot().specs
        if spec.source.startswith("capability:godot:")
    )
    record = (await store.list_active_versions())[0]
    current = tuple(tool_spec_fingerprint(spec) for spec in specs)
    legacy = tuple(
        legacy_tool_spec_fingerprint_pre_authority_v2(spec)
        for spec in specs
    )
    assert current == record.expected_tool_fingerprints
    assert legacy != current
    assert await store.migrate_version_tool_fingerprint_schema(
        pack_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        expected_fingerprints=current,
        replacement_fingerprints=legacy,
    )
    await platform.shutdown()

    restarted_registry = ToolRegistry()
    restarted = CapabilityPlatform(
        registry=restarted_registry,
        store=store,
        user_data_root=platform.manager.layout.root.parent,
        environment=TEST_ENVIRONMENT,
        first_party_pack_roots=(GODOT_PACK,),
        register_control_surface=False,
        command_finder=lambda _name: None,
    )
    initialized = await restarted.initialize()

    assert len(initialized.rehydrated_publications) == 1
    migrated = (await store.list_active_versions())[0]
    assert migrated.expected_tool_fingerprints == current
    assert {
        spec.name for spec in restarted_registry.catalog_snapshot().specs
    } == {"godot__detect", "godot__project_check"}
    assert await restarted.shutdown() == ()


@pytest.mark.asyncio
async def test_platform_rejects_unknown_fingerprint_drift_on_rehydrate(
    tmp_path: Path,
) -> None:
    platform, store, registry = await _platform(
        tmp_path,
        pack_root=GODOT_PACK,
    )
    await platform.initialize()
    record = (await store.list_active_versions())[0]
    current = record.expected_tool_fingerprints
    unknown = tuple("f" * 64 for _value in current)
    assert await store.migrate_version_tool_fingerprint_schema(
        pack_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        expected_fingerprints=current,
        replacement_fingerprints=unknown,
    )
    await platform.shutdown()

    restarted = CapabilityPlatform(
        registry=ToolRegistry(),
        store=store,
        user_data_root=platform.manager.layout.root.parent,
        environment=TEST_ENVIRONMENT,
        first_party_pack_roots=(GODOT_PACK,),
        register_control_surface=False,
        command_finder=lambda _name: None,
    )
    with pytest.raises(CapabilityManagerError) as raised:
        await restarted.initialize()

    assert raised.value.code == "installed_tool_fingerprint_mismatch"
    assert await restarted.shutdown() == ()


@pytest.mark.asyncio
async def test_platform_rejects_first_party_integrity_failure_before_publish(
    tmp_path: Path,
) -> None:
    pack = _copy_pack(GODOT_PACK, tmp_path / "tampered-godot")
    worker = pack / "tools" / "godot" / "main.py"
    worker.write_text(
        worker.read_text(encoding="utf-8") + "\n# tampered\n",
        encoding="utf-8",
        newline="\n",
    )
    platform, _store, registry = await _platform(tmp_path, pack_root=pack)

    with pytest.raises(PackManifestError) as raised:
        await platform.initialize()

    assert raised.value.code == "hash_mismatch"
    assert registry.catalog_snapshot().specs == ()
    assert await platform.shutdown() == ()


@pytest.mark.asyncio
async def test_healthcheck_crash_is_isolated_and_never_published(
    tmp_path: Path,
) -> None:
    pack = _copy_pack(GODOT_PACK, tmp_path / "crashing-godot")
    worker = pack / "tools" / "godot" / "main.py"
    worker.write_text(
        "raise SystemExit(7)\n",
        encoding="utf-8",
        newline="\n",
    )
    _replace_declared_hash(pack, "tools/godot/main.py")
    runtime = LocalToolRuntime(monitor_interval_seconds=0.01)
    platform, _store, registry = await _platform(
        tmp_path, pack_root=pack, runtime=runtime
    )

    with pytest.raises(CapabilityManagerError) as raised:
        await platform.initialize()

    assert raised.value.code == "capability_healthcheck_failed"
    assert registry.catalog_snapshot().specs == ()
    # The failed one-shot worker lease is already settled; shutdown has no
    # broad process-name cleanup to perform.
    assert await platform.shutdown() == ()


@pytest.mark.asyncio
async def test_brokered_profile_fails_closed_without_host_authority(
    tmp_path: Path,
) -> None:
    pack = _copy_pack(GODOT_PACK, tmp_path / "brokered-godot")
    manifest_path = pack / "deskpet-pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for tool in manifest["entries"]["tools"]:
        tool["execution_profile"] = "brokered-effect-v1"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    validation = load_and_validate_pack(pack, environment=TEST_ENVIRONMENT)
    runtime = LocalToolRuntime()
    proxy = LocalToolProxy(
        runtime=runtime,
        input_resolver=InputViewResolver(),
        brokered_planner=BrokeredEffectPlanner(),
    )
    factory = LocalCapabilityToolSpecFactory(proxy=proxy)
    specs = factory.build_specs(
        validation,
        install_root=pack,
        operation_id="operation",
    )
    detect = next(item for item in specs if item.name == "godot__detect")

    assert detect.dispatch_kind == "brokered_effect"
    assert detect.resource_scope_resolver is not None
    selectors = detect.resource_scope_resolver({}, _context(tmp_path))
    assert {selector.kind for selector in selectors} == {
        "capability_managed_root",
        "system_change",
    }
    assert detect.context_handler is not None
    outcome = await detect.context_handler({}, _context(tmp_path))
    assert outcome.to_dict()["error"]["code"] == "brokered_authority_unavailable"
    assert await runtime.close() == ()


@pytest.mark.asyncio
async def test_public_mcp_and_legacy_plugin_adapters_are_revisioned() -> None:
    class MCP:
        state = {"filesystem": "running"}

        def server_state(self) -> dict[str, str]:
            return dict(self.state)

    class Plugins:
        rows = [
            {
                "name": "example",
                "version": "1.2.3",
                "description": "Example plugin",
                "enabled": True,
                "dir": "C:/plugins/example",
                "requires": [],
            }
        ]

        def list_plugins(self) -> list[dict[str, object]]:
            return [dict(item) for item in self.rows]

    mcp = MCP()
    revisions = MCPManagerRevisionSource(mcp)
    assert await revisions.revision() == 1
    assert await revisions.revision() == 1
    mcp.state["filesystem"] = "reconnecting"
    assert await revisions.revision() == 2

    plugins = Plugins()
    source = LegacyPluginCatalogSource(plugins)
    first = await source.snapshot()
    second = await source.snapshot()
    assert first.revision == second.revision == 1
    assert first.entries[0].version.source == "plugin:example"
    plugins.rows[0]["enabled"] = False
    disabled = await source.snapshot()
    assert disabled.revision == 2
    assert disabled.entries == ()


@pytest.mark.asyncio
async def test_configured_pack_is_discoverable_before_integrity_checked_install(
    tmp_path: Path,
) -> None:
    database = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(database)
    registry = ToolRegistry()
    platform = CapabilityPlatform(
        registry=registry,
        store=store,
        user_data_root=tmp_path / "userdata",
        environment=TEST_ENVIRONMENT,
        configured_sources={
            "godot-catalog": PackSourceRequest(
                "local", str(GODOT_PACK), "catalog-v1"
            )
        },
        first_party_pack_roots=(),
        register_control_surface=False,
    )

    await platform.initialize()
    snapshot = await platform.hub.snapshot(CapabilityScope())
    descriptor = snapshot.get("godot")

    assert descriptor is not None
    assert descriptor.version.source == "configured:godot-catalog@catalog-v1"
    assert not descriptor.installed
    assert not descriptor.executable
    assert "godot-catalog" in descriptor.version.aliases
    await platform.shutdown()
