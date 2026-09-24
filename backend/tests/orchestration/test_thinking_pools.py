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
