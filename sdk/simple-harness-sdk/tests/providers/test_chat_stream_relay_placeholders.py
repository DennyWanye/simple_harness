# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Relay stream quirks observed 2026-09-24: a provisional-id opening chunk and 0/0/0 usage
placeholders on every delta, with the real usage only in the trailing chunk."""

from __future__ import annotations

import json

import pytest
from simple_harness.providers.chat_stream import ChatStream
from simple_harness.providers.errors import ProviderProtocolError

ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
REAL = {"prompt_tokens": 281, "completion_tokens": 38, "total_tokens": 319}


def _feed(stream: ChatStream, *events: object) -> None:
    for event in events:
        stream.line(
            "data: " + (event if isinstance(event, str) else json.dumps(event, ensure_ascii=False))
        )
        stream.line("")


def _chunk(
    chunk_id: str, delta: dict, finish: str | None = None, usage: dict | None = ZERO
) -> dict:
    return {
        "id": chunk_id,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": usage,
    }


def test_placeholder_chunks_pin_neither_identity_nor_billing() -> None:
    stream = ChatStream()
    _feed(
        stream,
        {
            "id": "chatcmpl-provisional",
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
        },
        _chunk(
            "chatcmpl-real",
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "history_search", "arguments": ""},
                    }
                ],
            },
        ),
        _chunk(
            "chatcmpl-real",
            {
                "role": None,
                "content": "",
                "tool_calls": [
                    {"index": 0, "type": "function", "function": {"arguments": '{"query": "暗号"}'}}
                ],
            },
        ),
        _chunk("chatcmpl-real", {"role": None, "content": ""}, finish="tool_calls"),
        {"id": "chatcmpl-real", "model": "m", "choices": [], "usage": REAL},
        "[DONE]",
    )
    payload = stream.payload()
    assert payload["id"] == "chatcmpl-real" and payload["usage"] == REAL
    [call] = payload["choices"][0]["message"]["tool_calls"]
    assert call["id"] == "call_1" and json.loads(call["function"]["arguments"]) == {"query": "暗号"}
    assert payload["choices"][0]["finish_reason"] == "tool_calls"


def test_an_all_zero_usage_is_never_a_billing_statement() -> None:
    stream = ChatStream()
    _feed(stream, _chunk("c", {"content": "hi"}), _chunk("c", {}, finish="stop"), "[DONE]")
    assert stream.payload()["usage"] is None  # zeros only: unknown, not zero


def test_contradictory_real_usages_and_model_changes_still_fail() -> None:
    stream = ChatStream()
    _feed(stream, _chunk("c", {"content": "hi"}, usage=REAL))
    with pytest.raises(ProviderProtocolError):
        _feed(
            stream,
            {
                "id": "c",
                "model": "m",
                "choices": [],
                "usage": {**REAL, "completion_tokens": 39, "total_tokens": 320},
            },
        )
    assert stream.usage is None
    other = ChatStream()
    _feed(other, _chunk("c", {"content": "hi"}))
    with pytest.raises(ProviderProtocolError):
        _feed(other, {**_chunk("c", {"content": "there"}), "model": "other"})


def test_relay_that_alternates_response_ids_keeps_the_first_id() -> None:
    """2026-10-18 真机：中转把正文片段改挂临时 id「chatcmpl-wb」，与真 id 的空片段交替出现。

    一次 HTTP 响应就是一次回复，id 只是标签；按第一次出现的 id 记账，不因改名拒收。
    """

    stream = ChatStream()
    events = (
        _chunk("real", {"role": "assistant", "content": ""}),
        _chunk("real", {"content": "", "reasoning_content": "想一想"}),
        _chunk("chatcmpl-wb", {"content": "关灯"}, usage=None),
        _chunk("real", {}),
        _chunk("chatcmpl-wb", {"content": "省电。"}, usage=None),
        _chunk("real", {}, finish="stop"),
        {"id": "real", "model": "m", "choices": [], "usage": REAL},
        "[DONE]",
    )
    try:
        _feed(stream, *events)
        rejected = None
    except ProviderProtocolError as exc:
        rejected = exc
    assert rejected is None, rejected
    payload = stream.payload()
    assert payload["id"] == "real" and payload["usage"] == REAL
    message = payload["choices"][0]["message"]
    assert message["content"] == "关灯省电。" and message["reasoning_content"] == "想一想"
