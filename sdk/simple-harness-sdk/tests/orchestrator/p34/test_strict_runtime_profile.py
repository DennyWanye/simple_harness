"""Strict wire/counting identity and cold isolation; no credentials or paid calls."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from agent_orchestrator.runtime.deepseek_tokens import DeepSeekV41TokenEstimator
from agent_orchestrator.runtime.model_router import RuntimeProfile
from agent_orchestrator.runtime.tool_gateway import read_tool_schemas
from simple_harness.agents.context.budget import ContextPolicy
from simple_harness.contracts import Message, MessageRole, RequestId
from simple_harness.providers import (
    CancelToken,
    OpenAICompatibleProvider,
    ProviderRequest,
    ProviderToolSpec,
    Secret,
)
from simple_harness.providers.openai_compatible import openai_chat_request_payload

MODEL = "deepseek-flash"
STRICT = "deepseek-strict-v1"
BETA = "https://api.deepseek.com/beta"
LEGACY_FINGERPRINT = "deepseek-v41:0f06e5994d18d90023a59966c593aef152d66a0679f0df56c35e7268e15c959d"


def test_strict_wire_body_survives_canonical_durable_request_roundtrip():
    from simple_harness.contracts import canonical_json
    from simple_harness.execution.provider_invocations import (
        provider_request_from_json,
        provider_request_json,
    )

    request = ProviderRequest(RequestId("strict-canonical-cold"), (
        Message(MessageRole.USER, "Preserve exact schema branch order across cold recovery."),
    ), tools=tuple(ProviderToolSpec(n, "Tool", s)
                   for n, s in read_tool_schemas(large=True).items()))
    cold = provider_request_from_json(
        request.request_id, json.loads(canonical_json(provider_request_json(request))),
    )
    assert openai_chat_request_payload(request, model=MODEL, tool_schema_mode=STRICT) == (
        openai_chat_request_payload(cold, model=MODEL, tool_schema_mode=STRICT)
    )


@pytest.fixture
def tokenizer_path():
    path = os.environ.get("SH_TOKENIZER_PATH")
    if not path:
        pytest.skip("requires pinned tokenizer supplied by parent runner")
    return Path(path)


def _provider(client, strict):
    return OpenAICompatibleProvider(
        client, BETA if strict else "https://api.deepseek.com", MODEL, Secret("fake-only"),
        tool_schema_mode=STRICT if strict else "legacy",
    )


def test_exact_counter_and_http_share_strict_body_and_legacy_identity(tokenizer_path, monkeypatch):
    import agent_orchestrator.runtime.deepseek_tokens as counting

    legacy = DeepSeekV41TokenEstimator(tokenizer_path)
    strict = DeepSeekV41TokenEstimator(tokenizer_path, tool_schema_mode=STRICT)
    assert legacy.fingerprint == LEGACY_FINGERPRINT
    assert strict.fingerprint != legacy.fingerprint
    assert strict.requires_prior_output_reserve
    serialized = []
    original = counting.openai_chat_request_payload

    def record(request, **kwargs):
        body = original(request, **kwargs)
        serialized.append(body)
        return body

    monkeypatch.setattr(counting, "openai_chat_request_payload", record)
    tools = tuple(ProviderToolSpec(n, "Use exact tool arguments.", s)
                  for n, s in read_tool_schemas(large=True).items())
    request = ProviderRequest(RequestId("strict-wire-count"), (
        Message(MessageRole.USER, "Read test.py and preserve optional argument semantics."),
    ), tools=tools, max_output_tokens=32768)
    assert strict.estimate_input_tokens(request) > 0
    counted = serialized[-1]
    assert all(t["function"]["strict"] is True for t in counted["tools"])
    sent = []

    def transport(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "call-read", "type": "function", "function": {
                        "name": "workspace_read_file", "arguments": '{"path":"test.py"}',
                    },
                }],
            }}], "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        })

    async def case():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            provider = _provider(client, True)
            response = await provider.invoke(request, cancel=CancelToken())
            assert dict(response.tool_calls[0].arguments) == {"path": "test.py"}
            assert provider.target.adapter_key.endswith(".deepseek-strict-v1")
    asyncio.run(case())
    assert sent == [counted]
    # No strict metadata is injected into the durable logical request or legacy wire.
    assert request.metadata == {}
    assert all("strict" not in tool["function"]
               for tool in original(request, model=MODEL)["tools"])


@pytest.mark.parametrize("provider_strict,counter_mode", [(True, "legacy"), (False, STRICT)])
def test_profile_rejects_mismatched_wire_counter_before_any_call(
    tokenizer_path, provider_strict, counter_mode,
):
    async def case():
        async with httpx.AsyncClient() as client:
            counter = DeepSeekV41TokenEstimator(tokenizer_path, tool_schema_mode=counter_mode)
            with pytest.raises(ValueError, match="tool schema modes must match"):
                RuntimeProfile("mismatch", _provider(client, provider_strict), MODEL,
                               context_policy=ContextPolicy(), tokenizer=counter)
    asyncio.run(case())
