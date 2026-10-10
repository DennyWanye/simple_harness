# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""2026-10-10 parse 局：思考模式每个 token 一个 SSE 事件、每个约 230 字节。流解析器原来按原始传输字节
累计到 16 MiB 就拒绝，所以约 3.5 万个思考 token 的调用一律在 4 分钟左右"回复无法解析"，同一步三次
都这样失败；而留在内存里的文字只有几百 KB。内存上限不变，改成只数留下来的文字（正文、思考、工具
参数）和正在拼装的那一个事件；每一条拒绝都说明破了哪条规则。"""

from __future__ import annotations

import json

import pytest

from simple_harness.providers.chat_stream import RETAINED_LIMIT, ChatStream
from simple_harness.providers.errors import ProviderProtocolError

USAGE = {"prompt_tokens": 40000, "completion_tokens": 120000, "total_tokens": 160000}


def _chunk(delta: dict, finish: str | None = None) -> str:
    # the relay's real shape: ~230 bytes per token, most of it framing
    return "data: " + json.dumps({"id": "cmb-5293d76ec49211f194fc460d9cacbd34", "model": "deepseek-v4.1-flash",
                                  "object": "chat.completion.chunk", "created": 1791626815,
                                  "choices": [{"index": 0, "delta": {"content": "", "function_call": None, **delta},
                                               "logprobs": None, "finish_reason": finish or ""}], "usage": None})


def test_a_long_thinking_stream_is_bounded_by_what_is_kept_not_by_transport_bytes() -> None:
    """120,000 thinking tokens = ~28 MB on the wire, ~360 KB kept.  Mutation: count transport
    bytes again (keep ``_pending`` across events) → red."""
    stream = ChatStream()
    wire = 0
    for n in range(120_000):
        line = _chunk({"reasoning_content": "，的"})
        wire += len(line.encode("utf-8"))
        try:
            stream.line(line)
            stream.line("")
        except ProviderProtocolError as refused:
            raise AssertionError(f"token {n} ({wire} wire bytes) refused: {refused}") from None
    assert wire > RETAINED_LIMIT  # the old rule would have rejected this stream long ago
    stream.line(_chunk({"content": "done"}, finish="stop")); stream.line("")
    stream.line("data: " + json.dumps({"id": "cmb-5293d76ec49211f194fc460d9cacbd34", "model": "deepseek-v4.1-flash",
                                       "choices": [], "usage": USAGE})); stream.line("")
    stream.line("data: [DONE]"); stream.line("")
    payload = stream.payload()
    assert payload["choices"][0]["message"]["content"] == "done"
    assert len(payload["choices"][0]["message"]["reasoning_content"]) == 240_000
    assert payload["usage"] == USAGE


def test_retained_text_past_the_memory_bound_is_still_refused_with_a_reason() -> None:
    stream = ChatStream()
    piece = "x" * 65536
    with pytest.raises(ProviderProtocolError) as refused:
        for _ in range(RETAINED_LIMIT // 65536 + 2):
            stream.line(_chunk({"reasoning_content": piece})); stream.line("")
    assert "retained text larger than the memory bound" in str(refused.value)


def test_one_oversized_event_is_refused_before_it_is_parsed() -> None:
    stream = ChatStream()
    with pytest.raises(ProviderProtocolError) as refused:
        for _ in range(RETAINED_LIMIT // 65536 + 2):
            stream.line("data: " + "y" * 65536)  # one event spread over many data lines
    assert "one event larger than the memory bound" in str(refused.value)


def test_every_refusal_names_the_rule_it_applies() -> None:
    cases = {
        "data: [DONE]": "[DONE] without a finish_reason",
        "data: not json": "event is not JSON",
        "data: [1, 2]": "event is not an object",
        'data: {"error": {"message": "daycard gate: X"}}': "event carries an error object",
        'data: {"id": "a", "choices": [{"index": 1, "delta": {"content": "x"}}]}': "choice is not index 0",
    }
    for line, reason in cases.items():
        stream = ChatStream()
        with pytest.raises(ProviderProtocolError) as refused:
            stream.line(line); stream.line("")
        assert reason in str(refused.value), (line, str(refused.value))
    stream = ChatStream()
    with pytest.raises(ProviderProtocolError) as refused:
        stream.payload()
    assert "stream ended without [DONE]" in str(refused.value)
