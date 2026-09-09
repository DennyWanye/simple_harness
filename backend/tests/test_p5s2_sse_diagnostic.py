# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 1 — SSE diagnostic logging tests.

Goal: when ``_stream_one_attempt`` finishes, every accumulated tool_call
buffer should emit a structured INFO log entry containing at least three
fields:

    - ``args_len``    — total length of the accumulated args buffer
    - ``args_preview`` — first 100 chars of the buffer (truncated)
    - ``parse_ok``    — whether ``json.loads(args_buf)`` succeeded

Why: your-llm-relay.example.com / sealos sometimes truncate SSE mid-frame leaving us with
half-baked tool_call args. Without dump-time visibility we can't tell whether
the model itself emitted broken JSON, the proxy chopped the stream, or our
parser dropped fragments. This test pins down the diagnostic emit so we can
attribute future bugs at log-grep time, not gdb time.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest
import structlog

from providers.openai_compatible import OpenAICompatibleProvider


@pytest.fixture
def structlog_to_stdlib():
    """Route structlog through stdlib logging so caplog captures records.

    Mirrors the production config in ``backend/main.py``. Without this,
    structlog's default ``PrintLoggerFactory`` writes to stdout — caplog
    then sees zero records and the assertion would fail for the wrong
    reason. Restored to default after the test.
    """
    prior = structlog.get_config()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.KeyValueRenderer(sort_keys=False),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    try:
        yield
    finally:
        structlog.configure(**prior)


def _sse_bytes(frames: list[dict | str]) -> bytes:
    """Serialize OpenAI-style SSE frames. ``str`` → raw data line."""
    parts: list[str] = []
    for frame in frames:
        if isinstance(frame, str):
            parts.append(f"data: {frame}\n")
        else:
            parts.append(f"data: {json.dumps(frame)}\n")
        parts.append("\n")
    return "".join(parts).encode("utf-8")


def _tool_call_delta(idx: int, *, call_id: str = "", name: str = "", args: str = "") -> dict:
    """Build one OpenAI tool_calls SSE delta frame."""
    fn: dict = {}
    if name:
        fn["name"] = name
    if args:
        fn["arguments"] = args
    tcd: dict = {"index": idx}
    if call_id:
        tcd["id"] = call_id
    if fn:
        tcd["function"] = fn
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"tool_calls": [tcd]}}],
    }


@pytest.mark.asyncio
async def test_tool_call_args_logged_with_length_and_parse_status(
    caplog, structlog_to_stdlib
):
    """A truncated tool_call args buffer must surface in the dump log line.

    Scenario: SSE drips a single tool_call whose ``arguments`` field is
    cut off mid-string (``{"path": "fo``). After the stream loop, the
    provider should log one entry per tool_buffer with ``args_len``,
    ``args_preview`` and ``parse_ok`` fields. Because the JSON is
    truncated, ``parse_ok`` MUST be ``False``.
    """
    truncated_args = '{"path": "fo'

    def handler(request: httpx.Request) -> httpx.Response:
        frames: list[dict | str] = [
            # First chunk: tool_call header (id + name).
            _tool_call_delta(0, call_id="call_abc", name="write_file"),
            # Second chunk: partial args — stream cuts here.
            _tool_call_delta(0, args=truncated_args),
            # Terminal frame so the SSE consumer exits cleanly.
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse_bytes(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="sk-test",
        model="qwen3.6-plus",
    )
    provider._test_transport = httpx.MockTransport(handler)

    # Drive the streaming entry point — drains until `final` event.
    # No logger= filter: structlog.get_logger() with no name returns a
    # bound logger whose stdlib counterpart is the root logger; scoping
    # caplog to a named logger would silently capture nothing.
    with caplog.at_level(logging.INFO):
        events = []
        async for ev in provider.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{
                "type": "function",
                "function": {"name": "write_file", "parameters": {}},
            }],
        ):
            events.append(ev)

    # Sanity: the final event should still fire (logging is non-fatal).
    assert events, "stream produced no events"
    assert events[-1]["type"] == "final"

    # Find the dump line. structlog routes through stdlib so caplog.text
    # contains the rendered key=value form.
    dump_lines = [
        rec for rec in caplog.records
        if "p5s2_tool_call_args_dump" in rec.getMessage()
    ]
    assert dump_lines, (
        "expected at least one p5s2_tool_call_args_dump log entry; "
        f"saw messages: {[r.getMessage() for r in caplog.records]}"
    )

    payload = dump_lines[-1].msg
    if isinstance(payload, dict):
        assert payload["args_len"] == len(truncated_args)
        assert payload["args_preview"] == truncated_args
        assert payload["parse_ok"] is False
    else:
        text = str(payload)
        assert f"args_len={len(truncated_args)}" in text
        assert f"args_preview={truncated_args!r}" in text
        assert "parse_ok=False" in text


@pytest.mark.asyncio
async def test_stream_stops_after_tool_batch_overflow_sentinel():
    """Runaway SSE tool batches stop at 33 so ReAct can reject and replan."""

    def handler(request: httpx.Request) -> httpx.Response:
        frames: list[dict | str] = [
            _tool_call_delta(
                idx,
                call_id=f"call_{idx}",
                name="tool_search",
                args=json.dumps({"query": f"query-{idx}"}),
            )
            for idx in range(100)
        ]
        frames.append("[DONE]")
        return httpx.Response(
            200,
            content=_sse_bytes(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="sk-test",
        model="qwen3.6-plus",
    )
    provider._test_transport = httpx.MockTransport(handler)

    events = []
    async for event in provider.chat_stream_with_tools(
        [{"role": "user", "content": "hi"}],
        tools=[{
            "type": "function",
            "function": {"name": "tool_search", "parameters": {}},
        }],
    ):
        events.append(event)

    assert events[-1]["type"] == "final"
    assert events[-1]["stop_reason"] == "tool_use"
    assert len(events[-1]["tool_calls"]) == 33
    assert events[-1]["tool_calls"][-1]["id"] == "call_32"


@pytest.mark.asyncio
async def test_stream_preserves_explicit_length_finish_reason_with_tool_call():
    """A truncated provider turn must not be promoted to executable tool_use."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        frames: list[dict | str] = [
            _tool_call_delta(
                0,
                call_id="spawn-1",
                name="tool_activate",
                args=json.dumps({"objective": "build slides"}),
            ),
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "length",
                    }
                ]
            },
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse_bytes(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="sk-test",
        model="sf-glm-5.2",
    )
    provider._test_transport = httpx.MockTransport(handler)

    events = [
        event
        async for event in provider.chat_stream_with_tools(
            [{"role": "user", "content": "build"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "tool_activate",
                    "parameters": {},
                },
            }],
        )
    ]

    assert events[-1]["type"] == "final"
    assert events[-1]["stop_reason"] == "length"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_reason",
    ["length", "max_tokens", "content_filter"],
)
async def test_explicit_truncation_reason_is_not_overridden_by_tool_call(
    finish_reason,
):
    """A partial tool call must not become executable after truncation."""

    def handler(request: httpx.Request) -> httpx.Response:
        frames: list[dict | str] = [
            _tool_call_delta(
                0,
                call_id="call_partial",
                name="tool_activate",
                args=json.dumps({"objective": "build slides"}),
            ),
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": finish_reason,
                }],
            },
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse_bytes(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="sk-test",
        model="sf-glm-5.2",
    )
    provider._test_transport = httpx.MockTransport(handler)

    events = []
    async for event in provider.chat_stream_with_tools(
        [{"role": "user", "content": "make slides"}],
        tools=[{
            "type": "function",
            "function": {"name": "tool_activate", "parameters": {}},
        }],
    ):
        events.append(event)

    assert events[-1]["type"] == "final"
    assert events[-1]["stop_reason"] == finish_reason
    assert len(events[-1]["tool_calls"]) == 1
