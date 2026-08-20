# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main


def test_sdk_runtime_has_no_placeholder_capability_or_authorization_authority():
    import inspect

    source = inspect.getsource(main._build_product_sdk_runtime_stack)

    assert "_MinimalCapabilityBridge" not in source
    assert "_SdkAuthorizationPolicy" not in source
    assert '"0" * 64' not in source
    assert "SdkCapabilityBridgeAdapter" in source
    assert "SdkPreparedAuthorizationPolicy" in source
    assert "SdkRunToolAuthorityRegistry" in source


def test_sdk_tool_authority_is_reachable_for_fresh_waiting_terminal_and_recovery():
    import inspect

    build = inspect.getsource(main._build_product_sdk_runtime_stack)
    foreground = inspect.getsource(main._run_product_harness_chat)
    execute = inspect.getsource(main._execute_sdk_run)
    watcher = inspect.getsource(main._watch_retained_sdk_run)

    assert "tool_authorities.prepare_run" in build
    assert "_sdk_tool_authority_registry.prepare_run" in foreground
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


@pytest.mark.asyncio
async def test_sdk_persona_and_memory_use_current_product_authorities(monkeypatch):
    memory = SimpleNamespace(
        recall_readonly=AsyncMock(return_value=[{"text": "owner memory", "score": 1}])
    )
    registry = SimpleNamespace(
        get_entry=lambda _provider_id: SimpleNamespace(base_url="https://relay.invalid")
    )
    monkeypatch.setattr(
        main,
        "config",
        SimpleNamespace(raw={"agent": {"persona": "Current persona"}}),
    )
    monkeypatch.setattr(
        main,
        "service_context",
        SimpleNamespace(
            get=lambda name: {
                "provider_registry": registry,
                "memory_recall_query": memory,
            }.get(name)
        ),
    )
    monkeypatch.setattr(
        main,
        "_companion_identity_gate",
        SimpleNamespace(
            freeze=lambda: SimpleNamespace(
                owner=SimpleNamespace(profile_id="owner-1", profile_generation=3),
                binding_epoch=5,
            )
        ),
    )
    scope = object()
    session_db = SimpleNamespace(
        capture_owner_memory_read_scope=AsyncMock(return_value=scope)
    )

    persona, items = await main._sdk_persona_and_memory_sources(
        session_db=session_db,
        text="current request",
        provider_binding={"provider_id": "deepseek", "model_id": "deepseek-v4-pro"},
    )

    assert persona.startswith("Current persona")
    session_db.capture_owner_memory_read_scope.assert_awaited_once_with(
        "owner-1", 3, 5
    )
    memory.recall_readonly.assert_awaited_once_with("current request", 8, scope)
    assert items == ({"text": "owner memory", "score": 1},)
