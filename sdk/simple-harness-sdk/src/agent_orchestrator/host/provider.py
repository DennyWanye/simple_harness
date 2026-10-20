# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""宿主入口的模型连接与用量计数器。

* 普通池用一个提供方（DeepSeek 显式关思考：它默认思考，而不回放思考的工具循环会被拒）；
* 思考池用第二个提供方：同端点、同客户端，开思考并带努力档，线路超时 900 秒；
* 每个池配一个认证计数器：官方端点精确计数，声明过的转发主机用上界计数器（可多算不可少算，
  2026-09-24 用户规则），转发线路加的工具前言边际按主机学习并落盘。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from simple_harness.providers import OpenAICompatibleProvider, Secret

from ..runtime.deepseek_meter import (
    CertifiedDeepSeekCounter,
    RelayDeepSeekCounter,
    RelayToolMargin,
)
from ..runtime.model_fallback import LEDGER_NAME, FallbackProvider
from ..runtime.openai_upper_bound_meter import OpenAIUpperBoundCounter, RelayMargin
from .settings import (
    DEEPSEEK_COUNTER_MODELS,
    DEEPSEEK_OFFICIAL_HOSTS,
    MODEL_ALIASES,
    THINKING_EFFORT,
    ModelConfig,
)

THINKING_TIMEOUT = 900.0


def requested_model(config: ModelConfig) -> str:
    """线路上实际请求的模型名：官方端点按别名表改名，别的端点原样。"""

    host = (urlparse(config.base_url).hostname or "").lower()
    if host in DEEPSEEK_OFFICIAL_HOSTS:
        return MODEL_ALIASES.get(config.model, config.model)
    return config.model


def is_deepseek(config: ModelConfig) -> bool:
    """这个配置能不能拿到认证计数器：模型名认得，且主机是官方或声明过的转发。"""

    if requested_model(config) not in DEEPSEEK_COUNTER_MODELS:
        return False
    host = (urlparse(config.base_url).hostname or "").lower()
    return host in DEEPSEEK_OFFICIAL_HOSTS or host in {h.lower() for h in config.compatible_hosts}


def provider_on_client(
    client: httpx.AsyncClient,
    config: ModelConfig,
    *,
    thinking: str | None,
    reasoning_effort: str | None = None,
    timeout: float | None = None,
) -> Any:
    options: dict[str, Any] = {"stream": True}
    if config.allow_private_http:
        options["allow_private_http"] = True
    if thinking is not None:
        options["thinking"] = thinking
    if reasoning_effort is not None:
        options["reasoning_effort"] = reasoning_effort
    if config.response_model_aliases:
        options["response_model_aliases"] = tuple(config.response_model_aliases)
    return OpenAICompatibleProvider(
        client,
        config.base_url,
        requested_model(config),
        Secret(config.api_key),
        timeout=config.timeout if timeout is None else timeout,
        **options,
    )


def build_providers(
    config: ModelConfig, client: httpx.AsyncClient, *, state_dir: Path | None = None
) -> tuple[Any, Any]:
    """``(普通提供方, 思考提供方)``；不是 DeepSeek 时思考提供方为 None，只有普通池。
    配了 ``fallback_model`` 时普通提供方外面套一层回退（台账落在 ``state_dir``）。"""

    deepseek = is_deepseek(config)
    # DeepSeek 默认思考而不回放思考的工具循环会被拒：名字像 DeepSeek 的（哪怕没有认证计数器）
    # 普通池也显式关思考
    plain_thinking = "disabled" if deepseek or "deepseek" in config.model.lower() else None
    plain = provider_on_client(client, config, thinking=plain_thinking)
    if config.fallback_model is not None:
        backup = provider_on_client(
            client,
            replace(config, model=config.fallback_model, fallback_model=None),
            thinking="disabled" if "deepseek" in config.fallback_model.lower() else None,
        )
        plain = FallbackProvider(
            plain,
            backup,
            primary_model=requested_model(config),
            fallback_model=config.fallback_model,
            ledger_path=None if state_dir is None else Path(state_dir) / LEDGER_NAME,
        )
    if not deepseek:
        return plain, None
    thinking = provider_on_client(
        client,
        config,
        thinking="enabled",
        reasoning_effort=THINKING_EFFORT,
        timeout=THINKING_TIMEOUT,
    )
    return plain, thinking


_RELAY_MARGINS: dict[str, Any] = {}


def relay_tool_margin(host: str, state_dir: Path | None) -> Any:
    """一个转发主机学到的工具前言边际：本进程所有池共用，落在 ``state_dir`` 下（只增不减）。"""

    key = host.lower()
    margin = _RELAY_MARGINS.get(key)
    if margin is None:
        path = None if state_dir is None else Path(state_dir) / "relay-tool-margin" / f"{key}.json"
        margin = _RELAY_MARGINS[key] = RelayToolMargin(state_path=path)
    return margin


def upper_bound_counter(config: ModelConfig, state_dir: Path | None) -> OpenAIUpperBoundCounter:
    """非 DeepSeek 模型的上界计数器：字节上界 + 这条线路学到的前言边际（按主机落盘）。"""

    host = (urlparse(config.base_url).hostname or "").lower() or "unknown-host"
    path = None if state_dir is None else Path(state_dir) / "relay-margin" / f"{host}.json"
    margin = RelayMargin(floor=config.relay_margin_floor_tokens, state_path=path)
    return OpenAIUpperBoundCounter(requested_model(config), margin=margin)


def deepseek_counter(
    config: ModelConfig,
    tokenizer_path: Path,
    *,
    thinking: str,
    state_dir: Path | None,
) -> Any:
    """这个端点的认证计数器，不是 DeepSeek 就返回 None。"""

    if not is_deepseek(config):
        return None
    host = (urlparse(config.base_url).hostname or "").lower()
    model = requested_model(config)
    effort = THINKING_EFFORT if thinking == "enabled" else None
    if host in DEEPSEEK_OFFICIAL_HOSTS:
        return CertifiedDeepSeekCounter(
            tokenizer_path, model=model, thinking=thinking, reasoning_effort=effort
        )
    return RelayDeepSeekCounter(
        tokenizer_path,
        model=model,
        thinking=thinking,
        reasoning_effort=effort,
        margin=relay_tool_margin(host, state_dir),
    )


__all__ = (
    "THINKING_TIMEOUT",
    "build_providers",
    "deepseek_counter",
    "is_deepseek",
    "upper_bound_counter",
    "provider_on_client",
    "relay_tool_margin",
    "requested_model",
)
