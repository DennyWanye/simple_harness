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

from agent_orchestrator.deployment.native_pools import calibrated, native_profile_id
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

    both = {native_profile_id(524_288), native_profile_id(524_288, thinking=True)}
    assert default_for("disabled", both) == native_profile_id(524_288)
    assert default_for("enabled", both) == native_profile_id(524_288, thinking=True)
    # No thinking pool assembled (not DeepSeek, no certified counter): the plain pool.
    assert default_for("enabled", {native_profile_id(524_288)}) == native_profile_id(524_288)


def test_new_missions_default_to_the_512k_window_and_256k_is_the_option() -> None:
    """用户 2026-10-04：新任务默认 512K，配置写 262144 才用 256K（已有任务沿用冻结的执行池）。
    2026-10-10 用户改为默认 800K；512K、256K 要在配置里写明。

    **改坏检验**（H-09）：配置解析的默认改回 256K → 变红。"""
    from deskpet.orchestration.settings import load_settings

    assert load_settings({}).context_input_tokens == 819_200
    assert load_settings({"context_input_tokens": 262_144}).context_input_tokens == 262_144
    assert load_settings({"context_input_tokens": 524_288}).context_input_tokens == 524_288
    assert load_settings({"context_input_tokens": 400_000}).context_input_tokens == 819_200
    assert load_settings({"context_input_tokens": 1_000}).context_input_tokens == 524_288


def test_the_thinking_provider_is_built_only_for_a_deepseek_deployment_with_a_client() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient() as client:
            def probe(snapshot, http_client):  # type: ignore[no-untyped-def]
                service = SimpleNamespace(_snapshot=snapshot, _http_client=http_client, settings=OrchestrationSettings())
                return OrchestrationService._thinking_provider(service)  # type: ignore[arg-type]

            built = probe(DEEPSEEK, client)
            # The switch alone did not make a relay think (2026-09-24); the effort is always sent.
            assert built is not None and built.thinking == "enabled" and built.reasoning_effort == "high"
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
    wrapped = calibrated(object(), counter)
    assert isinstance(wrapped, CalibratingProvider)
    raw = object()
    assert calibrated(raw, official) is raw  # the official endpoint is not recalibrated


def test_declared_relay_echo_aliases_reach_every_pool_provider() -> None:
    """A relay that echoes a vendor-prefixed model name is trusted only when declared."""

    import httpx

    from deskpet.orchestration.provider import provider_on_client

    relay = ProviderSnapshot("relay", "https://relay.example.test/v1", "deepseek-v4.1-flash", "deepseek-v4.1-flash", "fixture",
                             response_model_aliases=("deepseek-ai/DeepSeek-V4.1-Flash",))
    client = httpx.AsyncClient()
    try:
        for thinking in (None, "enabled", "disabled"):
            provider = provider_on_client(client, relay, thinking=thinking)
            assert provider._response_model_aliases == frozenset({"deepseek-ai/DeepSeek-V4.1-Flash"})
        assert provider_on_client(client, DEEPSEEK)._response_model_aliases == frozenset()
    finally:
        import asyncio

        asyncio.run(client.aclose())
