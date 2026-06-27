# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""空 api_key 护栏 + provider_registry_ready 可观测（Phase 4 + 5）。

根因（plans/2026-06-28-userdata-path-binding-fix）：装机版 relay provider 路径
丢失后退到 legacy 空 key，被拼成非法头 ``Authorization: Bearer `` → httpx
LocalProtocolError 把整轮对话刷崩。护栏在 **生产聊天 provider**
``providers.openai_compatible.OpenAICompatibleProvider._client`` 这个唯一出站
client 构造点拦截：非本地 endpoint 且 key 不可用时抛友好的 LLMProviderError。

同时给 SDK 适配器 ``llm.openai_adapter.OpenAIAdapter`` 加同语义防御（不同入口、
defense-in-depth），它抛类型化的 EmptyApiKeyError。
"""
from __future__ import annotations

import logging
import textwrap

import pytest

from llm.errors import LLMProviderError
from llm.provider_registry import EmptyApiKeyError, LLMProviderRegistry
from providers.openai_compatible import OpenAICompatibleProvider


# ───────── 生产聊天 provider：providers.openai_compatible ─────────

@pytest.mark.parametrize("api_key", ["", "   ", "from-keychain", "from-env", "your-key-here"])
def test_openai_compatible_empty_or_placeholder_cloud_raises(api_key: str):
    """非本地 endpoint + 空/占位符 key → _client() 抛 LLMProviderError，
    error_class=empty_api_key（不构造 client、不发请求、不拼空 Bearer）。"""
    p = OpenAICompatibleProvider(
        base_url="https://relay.example.com/v1", api_key=api_key, model="m"
    )
    with pytest.raises(LLMProviderError) as ei:
        p._client(p.timeout)
    assert ei.value.error_class == "empty_api_key"
    # 友好中文文案（surface 给用户）
    assert "登录" in str(ei.value) or "API Key" in str(ei.value)


@pytest.mark.asyncio
async def test_openai_compatible_local_ollama_placeholder_allowed():
    """本地 ollama：key 占位（server 忽略）→ 不拦，正常构造 client。"""
    p = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1", api_key="ollama", model="m"
    )
    client = p._client(p.timeout)  # 不应抛
    assert client is not None
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_compatible_empty_key_local_allowed():
    """本地 endpoint 即使 key 为空也放行（本地无需鉴权，不会拼出问题头给云端）。"""
    p = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11434/v1", api_key="", model="m"
    )
    client = p._client(p.timeout)
    assert client is not None
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_compatible_real_key_unaffected():
    """真 key → 正常构造 client，向后兼容不受影响。"""
    p = OpenAICompatibleProvider(
        base_url="https://relay.example.com/v1", api_key="sk-real-xxx", model="m"
    )
    client = p._client(p.timeout)
    assert client is not None
    await client.aclose()


# ───────── SDK 适配器 OpenAIAdapter：defense-in-depth ─────────

def test_openai_adapter_placeholder_cloud_raises_empty_api_key_error():
    from llm.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter(api_key="from-keychain", base_url="https://relay.example.com/v1")
    with pytest.raises(EmptyApiKeyError):
        a._ensure_api_key_usable()


def test_openai_adapter_local_placeholder_allowed():
    from llm.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter(api_key="ollama", base_url="http://localhost:11434/v1")
    a._ensure_api_key_usable()  # 不应抛


def test_openai_adapter_real_key_unaffected():
    from llm.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter(api_key="sk-real-xxx", base_url="https://relay.example.com/v1")
    a._ensure_api_key_usable()  # 不应抛


# ───────── provider_registry_ready 可观测（Phase 5） ─────────

def test_registry_ready_log(tmp_path, caplog):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            schema_version = 1

            [[llm.endpoints]]
            id = "relay-cloud"
            name = "Relay Cloud"
            base_url = "https://relay.example.com/v1"
            models = ["gpt-5.5"]
            default_model = "gpt-5.5"
            api_key_ref = "deskpet.provider.relay-cloud"
            priority = 1
            enabled = true
            """
        ).lstrip(),
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO, logger="deskpet.llm.provider_registry"):
        LLMProviderRegistry(config_path)

    assert "provider_registry_ready" in caplog.text
    assert "n=1" in caplog.text
    assert "enabled=1" in caplog.text
    assert "relay-cloud" in caplog.text
