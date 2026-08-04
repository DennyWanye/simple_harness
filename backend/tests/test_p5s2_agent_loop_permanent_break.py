# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Permanent tool outcomes are outside AgentLoop's ownership boundary.

AgentLoop can only expose requested calls.  ReActDriver/Kernel executes and
classifies the resulting outcome, including permanent and transient failures.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import AgentLoop, ErrorEvent, ToolBatchEvent
from llm.types import ChatResponse, ChatUsage, ToolCall


class _ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def chat_with_fallback(
        self, messages: list[dict[str, Any]], **_: Any
    ) -> ChatResponse:
        self.call_count += 1
        if not self._responses:
            raise AssertionError("AgentLoop crossed the tool-batch pause point")
        return self._responses.pop(0)


class _NoDispatchTools:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def schemas(self, enabled_toolsets: Any = None) -> list[dict[str, Any]]:
        return []

    async def execute_tool(self, name: str, *_: Any, **__: Any) -> Any:
        self.calls.append(name)
        raise AssertionError("AgentLoop must not execute tools")


def _response(*calls: ToolCall) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=list(calls),
        stop_reason="tool_use",
        model="stub",
        usage=ChatUsage(input_tokens=1, output_tokens=1),
    )


async def _run(*calls: ToolCall, max_iterations: int = 50):
    llm = _ScriptedLLM([_response(*calls)])
    tools = _NoDispatchTools()
    events = [
        event
        async for event in AgentLoop(
            llm, tools, max_iterations=max_iterations
        ).run([{"role": "user", "content": "go"}], session_id="s")
    ]
    return llm, tools, events


@pytest.mark.asyncio
async def test_missing_argument_call_is_exposed_without_preclassification() -> None:
    llm, tools, events = await _run(
        ToolCall(id="c1", name="write_file", arguments={})
    )

    assert llm.call_count == 1
    assert tools.calls == []
    assert isinstance(events[-1], ToolBatchEvent)
    assert not any(isinstance(event, ErrorEvent) for event in events)


@pytest.mark.asyncio
async def test_high_iteration_limit_does_not_cross_external_pause() -> None:
    llm, tools, events = await _run(
        ToolCall(id="c1", name="write_file", arguments={}),
        max_iterations=50,
    )

    assert llm.call_count == 1
    assert tools.calls == []
    assert isinstance(events[-1], ToolBatchEvent)


@pytest.mark.asyncio
async def test_transient_outcome_classification_is_not_guessed_by_agent_loop() -> None:
    _, tools, events = await _run(
        ToolCall(id="c1", name="web_search", arguments={"q": "deskpet"})
    )

    assert tools.calls == []
    assert isinstance(events[-1], ToolBatchEvent)


@pytest.mark.asyncio
async def test_unknown_tool_is_still_an_external_batch_call() -> None:
    _, tools, events = await _run(
        ToolCall(id="c1", name="do_magic", arguments={})
    )

    assert tools.calls == []
    assert events[-1].tool_calls[0].name == "do_magic"


@pytest.mark.asyncio
async def test_only_first_requested_batch_is_returned() -> None:
    llm = _ScriptedLLM([
        _response(ToolCall(id="c1", name="read_file", arguments={"path": "a"})),
        _response(ToolCall(id="c2", name="write_file", arguments={})),
    ])
    tools = _NoDispatchTools()
    events = [
        event
        async for event in AgentLoop(llm, tools).run(
            [{"role": "user", "content": "go"}], session_id="s"
        )
    ]

    assert llm.call_count == 1
    assert tools.calls == []
    assert [call.name for call in events[-1].tool_calls] == ["read_file"]


@pytest.mark.asyncio
async def test_concurrent_requests_remain_one_ordered_batch() -> None:
    _, tools, events = await _run(
        ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}),
        ToolCall(id="c2", name="write_file", arguments={}),
    )

    batch = next(event for event in events if isinstance(event, ToolBatchEvent))
    assert [call.id for call in batch.tool_calls] == ["c1", "c2"]
    assert tools.calls == []
