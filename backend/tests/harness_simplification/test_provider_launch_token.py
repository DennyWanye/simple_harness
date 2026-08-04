from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from agent.agent_loop import AgentLoop, FinalEvent
from agent.tool_use_shim import OpenAICompatibleAgentLLM
from deskpet.execution.contracts import ActorContext
from deskpet.harness.drivers.react import AgentLoopCollaborator, ReactFinal
from deskpet.harness.live_index import BoundedLiveIndex
from deskpet.harness.ports import DriverStart
from llm.types import ChatResponse
from providers.openai_compatible import OpenAICompatibleProvider


@dataclass(frozen=True)
class _LaunchSnapshot:
    provider_id: str = "relay"
    adapter_id: str = "openai-compatible"
    adapter_version: str = "v1"
    supports_idempotent_launch: bool = True
    token_field: str | None = "header:Idempotency-Key"


class _Tools:
    def schemas(self, enabled_toolsets=None):  # noqa: ANN001, ANN201, ARG002
        return []


def _response() -> httpx.Response:
    return httpx.Response(200, json={
        "model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "done"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })


@pytest.mark.asyncio
async def test_agent_loop_launch_token_reaches_the_real_adapter_request() -> None:
    requests: list[httpx.Request] = []
    provider = OpenAICompatibleProvider("https://relay.example/v1", "k", "m")
    provider.provider_id = "relay"
    provider._test_transport = httpx.MockTransport(
        lambda request: requests.append(request) or _response()
    )
    collaborator = AgentLoopCollaborator(
        lambda _request: AgentLoop(OpenAICompatibleAgentLLM(provider), _Tools())
    )
    live = BoundedLiveIndex()
    live.add("run-1", ActorContext("user", "session-1", 0, "run-1"))
    collaborator.bind_live_index(live)
    events = [event async for event in collaborator.start(DriverStart(
        run_id="run-1", session_id="session-1",
        canonical_messages=({"role": "user", "content": "hi"},),
        launch_operation_id="launch-123", provider_launch_snapshot=_LaunchSnapshot(),
    ))]
    assert any(isinstance(event, ReactFinal) for event in events)
    assert len(requests) == 1
    assert requests[0].headers["Idempotency-Key"] == "launch-123"


@pytest.mark.asyncio
async def test_non_idempotent_launch_never_retries_an_ambiguous_send() -> None:
    calls = 0

    def fail(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("ambiguous")

    provider = OpenAICompatibleProvider("https://relay.example/v1", "k", "m")
    provider.provider_id = "relay"
    provider._test_transport = httpx.MockTransport(fail)
    snapshot = _LaunchSnapshot(supports_idempotent_launch=False, token_field=None)
    with pytest.raises(httpx.ReadError, match="ambiguous"):
        _ = [event async for event in AgentLoop(
            OpenAICompatibleAgentLLM(provider), _Tools()
        ).run(
            [{"role": "user", "content": "hi"}], stream=True,
            launch_operation_id="launch-unsafe", provider_launch_snapshot=snapshot,
        )]
    assert calls == 1


@pytest.mark.asyncio
async def test_absent_launch_token_keeps_legacy_llm_signature_compatible() -> None:
    class LegacyLLM:
        async def chat_with_fallback(self, messages, tools=None, model=None):  # noqa: ANN001, ANN201, ARG002
            return ChatResponse(content="done", stop_reason="end_turn", model="m")

    events = [event async for event in AgentLoop(LegacyLLM(), _Tools()).run(
        [{"role": "user", "content": "hi"}]
    )]
    assert any(isinstance(event, FinalEvent) for event in events)


@pytest.mark.asyncio
async def test_frozen_adapter_identity_mismatch_fails_before_send() -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response()

    provider = OpenAICompatibleProvider("https://relay.example/v1", "k", "m")
    provider.provider_id = "other"
    provider._test_transport = httpx.MockTransport(respond)
    with pytest.raises(ValueError, match="identity mismatch"):
        _ = [event async for event in provider.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}], launch_operation_id="launch-123",
            provider_launch_snapshot=_LaunchSnapshot(),
        )]
    assert calls == 0
