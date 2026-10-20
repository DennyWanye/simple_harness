# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""通用 OpenAI 协议模型的上界计数器：字节上界、线路边际只升不降并落盘、认证绑定、学习装饰器。"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_orchestrator.runtime.openai_upper_bound_meter import (
    DEFAULT_MARGIN_FLOOR,
    MarginLearningProvider,
    OpenAIUpperBoundCounter,
    RelayMargin,
)
from simple_harness.contracts import Message, MessageRole
from simple_harness.contracts.identity import RequestId
from simple_harness.providers.base import (
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderToolSpec,
    ProviderUsage,
)


def _request(text: str, *, tools: bool = False) -> ProviderRequest:
    specs = (
        (ProviderToolSpec("get_time", "读当前时间", {"type": "object", "properties": {}}),)
        if tools
        else ()
    )
    return ProviderRequest(
        RequestId("r-1"), (Message(MessageRole.USER, text),), tools=specs, max_output_tokens=64
    )


@pytest.mark.parametrize(
    "text", ["1+1=?", "请调用工具查上海时间。" * 50, "emoji 🦒🦒 mixed 中文 text"]
)
def test_the_estimate_is_at_least_the_wire_bytes_plus_the_margin(text):
    counter = OpenAIUpperBoundCounter("gpt-6.1-sol", margin=RelayMargin(floor=100))
    request = _request(text, tools=True)
    wire = counter.exact_input_tokens(request)
    # 线路体包含正文、工具说明与 JSON 结构：字节数不少于正文字节数
    assert wire >= len(text.encode("utf-8"))
    assert counter.estimate_input_tokens(request) == wire + 100
    assert counter.count_text(text) == len(text.encode("utf-8"))
    assert counter.count_mode == "CERTIFIED_UPPER_BOUND"


def test_the_margin_only_rises_and_survives_restart(tmp_path):
    path = tmp_path / "margin.json"
    margin = RelayMargin(floor=1000, headroom=10, state_path=path)
    assert margin.charge(_request("x")) == 1000
    assert margin.observe(reported=500, counted=300) is False  # 盖得住：不动
    assert margin.value == 1000
    assert margin.observe(reported=5000, counted=300) is True  # 差 4700 > 1000：抬到 4710
    assert margin.value == 4710 and margin.alarms[-1]["excess"] == 4700
    assert margin.observe(reported=2000, counted=300) is False  # 之后小的差值不降
    assert margin.value == 4710
    again = RelayMargin(floor=1000, state_path=path)
    assert again.value == 4710 and again.max_excess == 4700
    assert json.loads(path.read_text())["value"] == 4710


def test_the_counter_binds_a_certified_meter():
    counter = OpenAIUpperBoundCounter("gpt-6.1-sol", margin=RelayMargin())
    binding = counter.meter_factory(counter, input_limit_tokens=262_144, max_output_tokens=8192)
    assert binding.count_mode == "CERTIFIED_UPPER_BOUND"
    assert binding.model_limits["model_ref"] == "gpt-6.1-sol"
    assert binding.model_limits["counter_id"] == "openai-chat-byte-upper-bound"
    assert counter.fingerprint.endswith(f"gpt-6.1-sol:floor-{DEFAULT_MARGIN_FLOOR}")
    with pytest.raises(TypeError):
        counter.meter_factory(object(), input_limit_tokens=1, max_output_tokens=1)  # type: ignore[arg-type]


def test_the_learning_provider_raises_the_margin_from_reported_usage():
    counter = OpenAIUpperBoundCounter("gpt-6.1-sol", margin=RelayMargin(floor=100))

    class Inner:
        target = "t"

        async def invoke(self, request, *, cancel):
            return ProviderResponse(
                request.request_id,
                Message(MessageRole.ASSISTANT, "2"),
                usage=ProviderUsage(9000, 5, 9005, cache_tokens=4224),
                model="gpt-6.1-sol",
            )

    provider = MarginLearningProvider(Inner(), counter)
    request = _request("1+1=?")
    response = asyncio.run(provider.invoke(request, cancel=CancelToken()))
    assert response.usage.input_tokens == 9000
    # 报告 9000 远大于本机字节数：边际抬到差值 + 余量，之后的估算不再少算
    assert counter.margin.value > 100
    assert counter.estimate_input_tokens(request) >= 9000
