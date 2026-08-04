from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.capabilities.contracts import (
    CapabilityBinding,
    CapabilityCatalogSnapshot,
    CapabilityDescriptor,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogStamp,
)
from deskpet.capabilities.tools import (
    CapabilityToolService,
    register_capability_tools,
)
from deskpet.capabilities.source import (
    CapabilitySourceResolver,
    PackSourceRequest,
)
from deskpet.permissions.task_grants import canonical_filesystem_path
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry


def _context(
    tmp_path: Path,
    *,
    workspace: str | None = None,
    call_id: str = "call-1",
) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id="root-1",
        run_id="run-1",
        call_id=call_id,
        effect_id="effect-1",
        turn_id="turn-1",
        capability_hash="a" * 64,
        scope_hash="b" * 64,
        trace_id="trace-1",
        workspace=workspace,
    )


class _FakeHub:
    def __init__(self) -> None:
        self.leases: list[tuple[str, str, CapabilityScope]] = []

    @staticmethod
    def _snapshot(scope: CapabilityScope) -> CapabilityCatalogSnapshot:
        return CapabilityCatalogSnapshot(
            stamp=CatalogStamp(1, 2, 3, 4, 5),
            scope=scope,
            descriptors=(),
            created_at=100.0,
        )

    async def snapshot(self, scope: CapabilityScope) -> CapabilityCatalogSnapshot:
        return self._snapshot(scope)

    async def snapshot_and_acquire_lease(
        self, *, run_id: str, root_run_id: str, scope: CapabilityScope
    ) -> CapabilityCatalogSnapshot:
        self.leases.append((run_id, root_run_id, scope))
        return self._snapshot(scope)


class _FakeManager:
    def __init__(
        self,
        root: Path | None = None,
        *,
        source_resolver=None,
    ) -> None:
        self.calls: list[tuple[str, object, dict[str, object]]] = []
        self.layout = SimpleNamespace(root=(root or Path("C:/capabilities")))
        self.store = _FakeCapabilityStore()
        self.source_resolver = (
            source_resolver or CapabilitySourceResolver()
        )

    async def install(self, source, **kwargs):
        self.calls.append(("install", source, kwargs))
        return SimpleNamespace(
            operation=SimpleNamespace(operation_id="op-1", status="succeeded", phase="published"),
            validation=SimpleNamespace(
                manifest=SimpleNamespace(
                    id="fixture",
                    version="1.0.0",
                    manifest_hash="c" * 64,
                )
            ),
            binding=SimpleNamespace(
                capability_id="fixture",
                version="1.0.0",
                manifest_hash="c" * 64,
                scope=kwargs["scope"],
                scope_key=kwargs["scope_key"],
                generation=1,
            ),
            install_path=Path("C:/capabilities/fixture"),
            registry_revision=9,
            tool_spec_fingerprints=("d" * 64,),
        )


class _UnusedSearch:
    pass


class _SearchOne:
    def __init__(self, descriptor: CapabilityDescriptor) -> None:
        self.descriptor = descriptor

    async def search(self, *, snapshot, root_run_id, query, limit):
        assert root_run_id == "root-1"
        assert query == "shell"
        assert limit == 30
        return SimpleNamespace(
            snapshot_ref=snapshot.snapshot_ref,
            stamp=snapshot.stamp,
            semantic_used=False,
            receipt=SimpleNamespace(receipt_id="search-receipt"),
            hits=(
                SimpleNamespace(
                    capability_id=self.descriptor.version.capability_id,
                    version=self.descriptor.version.version,
                    score=99.0,
                    match_kind="token",
                    executable=self.descriptor.executable,
                ),
            ),
        )


class _FakeCapabilityStore:
    async def get_version(self, *_args):
        return None

    async def put_operation_receipt(self, receipt):
        return receipt


@pytest.mark.asyncio
async def test_install_derives_project_scope_and_host_idempotency(
    tmp_path: Path,
) -> None:
    manager = _FakeManager()
    service = CapabilityToolService(
        hub=_FakeHub(),  # type: ignore[arg-type]
        search=_UnusedSearch(),  # type: ignore[arg-type]
        manager=manager,  # type: ignore[arg-type]
    )
    result = await service.install(
        {
            "source_type": "local",
            "uri": str(tmp_path / "source"),
            "revision": "r1",
            "scope": "project",
            "expected_pack_id": "fixture",
        },
        _context(tmp_path, workspace=str(tmp_path / "project with spaces")),
        kind="install",
    )
    assert result["ok"] is True
    kind, source, kwargs = manager.calls[0]
    assert kind == "install"
    assert source.uri == str(tmp_path / "source")
    assert kwargs["root_run_id"] == "root-1"
    assert str(kwargs["scope_key"]).startswith("project:")
    assert str(kwargs["idempotency_key"]).startswith(
        "capability:install:root-1:effect-1:"
    )


@pytest.mark.asyncio
async def test_refresh_acquires_snapshot_lease_for_same_run(tmp_path: Path) -> None:
    hub = _FakeHub()
    service = CapabilityToolService(
        hub=hub,  # type: ignore[arg-type]
        search=_UnusedSearch(),  # type: ignore[arg-type]
        manager=_FakeManager(),  # type: ignore[arg-type]
    )
    result = await service.refresh(
        _context(tmp_path, workspace=str(tmp_path / "project"))
    )
    assert result["status"] == "catalog_refreshed"
    assert hub.leases[0][0:2] == ("run-1", "root-1")
    assert hub.leases[0][2].run_key == "root-1"


@pytest.mark.asyncio
async def test_search_visible_returns_compact_activation_guidance(
    tmp_path: Path,
) -> None:
    stamp = CatalogStamp(1, 2, 3, 4, 5)
    version = CapabilityVersionDescriptor(
        capability_id="run_shell",
        display_name="Run shell",
        version="v1",
        kind="function_tool",
        source="builtin",
        description="Execute a shell command.",
        aliases=("shell",),
        logical_tool_ids=("run_shell",),
        provider_tool_names=("run_shell",),
        permission_categories=("shell",),
        effect_kinds=("opaque_manual",),
        schema_hash="a" * 64,
        manifest_hash="b" * 64,
        health="degraded",
    )
    descriptor = CapabilityDescriptor(
        version=version,
        visible_bindings=(
            CapabilityBinding(
                binding_id="binding-shell",
                capability_id="run_shell",
                version="v1",
                manifest_hash="b" * 64,
                scope="builtin",
                scope_key="builtin",
                active=True,
                generation=1,
            ),
        ),
        executable=False,
        installed=True,
        tool_spec_fingerprints=("c" * 64,),
        stamp=stamp,
    )

    class _Hub(_FakeHub):
        async def snapshot(self, scope):
            return CapabilityCatalogSnapshot(
                stamp=stamp,
                scope=scope,
                descriptors=(descriptor,),
                created_at=100.0,
            )

    service = CapabilityToolService(
        hub=_Hub(),  # type: ignore[arg-type]
        search=_SearchOne(descriptor),  # type: ignore[arg-type]
        manager=_FakeManager(),  # type: ignore[arg-type]
    )
    result = await service.search_visible(
        {"query": "shell", "limit": 30},
        _context(tmp_path, workspace=str(tmp_path)),
    )

    match = result["matches"][0]
    compact = match["descriptor"]
    assert compact["provider_tool_names"] == ["run_shell"]
    assert compact["next_action"]["tool"] == "tool_search"
    assert "visible_bindings" not in compact
    assert "tool_spec_fingerprints" not in compact
    assert "catalog_stamp" not in compact


@pytest.mark.asyncio
async def test_registered_tools_fail_closed_without_trusted_context() -> None:
    registry = ToolRegistry()
    service = CapabilityToolService(
        hub=_FakeHub(),  # type: ignore[arg-type]
        search=_UnusedSearch(),  # type: ignore[arg-type]
        manager=_FakeManager(),  # type: ignore[arg-type]
    )
    register_capability_tools(registry, service)
    expected = {
        "capability_list",
        "capability_search",
        "capability_catalog_refresh",
        "capability_install",
        "capability_update",
        "capability_uninstall",
        "capability_rollback",
    }
    assert expected == set(registry.list_tools())
    for name in expected:
        spec = registry.get(name)
        assert spec is not None
        assert spec.stable_handler_id == f"core.{name}.v1"
        assert spec.execution_build_identity is not None
        assert (
            spec.execution_build_identity.handler_id
            == spec.stable_handler_id
        )
    install = next(
        spec for spec in registry.all_specs() if spec.name == "capability_install"
    )
    assert install.permission_category == "skill_install"
    assert install.dangerous is True
    assert install.concurrency_safe is False
    assert "anyOf" in install.schema["parameters"]["properties"]["subdirectory"]

    assert install.context_handler is not None
    payload = json.loads(await install.context_handler({"uri": "x"}, None))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "ValueError"


def test_lifecycle_tools_prepare_exact_managed_and_source_scopes(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    managed_root = tmp_path / "managed capabilities"
    service = CapabilityToolService(
        hub=_FakeHub(),  # type: ignore[arg-type]
        search=_UnusedSearch(),  # type: ignore[arg-type]
        manager=_FakeManager(managed_root),  # type: ignore[arg-type]
    )
    register_capability_tools(registry, service)
    source = tmp_path / "generated pack"
    prepared = registry.prepare_call(
        "capability_install",
        {
            "source_type": "local",
            "uri": str(source),
            "revision": "draft-1",
            "scope": "run",
            "expected_pack_id": "photo-renamer",
            "generated": True,
        },
        "session",
        "call-install",
        execution_context=_context(
            tmp_path,
            workspace=str(tmp_path),
            call_id="call-install",
        ),
    )

    by_kind = {item.kind: item for item in prepared.resource_selectors}
    assert by_kind["capability_managed_root"].canonical_value == (
        canonical_filesystem_path(managed_root)
    )
    assert by_kind["capability_managed_root"].access == ("read", "write")
    assert by_kind["filesystem"].canonical_value == canonical_filesystem_path(
        source
    )
    assert by_kind["filesystem"].access == ("read",)
    assert by_kind["system_change"].canonical_value == (
        "capability:photo-renamer"
    )
    assert by_kind["system_change"].access == ("install",)


def test_configured_install_scope_resolves_alias_to_real_source(
    tmp_path: Path,
) -> None:
    actual_source = tmp_path / "configured godot pack"
    resolver = CapabilitySourceResolver(
        configured_sources={
            "godot": PackSourceRequest(
                source_type="local",
                uri=str(actual_source),
                revision="builtin",
            )
        }
    )
    registry = ToolRegistry()
    service = CapabilityToolService(
        hub=_FakeHub(),  # type: ignore[arg-type]
        search=_UnusedSearch(),  # type: ignore[arg-type]
        manager=_FakeManager(
            tmp_path / "managed",
            source_resolver=resolver,
        ),  # type: ignore[arg-type]
    )
    register_capability_tools(registry, service)

    prepared = registry.prepare_call(
        "capability_install",
        {
            "source_type": "configured",
            "uri": "godot",
            "revision": "configured",
            "scope": "run",
            "expected_pack_id": "deskpet.godot",
        },
        "session",
        "call-configured",
        execution_context=_context(
            tmp_path,
            workspace=str(tmp_path),
            call_id="call-configured",
        ),
    )

    filesystem = next(
        item
        for item in prepared.resource_selectors
        if item.kind == "filesystem"
    )
    assert filesystem.canonical_value == canonical_filesystem_path(
        actual_source
    )
