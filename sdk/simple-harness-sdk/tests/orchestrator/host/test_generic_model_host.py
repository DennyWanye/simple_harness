# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""非 DeepSeek 的 OpenAI 协议模型（Luna / Sol）：上界计数器建池、回退提供方、台账读取。"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.host import HostSettings, ModelConfig, OrchestrationHost, Principal
from agent_orchestrator.host.provider import build_providers, is_deepseek, upper_bound_counter
from agent_orchestrator.runtime.model_fallback import FallbackProvider
from agent_orchestrator.runtime.openai_upper_bound_meter import OpenAIUpperBoundCounter


def _config(**overrides):
    base = {
        "base_url": "https://relay.example.com/v1",
        "model": "gpt-6.1-sol",
        "api_key": "test-key-not-real",
        "thinking": "disabled",
    }
    return ModelConfig(**{**base, **overrides})


def test_a_generic_model_gets_an_upper_bound_counter_and_no_thinking_pool(tmp_path):
    config = _config()
    assert not is_deepseek(config)
    counter = upper_bound_counter(config, tmp_path)
    assert isinstance(counter, OpenAIUpperBoundCounter)
    assert counter.model == "gpt-6.1-sol" and counter.margin.floor == 8192
    assert counter.margin.state_path == tmp_path / "relay-margin" / "relay.example.com.json"


def test_fallback_model_wraps_the_provider(tmp_path):
    import httpx

    config = _config(fallback_model="gpt-5.6-luna")
    plain, thinking = build_providers(config, httpx.AsyncClient(), state_dir=tmp_path)
    assert isinstance(plain, FallbackProvider) and thinking is None
    assert plain.primary_model == "gpt-6.1-sol" and plain.fallback_model == "gpt-5.6-luna"


def test_model_config_refuses_a_fallback_equal_to_the_model():
    with pytest.raises(ValueError, match="fallback_model"):
        _config(fallback_model="gpt-6.1-sol")
    with pytest.raises(ValueError, match="relay_margin_floor_tokens"):
        _config(relay_margin_floor_tokens=-1)


def test_the_host_opens_pools_for_a_generic_model_without_calling_it(tmp_path):
    async def case():
        host = await OrchestrationHost.open(
            tmp_path / "host",
            principal=Principal("u", "用户"),
            model=_config(fallback_model="gpt-5.6-luna"),
            settings=HostSettings(),
            require_acceptance=False,
        )
        try:
            status = host.status()
            assert status["state"] == "available", status
            assert status["model"]["model"] == "gpt-6.1-sol"
            assert status["model"]["fallback_model"] == "gpt-5.6-luna"
            profiles = host.context_profiles()
            assert profiles and all(not p["profile_id"].endswith("thinking") for p in profiles)
            assert host.model_calls() == []
        finally:
            await host.close()

    asyncio.run(asyncio.wait_for(case(), timeout=120))
