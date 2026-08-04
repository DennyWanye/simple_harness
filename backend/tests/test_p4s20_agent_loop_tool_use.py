# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""AgentLoop's external tool-batch boundary.

Tool permission checks, physical execution, result normalization, and resume
belong to ReActDriver/Kernel.  These tests deliberately provide registries
whose execution entrypoints fail if AgentLoop calls them.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import AgentLoop, ToolBatchEvent
from llm.types import ChatResponse, ChatUsage as Usage, ToolCall


class _ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def chat_with_fallback(
        self, messages: list[dict[str, Any]], **_: Any
    ) -> ChatResponse:
        self.call_count += 1
        if not self._responses:
            raise AssertionError("AgentLoop crossed the external pause point")
        return self._responses.pop(0)


class _V2Registry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def schemas(self, enabled_toolsets: Any = None) -> list[dict[str, Any]]:
        return []

    async def execute_tool(self, name: str, *_: Any, **__: Any) -> Any:
        self.calls.append(name)
        raise AssertionError("AgentLoop must not call execute_tool")


class _LegacyRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def schemas(self, enabled_toolsets: Any = None) -> list[dict[str, Any]]:
        return []

    def dispatch(self, name: str, *_: Any, **__: Any) -> Any:
        self.calls.append(name)
        raise AssertionError("AgentLoop must not call legacy dispatch")


def _tool_response(*calls: ToolCall) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=list(calls),
        stop_reason="tool_use",
        usage=Usage(),
        model="stub",
    )


@pytest.mark.asyncio
async def test_agent_loop_never_calls_execute_tool() -> None:
    registry = _V2Registry()
    llm = _ScriptedLLM([
        _tool_response(ToolCall(id="c1", name="todo_create", arguments={"text": "x"}))
    ])
    events = [
        event
        async for event in AgentLoop(llm, registry).run(
            [{"role": "user", "content": "create todo"}], session_id="s1"
        )
    ]

    batch = next(event for event in events if isinstance(event, ToolBatchEvent))
    assert [(call.name, call.arguments) for call in batch.tool_calls] == [
        ("todo_create", {"text": "x"})
    ]
    assert registry.calls == []


@pytest.mark.asyncio
async def test_agent_loop_does_not_own_permission_denial() -> None:
    registry = _V2Registry()
    llm = _ScriptedLLM([
        _tool_response(ToolCall(id="c1", name="os_run", arguments={"command": "echo ok"}))
    ])
    events = [
        event
        async for event in AgentLoop(llm, registry).run(
            [{"role": "user", "content": "run command"}], session_id="s1"
        )
    ]

    assert isinstance(events[-1], ToolBatchEvent)
    assert events[-1].tool_calls[0].name == "os_run"
    assert registry.calls == []


@pytest.mark.asyncio
async def test_agent_loop_never_falls_back_to_legacy_dispatch() -> None:
    registry = _LegacyRegistry()
    llm = _ScriptedLLM([
        _tool_response(ToolCall(id="c1", name="echo", arguments={"x": 1}))
    ])
    events = [
        event
        async for event in AgentLoop(llm, registry).run(
            [{"role": "user", "content": "echo"}]
        )
    ]

    assert isinstance(events[-1], ToolBatchEvent)
    assert registry.calls == []


@pytest.mark.asyncio
async def test_agent_loop_returns_after_first_tool_batch() -> None:
    registry = _V2Registry()
    llm = _ScriptedLLM([
        _tool_response(ToolCall(id=f"c{i}", name="ping", arguments={}))
        for i in range(20)
    ])
    events = [
        event
        async for event in AgentLoop(
            llm, registry, max_iterations=3
        ).run([{"role": "user", "content": "loop"}], session_id="s1")
    ]

    assert isinstance(events[-1], ToolBatchEvent)
    assert llm.call_count == 1
    assert registry.calls == []
