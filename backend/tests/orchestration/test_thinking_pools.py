"""思考模式双支持（2026-09-24 用户决定）的 Host 侧：

- 共享提供方对 DeepSeek 型号一律显式关思考（DeepSeek 默认开思考，而共享提供方不回传思考）；
  其它型号的请求不变。
- 开思考的工作跑在独立的"思考池"上（独立 id、独立执行库、独立提供方与计数器），
  配置项 ``[orchestration] thinking`` 默认开，只决定新 Mission 的默认池；已有 Mission 留在
  原池，所以改配置（包括改回关）不会让已有 Agent 的冻结绑定失配。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from deskpet.orchestration.native_plane import native_profile_id
from deskpet.orchestration.provider import ProviderSnapshot, build_provider, provider_on_client
from deskpet.orchestration.runtime_profile import deepseek_thinking
from deskpet.orchestration.service import OrchestrationService
from deskpet.orchestration.settings import OrchestrationSettings, load_settings

DEEPSEEK = ProviderSnapshot("deepseek", "https://api.deepseek.com/v1", "deepseek-v4.1-flash", "deepseek-v4.1-flash", "fixture")
OTHER = ProviderSnapshot("luna", "https://example.test/v1", "gpt-5.6-luna", "gpt-5.6-luna", "fixture")


def test_thinking_pools_have_their_own_ids() -> None:
    assert native_profile_id(262_144) == "deepseek-native-256k-v1"  # unchanged for existing Missions
    assert native_profile_id(262_144, thinking=True) == "deepseek-native-256k-thinking-v1"
    assert native_profile_id(524_288, thinking=True) == "deepseek-native-512k-thinking-v1"


def test_the_setting_defaults_to_enabled_and_can_be_switched_off() -> None:
    assert load_settings({}).thinking == "enabled"
    assert OrchestrationSettings().thinking == "enabled"
    assert load_settings({"thinking": "Disabled"}).thinking == "disabled"
    assert load_settings({"thinking": "maybe"}).thinking == "enabled"
    assert load_settings({"thinking": 1}).thinking == "enabled"


def test_only_deepseek_gets_an_explicit_switch_and_the_shared_provider_never_thinks() -> None:
    enabled = OrchestrationSettings(thinking="enabled")
    assert deepseek_thinking(DEEPSEEK, enabled) == "disabled"  # thinking runs on the separate pools
    assert deepseek_thinking(OTHER, enabled) is None
    assert deepseek_thinking(None, enabled) is None

    async def exercise() -> None:
        shared, client = build_provider(DEEPSEEK, thinking=deepseek_thinking(DEEPSEEK))
        try:
            assert shared.thinking == "disabled"
            thinking = provider_on_client(client, DEEPSEEK, thinking="enabled")
            assert thinking.thinking == "enabled" and thinking.target != shared.target
            other, other_client = build_provider(OTHER, thinking=deepseek_thinking(OTHER))
            await other_client.aclose()
            assert other.thinking is None
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_a_new_mission_takes_the_thinking_pool_only_when_the_setting_asks_and_the_pool_exists() -> None:
    def default_for(thinking: str, available: set[str]) -> str | None:
        service = SimpleNamespace(settings=OrchestrationSettings(thinking=thinking), _runtime_options={"profiles": {}})
        service._context_profiles = lambda: [{"profile_id": pid} for pid in available]  # type: ignore[attr-defined]
        return OrchestrationService._context_default(service)  # type: ignore[arg-type]

    both = {native_profile_id(262_144), native_profile_id(262_144, thinking=True)}
    assert default_for("disabled", both) == native_profile_id(262_144)
    assert default_for("enabled", both) == native_profile_id(262_144, thinking=True)
    # No thinking pool assembled (not DeepSeek, no certified counter): the plain pool.
    assert default_for("enabled", {native_profile_id(262_144)}) == native_profile_id(262_144)


def test_the_thinking_provider_is_built_only_for_a_deepseek_deployment_with_a_client() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient() as client:
            def probe(snapshot, http_client):  # type: ignore[no-untyped-def]
                service = SimpleNamespace(_snapshot=snapshot, _http_client=http_client, settings=OrchestrationSettings())
                return OrchestrationService._thinking_provider(service)  # type: ignore[arg-type]

            built = probe(DEEPSEEK, client)
            assert built is not None and built.thinking == "enabled"
            assert probe(OTHER, client) is None
            assert probe(DEEPSEEK, None) is None
            assert probe(None, client) is None

    asyncio.run(exercise())


def test_official_endpoint_is_exact_and_a_relay_gets_the_learned_upper_bound(tmp_path, monkeypatch) -> None:
    import pytest

    from deskpet.orchestration import runtime_profile

    try:
        from agent_orchestrator.runtime.deepseek_meter import CalibratingProvider, RelayDeepSeekCounter
    except ImportError:
        pytest.skip("SDK without the relay counter")
    tokenizer = runtime_profile.tokenizer_path()
    if tokenizer is None:
        pytest.skip("pinned DeepSeek tokenizer not installed")
    relay = ProviderSnapshot("relay", "https://relay.example.test/v1", "deepseek-v4.1-flash", "deepseek-v4.1-flash", "fixture")
    settings = OrchestrationSettings(deepseek_compatible_hosts="relay.example.test")
    official = runtime_profile.deepseek_counter_for(DEEPSEEK, settings, state_dir=tmp_path)
    assert official.count_mode == "EXACT" and not isinstance(official, RelayDeepSeekCounter)
    counter = runtime_profile.deepseek_counter_for(relay, settings, state_dir=tmp_path)
    thinking_counter = runtime_profile.deepseek_counter_for(relay, settings, thinking="enabled", state_dir=tmp_path)
    assert isinstance(counter, RelayDeepSeekCounter) and counter.count_mode == "CERTIFIED_UPPER_BOUND"
    assert counter.margin is thinking_counter.margin and counter.margin.value == 160  # one margin per relay host
    counter.margin.observe(reported=500, counted=200, has_tools=True)
    assert (tmp_path / "relay-tool-margin" / "relay.example.test.json").is_file()
    wrapped = runtime_profile.calibrated(object(), counter)
    assert isinstance(wrapped, CalibratingProvider)
    raw = object()
    assert runtime_profile.calibrated(raw, official) is raw  # the official endpoint is not recalibrated


def test_pools_frozen_with_an_older_counter_identity_keep_it_or_are_retired(tmp_path) -> None:
    """A pool's context identity pins its counter (nothing migrates old requests): a pool
    frozen with the identity released before 2026-09-24 keeps that exact counter; an ARP pool
    or an unknown identity is not registered, so the service still starts."""
    import json

    import pytest

    from deskpet.orchestration import runtime_profile

    try:
        from agent_orchestrator.runtime.assembly import execution_db_for
        from agent_orchestrator.runtime.deepseek_tokens import LegacyPriorOutputDeepSeekCounter
    except ImportError:
        pytest.skip("SDK without the legacy counter")
    if runtime_profile.tokenizer_path() is None:
        pytest.skip("pinned DeepSeek tokenizer not installed")
    config = SimpleNamespace(evidence_root=tmp_path, execution_db=tmp_path / "execution.db")
    legacy = runtime_profile.legacy_counter_for(DEEPSEEK)
    assert isinstance(legacy, LegacyPriorOutputDeepSeekCounter) and legacy.requires_prior_output_reserve is True
    assert runtime_profile.legacy_counter_for(OTHER) is None

    def freeze(profile_id: str, fingerprint: str) -> None:
        database = execution_db_for(config, profile_id)
        database.with_name(database.name + ".context.json").write_text(json.dumps({"tokenizer_fingerprint": fingerprint}))

    current = runtime_profile.deepseek_counter_for(DEEPSEEK, OrchestrationSettings(), state_dir=tmp_path)
    pick = runtime_profile.counter_for_pool
    assert runtime_profile.frozen_tokenizer_fingerprint(config, "default") is None
    assert pick(config, "default", current, legacy, native_pool=False) is current  # a fresh pool
    freeze("default", legacy.fingerprint)
    assert runtime_profile.frozen_tokenizer_fingerprint(config, "default") == legacy.fingerprint
    assert pick(config, "default", current, legacy, native_pool=False) is legacy  # keeps its old counter
    freeze("deepseek-long-256k-v1", current.fingerprint)
    assert pick(config, "deepseek-long-256k-v1", current, legacy, native_pool=False) is current
    freeze("deepseek-native-256k-v1", legacy.fingerprint)
    assert pick(config, "deepseek-native-256k-v1", current, legacy, native_pool=True) is None  # retired
    freeze("deepseek-native-512k-v1", "deepseek-v41:an-intermediate-identity")
    assert pick(config, "deepseek-native-512k-v1", current, legacy, native_pool=True) is None  # retired


def test_the_legacy_counter_on_a_relay_charges_that_hosts_margin(tmp_path) -> None:
    import pytest

    from deskpet.orchestration import runtime_profile

    try:
        from agent_orchestrator.runtime.deepseek_meter import LegacyRelayDeepSeekCounter
        from agent_orchestrator.runtime.deepseek_tokens import LegacyPriorOutputDeepSeekCounter
    except ImportError:
        pytest.skip("SDK without the legacy relay counter")
    if runtime_profile.tokenizer_path() is None:
        pytest.skip("pinned DeepSeek tokenizer not installed")
    relay = ProviderSnapshot("relay", "https://legacy-relay.example.test/v1", "deepseek-v4.1-flash", "deepseek-v4.1-flash", "fixture")
    official = runtime_profile.legacy_counter_for(DEEPSEEK, tmp_path)
    on_relay = runtime_profile.legacy_counter_for(relay, tmp_path)
    assert type(official) is LegacyPriorOutputDeepSeekCounter
    assert isinstance(on_relay, LegacyRelayDeepSeekCounter) and on_relay.fingerprint == official.fingerprint
    assert on_relay.margin is runtime_profile.relay_tool_margin("legacy-relay.example.test", tmp_path)
    assert runtime_profile.calibrated(object(), on_relay) is not None
