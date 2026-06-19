from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent.agent_loop import AgentLoop, FinalEvent
from llm.types import ChatResponse, ToolCall
from providers.openai_compatible import OpenAICompatibleProvider


def _raw_response(
    *,
    stop_reason: str = "end_turn",
    content: str = "done",
    value: int = 1,
) -> dict:
    tool_calls = []
    finish_reason = "stop"
    if stop_reason == "tool_use":
        finish_reason = "tool_calls"
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "noop",
                    "arguments": json.dumps({"value": value}),
                },
            }
        ]
    return {
        "content": content,
        "reasoning_content": "",
        "tool_calls": [
            {"id": "call_1", "name": "noop", "arguments": {"value": value}}
        ] if stop_reason == "tool_use" else [],
        "stop_reason": stop_reason,
        "model": "stub-model",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "_openai": {
            "id": "cmpl_1",
            "model": "stub-model",
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    }


class _Tools:
    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:  # noqa: ARG002
        return [
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "noop",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def dispatch(self, name: str, args: dict, task_id: str) -> str:  # noqa: ARG002
        return json.dumps({"ok": True}, ensure_ascii=False)


class _ChainProvider:
    model = "stub-model"

    def __init__(self) -> None:
        self.tool_choices: list[str | None] = []
        self.calls = 0

    async def chat_with_tools(
        self,
        messages: list[dict],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> dict:
        self.calls += 1
        self.tool_choices.append(tool_choice)
        stop = "end_turn" if self.calls >= 30 else "tool_use"
        return _raw_response(
            stop_reason=stop,
            content=f"turn {self.calls}",
            value=self.calls,
        )


class _FallbackLLM:
    def __init__(self) -> None:
        self.tool_choices: list[str | None] = []
        self.calls = 0

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        self.tool_choices.append(kwargs.get("tool_choice"))
        stop = "end_turn" if self.calls >= 30 else "tool_use"
        return ChatResponse(
            content=f"turn {self.calls}",
            stop_reason=stop,
            model="stub-model",
            tool_calls=[
                ToolCall(id="call_1", name="noop", arguments={"value": self.calls})
            ] if stop == "tool_use" else [],
        )


class _StreamingLLM(_FallbackLLM):
    async def chat_with_fallback_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ):
        self.calls += 1
        self.tool_choices.append(kwargs.get("tool_choice"))
        stop = "end_turn" if self.calls >= 30 else "tool_use"
        yield {
            "type": "final",
            "content": f"turn {self.calls}",
            "reasoning_content": "",
            "tool_calls": [
                {"id": "call_1", "name": "noop", "arguments": {"value": self.calls}}
            ] if stop == "tool_use" else [],
            "stop_reason": stop,
            "model": "stub-model",
            "usage": {},
        }


async def _collect(agen):
    out = []
    async for ev in agen:
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_provider_passes_tool_choice_none() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json=_raw_response(stop_reason="end_turn")["_openai"],
        )

    provider = OpenAICompatibleProvider("https://relay.example/v1", "k", "m")
    provider._test_transport = httpx.MockTransport(handler)

    async for _ in provider.chat_stream_with_tools(
        [{"role": "user", "content": "hi"}],
        tools=_Tools().schemas(),
        tool_choice="none",
    ):
        pass

    assert bodies[0]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_provider_default_tool_choice_is_auto() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json=_raw_response(stop_reason="end_turn")["_openai"],
        )

    provider = OpenAICompatibleProvider("https://relay.example/v1", "k", "m")
    provider._test_transport = httpx.MockTransport(handler)

    async for _ in provider.chat_stream_with_tools(
        [{"role": "user", "content": "hi"}],
        tools=_Tools().schemas(),
    ):
        pass

    assert bodies[0]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_tier3_forces_tool_choice_none_in_provider_chain() -> None:
    provider = _ChainProvider()
    loop = AgentLoop(None, _Tools(), max_iterations=31)

    events = await _collect(
        loop.run(
            [{"role": "user", "content": "go"}],
            provider_chain=[provider],
        )
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert provider.tool_choices[29] == "none"


@pytest.mark.asyncio
async def test_tier3_forces_tool_choice_none_in_stream_path() -> None:
    llm = _StreamingLLM()
    loop = AgentLoop(llm, _Tools(), max_iterations=31)

    events = await _collect(
        loop.run([{"role": "user", "content": "go"}], stream=True)
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert llm.tool_choices[29] == "none"


@pytest.mark.asyncio
async def test_tier3_forces_tool_choice_none_in_nonstream_path() -> None:
    llm = _FallbackLLM()
    loop = AgentLoop(llm, _Tools(), max_iterations=31)

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert llm.tool_choices[29] == "none"


@pytest.mark.asyncio
async def test_force_finish_flag_off_never_forces_none() -> None:
    llm = _FallbackLLM()
    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=31,
        force_finish_via_tool_choice=False,
    )

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert "none" not in llm.tool_choices


@pytest.mark.asyncio
async def test_tier3_suppresses_completion_nudge() -> None:
    llm = _FallbackLLM()
    probe_calls = 0

    async def completion_probe(session_id: str) -> list[dict]:  # noqa: ARG001
        nonlocal probe_calls
        probe_calls += 1
        return [{"content": "unfinished", "status": "pending"}]

    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=31,
        completion_probe=completion_probe,
        max_completion_nudges=2,
    )

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert probe_calls == 0
