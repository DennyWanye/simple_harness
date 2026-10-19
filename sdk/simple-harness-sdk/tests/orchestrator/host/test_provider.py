# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""宿主入口的模型连接与认证计数器（原桌面端 ``provider.py`` / ``runtime_profile.py`` 的检验）。

- 共享提供方对 DeepSeek 一律显式关思考（它默认思考，而不回放思考的工具循环会被拒）；其它模型不变。
- 思考池用独立提供方：开思考并带努力档；只有 DeepSeek 才有。
- 官方端点精确计数；声明过的转发主机用上界计数器，边际按主机学习并落盘；别的端点没有计数器。
- 新任务默认的池：设置的窗口，开思考时思考池优先；没有思考池就普通池。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent_orchestrator.deployment.native_pools import calibrated, native_profile_id
from agent_orchestrator.host import HostSettings, ModelConfig, OrchestrationHost, Principal
from agent_orchestrator.host.provider import (
    build_providers,
    deepseek_counter,
    is_deepseek,
    provider_on_client,
    requested_model,
)
from agent_orchestrator.host.tokenizer import tokenizer_matches
from agent_orchestrator.runtime.deepseek_meter import CalibratingProvider, RelayDeepSeekCounter

DEEPSEEK = ModelConfig(
    base_url="https://api.deepseek.com/v1", model="deepseek-v4.1-flash", api_key="fixture"
)
OTHER = ModelConfig(base_url="https://example.test/v1", model="gpt-5.6-luna", api_key="fixture")
RELAY = ModelConfig(
    base_url="https://relay.example.test/v1",
    model="deepseek-v4.1-flash",
    api_key="fixture",
    compatible_hosts=("relay.example.test",),
    response_model_aliases=("deepseek-ai/DeepSeek-V4.1-Flash",),
)


def _tokenizer() -> Path | None:
    for candidate in (
        os.environ.get("DESKPET_ORCH_TOKENIZER_PATH"),
        os.environ.get("SH_TOKENIZER"),
        Path.home() / "Library/Application Support/deskpet/models/deepseek-v41/tokenizer.json",
    ):
        if candidate and tokenizer_matches(candidate):
            return Path(candidate)
    return None


def test_thinking_pools_have_their_own_ids() -> None:
    assert native_profile_id(262_144) == "deepseek-native-256k-v1"
    assert native_profile_id(262_144, thinking=True) == "deepseek-native-256k-thinking-v1"
    assert native_profile_id(524_288, thinking=True) == "deepseek-native-512k-thinking-v1"


def test_the_official_endpoint_maps_the_flash_alias_and_only_deepseek_is_recognised() -> None:
    assert (
        requested_model(
            ModelConfig(
                base_url="https://api.deepseek.com/v1", model="deepseek-v4-flash", api_key="k"
            )
        )
        == "deepseek-flash"
    )
    assert requested_model(OTHER) == "gpt-5.6-luna"
    assert is_deepseek(DEEPSEEK) and is_deepseek(RELAY) and not is_deepseek(OTHER)
    undeclared = ModelConfig(
        base_url="https://relay.example.test/v1", model="deepseek-v4.1-flash", api_key="k"
    )
    assert not is_deepseek(undeclared)


def test_the_shared_provider_never_thinks_and_the_thinking_provider_is_deepseek_only() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient() as client:
            shared, thinking = build_providers(DEEPSEEK, client)
            assert shared.thinking == "disabled"
            assert thinking is not None and thinking.thinking == "enabled"
            # 只开开关转发线路不出思考（2026-09-24），努力档一并送出
            assert thinking.reasoning_effort == "high" and thinking.target != shared.target
            other, other_thinking = build_providers(OTHER, client)
            assert other.thinking is None and other_thinking is None
            # 声明的回显别名到每个池的提供方
            for mode in (None, "enabled", "disabled"):
                provider = provider_on_client(client, RELAY, thinking=mode)
                assert provider._response_model_aliases == frozenset(
                    {"deepseek-ai/DeepSeek-V4.1-Flash"}
                )
            assert provider_on_client(client, DEEPSEEK, thinking=None)._response_model_aliases == (
                frozenset()
            )

    asyncio.run(exercise())


def test_official_endpoint_is_exact_and_a_relay_gets_the_learned_upper_bound(tmp_path) -> None:
    pytest.importorskip("deepseek_recipe")
    tokenizer = _tokenizer()
    if tokenizer is None:
        pytest.skip("pinned DeepSeek tokenizer not installed")
    official = deepseek_counter(DEEPSEEK, tokenizer, thinking="disabled", state_dir=tmp_path)
    assert official.count_mode == "EXACT" and not isinstance(official, RelayDeepSeekCounter)
    counter = deepseek_counter(RELAY, tokenizer, thinking="disabled", state_dir=tmp_path)
    thinking_counter = deepseek_counter(RELAY, tokenizer, thinking="enabled", state_dir=tmp_path)
    assert isinstance(counter, RelayDeepSeekCounter)
    assert counter.count_mode == "CERTIFIED_UPPER_BOUND"
    assert counter.margin is thinking_counter.margin and counter.margin.value == 160
    counter.margin.observe(reported=500, counted=200, has_tools=True)
    assert (tmp_path / "relay-tool-margin" / "relay.example.test.json").is_file()
    assert isinstance(calibrated(object(), counter), CalibratingProvider)
    raw = object()
    assert calibrated(raw, official) is raw  # the official endpoint is not recalibrated
    assert deepseek_counter(OTHER, tokenizer, thinking="disabled", state_dir=tmp_path) is None


def test_a_new_mission_takes_the_thinking_pool_only_when_asked_and_present(tmp_path) -> None:
    def default_for(thinking: str, available: set[str]) -> str | None:
        host = OrchestrationHost(
            tmp_path / thinking,
            principal=Principal("p"),
            model=ModelConfig(
                base_url="https://api.deepseek.com/v1",
                model="deepseek-flash",
                api_key="k",
                thinking=thinking,
            ),
            settings=HostSettings(),
        )
        host.runtime_options = {
            "profiles": {
                pid: SimpleNamespace(context_policy=SimpleNamespace(input_budget=lambda: 1))
                for pid in available
            }
        }
        return host.context_default()

    both = {native_profile_id(614_400), native_profile_id(614_400, thinking=True)}
    assert default_for("disabled", both) == native_profile_id(614_400)
    assert default_for("enabled", both) == native_profile_id(614_400, thinking=True)
    assert default_for("enabled", {native_profile_id(614_400)}) == native_profile_id(614_400)


def test_a_deepseek_host_fetches_the_pinned_tokenizer_into_its_root_once(tmp_path, monkeypatch):
    """没给分词文件也没给计数器：启动时下载到数据目录；非 DeepSeek 或给了路径就不下载。"""
    from agent_orchestrator.host import host as host_module

    fetched: list[Path] = []

    def fake_fetch(dest, **_kwargs):
        fetched.append(Path(dest))
        Path(dest).write_text("{}")
        return Path(dest)

    monkeypatch.setattr(host_module, "ensure_deepseek_tokenizer", fake_fetch)
    host = OrchestrationHost(
        tmp_path / "a", principal=Principal("p"), model=DEEPSEEK, settings=HostSettings()
    )
    (tmp_path / "a").mkdir()
    host._ensure_tokenizer()
    assert fetched == [tmp_path / "a" / "deepseek-v41-tokenizer.json"]
    assert host._tokenizer_path == fetched[0]
    host._ensure_tokenizer()  # 已有路径不再下载
    given = OrchestrationHost(
        tmp_path / "b",
        principal=Principal("p"),
        model=DEEPSEEK,
        settings=HostSettings(),
        tokenizer_path=tmp_path / "mine.json",
    )
    other = OrchestrationHost(tmp_path / "c", principal=Principal("p"), model=OTHER)
    given._ensure_tokenizer()
    other._ensure_tokenizer()
    assert len(fetched) == 1 and other._tokenizer_path is None

    def broken(dest, **_kwargs):
        raise OSError("no network")

    monkeypatch.setattr(host_module, "ensure_deepseek_tokenizer", broken)
    fresh = OrchestrationHost(tmp_path / "d", principal=Principal("p"), model=DEEPSEEK)
    with pytest.raises(host_module.HostUnavailable, match="分词文件"):
        fresh._ensure_tokenizer()
