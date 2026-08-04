# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unit tests for OpenAICompatibleProvider (P2-1-S1).

Covers:
- Protocol conformance (runtime-checkable LLMProvider)
- chat_stream against a mocked SSE transport (no network)
- health_check against mocked /models endpoints
- Two integration tests (skip if endpoints offline / no api key)
"""
from __future__ import annotations

import json
import os
import asyncio

import httpx
import pytest

from providers.base import LLMProvider
from providers.openai_compatible import OpenAICompatibleProvider


def test_openai_compatible_implements_protocol():
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4:e4b",
    )
    assert isinstance(provider, LLMProvider)


@pytest.mark.asyncio
async def test_stream_attempt_times_out_without_model_progress():
    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._stream_attempt_timeout_s = 0.01

    async def never_finishes(*_args, **_kwargs):
        await asyncio.Event().wait()
        yield {"type": "final"}  # pragma: no cover

    provider._stream_one_attempt = never_finishes

    with pytest.raises(httpx.ReadTimeout, match="no model progress"):
        async for _ in provider._stream_one_attempt_with_deadline({}):
            pass


@pytest.mark.asyncio
async def test_stream_attempt_deadline_resets_after_each_model_event():
    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._stream_attempt_timeout_s = 0.1

    async def progresses_longer_than_one_deadline(*_args, **_kwargs):
        for index in range(4):
            await asyncio.sleep(0.03)
            yield {"type": "reasoning", "text": str(index)}
        yield {"type": "final"}

    provider._stream_one_attempt = progresses_longer_than_one_deadline

    events = [
        event
        async for event in provider._stream_one_attempt_with_deadline({})
    ]

    assert [event["type"] for event in events] == [
        "reasoning",
        "reasoning",
        "reasoning",
        "reasoning",
        "final",
    ]


@pytest.mark.asyncio
async def test_stream_attempt_closes_source_after_progress_timeout():
    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._stream_attempt_timeout_s = 0.01
    closed = asyncio.Event()

    async def stalls_after_progress(*_args, **_kwargs):
        try:
            yield {"type": "reasoning", "text": "working"}
            await asyncio.Event().wait()
        finally:
            closed.set()

    provider._stream_one_attempt = stalls_after_progress

    with pytest.raises(httpx.ReadTimeout, match="no model progress"):
        async for _ in provider._stream_one_attempt_with_deadline({}):
            pass

    assert closed.is_set()


@pytest.mark.asyncio
async def test_health_check_returns_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"id": "any-model"}]})

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    transport = httpx.MockTransport(handler)
    # Inject the mock transport via a provider hook (see impl below).
    provider._test_transport = transport
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_returns_false_on_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._test_transport = httpx.MockTransport(handler)
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_returns_false_on_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._test_transport = httpx.MockTransport(handler)
    assert await provider.health_check() is False


def _sse(frames: list[dict | str]) -> bytes:
    """Serialize OpenAI-style SSE frames. A str entry is treated as raw data (e.g. '[DONE]')."""
    lines: list[str] = []
    for frame in frames:
        if isinstance(frame, str):
            lines.append(f"data: {frame}\n")
        else:
            lines.append(f"data: {json.dumps(frame)}\n")
        lines.append("\n")
    return "".join(lines).encode("utf-8")


def _delta(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }


@pytest.mark.asyncio
async def test_chat_stream_yields_tokens_in_order():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        body = _sse([_delta("Hello"), _delta(" "), _delta("world"), "[DONE]"])
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="sk-test",
        model="qwen3.6-plus",
    )
    provider._test_transport = httpx.MockTransport(handler)

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
    ):
        tokens.append(tok)

    assert tokens == ["Hello", " ", "world"]
    assert captured["url"] == "http://example.invalid/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "qwen3.6-plus"
    assert captured["body"]["stream"] is True
    assert captured["body"]["max_tokens"] == 32
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_chat_stream_skips_empty_and_missing_content_deltas():
    """Role-only opening delta and empty content chunks must not emit tokens."""

    def handler(request: httpx.Request) -> httpx.Response:
        frames = [
            # First frame is role-only (no 'content') — OpenAI sends this.
            {
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            },
            _delta(""),        # empty string — skip
            _delta("abc"),
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)

    tokens = [t async for t in provider.chat_stream([{"role": "user", "content": "x"}])]
    assert tokens == ["abc"]


@pytest.mark.asyncio
async def test_chat_stream_respects_explicit_temperature_override():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(["[DONE]"]),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
        temperature=0.7,
    )
    provider._test_transport = httpx.MockTransport(handler)
    async for _ in provider.chat_stream(
        [{"role": "user", "content": "x"}],
        temperature=0.2,
    ):
        pass
    assert captured["body"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_kimi_k3_tool_stream_uses_required_temperature_one():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(["[DONE]"]),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="kimi-k3",
        temperature=0.7,
    )
    provider._test_transport = httpx.MockTransport(handler)

    async for _ in provider.chat_stream_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[{"type": "function", "function": {"name": "demo"}}],
        temperature=0.2,
    ):
        pass

    assert captured["body"]["temperature"] == 1.0


@pytest.mark.asyncio
async def test_chat_stream_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="wrong",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(httpx.HTTPStatusError):
        async for _ in provider.chat_stream([{"role": "user", "content": "x"}]):
            pass


@pytest.mark.asyncio
async def test_tool_stream_preserves_structured_catalog_stale_error():
    from llm.errors import LLMProviderError

    class DeferredErrorBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(
                {
                    "error": {
                        "message": "tool_catalog_stale",
                        "type": "scenario_error",
                        "code": "tool_catalog_stale",
                    }
                }
            ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            headers={"content-type": "application/json"},
            stream=DeferredErrorBody(),
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(LLMProviderError) as ei:
        async for _ in provider._chat_stream_with_tools_impl(
            [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "demo"}}],
        ):
            pass
    assert ei.value.status_code == 409
    assert ei.value.error_class == "tool_catalog_stale"
    assert "tool_catalog_stale" in str(ei.value)


@pytest.mark.asyncio
async def test_at_most_once_tool_stream_preserves_http_error_body():
    """A dispatch handoff forbids retry but must not discard relay details."""
    from llm.errors import LLMProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "unsupported request field: tools"}},
        )

    class NonIdempotentSnapshot:
        provider_id = "relay-cloud"
        adapter_id = "openai-compatible"
        adapter_version = "v1"
        supports_idempotent_launch = False
        token_field = None

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider.provider_id = "relay-cloud"
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(LLMProviderError) as ei:
        async for _ in provider._chat_stream_with_tools_impl(
            [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "demo"}}],
            launch_operation_id="launch-1",
            provider_launch_snapshot=NonIdempotentSnapshot(),
        ):
            pass
    assert ei.value.status_code == 400
    assert "unsupported request field: tools" in str(ei.value)


# --------------------------------------------------------------------------
# P2-1-S8 — last_usage capture from the OpenAI stream_options.include_usage
# terminal chunk. The provider must record it for BillingLedger to bill.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_stream_captures_usage():
    """OpenAI/DashScope emit a terminal chunk with `usage` when include_usage
    is set. The provider must stash it in self.last_usage."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        # Shape matches what OpenAI/DashScope actually emit: the last data
        # frame has empty choices and a populated usage.
        frames = [
            _delta("hi"),
            {
                "id": "chatcmpl",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)
    tokens = [t async for t in provider.chat_stream(
        [{"role": "user", "content": "q"}],
    )]
    assert tokens == ["hi"]
    assert provider.last_usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    # Sanity: include_usage must be in the outgoing body.
    assert captured["body"]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_chat_stream_last_usage_resets_when_absent():
    """Second call without a usage chunk must leave last_usage=None, not
    reuse the previous stream's usage."""
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if calls[0] == 0:
            frames = [
                _delta("a"),
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
                "[DONE]",
            ]
        else:
            # Ollama-style: no usage frame at all.
            frames = [_delta("b"), "[DONE]"]
        calls[0] += 1
        return httpx.Response(
            200,
            content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)
    _ = [t async for t in provider.chat_stream([{"role": "user", "content": "q"}])]
    assert provider.last_usage is not None
    _ = [t async for t in provider.chat_stream([{"role": "user", "content": "q"}])]
    assert provider.last_usage is None


# --------------------------------------------------------------------------
# Integration tests — skipped by default unless the endpoint is reachable.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_ollama_v1_roundtrip():
    """Hits local Ollama's OpenAI-compatible endpoint. Skipped if not running."""
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model=os.environ.get("DESKPET_OLLAMA_MODEL", "gemma4:e4b"),
    )
    if not await provider.health_check():
        pytest.skip("Ollama /v1 not reachable — start ollama or set DESKPET_OLLAMA_MODEL")

    # A healthy Ollama daemon may not have this test's default model pulled.
    # Treat that as an unavailable integration fixture, not a provider failure.
    async with provider._client(timeout=5.0) as client:
        models_response = await client.get(f"{provider.base_url}/models")
    models_payload = models_response.json()
    model_rows = models_payload.get("data") or models_payload.get("models") or []
    model_ids = {
        str(row.get("id") or row.get("name") or row.get("model"))
        for row in model_rows
        if isinstance(row, dict)
        and (row.get("id") or row.get("name") or row.get("model"))
    }
    if provider.model not in model_ids:
        pytest.skip(
            f"Ollama model {provider.model!r} is not installed; "
            "set DESKPET_OLLAMA_MODEL to an available model"
        )

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "Reply with the single word: ping"}],
        max_tokens=16,
    ):
        tokens.append(tok)
    joined = "".join(tokens).lower()
    assert "ping" in joined


@pytest.mark.asyncio
async def test_integration_dashscope_roundtrip():
    """Hits DashScope compat-mode endpoint. Skipped if DESKPET_DASHSCOPE_KEY unset."""
    api_key = os.environ.get("DESKPET_DASHSCOPE_KEY")
    if not api_key:
        pytest.skip("DESKPET_DASHSCOPE_KEY not set — skipping live cloud test")

    provider = OpenAICompatibleProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=api_key,
        model=os.environ.get("DESKPET_DASHSCOPE_MODEL", "qwen3.6-plus"),
    )
    if not await provider.health_check():
        pytest.skip("DashScope /models 非 200 — 可能是密钥无效或网络问题")

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "请用一个字回答：好"}],
        max_tokens=8,
    ):
        tokens.append(tok)
    assert len("".join(tokens)) >= 1


# --------------------------------------------------------------------------
# WI-R5 — relay error classification wired into chat_with_tools.
# A relay 402 / 401 must surface as LLMProviderError.error_class so the
# chat layer can show a friendly 余额不足 message / drive the key retry.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_with_tools_classifies_relay_402_insufficient_balance():
    from llm.errors import LLMProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"message": "insufficient balance"}})

    provider = OpenAICompatibleProvider(
        base_url="https://your-llm-relay.example.com/v1",
        api_key="tsk_x",
        model="gpt-5.5",
        is_relay=True,
    )
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(LLMProviderError) as ei:
        await provider.chat_with_tools([{"role": "user", "content": "hi"}])
    assert ei.value.error_class == "insufficient_balance"


@pytest.mark.asyncio
async def test_chat_with_tools_classifies_relay_401_key_invalid():
    from llm.errors import LLMProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    provider = OpenAICompatibleProvider(
        base_url="https://your-llm-relay.example.com/v1",
        api_key="tsk_stale",
        model="gpt-5.5",
        is_relay=True,
    )
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(LLMProviderError) as ei:
        await provider.chat_with_tools([{"role": "user", "content": "hi"}])
    assert ei.value.error_class == "relay_key_invalid"
