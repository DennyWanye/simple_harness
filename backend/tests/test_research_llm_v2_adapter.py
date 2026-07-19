from __future__ import annotations

import httpx
import pytest

from deskpet.tools import research_tools
from deskpet.workflows.contracts import NodeExecutionIdentity, WorkflowContext
from deskpet.workflows.definitions.research_core import (
    ResearchCallEffectPort,
    ResearchLLMResult,
)
from providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_provider_at_most_once_does_not_retry_ambiguous_transport() -> None:
    attempts = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("ambiguous transport failure", request=request)

    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:9999/v1",
        api_key="ollama",
        model="test-model",
    )
    provider._test_transport = httpx.MockTransport(fail)

    with pytest.raises(httpx.ConnectError, match="ambiguous transport failure"):
        await provider.chat_with_tools_at_most_once(
            [{"role": "user", "content": "hello"}],
            tools=[],
            max_tokens=32,
        )

    assert attempts == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {
                "content": "answer",
                "model": "relay-model",
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 40},
                },
                "id": "chatcmpl-1",
            },
            (120, 30, 40, "provider", "chatcmpl-1"),
        ),
        (
            {
                "content": "answer",
                "model": "relay-model",
                "usage": {
                    "input_tokens": 90,
                    "output_tokens": 20,
                    "cached_tokens": 10,
                },
                "request_id": "relay-request-1",
            },
            (90, 20, 10, "provider", "relay-request-1"),
        ),
        (
            {"content": "answer", "model": "relay-model", "usage": {}},
            (None, None, None, "usage_unknown", None),
        ),
    ],
)
def test_provider_usage_shapes_are_normalized_without_fake_zeroes(
    response: dict,
    expected: tuple[int | None, int | None, int | None, str, str | None],
) -> None:
    result = research_tools._research_llm_result_from_provider_response(
        response,
        fallback_model="fallback-model",
    )

    assert (
        result.input_tokens,
        result.output_tokens,
        result.cache_tokens,
        result.usage_source,
        result.request_id,
    ) == expected
    assert ResearchLLMResult.from_json(result.to_json()) == result


@pytest.mark.asyncio
async def test_research_effect_port_requires_context_node_identity() -> None:
    calls: list[NodeExecutionIdentity] = []

    async def complete_call(**kwargs) -> ResearchLLMResult:
        calls.append(kwargs["execution_identity"])
        return ResearchLLMResult(
            content="ok",
            model="model-a",
            input_tokens=2,
            output_tokens=1,
            cache_tokens=0,
            usage_source="provider",
            request_id=None,
        )

    port = ResearchCallEffectPort(complete_call)
    identity = NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v5",
        thread_id="thread-1",
        run_id="run-1",
        checkpoint_id="checkpoint-1",
        checkpoint_ns="",
        task_id="task-1",
        node_id="quality_audit",
        attempt=1,
    )
    node_context = WorkflowContext().for_node(identity)

    result = await port.complete(
        role="quality_audit",
        payload_ref="sha256:prompt",
        max_output_tokens=128,
        stable_call_id="quality-audit-1",
        execution_identity=node_context.identity,
    )

    assert result.content == "ok"
    assert calls == [identity]

    with pytest.raises(TypeError, match="requires context.identity"):
        await port.complete(
            role="quality_audit",
            payload_ref="sha256:prompt",
            max_output_tokens=128,
            stable_call_id="quality-audit-2",
            execution_identity=None,
        )
    assert calls == [identity]


@pytest.mark.asyncio
async def test_v2_live_bridge_is_independent_from_legacy_bridge() -> None:
    async def legacy(_prompt: str) -> str:
        return "legacy"

    async def v2(
        _prompt: str,
        *,
        max_output_tokens: int,
        stable_call_id: str,
    ) -> ResearchLLMResult:
        assert max_output_tokens == 64
        assert stable_call_id == "call-1"
        return ResearchLLMResult(
            content="v2",
            model="model-v2",
            input_tokens=None,
            output_tokens=None,
            cache_tokens=None,
            usage_source="usage_unknown",
            request_id=None,
        )

    research_tools.set_live_llm_call(legacy)
    research_tools.set_live_llm_call_v2(v2)
    try:
        assert await research_tools._resolve_default_llm_call() is legacy
        resolved = await research_tools._resolve_default_llm_call_v2()
        result = await resolved(
            "prompt",
            max_output_tokens=64,
            stable_call_id="call-1",
        )
        assert result.content == "v2"
    finally:
        research_tools.set_live_llm_call(None)
        research_tools.set_live_llm_call_v2(None)
