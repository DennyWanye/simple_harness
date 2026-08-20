# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main


def test_sdk_tool_grant_uses_supported_auto_policy_source():
    prepared = SimpleNamespace(
        effect_id=SimpleNamespace(value="effect-1"),
        run_id=SimpleNamespace(value="run-1"),
        context_metadata={
            "session_id": "session-1",
            "root_run_id": "canonical-root-1",
        },
    )

    grant = main._build_sdk_task_grant(prepared, generation=7)

    assert grant.source == "policy:auto"
    assert grant.policy_generation == 7
    assert grant.root_run_id == "canonical-root-1"
    assert grant.expires_at is None

    second = main._build_sdk_task_grant(prepared, generation=7)
    assert second.fingerprint == grant.fingerprint


def test_sdk_prepared_identity_uses_context_metadata_without_request_id():
    prepared = SimpleNamespace(
        run_id=SimpleNamespace(value="sdk-run-1"),
        context_metadata={
            "session_id": "session-1",
            "root_run_id": "canonical-root-1",
        },
    )

    assert main._sdk_prepared_identity(prepared) == (
        "session-1",
        "canonical-root-1",
    )


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
    resolver.mark_terminal("run-b", "completed")
    with pytest.raises(KeyError):
        resolver.resolve("run-b")


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
