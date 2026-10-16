from __future__ import annotations

import pytest
from agent.agent_loop import AgentLoop, FinalEvent
from llm.types import ChatResponse, ChatUsage


class _Tools:
    def schemas(self, enabled_toolsets=None):
        return []


class _LLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat_with_fallback(
        self,
        messages,
        tools=None,
        model=None,
        **kwargs,
    ):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "kwargs": kwargs,
            }
        )
        return self.responses.pop(0)


def _response(content: str, *, input_tokens: int = 2, output_tokens: int = 1):
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=ChatUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


@pytest.mark.asyncio
async def test_agent_loop_without_quality_gate_keeps_single_provider_call() -> None:
    llm = _LLM([_response("简短回答")])
    loop = AgentLoop(llm_registry=llm, tool_registry=_Tools())

    events = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "你好"}],
            loop_user_request="你好",
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    assert events[-1].content == "简短回答"
    assert len(llm.calls) == 1
