# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness import CallId, EffectId, RequestId, RunId, thaw_json
from simple_harness.execution.context_authority import DurableToolCatalogResolver
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.providers import ProviderToolSpec
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationReceipt,
    CancellationToken,
    FunctionTool,
    PreparedToolEffect,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)

from deskpet.permissions.policy import AuthorizationPolicyState
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
from deskpet.sdk_adapters.tool_authority import (
    SDK_DIRECT_TOOL_KERNEL,
    SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
    SDK_FULL_CATALOG_DISCLOSURE_POLICY,
    SdkCapabilityBridgeAdapter,
    SdkPreparedAuthorizationPolicy,
    SdkRuntimeCapabilityBridgeAdapter,
    SdkRunToolAuthorityRegistry,
    SdkToolAuthorityMigrationUnavailable,
    _v2_record_hashes,
    confirm_only_tool_names,
)
from deskpet.sdk_adapters.tools import (
    ProductEffectExecutor,
    ProductToolsAdapter,
    SdkToolExecutorCatalogUnavailable,
)
from deskpet.tools.capabilities import ToolCapabilityScopeStore


def test_directory_picker_is_discoverable_not_in_the_direct_kernel() -> None:
    assert "tool_search" in SDK_DIRECT_TOOL_KERNEL
    assert {
        "project_directory_select",
        "workspace_prepare",
        "workspace_recall",
    }.isdisjoint(SDK_DIRECT_TOOL_KERNEL)


def test_skill_install_is_a_direct_confirm_only_product_control() -> None:
    assert "skill_install" in SDK_DIRECT_TOOL_KERNEL


@dataclass(frozen=True)
class _Inventory:
    name: str
    dispatch_kind: str
    permission_category: str
    source: str = "real-tool-manifest"
    version: str = "v1"
    execution_identity: str = "execution-identity-v1"
    projectless_admission: str = "requires_project"
    availability_reason: str | None = None
    effect_class: str | None = None  # S5b Task 6：inventory 冻结 EffectClass（None → manifest / unknown）


class _AuthorizationStore:
    def __init__(self, mode: str = "auto", generation: int = 0) -> None:
        self.state = AuthorizationPolicyState(mode, generation, 10.0)

    async def get_policy_state(self):
        return self.state

    async def get_task_grant(self, _task_grant_id: str):
        return None


def _catalog() -> tuple[dict, tuple[_Inventory, ...]]:
    specs = [
        {
            "name": "tool_search",
            "description": "Search capabilities",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "read_file",
            "description": "Read one file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    ]
    from deskpet.sdk_adapters.context_authority import canonical_sha256

    schema_fingerprints = {
        item["name"]: canonical_sha256(item["input_schema"])
        for item in specs
    }
    return (
        {
            "generation": 7,
            "content_fingerprint": canonical_sha256(specs),
            "specs": specs,
            "schema_fingerprints": schema_fingerprints,
        },
        (
            _Inventory("tool_search", "control", "read_file"),
            _Inventory("read_file", "async", "read_file"),
        ),
    )


def _prepare(
    registry: SdkRunToolAuthorityRegistry,
    run_id: str,
    *,
    session_id: str = "session-a",
    deferred_names=(),
):
    catalog, inventory = _catalog()
    return registry.prepare_run(
        run_id=run_id,
        session_id=session_id,
        request_id=f"request-{run_id}",
        root_run_id=f"root-{run_id}",
        task_scope_id=f"task-{run_id}",
        workspace_root=None,
        catalog=catalog,
        inventory=inventory,
        deferred_names=deferred_names,
    )


def test_unavailable_descriptor_is_searchable_but_cannot_activate() -> None:
    registry = SdkRunToolAuthorityRegistry()
    catalog, inventory = _catalog()
    catalog = {
        **catalog,
        "descriptor_specs": list(catalog["specs"]),
        "specs": [catalog["specs"][0]],
        "schema_fingerprints": {
            "tool_search": catalog["schema_fingerprints"]["tool_search"]
        },
    }
    inventory = (
        inventory[0],
        replace(inventory[1], availability_reason="workspace_unscoped"),
    )
    registry.prepare_run(
        run_id="run-unavailable", session_id="session-a", request_id="request-a",
        root_run_id="root-a", task_scope_id="task-a", workspace_root=None,
        catalog=catalog, inventory=inventory,
    )
    run_id = RunId("run-unavailable")
    exposure = registry.resolve_exposure(run_id)
    exposure.restore(run_id, None)
    bridge = SdkRuntimeCapabilityBridgeAdapter(
        registry, lambda: registry.resolve(run_id).execution_context()
    )
    assert bridge.search("workspace_unscoped")["matches"][0]["capability_id"] == (
        "builtin:read_file"
    )
    described = bridge.describe("builtin:read_file")
    with pytest.raises(RuntimeError, match="tool_unavailable:workspace_unscoped"):
        bridge.activate(
            "builtin:read_file", described["schema_hash"],
            described["describe_nonce"],
        )
    assert [item.name for item in exposure.provider_specs(run_id)] == ["tool_search"]


def _effect(
    run_id: str,
    *,
    effect_id: str = "effect-a",
    call_id: str = "call-a",
) -> PreparedToolEffect:
    return PreparedToolEffect(
        EffectId(effect_id),
        RunId(run_id),
        ToolCall(CallId(call_id), "read_file", {"path": "/tmp/input.txt"}),
        ToolSpec(
            "read_file",
            "Read one file",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        {
            "session_id": "session-a",
            "root_run_id": f"root-{run_id}",
        },
    )


def test_run_authority_writes_the_supplied_physical_scope_store() -> None:
    physical_store = ToolCapabilityScopeStore()
    registry = SdkRunToolAuthorityRegistry(scope_store=physical_store)

    authority = _prepare(registry, "run-shared-store")

    assert registry.scope_store is physical_store
    assert physical_store.get(
        authority.prepared_tool_set.scope_id,
        session_id=authority.session_id,
        request_id=authority.request_id,
    ) is not None


def test_run_authority_freezes_and_persists_physical_policy_fingerprint() -> None:
    catalog, inventory = _catalog()
    catalog["policy_fingerprint"] = "physical-policy-v7"
    registry = SdkRunToolAuthorityRegistry()

    authority = registry.prepare_run(
        run_id="run-physical-policy",
        session_id="session-a",
        request_id="request-physical-policy",
        root_run_id="root-physical-policy",
        task_scope_id="task-physical-policy",
        workspace_root=None,
        catalog=catalog,
        inventory=inventory,
    )

    assert authority.prepared_tool_set.policy_fingerprint == "physical-policy-v7"
    assert authority.run_start_record()["policy_fingerprint"] == "physical-policy-v7"


def test_projectless_authority_accepts_only_explicit_safe_projection() -> None:
    catalog, _ = _catalog()
    safe_catalog = {
        **catalog,
        "specs": [catalog["specs"][0]],
        "schema_fingerprints": {
            "tool_search": catalog["schema_fingerprints"]["tool_search"]
        },
    }
    safe_inventory = (
        _Inventory(
            "tool_search",
            "control",
            "read_file",
            projectless_admission="safe",
        ),
    )
    registry = SdkRunToolAuthorityRegistry()

    authority = registry.prepare_run(
        run_id="run-projectless",
        session_id="session-projectless",
        request_id="request-projectless",
        root_run_id="root-projectless",
        task_scope_id="task-projectless",
        workspace_root=None,
        workspace_resolution={"kind": "projectless", "effective_root": None},
        catalog=safe_catalog,
        inventory=safe_inventory,
    )

    assert set(authority.specs) == {"tool_search"}
    assert authority.run_start_record()["workspace_resolution"]["kind"] == "projectless"
    with pytest.raises(RuntimeError, match="projectless_catalog_contains_project_tool"):
        registry.prepare_run(
            run_id="run-projectless-unsafe",
            session_id="session-projectless",
            request_id="request-projectless-unsafe",
            root_run_id="root-projectless-unsafe",
            task_scope_id="task-projectless-unsafe",
            workspace_root=None,
            workspace_resolution={"kind": "projectless", "effective_root": None},
            catalog=catalog,
            inventory=_catalog()[1],
        )


def test_project_bound_authority_revalidates_identity() -> None:
    observed: list[dict] = []

    def validate(resolution):
        observed.append(dict(resolution))

    registry = SdkRunToolAuthorityRegistry(workspace_identity_validator=validate)
    catalog, inventory = _catalog()
    authority = registry.prepare_run(
        run_id="run-project",
        session_id="session-project",
        request_id="request-project",
        root_run_id="root-project",
        task_scope_id="task-project",
        workspace_root="/workspace",
        workspace_resolution={
            "kind": "project_bound",
            "project_id": "project-1",
            "execution_kind": "project_root",
            "effective_root": "/workspace",
            "project_identity": "identity-project",
            "execution_identity": "identity-execution",
            "project_revision": 1,
        },
        catalog=catalog,
        inventory=inventory,
    )

    authority.assert_workspace_current()
    assert observed == [authority.workspace_resolution]
    context = authority.execution_context()
    assert context.project_id == "project-1"
    assert context.project_revision == 1
    assert context.project_identity == "identity-project"


def _run_aware_executor_registry(
    authorities: SdkRunToolAuthorityRegistry,
    *,
    read_schema: dict | None = None,
    execution_identity: str = "execution-identity-v1",
):
    catalog, _inventory = _catalog()

    def tool(raw):
        schema = (
            read_schema
            if raw["name"] == "read_file" and read_schema
            else raw["input_schema"]
        )

        async def handler(arguments, context):
            return ToolResult.succeeded(
                context.call_id,
                {"tool": raw["name"], "arguments": dict(arguments)},
            )

        return FunctionTool(
            ToolSpec(raw["name"], raw["description"], schema),
            handler,
        )

    registry = ProductToolsAdapter(
        tuple(tool(raw) for raw in catalog["specs"]),
        execution_identities={
            name: execution_identity for name in ("tool_search", "read_file")
        },
    )
    registry.bind_run_authorities(authorities)
    executor = ProductEffectExecutor(
        uow=object(),
        registry=registry,
        authorization=object(),
        reconciliation=object(),
    )
    return executor, registry


def test_run_authority_isolated_and_waiting_scope_survives_until_terminal():
    registry = SdkRunToolAuthorityRegistry()
    first = _prepare(registry, "run-a")
    second = _prepare(registry, "run-b", session_id="session-b")

    assert first.capability_hash == second.capability_hash
    assert first.scope_hash != second.scope_hash
    assert len(first.prepared_tool_set.logical_schemas()) == 2
    assert first.task_work_context.root_run_id == "root-run-a"
    assert first.capability_hash != "0" * 64
    assert first.scope_hash != "0" * 64

    waiting = registry.mark_waiting("run-a")
    assert waiting.lease_state == "waiting"
    assert registry.scope_store.get(
        waiting.prepared_tool_set.scope_id,
        session_id=waiting.session_id,
        request_id=waiting.request_id,
    ) is not None
    registry.mark_terminal("run-b", "completed")
    with pytest.raises(KeyError):
        registry.resolve("run-b")
    assert registry.resolve("run-a").lease_state == "waiting"

    # Restart reconstruction is deterministic and does not borrow another
    # Session's scope or catalog authority.
    reconstructed = SdkRunToolAuthorityRegistry()
    restored = _prepare(reconstructed, "run-a")
    assert restored.capability_hash == first.capability_hash
    assert restored.scope_hash == first.scope_hash


def test_waiting_restart_uses_exact_durable_catalog_and_run_start_inventory(
    tmp_path: Path,
):
    old_specs = tuple(
        ProviderToolSpec(
            item["name"], item["description"], item["input_schema"]
        )
        for item in _catalog()[0]["specs"]
    )
    new_specs = (
        ProviderToolSpec(
            "new_tool",
            "A tool introduced after the Run started",
            {"type": "object", "properties": {}},
        ),
    )
    with Database.open(tmp_path / "waiting-restart.db") as database:
        uow = SqliteExecutionUnitOfWork(database)
        old_snapshot = uow.put_tool_catalog_snapshot(old_specs, created_at=10.0)
        uow.put_tool_catalog_snapshot(new_specs, created_at=20.0)
        old_catalog = {
            "generation": old_snapshot.generation,
            "content_fingerprint": old_snapshot.content_fingerprint,
            "specs": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": thaw_json(spec.parameters),
                }
                for spec in old_specs
            ],
            "schema_fingerprints": _catalog()[0]["schema_fingerprints"],
        }
        original_registry = SdkRunToolAuthorityRegistry()
        original = original_registry.prepare_run(
            run_id="run-old",
            session_id="session-old",
            request_id="request-old",
            root_run_id="root-old",
            task_scope_id="task-old",
            workspace_root="/trusted/old-workspace",
            catalog=old_catalog,
            inventory=_catalog()[1],
            disclosure_policy=SDK_FULL_CATALOG_DISCLOSURE_POLICY,
        )
        durable_record = original.run_start_record()

        restarted_registry = SdkRunToolAuthorityRegistry()
        restored = restarted_registry.restore_waiting_run(
            run_start_record=durable_record,
            run_binding=durable_record,
            catalog_resolver=DurableToolCatalogResolver(uow),
        )

    assert restored.lease_state == "waiting"
    assert restored.catalog_generation == old_snapshot.generation
    assert restored.catalog_fingerprint == old_snapshot.content_fingerprint
    assert set(restored.specs) == {"tool_search", "read_file"}
    assert "new_tool" not in restored.specs
    assert restored.permission_categories == original.permission_categories
    assert restored.dispatch_kinds == original.dispatch_kinds
    assert restored.specs["read_file"].source == "real-tool-manifest"
    assert restored.specs["read_file"].spec_version == "v1"
    assert restored.capability_hash == original.capability_hash
    assert restored.scope_hash == original.scope_hash


def test_waiting_restart_fails_closed_when_exact_catalog_or_authority_differs(
    tmp_path: Path,
):
    original = _prepare(SdkRunToolAuthorityRegistry(), "run-a")
    record = original.run_start_record()

    class _MissingCatalog:
        def resolve(self, _generation, _fingerprint):
            return None

    with pytest.raises(RuntimeError, match="snapshot_unavailable"):
        SdkRunToolAuthorityRegistry().restore_waiting_run(
            run_start_record=record,
            run_binding=record,
            catalog_resolver=_MissingCatalog(),
        )
    mismatched_binding = {**record, "session_id": "another-session"}
    with pytest.raises(RuntimeError, match="binding_identity_mismatch"):
        SdkRunToolAuthorityRegistry().restore_waiting_run(
            run_start_record=record,
            run_binding=mismatched_binding,
            catalog_resolver=_MissingCatalog(),
        )

    specs = tuple(
        ProviderToolSpec(
            item["name"], item["description"], item["input_schema"]
        )
        for item in _catalog()[0]["specs"]
    )
    with Database.open(tmp_path / "authority-mismatch.db") as database:
        uow = SqliteExecutionUnitOfWork(database)
        snapshot = uow.put_tool_catalog_snapshot(specs)
        uow.put_tool_catalog_snapshot(
            (
                ProviderToolSpec(
                    "new_tool",
                    "A later generation",
                    {"type": "object", "properties": {}},
                ),
            )
        )
        exact_catalog = {
            "generation": snapshot.generation,
            "content_fingerprint": snapshot.content_fingerprint,
            "specs": _catalog()[0]["specs"],
            "schema_fingerprints": _catalog()[0]["schema_fingerprints"],
        }
        authority = SdkRunToolAuthorityRegistry().prepare_run(
            run_id="run-exact",
            session_id="session-a",
            request_id="request-run-exact",
            root_run_id="root-run-exact",
            task_scope_id="task-run-exact",
            workspace_root=None,
            catalog=exact_catalog,
            inventory=_catalog()[1],
            disclosure_policy=SDK_FULL_CATALOG_DISCLOSURE_POLICY,
        )
        tampered = authority.run_start_record()
        tampered["scope_hash"] = "f" * 64
        restarted = SdkRunToolAuthorityRegistry()
        with pytest.raises(RuntimeError, match="authority_hash_mismatch"):
            restarted.restore_waiting_run(
                run_start_record=tampered,
                run_binding=tampered,
                catalog_resolver=DurableToolCatalogResolver(uow),
            )
        with pytest.raises(KeyError):
            restarted.resolve("run-exact")

        inventory_tampered = authority.run_start_record()
        inventory_tampered["inventory"] = [
            {
                **item,
                "permission_category": (
                    "network" if item["name"] == "read_file" else item["permission_category"]
                ),
            }
            for item in inventory_tampered["inventory"]
        ]
        with pytest.raises(RuntimeError, match="authority_hash_mismatch"):
            SdkRunToolAuthorityRegistry().restore_waiting_run(
                run_start_record=inventory_tampered,
                run_binding=inventory_tampered,
                catalog_resolver=DurableToolCatalogResolver(uow),
            )


def test_pre_change_v1_is_unmigratable_without_identity_but_v2_and_fresh_survive(
    tmp_path: Path,
):
    specs = tuple(
        ProviderToolSpec(
            item["name"], item["description"], item["input_schema"]
        )
        for item in _catalog()[0]["specs"]
    )
    with Database.open(tmp_path / "mixed-authority-versions.db") as database:
        uow = SqliteExecutionUnitOfWork(database)
        snapshot = uow.put_tool_catalog_snapshot(specs)
        catalog = {
            "generation": snapshot.generation,
            "content_fingerprint": snapshot.content_fingerprint,
            "specs": _catalog()[0]["specs"],
            "schema_fingerprints": _catalog()[0]["schema_fingerprints"],
        }
        source_registry = SdkRunToolAuthorityRegistry()
        old_source = source_registry.prepare_run(
            run_id="run-v1",
            session_id="session-v1",
            request_id="request-v1",
            root_run_id="root-v1",
            task_scope_id="task-v1",
            workspace_root=None,
            catalog=catalog,
            inventory=_catalog()[1],
        )
        v1_fixture = old_source.run_start_record()
        v1_fixture["schema_version"] = 1
        for item in v1_fixture["inventory"]:
            item.pop("execution_identity", None)
        v2_source = source_registry.prepare_run(
            run_id="run-v2",
            session_id="session-v2",
            request_id="request-v2",
            root_run_id="root-v2",
            task_scope_id="task-v2",
            workspace_root=None,
            catalog=catalog,
            inventory=_catalog()[1],
        )
        v2_record = v2_source.run_start_record()
        v2_record["schema_version"] = 2
        v2_record.pop("workspace_resolution")
        for item in v2_record["inventory"]:
            item.pop("projectless_admission")
        (
            v2_record["authority_fingerprint"],
            v2_record["scope_hash"],
        ) = _v2_record_hashes(v2_record)
        assert v2_record["schema_version"] == 2
        restarted = SdkRunToolAuthorityRegistry()

        with pytest.raises(
            SdkToolAuthorityMigrationUnavailable,
            match="migration_unavailable",
        ):
            restarted.restore_waiting_run(
                run_start_record=v1_fixture,
                run_binding=v1_fixture,
                catalog_resolver=DurableToolCatalogResolver(uow),
            )
        restored_v2 = restarted.restore_waiting_run(
            run_start_record=v2_record,
            run_binding=v2_record,
            catalog_resolver=DurableToolCatalogResolver(uow),
        )
        fresh = restarted.prepare_run(
            run_id="run-fresh",
            session_id="session-fresh",
            request_id="request-fresh",
            root_run_id="root-fresh",
            task_scope_id="task-fresh",
            workspace_root=None,
            catalog=catalog,
            inventory=_catalog()[1],
        )

    with pytest.raises(KeyError):
        restarted.resolve("run-v1")
    assert restored_v2.lease_state == "waiting"
    assert fresh.lease_state == "active"


def test_disclosure_policy_is_explicit_and_full_catalog_is_direct():
    full = _prepare(SdkRunToolAuthorityRegistry(), "run-full")
    full_record = full.run_start_record()

    assert full.disclosure_policy == SDK_FULL_CATALOG_DISCLOSURE_POLICY
    assert full_record["direct_names"] == ["read_file", "tool_search"]
    assert full_record["deferred_names"] == []
    assert len(full.prepared_tool_set.logical_schemas()) == len(full.specs)

    deferred = _prepare(
        SdkRunToolAuthorityRegistry(),
        "run-deferred",
        deferred_names=("read_file",),
    )
    assert (
        deferred.disclosure_policy
        == SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY
    )
    with pytest.raises(ValueError, match="full-direct"):
        SdkRunToolAuthorityRegistry().prepare_run(
            run_id="run-invalid",
            session_id="session-a",
            request_id="request-invalid",
            root_run_id="root-invalid",
            task_scope_id="task-invalid",
            workspace_root=None,
            catalog=_catalog()[0],
            inventory=_catalog()[1],
            deferred_names=("read_file",),
            disclosure_policy=SDK_FULL_CATALOG_DISCLOSURE_POLICY,
        )


@pytest.mark.asyncio
async def test_run_aware_executor_prepares_and_invokes_matching_frozen_registration():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-execute")
    executor, registry = _run_aware_executor_registry(authorities)
    call = ToolCall(CallId("call-execute"), "read_file", {"path": "README.md"})
    context = ToolContext(
        RunId("run-execute"),
        RequestId("request-run-execute"),
        CancellationToken(),
    )

    prepared = await executor._prepared(
        effect_id=EffectId("effect-execute"),
        call=call,
        context=context,
    )
    result = await registry.invoke(call, context)

    assert prepared.spec.name == "read_file"
    assert result.error_code is None
    assert thaw_json(result.value)["tool"] == "read_file"


@pytest.mark.asyncio
async def test_restarted_old_generation_executes_only_with_compatible_handler(
    tmp_path: Path,
):
    specs = tuple(
        ProviderToolSpec(
            item["name"], item["description"], item["input_schema"]
        )
        for item in _catalog()[0]["specs"]
    )
    with Database.open(tmp_path / "old-executor.db") as database:
        uow = SqliteExecutionUnitOfWork(database)
        snapshot = uow.put_tool_catalog_snapshot(specs)
        uow.put_tool_catalog_snapshot(
            (
                ProviderToolSpec(
                    "new_tool",
                    "A later generation",
                    {"type": "object", "properties": {}},
                ),
            )
        )
        catalog = {
            "generation": snapshot.generation,
            "content_fingerprint": snapshot.content_fingerprint,
            "specs": _catalog()[0]["specs"],
            "schema_fingerprints": _catalog()[0]["schema_fingerprints"],
        }
        original = SdkRunToolAuthorityRegistry().prepare_run(
            run_id="run-old-executor",
            session_id="session-old",
            request_id="request-old",
            root_run_id="root-old",
            task_scope_id="task-old",
            workspace_root=None,
            catalog=catalog,
            inventory=_catalog()[1],
            disclosure_policy=SDK_FULL_CATALOG_DISCLOSURE_POLICY,
        )
        record = original.run_start_record()
        authorities = SdkRunToolAuthorityRegistry()
        authorities.restore_waiting_run(
            run_start_record=record,
            run_binding=record,
            catalog_resolver=DurableToolCatalogResolver(uow),
        )
        executor, registry = _run_aware_executor_registry(authorities)
        call = ToolCall(CallId("call-old"), "read_file", {"path": "README.md"})
        context = ToolContext(
            RunId("run-old-executor"),
            RequestId("request-old"),
            CancellationToken(),
        )

        prepared = await executor._prepared(
            effect_id=EffectId("effect-old"), call=call, context=context
        )
        result = await registry.invoke(call, context)

    assert prepared.spec.name == "read_file"
    assert result.error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["schema", "handler"])
async def test_run_aware_executor_rejects_schema_or_handler_identity_drift(drift):
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-drift")
    executor, registry = _run_aware_executor_registry(
        authorities,
        read_schema=(
            {"type": "object", "properties": {"changed": {"type": "boolean"}}}
            if drift == "schema"
            else None
        ),
        execution_identity=(
            "execution-identity-handler-v2"
            if drift == "handler"
            else "execution-identity-v1"
        ),
    )
    call = ToolCall(CallId(f"call-{drift}"), "read_file", {"path": "README.md"})
    context = ToolContext(
        RunId("run-drift"),
        RequestId("request-run-drift"),
        CancellationToken(),
    )

    with pytest.raises(
        SdkToolExecutorCatalogUnavailable,
        match="sdk_tool_executor_catalog_unavailable",
    ):
        await executor._prepared(
            effect_id=EffectId(f"effect-{drift}"),
            call=call,
            context=context,
        )
    with pytest.raises(SdkToolExecutorCatalogUnavailable):
        await registry.invoke(call, context)


def test_restart_policy_generation_is_available_before_next_tool_decision():
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(_AuthorizationStore("manual", 9)),
        SdkRunToolAuthorityRegistry(),
        initial_policy_generation=9,
    )

    assert policy.current_policy_generation() == 9
    policy.update_policy_generation(10)
    assert policy.current_policy_generation() == 10
    with pytest.raises(RuntimeError, match="regressed"):
        policy.update_policy_generation(9)


def test_real_capability_bridge_search_describe_activate_updates_exact_scope():
    registry = SdkRunToolAuthorityRegistry()
    _prepare(registry, "run-a", deferred_names=("read_file",))

    def context():
        return registry.resolve("run-a").execution_context()

    bridge = SdkCapabilityBridgeAdapter(registry, context)
    result = bridge.search("read file")
    assert [item["name"] for item in result["matches"]] == ["read_file"]
    capability_id = result["matches"][0]["capability_id"]
    described = bridge.describe(capability_id)
    assert described["schema_hash"] != "0" * 64
    proposal = bridge.activate(
        capability_id,
        described["schema_hash"],
        described["describe_nonce"],
    )
    assert proposal.prepared_capability.ref.name == "read_file"
    activated = registry.resolve("run-a")
    assert activated.prepared_tool_set.has_direct("read_file")
    assert activated.prepared_tool_set.revision == 2
    assert activated.capability_hash != _prepare(
        SdkRunToolAuthorityRegistry(), "run-a", deferred_names=("read_file",)
    ).capability_hash


def test_sdk_runtime_catalog_activation_changes_next_projection_only_after_receipt():
    registry = SdkRunToolAuthorityRegistry()
    _prepare(registry, "run-a", deferred_names=("read_file",))
    run_id = RunId("run-a")
    exposure = registry.resolve_exposure(run_id)
    exposure.restore(run_id, None)

    def context():
        return registry.resolve("run-a").execution_context()

    bridge = SdkRuntimeCapabilityBridgeAdapter(registry, context)
    searched = bridge.search("read file")
    assert [item["capability_id"] for item in searched["matches"]] == [
        "builtin:read_file"
    ]
    described = bridge.describe("builtin:read_file")
    assert described["schema_hash"] == described["capability_hash"]
    assert "schema_hash" not in described["projection"]
    receipt = bridge.activate(
        "builtin:read_file",
        described["schema_hash"],
        described["describe_nonce"],
    )
    assert [item.name for item in exposure.provider_specs(run_id)] == ["tool_search"]
    exposure.observe_tool_result(run_id, "tool_activate", receipt.to_json())
    assert [item.name for item in exposure.provider_specs(run_id)] == [
        "read_file",
        "tool_search",
    ]


def test_sdk_runtime_catalog_describe_exposes_one_unambiguous_activation_hash():
    registry = SdkRunToolAuthorityRegistry()
    _prepare(registry, "run-a", deferred_names=("read_file",))
    run_id = RunId("run-a")
    exposure = registry.resolve_exposure(run_id)
    exposure.restore(run_id, None)

    def context():
        return registry.resolve("run-a").execution_context()

    described = SdkRuntimeCapabilityBridgeAdapter(registry, context).describe(
        "builtin:read_file"
    )

    assert described["capability_id"] == "builtin:read_file"
    assert described["schema_hash"] == described["capability_hash"]
    assert len(described["schema_hash"]) == 64
    assert json.dumps(described, sort_keys=True).count('"schema_hash"') == 1
    assert "schema_hash" not in described["projection"]
    assert described["projection"]["provider_name"] == "read_file"
    assert described["activation_required"] is True
    assert "Call tool_activate now" in described["next_action"]


@pytest.mark.asyncio
async def test_runtime_activated_tool_is_admitted_by_product_authorization():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a", deferred_names=("read_file",))
    run_id = RunId("run-a")
    exposure = authorities.resolve_exposure(run_id)
    exposure.restore(run_id, None)
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("auto", 0), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )

    with pytest.raises(RuntimeError, match="capability_denied"):
        await policy.decide(_effect("run-a"), request=None)

    def context():
        return authorities.resolve("run-a").execution_context()

    bridge = SdkRuntimeCapabilityBridgeAdapter(authorities, context)
    described = bridge.describe("builtin:read_file")
    receipt = bridge.activate(
        "builtin:read_file",
        described["schema_hash"],
        described["describe_nonce"],
    )
    exposure.observe_tool_result(run_id, "tool_activate", receipt.to_json())

    result = await policy.decide(_effect("run-a"), request=None)
    assert result.decision.value == "allow"


@pytest.mark.asyncio
async def test_unambiguous_write_activation_survives_manual_authorization(
    tmp_path: Path,
):
    from deskpet.sdk_adapters.context_authority import canonical_sha256

    write_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    catalog, _ = _catalog()
    catalog["specs"] = [
        catalog["specs"][0],
        {
            "name": "write_file",
            "description": "Write one file",
            "input_schema": write_schema,
        },
    ]
    catalog["schema_fingerprints"] = {
        item["name"]: canonical_sha256(item["input_schema"])
        for item in catalog["specs"]
    }
    catalog["content_fingerprint"] = canonical_sha256(catalog["specs"])
    authorities = SdkRunToolAuthorityRegistry()
    authority = authorities.prepare_run(
        run_id="run-write",
        session_id="session-a",
        request_id="request-run-write",
        root_run_id="root-run-write",
        task_scope_id="task-run-write",
        workspace_root=str(tmp_path),
        catalog=catalog,
        inventory=(
            _Inventory("tool_search", "control", "read_file"),
            _Inventory("write_file", "async", "write_file"),
        ),
        deferred_names=("write_file",),
    )
    run_id = RunId("run-write")
    exposure = authorities.resolve_exposure(run_id)
    exposure.restore(run_id, None)
    prepared = PreparedToolEffect(
        EffectId("effect-write"),
        run_id,
        ToolCall(
            CallId("call-write"),
            "write_file",
            {"path": str(tmp_path / "output.txt"), "content": "ok"},
        ),
        ToolSpec("write_file", "Write one file", write_schema),
        {"session_id": "session-a", "root_run_id": "root-run-write"},
    )
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("manual", 3), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )

    with pytest.raises(RuntimeError, match="capability_denied"):
        await policy.decide(prepared, request=None)

    bridge = SdkRuntimeCapabilityBridgeAdapter(
        authorities, lambda: authority.execution_context()
    )
    described = bridge.describe("builtin:write_file")
    assert json.dumps(described, sort_keys=True).count('"schema_hash"') == 1
    receipt = bridge.activate(
        described["capability_id"],
        described["schema_hash"],
        described["describe_nonce"],
    )
    exposure.observe_tool_result(run_id, "tool_activate", receipt.to_json())
    assert authorities.is_tool_exposed(run_id, "write_file")

    database = ProductStateDatabase(tmp_path / "product.db")
    database.initialize()
    repository = AuthorizationSagaRepository(database, owner_id="test-sdk")
    grants = DurableTaskGrantAuthority(
        database,
        policy_generation_provider=policy.current_policy_generation,
    )
    adapter = ProductAuthorizationAdapter(
        repository,
        policy=policy,
        identity_factory=policy.identity_factory,
        grant_authority=grants,
        grant_factory=policy.grant_factory,
        clock=lambda: 100.0,
    )

    pending = await adapter.prepare(prepared)
    assert pending.decision is AuthorizationDecision.REQUIRE_USER
    assert pending.request is not None
    await adapter.bind_decision(
        prepared,
        pending.request,
        AuthorizationDecision.ALLOW,
        AuthorizationReceipt("sdk:decision", "a" * 64, "a" * 64),
    )
    grant = policy.facts_for(prepared).grant
    assert grants.assert_active(
        grant.task_grant_id,
        version=grant.version,
        policy_generation=grant.policy_generation,
        now=100.0,
    ).task_grant_id == grant.task_grant_id


@pytest.mark.asyncio
async def test_product_authorization_uses_exact_hashes_and_real_auto_grant(
    tmp_path: Path,
):
    authorities = SdkRunToolAuthorityRegistry()
    authority = _prepare(authorities, "run-a")
    runtime = PreparedAuthorizationRuntime(
        _AuthorizationStore("auto", 0), clock=lambda: 100.0
    )
    policy = SdkPreparedAuthorizationPolicy(
        runtime, authorities, clock=lambda: 100.0
    )
    database = ProductStateDatabase(tmp_path / "product.db")
    database.initialize()
    repository = AuthorizationSagaRepository(database, owner_id="test-sdk")
    adapter = ProductAuthorizationAdapter(
        repository,
        policy=policy,
        identity_factory=policy.identity_factory,
        grant_authority=DurableTaskGrantAuthority(
            database,
            policy_generation_provider=policy.current_policy_generation,
        ),
        grant_factory=policy.grant_factory,
        clock=lambda: 100.0,
    )
    prepared = _effect("run-a")

    allowed = await adapter.prepare(prepared)

    assert allowed.decision.value == "allow"
    facts = policy.facts_for(prepared)
    identity = policy.identity_factory(prepared, None)
    stored = repository.read(identity.authorization_id)
    assert stored is not None
    assert stored.identity.capability_hash == authority.capability_hash
    assert stored.identity.schema_hash == authority.specs["read_file"].schema_hash
    assert stored.identity.schema_hash == (
        _catalog()[0]["schema_fingerprints"]["read_file"]
    )
    assert stored.identity.scope_hash == authority.scope_hash
    assert "0" * 64 not in {
        stored.identity.capability_hash,
        stored.identity.schema_hash,
        stored.identity.scope_hash,
    }
    assert stored.identity.grant_fingerprint == facts.grant.fingerprint
    assert facts.grant.source == "policy:auto"
    assert facts.grant.policy_generation == 0
    assert facts.grant.resource_selectors[0].kind == "system_change"
    assert facts.call.args_hash in facts.grant.resource_selectors[0].canonical_value

    replay = await adapter.prepare(prepared)
    assert replay.decision.value == "allow"
    rows = database.connection.execute(
        "SELECT COUNT(*) FROM authorization_sagas"
    ).fetchone()
    assert int(rows[0]) == 1
    authorities.mark_terminal("run-a", "completed")
    with pytest.raises(RuntimeError, match="facts are unavailable"):
        policy.facts_for(prepared)


@pytest.mark.asyncio
async def test_auto_policy_uses_distinct_immutable_grants_for_repeated_tool_effects(
    tmp_path: Path,
) -> None:
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a")
    clock_values = iter((100.0, 100.0, 101.0, 101.0))
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("auto", 0), clock=lambda: next(clock_values)
        ),
        authorities,
        clock=lambda: 101.0,
    )
    database = ProductStateDatabase(tmp_path / "product.db")
    database.initialize()
    adapter = ProductAuthorizationAdapter(
        AuthorizationSagaRepository(database, owner_id="test-sdk"),
        policy=policy,
        identity_factory=policy.identity_factory,
        grant_authority=DurableTaskGrantAuthority(
            database,
            policy_generation_provider=policy.current_policy_generation,
        ),
        grant_factory=policy.grant_factory,
        clock=lambda: 101.0,
    )
    first = _effect("run-a", effect_id="effect-a", call_id="call-a")
    second = _effect("run-a", effect_id="effect-b", call_id="call-b")

    first_result = await adapter.prepare(first)
    second_result = await adapter.prepare(second)

    assert first_result.decision is AuthorizationDecision.ALLOW
    assert second_result.decision is AuthorizationDecision.ALLOW
    assert (
        policy.facts_for(first).grant.task_grant_id
        != policy.facts_for(second).grant.task_grant_id
    )


@pytest.mark.asyncio
async def test_manual_policy_prepares_user_grant_and_waits_for_sdk_decision():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a")
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("manual", 3), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )
    prepared = _effect("run-a", effect_id="effect-manual")

    result = await policy.decide(prepared, request=None)

    assert result.decision.value == "require_user"
    assert result.request is not None
    facts = policy.facts_for(prepared)
    assert facts.grant.source == "user"
    assert facts.grant.policy_generation == 3
    identity = policy.identity_factory(prepared, result.request)
    assert identity.grant_fingerprint == facts.grant.fingerprint
    assert identity.decision_nonce == result.request.nonce


@pytest.mark.asyncio
async def test_manual_policy_uses_distinct_immutable_grants_per_effect():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a")
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("manual", 3), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )
    first = _effect("run-a", effect_id="effect-manual-a")
    second = _effect("run-a", effect_id="effect-manual-b")

    await policy.decide(first, request=None)
    await policy.decide(second, request=None)

    assert (
        policy.facts_for(first).grant.task_grant_id
        != policy.facts_for(second).grant.task_grant_id
    )


@pytest.mark.asyncio
async def test_authorization_fails_closed_for_deferred_or_cross_run_tool():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a", deferred_names=("read_file",))
    _prepare(authorities, "run-b", session_id="session-b")
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("auto", 0), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )

    with pytest.raises(RuntimeError, match="capability_denied"):
        await policy.decide(_effect("run-a"), request=None)
    with pytest.raises(KeyError):
        await policy.decide(_effect("unknown-run"), request=None)


@pytest.mark.asyncio
async def test_same_effect_id_is_isolated_by_run_identity():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a")
    _prepare(authorities, "run-b", session_id="session-b")
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("auto", 0), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )
    first = _effect("run-a", effect_id="same-effect")
    second = _effect("run-b", effect_id="same-effect")

    await policy.decide(first, request=None)
    await policy.decide(second, request=None)

    assert policy.facts_for(first).authority.run_id == "run-a"
    assert policy.facts_for(second).authority.run_id == "run-b"


# --- S5b Task 1：hidden 工具的 catalog_execution_policy_unavailable 进 Run 故障备忘 -------


def test_hidden_tool_policy_fault_is_recorded_for_run_terminal() -> None:
    from simple_harness.tools import RuntimeToolCatalogError

    from deskpet.sdk_adapters.run_faults import RunFaultMemo

    memo = RunFaultMemo()
    registry = SdkRunToolAuthorityRegistry(run_fault_sink=memo)
    _prepare(registry, "run-hidden", deferred_names=("read_file",))
    run_id = RunId("run-hidden")
    exposure = registry.resolve_exposure(run_id)
    exposure.restore(run_id, None)
    assert [spec.name for spec in exposure.provider_specs(run_id)] == ["tool_search"]
    with pytest.raises(RuntimeToolCatalogError) as hidden:
        exposure.execution_policy(run_id, "read_file")
    assert hidden.value.code == "catalog_execution_policy_unavailable"
    assert memo.read("run-hidden") == "catalog_execution_policy_unavailable"
    # 可见工具的策略读取不留痕。
    exposure.execution_policy(run_id, "tool_search")
    memo.release("run-hidden")
    assert memo.read("run-hidden") is None


# ---- S5b Task 6 / AC-3⑤（A5）：Auto explicit_only —— confirm-only 类永不 auto-grant ----


def _catalog_for(specs: list[dict], inventory: tuple) -> dict:  # type: ignore[no-untyped-def]
    from deskpet.sdk_adapters.context_authority import canonical_sha256

    return {
        "generation": 9,
        "content_fingerprint": canonical_sha256(specs),
        "specs": specs,
        "schema_fingerprints": {item["name"]: canonical_sha256(item["input_schema"]) for item in specs},
    }


def _write_specs() -> list[dict]:
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    return [
        {"name": "write_file", "description": "Write a project file", "input_schema": schema},
        {"name": "purge_dir", "description": "Delete a directory tree", "input_schema": schema},
        {"name": "run_shell", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    ]


def _effect_for(run_id: str, name: str, *, effect_id: str) -> PreparedToolEffect:
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    if name == "run_shell":
        schema = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
        arguments = {"command": "rm -rf build"}
    else:
        arguments = {"path": "/tmp/project/x"}
    return PreparedToolEffect(
        EffectId(effect_id), RunId(run_id), ToolCall(CallId(f"call-{effect_id}"), name, arguments),
        ToolSpec(name, "spec", schema), {"session_id": "session-a", "root_run_id": f"root-{run_id}"},
    )


async def _auto_policy(authorities: SdkRunToolAuthorityRegistry) -> SdkPreparedAuthorizationPolicy:
    return SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(_AuthorizationStore("auto", 0), clock=lambda: 100.0),
        authorities, clock=lambda: 100.0,
    )


@pytest.mark.asyncio
async def test_auto_mode_grants_confirm_only_without_prompt() -> None:
    """2026-09-07 用户产品决定：只有 manual / auto 两种模式，默认 auto，auto 下不弹任何
    授权提示。(a) auto + DESTRUCTIVE（inventory 冻结 EffectClass）→ ALLOW，grant 来源
    ``policy:auto``；manifest dangerous（run_shell）同样 ALLOW；(b) write_file（reversible_local）
    → ALLOW。冻结 EffectClass 仍保留在 spec/facts 中（审计可见），只是 auto 模式不再 REQUIRE_USER。
    原 S5b Task 6「auto 永不授予 confirm-only」的口径由该决定替代。"""

    inventory = (
        _Inventory("write_file", "async", "write_file"),
        _Inventory("purge_dir", "async", "write_file", effect_class="destructive"),
        _Inventory("run_shell", "async", "shell"),
    )
    authorities = SdkRunToolAuthorityRegistry()
    authority = authorities.prepare_run(
        run_id="run-auto", session_id="session-a", request_id="request-auto", root_run_id="root-run-auto",
        task_scope_id="task-auto", workspace_root=None, catalog=_catalog_for(_write_specs(), inventory),
        inventory=inventory,
    )
    assert authority.specs["purge_dir"].effect_class == "destructive"
    assert authority.specs["write_file"].effect_class == "reversible_local"
    assert authority.specs["run_shell"].effect_class == "unknown" and authority.specs["run_shell"].manifest_dangerous is True
    assert confirm_only_tool_names(authority) == ("purge_dir", "run_shell")
    policy = await _auto_policy(authorities)

    seen: list[tuple[str, bool, bool]] = []
    original = policy._runtime.plan_prepared_call

    async def spy(**kwargs):  # type: ignore[no-untyped-def]
        seen.append((kwargs["call"].tool_name, bool(kwargs.get("explicit_only", False)), bool(kwargs.get("confirmed", False))))
        return await original(**kwargs)

    policy._runtime.plan_prepared_call = spy  # type: ignore[method-assign]

    # (a) DESTRUCTIVE → ALLOW，无 REQUIRE_USER；grant 来源 policy:auto。
    destructive = await policy.decide(_effect_for("run-auto", "purge_dir", effect_id="effect-purge"), request=None)
    assert destructive.decision is AuthorizationDecision.ALLOW
    assert destructive.request is None
    facts = policy.facts_for(_effect_for("run-auto", "purge_dir", effect_id="effect-purge"))
    assert facts.grant.source == "policy:auto"
    # 第一次仍以 explicit_only 规划（保留冻结 EffectClass 事实），auto 下第二次以 explicit_only=False 授予。
    assert [item for item in seen if item[0] == "purge_dir"] == [("purge_dir", True, False), ("purge_dir", False, False)]
    # manifest dangerous（run_shell, EffectClass unknown）→ 同样 ALLOW。
    shell = await policy.decide(_effect_for("run-auto", "run_shell", effect_id="effect-shell"), request=None)
    assert shell.decision is AuthorizationDecision.ALLOW
    assert policy.facts_for(_effect_for("run-auto", "run_shell", effect_id="effect-shell")).grant.source == "policy:auto"
    # (b) write_file（reversible_local）→ ALLOW，既有 auto grant。
    allowed = await policy.decide(_effect_for("run-auto", "write_file", effect_id="effect-write"), request=None)
    assert allowed.decision is AuthorizationDecision.ALLOW
    assert policy.facts_for(_effect_for("run-auto", "write_file", effect_id="effect-write")).grant.source == "policy:auto"
    assert [item for item in seen if item[0] == "write_file"] == [("write_file", False, False)]


def test_project_effect_tools_frozen_effect_class_and_confirm_only_subset() -> None:
    """(d) PROJECT_EFFECT 清单逐项冻结 EffectClass（与 manifest 一致，manifest 缺 EffectClass 的按名 unknown），
    confirm-only 子集按名断言：run_shell / process_start（manifest dangerous + unknown）、
    move_file / file_organize / ppt_create（EffectClass unknown，design-freeze §11）。"""

    from deskpet.permissions.effect_policy import IrreversibleEffectPolicy
    from deskpet.sdk_adapters.tool_authority import (
        PROJECT_EFFECT_TOOL_NAMES,
        frozen_tool_effect,
    )
    from deskpet.tool_catalog.manifest import load_tool_manifest

    by_name = {str(item["name"]): item for item in load_tool_manifest().tools}
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    specs = [{"name": name, "description": f"tool {name}", "input_schema": schema} for name in PROJECT_EFFECT_TOOL_NAMES]
    inventory = tuple(
        _Inventory(name, str(by_name[name].get("dispatch_kind") or "handler"), str(by_name[name].get("permission_category") or "write_file"))
        for name in PROJECT_EFFECT_TOOL_NAMES
    )
    registry = SdkRunToolAuthorityRegistry()
    authority = registry.prepare_run(
        run_id="run-frozen", session_id="s", request_id="r", root_run_id="root", task_scope_id="task",
        workspace_root=None, catalog=_catalog_for(specs, inventory), inventory=inventory,
    )
    decision = IrreversibleEffectPolicy().classify(
        owner_key="sdk-runtime", run_kind="chat",
        specs=[SimpleNamespace(**dict(by_name[name])) for name in PROJECT_EFFECT_TOOL_NAMES],
    )
    classified = {item.name: item.effect_class for item in decision.classifications}
    for name in PROJECT_EFFECT_TOOL_NAMES:
        assert authority.specs[name].effect_class == classified[name] == str(by_name[name]["effect_class"]), name
        assert frozen_tool_effect(name) == (classified[name], bool(by_name[name].get("dangerous", False)))
    assert confirm_only_tool_names(authority) == ("file_organize", "move_file", "ppt_create", "process_start", "run_shell")
    # 未知工具（不在 manifest、无 inventory EffectClass）→ unknown → confirm-only；inventory 显式 EffectClass 优先。
    assert frozen_tool_effect("mcp__fs__write_file") == ("unknown", False)
    assert frozen_tool_effect("mcp__fs__write_file", _Inventory("mcp__fs__write_file", "async", "write_file", effect_class="reversible_local")) == ("reversible_local", False)
    assert frozen_tool_effect("write_file", _Inventory("write_file", "async", "write_file", effect_class="bogus")) == ("unknown", False)


def test_restored_run_keeps_frozen_effect_class_and_older_records_fall_back_to_manifest() -> None:
    """WAITING 重启：v3 记录携带 effect_class → 原样恢复（不按新 manifest 重分类）；缺该字段的旧记录按 manifest 回落。"""

    from types import SimpleNamespace as _NS

    inventory = (
        _Inventory("write_file", "async", "write_file", effect_class="destructive"),
        _Inventory("read_file", "async", "read_file"),
    )
    specs = [
        {"name": "write_file", "description": "w", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        {"name": "read_file", "description": "r", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
    ]
    catalog = _catalog_for(specs, inventory)
    registry = SdkRunToolAuthorityRegistry()
    authority = registry.prepare_run(
        run_id="run-restore", session_id="s", request_id="r", root_run_id="root", task_scope_id="task",
        workspace_root=None, catalog=catalog, inventory=inventory,
    )
    record = authority.run_start_record()
    assert {item["name"]: item["effect_class"] for item in record["inventory"]} == {"write_file": "destructive", "read_file": "read_only"}
    binding = _NS(run_id="run-restore", session_id="s", request_id="r", catalog_generation=9, catalog_fingerprint=catalog["content_fingerprint"])
    snapshot = _NS(
        generation=9, content_fingerprint=catalog["content_fingerprint"],
        specs=tuple(_NS(name=item["name"], description=item["description"], parameters=item["input_schema"]) for item in specs),
    )
    resolver = _NS(resolve=lambda generation, fingerprint: snapshot)
    restored = SdkRunToolAuthorityRegistry().restore_run(
        run_start_record=record, run_binding=binding, catalog_resolver=resolver, lease_state="waiting",
    )
    assert restored.specs["write_file"].effect_class == "destructive" and restored.specs["write_file"].confirm_only
    # 旧记录（无 effect_class 字段）→ manifest 回落（write_file reversible_local → 非 confirm-only）。
    legacy = dict(record)
    legacy["inventory"] = [{k: v for k, v in item.items() if k not in {"effect_class", "manifest_dangerous"}} for item in record["inventory"]]
    fallback = SdkRunToolAuthorityRegistry().restore_run(
        run_start_record=legacy, run_binding=binding, catalog_resolver=resolver, lease_state="waiting",
    )
    assert fallback.specs["write_file"].effect_class == "reversible_local" and not fallback.specs["write_file"].confirm_only


def test_primary_tool_authority_restores_exact_projectless_context(tmp_path):
    catalog, inventory = _catalog()
    catalog["specs"] = catalog["specs"][:1]
    inventory = (replace(inventory[0], projectless_admission="safe"),)
    with Database.open(tmp_path / "primary-sdk.db") as database:
        uow = SqliteExecutionUnitOfWork(database)
        snapshot = uow.put_tool_catalog_snapshot(tuple(
            ProviderToolSpec(item["name"], item["description"], item["input_schema"])
            for item in catalog["specs"]
        ))
        catalog.update(generation=snapshot.generation, content_fingerprint=snapshot.content_fingerprint)
        registry = SdkRunToolAuthorityRegistry()
        authority = registry.prepare_run(
            run_id="primary-run", session_id="execution-session", request_id="request", root_run_id="host-run",
            task_scope_id=None, workspace_root=None, binding_version=0,
            workspace_resolution={"kind": "projectless", "effective_root": None, "binding_version": 0},
            catalog=catalog, inventory=inventory,
        )
        binding = SimpleNamespace(run_id="primary-run", session_id="execution-session", request_id="request",
                                  catalog_generation=snapshot.generation, catalog_fingerprint=snapshot.content_fingerprint)
        restored = SdkRunToolAuthorityRegistry().restore_run(
            run_start_record=authority.run_start_record(), run_binding=binding,
            catalog_resolver=DurableToolCatalogResolver(uow), lease_state="waiting",
        )
        assert restored.task_work_context == authority.task_work_context
        assert restored.task_work_context.task_scope_id is None
        assert restored.task_work_context.binding_version == 0
        assert restored.execution_context().write_scope_root is None
        assert restored.authority_fingerprint == authority.authority_fingerprint


@pytest.mark.parametrize("overrides", [
    {"workspace_root": "/tmp/project"},
    {"binding_version": 1},
    {"binding_version": False},
    {"workspace_resolution": {"kind": "legacy"}},
    {"workspace_resolution": {"kind": "project_bound", "effective_root": None}},
])
def test_primary_tool_authority_cannot_smuggle_scope_or_workspace(overrides):
    catalog, inventory = _catalog()
    values = dict(run_id="primary-run", session_id="execution-session", request_id="request", root_run_id="host-run",
                  task_scope_id=None, workspace_root=None, binding_version=0,
                  workspace_resolution={"kind": "projectless", "effective_root": None, "binding_version": 0},
                  catalog=catalog, inventory=inventory)
    values.update(overrides)
    with pytest.raises(ValueError, match="unscoped primary"):
        SdkRunToolAuthorityRegistry().prepare_run(**values)



def test_primary_tool_authority_rejects_project_effect_inventory():
    catalog, inventory = _catalog()
    with pytest.raises(RuntimeError, match="projectless_catalog_contains_project_tool"):
        SdkRunToolAuthorityRegistry().prepare_run(
            run_id="primary-run", session_id="execution-session", request_id="request", root_run_id="host-run",
            task_scope_id=None, workspace_root=None, binding_version=0,
            workspace_resolution={"kind": "projectless", "effective_root": None, "binding_version": 0},
            catalog=catalog, inventory=inventory,
        )
