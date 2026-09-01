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
        "sdk_context_source_repository",
        "memory_identity_resolver",
        "memory_facts_surface",
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


@pytest.mark.asyncio
async def test_real_product_sdk_production_composition_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simple_harness_memory import MemoryManager

    from deskpet.memory.session_db import SessionDB
    from deskpet.sdk_adapters.skill_install_verification import (
        BindableSkillInstallVerificationDriverFactory,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    memory = await MemoryManager.build_development(data_dir / "memory.db")
    session = SessionDB(data_dir / "state.db", memory_backend=memory)
    await session.initialize()
    services = {
        "session_db": session,
        "capability_platform": object(),
        "provider_registry": _ProductionProviderRegistry(),
        "workflow_service": object(),
        "context_page_in_store": _ProductionContextPages(),
        "memory_recall_query": _ProductionMemoryQuery(),
        "memory_recall_scope_resolver": _ProductionMemoryScope(),
        "search_gateway": _ProductionSearchGateway(),
        "authorization_runtime": object(),
        "capability_store": _ProductionCapabilityStore(),
        "project_skill_install_service": object(),
        "skill_install_verification_driver_factory": (
            BindableSkillInstallVerificationDriverFactory()
        ),
        # Production bootstrap publishes both resolver seams before the SDK
        # runtime stack is composed. This smoke test does not invoke a Skill,
        # but it must still model that complete dependency graph.
        "frozen_skill_instruction_resolver": object(),
        "legacy_frozen_skill_instruction_resolver": object(),
        "project_skill_install_service": SimpleNamespace(
            confirm_authorized=AsyncMock(),
            stage_authorization_preflight=AsyncMock(),
            settle_authorization_terminal=lambda _evidence: None,
        ),
    }
    publications = (
        "sdk_runtime_ready",
        "sdk_runtime_catalog",
        "sdk_provider_binding_resolver",
        "sdk_tool_authority_registry",
        "sdk_runtime_tool_inventory",
        "sdk_prepared_authorization_policy",
        "sdk_context_staging",
        "conversation_memory",
        "memory_identity_authority",
        "memory_identity_resolver",
        "memory_facts_surface",
    )
    previous = {
        name: main.service_context.get(name)
        for name in (*services, *publications)
    }
    stack = None
    monkeypatch.setattr(main, "_memory_backend", memory)
    monkeypatch.setattr(main._paths, "user_data_dir", lambda: tmp_path)
    try:
        for name, value in services.items():
            main.service_context.register(name, value)
        stack = await main._build_product_sdk_runtime_stack(1)
        ready = await stack.start()

        assert stack.phase == "ready"
        assert ready.runtime.state.value == "ready"
        assert main.service_context.get("sdk_context_staging") is not None
        assert main.service_context.get("conversation_memory") is None
        frozen_catalog = main.service_context.get("sdk_runtime_catalog")
        sdk_inventory = main.service_context.get("sdk_runtime_tool_inventory")
        assert {item.name for item in sdk_inventory} == set(
            frozen_catalog["tool_names"]
        )
        assert "memory_recall" not in frozen_catalog["tool_names"]
        assert "memory_search" not in frozen_catalog["tool_names"]
        memory_write = next(
            item for item in frozen_catalog["specs"] if item["name"] == "memory_write"
        )
        assert "recorded automatically" in memory_write["description"]
        assert "do not call this tool" in memory_write["description"]
        context_sources = main.service_context.get("sdk_context_source_repository")
        assert context_sources is not None
        assert context_sources._path == data_dir / "state.db"
        binding_id, source_ref = await context_sources.put_pending(
            root_run_id="runtime-wiring-smoke",
            continuation_id=None,
            payload={"provider_messages": [{"role": "user", "content": "hello"}]},
        )
        assert binding_id
        assert source_ref.startswith("sha256:")
    finally:
        if stack is not None:
            await stack.close()
        for name, value in previous.items():
            main.service_context.register(name, value)
        await session.close()


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


@pytest.mark.asyncio
async def test_human_epoch_composition_registers_three_authorities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5A-S6/AC-6: on a human-memory (v45) state db the stack build registers
    the three concrete SDK authorities; a pre-v45 human db fails stably."""

    from simple_harness_memory import MemoryManager

    from deskpet.memory.schema import initialize_human_memory_program_state_db
    from deskpet.memory.session_db import SessionDB
    from deskpet.sdk_adapters.context_authority import (
        ProductRunContextAuthority,
        ProductRuntimeDecisionSink,
    )
    from deskpet.sdk_adapters.skill_install_verification import (
        BindableSkillInstallVerificationDriverFactory,
    )
    from deskpet.sdk_adapters.task_execution import ProductTaskExecutionAuthority

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    human_state = tmp_path / "human-state.db"
    await initialize_human_memory_program_state_db(human_state)

    memory = await MemoryManager.build_development(data_dir / "memory.db")
    session = SessionDB(data_dir / "state.db", memory_backend=memory)
    await session.initialize()
    services = {
        "session_db": session,
        "capability_platform": object(),
        "provider_registry": _ProductionProviderRegistry(),
        "workflow_service": object(),
        "context_page_in_store": _ProductionContextPages(),
        "memory_recall_query": _ProductionMemoryQuery(),
        "memory_recall_scope_resolver": _ProductionMemoryScope(),
        "search_gateway": _ProductionSearchGateway(),
        "authorization_runtime": object(),
        "capability_store": _ProductionCapabilityStore(),
        "skill_install_verification_driver_factory": (
            BindableSkillInstallVerificationDriverFactory()
        ),
        "frozen_skill_instruction_resolver": object(),
        "legacy_frozen_skill_instruction_resolver": object(),
        "project_skill_install_service": SimpleNamespace(
            confirm_authorized=AsyncMock(),
            stage_authorization_preflight=AsyncMock(),
            settle_authorization_terminal=lambda _evidence: None,
        ),
    }
    slots = (
        "sdk_run_context_authority",
        "sdk_runtime_decision_sink",
        "sdk_task_execution_authority",
        "human_memory_v7_runtime",
    )
    previous = {
        name: main.service_context.get(name) for name in (*services, *slots)
    }
    stack = None
    monkeypatch.setattr(main, "_memory_backend", memory)
    monkeypatch.setattr(main._paths, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_state_db_path", human_state)
    try:
        for name, value in services.items():
            main.service_context.register(name, value)
        stack = await main._build_product_sdk_runtime_stack(1)
        assert isinstance(
            main.service_context.get("sdk_run_context_authority"),
            ProductRunContextAuthority,
        )
        assert isinstance(
            main.service_context.get("sdk_runtime_decision_sink"),
            ProductRuntimeDecisionSink,
        )
        assert isinstance(
            main.service_context.get("sdk_task_execution_authority"),
            ProductTaskExecutionAuthority,
        )
        assert main.service_context.get("human_memory_v7_runtime") is not None
    finally:
        if stack is not None:
            await stack.close()
        for name, value in previous.items():
            main.service_context.register(name, value)
        await session.close()

    # Missing prerequisite drill: a human-epoch db stuck below v45 must fail
    # the stack build stably (no Noop fallback).
    import sqlite3 as _sqlite3

    stale = tmp_path / "stale-state.db"
    with _sqlite3.connect(stale) as db:
        db.execute("PRAGMA user_version=44")
        db.commit()
    monkeypatch.setattr(main, "_state_db_path", stale)
    memory2 = await MemoryManager.build_development(data_dir / "memory2.db")
    session2 = SessionDB(data_dir / "state2.db", memory_backend=memory2)
    await session2.initialize()
    monkeypatch.setattr(main, "_memory_backend", memory2)
    try:
        for name, value in services.items():
            if name != "session_db":
                main.service_context.register(name, value)
        main.service_context.register("session_db", session2)
        with pytest.raises(Exception, match="sdk_context_route_ledger_schema_missing"):
            await main._build_product_sdk_runtime_stack(2)
    finally:
        for name, value in previous.items():
            main.service_context.register(name, value)
        await session2.close()
