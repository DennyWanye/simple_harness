from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
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
        "tool_calls": [{"id": "call_1", "name": "noop", "arguments": {"value": value}}]
        if stop_reason == "tool_use"
        else [],
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
        stop = "end_turn" if tool_choice == "none" or self.calls >= 30 else "tool_use"
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
        stop = "end_turn" if kwargs.get("tool_choice") == "none" or self.calls >= 30 else "tool_use"
        return ChatResponse(
            content=f"turn {self.calls}",
            stop_reason=stop,
            model="stub-model",
            tool_calls=[ToolCall(id="call_1", name="noop", arguments={"value": self.calls})]
            if stop == "tool_use"
            else [],
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
        stop = "end_turn" if kwargs.get("tool_choice") == "none" or self.calls >= 30 else "tool_use"
        yield {
            "type": "final",
            "content": f"turn {self.calls}",
            "reasoning_content": "",
            "tool_calls": [{"id": "call_1", "name": "noop", "arguments": {"value": self.calls}}]
            if stop == "tool_use"
            else [],
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


class _AlwaysEndTurnLLM:
    """Every turn returns plain end_turn text → always reaches the verify gate."""

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
        return ChatResponse(
            content=f"claim turn {self.calls}",
            stop_reason="end_turn",
            model="stub-model",
            tool_calls=[],
        )


class _FakeClaim:
    def __init__(self, raw_text: str = "I created report.pptx") -> None:
        self.raw_text = raw_text
        self.reason = "no_receipt"
        self.pattern_id = "p1"


class _FailVerifyOutcome:
    passed = False
    unmatched_claims = [_FakeClaim()]
    goal_alignment = None


class _FailVerifyGate:
    """Verify gate that never matches → forces exhaustion path (B5)."""

    mode = "strict"

    def check(self, *, assistant_text: str, ledger: Any, goal_text: Any = None):  # noqa: ARG002
        return _FailVerifyOutcome()

    def build_rebound_message(self, unmatched: Any) -> str:  # noqa: ARG002
        return "rebound: please prove your claims"

    async def consult_ephemeral_subagent(
        self,
        *,
        ledger: Any,
        failed_claims: Any,
        assistant_text: Any,  # noqa: ARG002
    ) -> bool:
        return False
