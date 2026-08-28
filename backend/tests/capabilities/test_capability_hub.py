from __future__ import annotations

import hashlib

import pytest

from deskpet.capabilities.contracts import (
    CapabilityBinding,
    CapabilityCatalogEntry,
    CapabilityCollisionError,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogUnstableError,
    PendingPublishError,
    RegistryCatalogSnapshot,
    RegistryToolDescriptor,
    canonical_project_identity_scope_key,
    canonical_project_scope_key,
)
from deskpet.capabilities.hub import (
    CapabilityHub,
    StaticCapabilityEntrySource,
    ToolRegistryCatalogSource,
)
from deskpet.capabilities.store import (
    CapabilityStore,
    CapabilityVersionRecord,
    initialize_capability_database,
)
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.build_identity import ExecutionBuildIdentity


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _version(
    capability_id: str,
    version: str,
    *,
    kind: str = "instruction",
    provider_names: tuple[str, ...] = (),
) -> CapabilityVersionDescriptor:
    return CapabilityVersionDescriptor(
        capability_id=capability_id,
        display_name=f"{capability_id} {version}",
        version=version,
        kind=kind,  # type: ignore[arg-type]
        source=f"fixture:{capability_id}@{version}",
        description=f"{capability_id} capability",
        aliases=(capability_id,),
        logical_tool_ids=tuple(
            name.removeprefix(f"{capability_id}__") for name in provider_names
        ),
        provider_tool_names=provider_names,
        permission_categories=(
            ("filesystem_read",) if provider_names else ()
        ),
        effect_kinds=(("read_only",) if provider_names else ()),
        schema_hash=_hash(f"schema:{capability_id}:{version}"),
        manifest_hash=_hash(f"manifest:{capability_id}:{version}"),
        health="healthy",
    )


def _binding(
    descriptor: CapabilityVersionDescriptor,
    *,
    scope: str,
    scope_key: str,
    suffix: str = "",
) -> CapabilityBinding:
    return CapabilityBinding(
        binding_id=_hash(
            f"{descriptor.capability_id}:{descriptor.version}:{scope}:{scope_key}:{suffix}"
        ),
        capability_id=descriptor.capability_id,
        version=descriptor.version,
        manifest_hash=descriptor.manifest_hash,
        scope=scope,  # type: ignore[arg-type]
        scope_key=scope_key,
        active=True,
        generation=1,
    )


def _tool(
    name: str = "godot__check",
    *,
    source: str = "capability:godot",
) -> RegistryToolDescriptor:
    return RegistryToolDescriptor(
        provider_name=name,
        source=source,
        description=f"{name} fixture",
        schema_hash=_hash(f"schema:{name}"),
        permission_category="filesystem_read",
        effect_kind="read_only",
        spec_version="v1",
    )


class _RegistrySource:
    def __init__(self, *snapshots: RegistryCatalogSnapshot) -> None:
        self._snapshots = list(snapshots)
        self.calls = 0

    async def snapshot(self) -> RegistryCatalogSnapshot:
        self.calls += 1
        if len(self._snapshots) > 1:
            return self._snapshots.pop(0)
        return self._snapshots[0]


class _AlwaysDriftingRegistry:
    def __init__(self) -> None:
        self.revision = 0

    async def snapshot(self) -> RegistryCatalogSnapshot:
        self.revision += 1
        return RegistryCatalogSnapshot(self.revision, ())


@pytest.mark.asyncio
async def test_registry_source_excludes_environment_gated_tools(
    monkeypatch,
) -> None:
    registry = ToolRegistry()
    registry.register(
        "disabled_fixture",
        "fixture",
        {
            "name": "disabled_fixture",
            "description": "must not be advertised",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda _args, _task_id: "{}",
        requires_env=["DESKPET_TEST_DISABLED_FIXTURE"],
    )
    monkeypatch.delenv("DESKPET_TEST_DISABLED_FIXTURE", raising=False)

    snapshot = await ToolRegistryCatalogSource(registry).snapshot()

    assert snapshot.tools == ()


@pytest.mark.asyncio
async def test_store_pack_is_executable_only_when_registry_fingerprints_match(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    tool = _tool()
    descriptor = _version(
        "godot",
        "1.0.0",
        kind="function_tool",
        provider_names=(tool.provider_name,),
    )
    await store.record_version(
        CapabilityVersionRecord(
            descriptor=descriptor,
            install_path=tmp_path / "packs" / "godot" / "1.0.0",
            validation_status="healthy",
            expected_tool_fingerprints=(tool.fingerprint,),
            parent_version=None,
            parent_manifest_hash=None,
            derived_from_receipt_ref=None,
            created_at=100.0,
        )
    )
    await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id="godot",
        version="1.0.0",
        manifest_hash=descriptor.manifest_hash,
        expected_generation=0,
    )

    hub = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(
            RegistryCatalogSnapshot(3, (tool,))
        ),
    )
    snapshot = await hub.snapshot_and_acquire_lease(
        run_id="run-1",
        root_run_id="root-1",
        scope=CapabilityScope(),
    )
    projected = snapshot.get("godot")
    assert projected is not None
    assert projected.executable
    assert projected.version.health == "healthy"
    assert not await hub.version_can_be_collected(
        pack_id="godot",
        version="1.0.0",
        manifest_hash=descriptor.manifest_hash,
    )
    assert await hub.release_lease(snapshot.snapshot_ref, "run-1") == 1
    assert await hub.version_can_be_collected(
        pack_id="godot",
        version="1.0.0",
        manifest_hash=descriptor.manifest_hash,
    )

    missing_registry = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(RegistryCatalogSnapshot(4, ())),
    )
    degraded = (await missing_registry.snapshot(CapabilityScope())).get("godot")
    assert degraded is not None
    assert not degraded.executable
    assert degraded.version.health == "degraded"


@pytest.mark.asyncio
async def test_builtin_projection_does_not_create_store_version_lease(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    tool = _tool("read_file", source="builtin")
    hub = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(
            RegistryCatalogSnapshot(1, (tool,))
        ),
    )

    snapshot = await hub.snapshot_and_acquire_lease(
        run_id="run-1",
        root_run_id="root-1",
        scope=CapabilityScope(),
    )
    projected = snapshot.get("read_file")
    assert projected is not None and projected.executable
    assert await hub.release_lease(snapshot.snapshot_ref, "run-1") == 0


@pytest.mark.asyncio
async def test_scope_precedence_is_run_project_user_builtin(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    scoped = (
        ("builtin", "builtin", "1.0.0"),
        ("user", "default", "2.0.0"),
        ("project", "project-1", "3.0.0"),
        ("run", "root-1", "4.0.0"),
    )
    entries = []
    for scope, scope_key, version in scoped:
        descriptor = _version("renderer", version)
        entries.append(
            CapabilityCatalogEntry(
                descriptor,
                (_binding(descriptor, scope=scope, scope_key=scope_key),),
            )
        )
    hub = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(RegistryCatalogSnapshot(0, ())),
        legacy_source=StaticCapabilityEntrySource(tuple(entries), revision=1),
    )

    cases = (
        (
            CapabilityScope(
                run_key="root-1",
                project_key="project-1",
                user_key="default",
            ),
            "4.0.0",
        ),
        (
            CapabilityScope(project_key="project-1", user_key="default"),
            "3.0.0",
        ),
        (CapabilityScope(user_key="default"), "2.0.0"),
        (CapabilityScope(user_key="another-user"), "1.0.0"),
    )
    for scope, expected_version in cases:
        descriptor = (await hub.snapshot(scope)).get("renderer")
        assert descriptor is not None
        assert descriptor.version.version == expected_version


@pytest.mark.asyncio
async def test_owner_key_reaches_store_snapshot_and_partitions_cache(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    scope = CapabilityScope(project_key="project-1", user_key="profile-1")
    for owner_key, capability_id in (
        ("owner-alpha", "alpha-skill"),
        ("owner-beta", "beta-skill"),
    ):
        descriptor = _version(capability_id, "1.0.0")
        await store.record_version(
            CapabilityVersionRecord(
                descriptor=descriptor,
                install_path=tmp_path / "packs" / capability_id,
                validation_status="healthy",
                expected_tool_fingerprints=(),
                parent_version=None,
                parent_manifest_hash=None,
                derived_from_receipt_ref=None,
                created_at=100.0,
            )
        )
        await store.set_binding(
            owner_key=owner_key,
            management_policy="user_managed",
            scope="project",
            scope_key="project-1",
            pack_id=capability_id,
            version="1.0.0",
            manifest_hash=descriptor.manifest_hash,
            expected_generation=0,
        )
    hub = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(RegistryCatalogSnapshot(0, ())),
    )

    alpha = await hub.snapshot(scope, owner_key="owner-alpha")
    beta = await hub.snapshot(scope, owner_key="owner-beta")

    assert alpha.get("alpha-skill") is not None
    assert alpha.get("beta-skill") is None
    assert beta.get("beta-skill") is not None
    assert beta.get("alpha-skill") is None


@pytest.mark.asyncio
async def test_new_run_fails_closed_for_legacy_and_prior_revision_bindings(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    descriptor = _version("project_skill", "1.0.0")
    legacy_key = canonical_project_scope_key(tmp_path / "same-path")
    revision_one_key = canonical_project_identity_scope_key(
        "project-1", 1, "filesystem-1"
    )
    entries = tuple(
        CapabilityCatalogEntry(
            descriptor,
            (_binding(descriptor, scope="project", scope_key=key),),
        )
        for key in (legacy_key, revision_one_key)
    )
    hub = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(RegistryCatalogSnapshot(0, ())),
        legacy_source=StaticCapabilityEntrySource(entries, revision=1),
    )

    relocated_scope = CapabilityScope.for_run(
        "run-2",
        project_id="project-1",
        project_revision=2,
        project_identity="filesystem-1",
    )
    assert (await hub.snapshot(relocated_scope)).get("project_skill") is None

    restarted_revision_one = CapabilityScope.for_run(
        "run-restarted",
        project_id="project-1",
        project_revision=1,
        project_identity="filesystem-1",
    )
    assert (
        await hub.snapshot(restarted_revision_one)
    ).get("project_skill") is not None


@pytest.mark.asyncio
async def test_same_scope_collision_fails_closed(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    first = _version("renderer", "1.0.0")
    second = _version("renderer", "2.0.0")
    entries = (
        CapabilityCatalogEntry(
            first,
            (_binding(first, scope="user", scope_key="default"),),
        ),
        CapabilityCatalogEntry(
            second,
            (_binding(second, scope="user", scope_key="default"),),
        ),
    )
    hub = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(RegistryCatalogSnapshot(0, ())),
        legacy_source=StaticCapabilityEntrySource(entries, revision=1),
    )

    with pytest.raises(CapabilityCollisionError) as caught:
        await hub.snapshot(CapabilityScope())
    assert caught.value.code == "active_binding_collision"


@pytest.mark.asyncio
async def test_snapshot_retries_revision_drift_and_bounds_instability(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    source = _RegistrySource(
        RegistryCatalogSnapshot(1, ()),
        RegistryCatalogSnapshot(2, ()),
        RegistryCatalogSnapshot(2, ()),
        RegistryCatalogSnapshot(2, ()),
    )
    hub = CapabilityHub(store=store, registry_source=source)
    snapshot = await hub.snapshot(CapabilityScope())
    assert snapshot.stamp.registry_revision == 2
    assert source.calls == 4

    unstable = CapabilityHub(
        store=store,
        registry_source=_AlwaysDriftingRegistry(),
        max_snapshot_retries=2,
    )
    with pytest.raises(CatalogUnstableError):
        await unstable.snapshot(CapabilityScope())


@pytest.mark.asyncio
async def test_pending_publish_blocks_snapshot_until_reconciled(tmp_path) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path)
    await store.create_operation(
        operation_id="op-1",
        idempotency_key="request-1",
        kind="install",
        request={"source": "fixture"},
    )
    await store.create_publish_intent(
        intent_id="intent-1",
        operation_id="op-1",
        expected_registry_revision=0,
        old_specs=(),
        new_specs=(),
        old_binding=None,
        new_binding={"pack_id": "fixture"},
    )
    hub = CapabilityHub(
        store=store,
        registry_source=_RegistrySource(RegistryCatalogSnapshot(0, ())),
    )

    with pytest.raises(PendingPublishError):
        await hub.snapshot(CapabilityScope())
    await store.advance_publish_intent(
        "intent-1",
        phase="publish_intent",
        status="rolled_back",
    )
    assert (await hub.snapshot(CapabilityScope())).descriptors == ()


@pytest.mark.asyncio
async def test_tool_registry_adapter_reads_one_immutable_catalog_snapshot() -> None:
    registry = ToolRegistry()
    build = ExecutionBuildIdentity(
        provider="fixture",
        handler_id="fixture.echo.v1",
        build_digest=_hash("build"),
        sources_manifest_hash=_hash("sources"),
        artifacts=(("fixture.py", _hash("fixture.py")),),
    )
    registry.register(
        "fixture_echo",
        "fixture",
        {
            "name": "fixture_echo",
            "description": "Echo a fixture",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda _args: {"ok": True},
        permission_category="read_file",
        stable_handler_id=build.handler_id,
        execution_build_identity=build,
    )

    snapshot = await ToolRegistryCatalogSource(registry).snapshot()
    assert snapshot.revision == registry.catalog_snapshot().revision
    assert len(snapshot.tools) == 1
    assert snapshot.tools[0].provider_name == "fixture_echo"
    assert snapshot.tools[0].description == "Echo a fixture"


@pytest.mark.asyncio
async def test_registry_source_excludes_tools_without_durable_build_identity() -> None:
    registry = ToolRegistry()
    registry.register(
        "product_only_bridge",
        "fixture",
        {
            "name": "product_only_bridge",
            "description": "not a durable Run tool",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda _args: {"ok": True},
        permission_category="read_file",
    )

    snapshot = await ToolRegistryCatalogSource(registry).snapshot()

    assert snapshot.tools == ()
