# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main


class _ProductionProviderRegistry:
    def get_chain(self):  # type: ignore[no-untyped-def]
        return []


class _ProductionCapabilityStore:
    async def get_policy_state(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(generation=1)


class _ProductionContextPages:
    async def get(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None

    async def mark_active(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None


class _ProductionMemoryQuery:
    async def recall_readonly(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return []


class _ProductionMemoryScope:
    async def resolve_for_run(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace()


class _ProductionSearchGateway:
    async def search(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return []


def test_freeze_sdk_catalog_thaws_nested_frozen_tool_schema():
    from simple_harness.tools import FunctionTool, ToolSpec

    async def invoke(_arguments, _context):
        return None

    tool = FunctionTool(
        ToolSpec(
            "nested_schema",
            "Nested schema regression fixture",
            {
                "type": "object",
                "properties": {
                    "payload": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                },
            },
        ),
        invoke,
    )
    adapter = SimpleNamespace(specs=(tool.spec,))

    frozen = main._freeze_sdk_catalog(adapter, 7)

    assert frozen["generation"] == 7
    assert frozen["specs"][0]["input_schema"]["properties"]["payload"][
        "properties"
    ]["name"] == {"type": "string"}
    assert len(frozen["schema_fingerprints"]["nested_schema"]) == 64


def test_sdk_runtime_publications_are_declared_service_context_slots():
    from context import ServiceContext

    services = ServiceContext()
    names = (
        "sdk_runtime_catalog",
        "sdk_provider_binding_resolver",
        "sdk_tool_authority_registry",
        "sdk_runtime_tool_inventory",
        "sdk_prepared_authorization_policy",
        "sdk_context_staging",
        "conversation_memory",
        "sdk_context_source_repository",
        "memory_identity_resolver",
        "project_skill_install_service",
        "skill_install_runtime_verifier",
    )
    for name in names:
        marker = object()
        services.register(name, marker)
        assert services.get(name) is marker


def test_skill_install_verifier_binds_only_after_sdk_runtime_is_ready():
    import inspect

    capability_bootstrap = inspect.getsource(main._initialize_capability_runtime)
    sdk_activation = inspect.getsource(main._activate_product_sdk_runtime)

    assert "skill_install_runtime_verifier.bind(" not in capability_bootstrap
    assert (
        "skill_install_runtime_verifier.bind(skill_install_verification)"
        in sdk_activation
    )


# 2026-09-10 removed with the Memory SDK: test_real_product_sdk_production_composition_starts


def test_sdk_runtime_has_no_placeholder_capability_or_authorization_authority():
    import inspect

    source = inspect.getsource(main._build_product_sdk_runtime_stack)

    assert "_MinimalCapabilityBridge" not in source
    assert "_SdkAuthorizationPolicy" not in source
    assert '"0" * 64' not in source
    assert "SdkRuntimeCapabilityBridgeAdapter" in source
    assert "tool_exposure_resolver=tool_authorities.resolve_exposure" in source
    assert "SdkPreparedAuthorizationPolicy" in source
    assert "SdkRunToolAuthorityRegistry" in source
    assert "ProductEffectExecutor" in source
    assert "tools_adapter.bind_run_authorities(tool_authorities)" in source


def test_sdk_tool_authority_is_reachable_for_fresh_waiting_terminal_and_recovery():
    import inspect

    build = inspect.getsource(main._build_product_sdk_runtime_stack)
    foreground = inspect.getsource(main._run_product_harness_chat)
    execute = inspect.getsource(main._execute_sdk_run)
    watcher = inspect.getsource(main._watch_retained_sdk_run)

    assert "tool_authorities.restore_waiting_run" in build
    assert "except SdkToolAuthorityMigrationUnavailable" in build
    assert "_isolate_unrestorable_sdk_tool_authority" in build
    assert "DurableToolCatalogResolver" in build
    assert "catalog=frozen_catalog" not in build
    assert "inventory=tool_inventory" not in build
    assert "_sdk_tool_authority_registry.prepare_run" in foreground
    assert "SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY" in foreground
    assert "SDK_DIRECT_TOOL_KERNEL" in foreground
    assert "tool_authority.run_start_record()" in execute
    assert "_sdk_tool_authority_registry.mark_waiting" in execute
    assert "_sdk_tool_authority_registry.mark_terminal" in execute
    assert "_sdk_tool_authority_registry.mark_terminal" in watcher


def test_foreground_sdk_path_has_no_global_refresh_lock_or_legacy_authority():
    import inspect

    foreground = inspect.getsource(main._run_product_harness_chat)
    execute = inspect.getsource(main._execute_sdk_run)

    assert "_sdk_runtime_run_lock" not in foreground
    assert "_refresh_product_sdk_runtime_after_provider_change" not in foreground
    assert "_assemble_sdk_messages" not in execute
    assert "last_usage" not in execute
    assert "session_generation=1" not in execute


def test_per_run_binding_resolver_retains_waiting_and_releases_terminal(monkeypatch):
    resolver = main._ProductSdkProviderBindingResolver(object(), object())
    authorities = {}

    def build_authority(binding):
        authority = SimpleNamespace(
            budget_fingerprint=f"budget:{binding.run_id}",
            provider=SimpleNamespace(target=SimpleNamespace(model=binding.model_id)),
        )
        authorities[binding.run_id] = authority
        return authority

    monkeypatch.setattr(resolver, "build_authority", build_authority)
    common = {
        "session_id": "session-a",
        "request_id": "request-a",
        "snapshot_id": "snapshot-a",
        "provider_id": "provider-a",
        "provider_incarnation_id": "incarnation-a",
        "provider_config_revision": 1,
        "binding_epoch": 2,
        "model_params": {},
        "context_window": 128_000,
        "catalog_generation": 4,
        "catalog_fingerprint": "catalog-a",
    }
    first = resolver.create_binding(run_id="run-a", model_id="model-a", **common)
    second = resolver.create_binding(run_id="run-b", model_id="model-b", **common)

    assert resolver.resolve("run-a").provider.target.model == "model-a"
    assert resolver.resolve("run-b").provider.target.model == "model-b"
    assert first.budget_fingerprint == "budget:run-a"
    assert second.budget_fingerprint == "budget:run-b"

    resolver.mark_waiting("run-a")
    assert resolver.registry.resolve("run-a").lease_state == "waiting"
    monkeypatch.setattr(main, "_sdk_provider_binding_resolver", resolver)
    with pytest.raises(main.ProviderMutationConflict):
        main._assert_sdk_provider_mutation_allowed(("provider-a",))
    resolver.mark_terminal("run-b", "completed")
    with pytest.raises(KeyError):
        resolver.resolve("run-b")
    resolver.mark_terminal("run-a", "cancelled")
    main._assert_sdk_provider_mutation_allowed(("provider-a",))


@pytest.mark.asyncio
async def test_provider_authority_uses_session_params_and_epoch_cas():
    host = SimpleNamespace(
        provider_bindings=(("deepseek", "deepseek-v4-pro", "inc-1", 4, 7),)
    )
    session_db = SimpleNamespace(
        get_session_provider_binding_authority=AsyncMock(
            return_value={
                "provider_id": "deepseek",
                "preferred_model": "deepseek-v4-pro",
                "model_params": {
                    "thinking": True,
                    "effort": "max",
                    "context": "1m",
                },
                "binding_epoch": 7,
            }
        )
    )

    authority = await main._freeze_sdk_provider_authority(
        session_db, "session-1", host
    )

    assert authority["model_params"]["thinking"] is True
    assert authority["model_params"]["effort"] == "max"
    assert authority["context_window"] == 1_000_000

    session_db.get_session_provider_binding_authority.return_value[
        "binding_epoch"
    ] = 8
    with pytest.raises(RuntimeError, match="changed_during_start"):
        await main._freeze_sdk_provider_authority(session_db, "session-1", host)


def test_foreground_no_longer_owns_manual_memory_prepare() -> None:
    import inspect

    source = inspect.getsource(main._run_product_harness_chat)
    continuation = inspect.getsource(main._run_product_harness_continuation)
    assert "ConversationMemoryRecallQuery" not in source + continuation
    assert "prepare_consumer_conversation_context" not in source + continuation
    assert "ConversationTurnInput" in source
    assert "ConversationContinuationInput" in continuation
    assert "context_source_snapshot_ref=source_ref" in continuation


# 2026-09-10 removed with the Memory SDK: test_human_epoch_composition_registers_three_authorities
