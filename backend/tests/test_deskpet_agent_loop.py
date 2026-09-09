# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unit tests for agent.agent_loop. All LLM + tool calls are mocked.

Coverage:
    - final-only response path
    - external ToolBatchEvent pause point and canonical transcript
    - multi-call batch ordering without internal dispatch
    - budget and provider errors before tool execution
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from agent.agent_loop import (
    AgentLoop,
    AssistantMessageEvent,
    ErrorEvent,
    FinalEvent,
    ToolBatchEvent,
)
from agent.task_id import new_task_id
from llm.budget import DailyBudget
from llm.errors import LLMProviderError
from llm.types import ChatResponse, ChatUsage, ToolCall


class FakeLLMRegistry:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "model": model,
            "kwargs": dict(kwargs),
        })
        if not self._responses:
            raise AssertionError("FakeLLMRegistry ran out of programmed responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolRegistry:
    def __init__(
        self,
        schemas: Optional[list[dict[str, Any]]] = None,
        handlers: Optional[dict[str, Any]] = None,
        completion_semantics: Optional[dict[str, str]] = None,
    ) -> None:
        self._schemas = schemas or []
        self._handlers = handlers or {}
        self._completion_semantics = completion_semantics or {}
        self.calls: list[dict[str, Any]] = []

    def schemas(self, enabled_toolsets: Optional[list[str]] = None) -> list[dict[str, Any]]:
        return list(self._schemas)

    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any:
        self.calls.append({"name": name, "args": args, "task_id": task_id})
        if name not in self._handlers:
            raise KeyError(f"tool {name!r} not registered")
        return self._handlers[name](args)

    def get(self, name: str) -> Any:
        return SimpleNamespace(
            completion_semantics=self._completion_semantics.get(name, "sync")
        )


# ───────────────────── tests ─────────────────────


def test_task_id_format():
    tid = new_task_id()
    assert tid.startswith("task_")
    # task_<YYMMDDHHMMSS>_<8 hex>
    parts = tid.split("_")
    assert len(parts) == 3
    assert len(parts[1]) == 12
    assert len(parts[2]) == 8


@pytest.mark.asyncio
async def test_happy_path_no_tools():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="Hello, world!",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=5, output_tokens=3),
                model="claude-sonnet-4-5",
            )
        ]
    )
    tools = FakeToolRegistry()
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=5)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "Hi"}])]
    kinds = [e.type for e in events]
    assert kinds == ["assistant_message", "final"]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.content == "Hello, world!"
    assert final.total_input_tokens == 5
    assert final.total_output_tokens == 3


@pytest.mark.asyncio
async def test_reasoning_only_turn_is_retried_and_can_emit_tool_call():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                reasoning_content="I should call tool_activate now.",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=20, output_tokens=10),
                model="reasoning-model",
            ),
            ChatResponse(
                content="",
                reasoning_content="Calling it now.",
                tool_calls=[
                    ToolCall(
                        id="activate_1",
                        name="tool_activate",
                        arguments={"name": "ppt_create"},
                    )
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=30, output_tokens=8),
                model="reasoning-model",
            ),
        ]
    )
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        max_iterations=3,
    )

    events = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "finish the durable task"}]
        )
    ]

    assert len(llm.calls) == 2
    assert not any(isinstance(event, FinalEvent) for event in events)
    batch = next(event for event in events if isinstance(event, ToolBatchEvent))
    assert [call.name for call in batch.tool_calls] == ["tool_activate"]
    second_messages = llm.calls[1]["messages"]
    assert any(
        message.get("role") == "assistant"
        and message.get("reasoning_content") == "I should call tool_activate now."
        for message in second_messages
    )
    assert any(
        message.get("role") == "system"
        and "emit the actual structured tool call now"
        in str(message.get("content") or "")
        for message in second_messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exclusive_name", ["capability_build", "tool_activate"])
async def test_exclusive_tool_is_split_from_mixed_batch_without_model_retry(
    exclusive_name: str,
):
    response_calls = [
        ToolCall(
            id="sibling_1",
            name="memory_search",
            arguments={"query": "deck"},
        ),
        ToolCall(
            id="exclusive_1",
            name=exclusive_name,
            arguments={"objective": "make the deck"},
        ),
        ToolCall(
            id="sibling_2",
            name="tool_search",
            arguments={"query": "ppt"},
        ),
    ]
    llm = FakeLLMRegistry(responses=[
        ChatResponse(
            content="",
            tool_calls=response_calls,
            stop_reason="tool_use",
            model="fixture-model",
        )
    ])
    tools = FakeToolRegistry(
        completion_semantics={
            exclusive_name: (
                "accepted_async" if exclusive_name == "capability_build" else "sync"
            )
        }
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=2)

    events = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "make a deck"}]
        )
    ]

    assert len(llm.calls) == 1
    batch = next(event for event in events if isinstance(event, ToolBatchEvent))
    assert [call.id for call in batch.tool_calls] == ["exclusive_1"]
    sibling_results = {
        str(message.get("tool_call_id")): message
        for message in batch.canonical_messages
        if message.get("role") == "tool"
    }
    assert set(sibling_results) == {"sibling_1", "sibling_2"}
    for message in sibling_results.values():
        payload = json.loads(str(message["content"]))
        assert payload["error"] == "deferred_by_exclusive_tool"
        assert payload["executed"] is False
        assert payload["exclusive_tool_call_id"] == "exclusive_1"


@pytest.mark.asyncio
async def test_mixed_batch_executes_only_first_exclusive_call():
    llm = FakeLLMRegistry(responses=[
        ChatResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="activate_1",
                    name="tool_activate",
                    arguments={"name": "ppt_create"},
                ),
                ToolCall(
                    id="build_2",
                    name="capability_build",
                    arguments={"objective": "make the deck"},
                ),
                ToolCall(
                    id="search_3",
                    name="tool_search",
                    arguments={"query": "ppt"},
                ),
            ],
            stop_reason="tool_use",
            model="fixture-model",
        )
    ])
    tools = FakeToolRegistry(
        completion_semantics={"capability_build": "accepted_async"}
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=2)

    events = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "make a deck"}]
        )
    ]

    batch = next(event for event in events if isinstance(event, ToolBatchEvent))
    assert [call.id for call in batch.tool_calls] == ["activate_1"]
    deferred_ids = {
        str(message.get("tool_call_id"))
        for message in batch.canonical_messages
        if message.get("role") == "tool"
    }
    assert deferred_ids == {"build_2", "search_3"}


@pytest.mark.asyncio
async def test_empty_turn_is_retried_and_can_emit_visible_answer():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                reasoning_content="",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=20, output_tokens=0),
                model="reasoning-model",
            ),
            ChatResponse(
                content="Recovered answer",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=25, output_tokens=3),
                model="reasoning-model",
            ),
        ]
    )
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        max_iterations=3,
    )

    events = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "answer the task"}]
        )
    ]

    assert len(llm.calls) == 2
    assert isinstance(events[-1], FinalEvent)
    assert events[-1].content == "Recovered answer"
    assert any(
        message.get("role") == "system"
        and "contained no output" in str(message.get("content") or "")
        for message in llm.calls[1]["messages"]
    )


@pytest.mark.asyncio
async def test_repeated_reasoning_only_turn_fails_instead_of_empty_success():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                reasoning_content="Still thinking.",
                stop_reason="end_turn",
                usage=ChatUsage(),
            ),
            ChatResponse(
                content="",
                reasoning_content="Still only thinking.",
                stop_reason="end_turn",
                usage=ChatUsage(),
            ),
            ChatResponse(
                content="",
                reasoning_content="Still only thinking after two nudges.",
                stop_reason="end_turn",
                usage=ChatUsage(),
            ),
            ChatResponse(
                content="",
                reasoning_content="Still only thinking after three nudges.",
                stop_reason="end_turn",
                usage=ChatUsage(),
            ),
        ]
    )
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        max_iterations=5,
    )

    events = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "do the task"}]
        )
    ]

    assert len(llm.calls) == 4
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].reason == "model_reasoning_only_response"
    assert not any(isinstance(event, FinalEvent) for event in events)


@pytest.mark.asyncio
async def test_tool_name_filter_reaches_llm_schema_surface():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="done",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="test",
            )
        ]
    )
    tools = FakeToolRegistry(
        schemas=[
            {"type": "function", "function": {"name": "deepresearch"}},
            {"type": "function", "function": {"name": "agent_reach_read"}},
        ]
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)

    _ = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "research"}],
            tool_names_filter=["deepresearch"],
        )
    ]

    assert [schema["function"]["name"] for schema in llm.calls[0]["tools"]] == [
        "deepresearch"
    ]


@pytest.mark.asyncio
async def test_tool_name_filter_also_narrows_prepared_context_schema_surface():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="done",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="test",
            )
        ]
    )
    tools = FakeToolRegistry()
    tools.capability_scope_store = SimpleNamespace(
        get=lambda *args, **kwargs: SimpleNamespace(eligibility={})
    )
    tools.validate_prepared_tool_set = lambda *args, **kwargs: None
    prepared_context = SimpleNamespace(
        messages=[{"role": "user", "content": "do it"}],
        tool_set=SimpleNamespace(
            scope_id="scope-1",
            logical_schemas=lambda: [
                {"type": "function", "function": {"name": "allowed"}},
                {"type": "function", "function": {"name": "not_exposed"}},
            ]
        )
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)

    _ = [
        event
        async for event in loop.run(
            messages=[{"role": "user", "content": "do it"}],
            tool_names_filter=["allowed"],
            prepared_context=prepared_context,
        )
    ]

    assert [schema["function"]["name"] for schema in llm.calls[0]["tools"]] == [
        "allowed"
    ]


@pytest.mark.asyncio
async def test_tool_use_yields_external_batch_and_pauses():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="Let me check...",
                tool_calls=[ToolCall(id="call_1", name="get_time", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=20, output_tokens=10),
                model="claude-sonnet-4-5",
            ),
            ChatResponse(
                content="It's 10:30.",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=30, output_tokens=5),
                model="claude-sonnet-4-5",
            ),
        ]
    )
    tools = FakeToolRegistry(
        schemas=[
            {
                "type": "function",
                "function": {"name": "get_time", "description": "current time", "parameters": {"type": "object"}},
            }
        ],
        handlers={"get_time": lambda args: {"time": "10:30"}},
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "What time is it?"}])]
    assert [e.type for e in events] == ["assistant_message", "tool_batch"]
    batch = next(e for e in events if isinstance(e, ToolBatchEvent))
    assert [(call.name, call.arguments) for call in batch.tool_calls] == [
        ("get_time", {})
    ]
    assert tools.calls == []
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_deepresearch_is_exposed_to_external_executor():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="我先调研一下。",
                tool_calls=[
                    ToolCall(
                        id="dr_1",
                        name="deepresearch",
                        arguments={"topic": "俄乌最近局势"},
                    )
                ],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=20, output_tokens=10),
            ),
            ChatResponse(
                content="基于已生成的调研报告，结论如下。",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=30, output_tokens=8),
            ),
        ]
    )

    def deepresearch_handler(args):
        return {
            "ok": True,
            "topic": args["topic"],
            "report_md": "# 报告\n\n已有结论。",
            "citations": [{"id": 1, "url": "https://example.com", "title": "source"}],
            "path": "DeepResearch/report.md",
        }

    tools = FakeToolRegistry(
        schemas=[
            {
                "type": "function",
                "function": {
                    "name": "deepresearch",
                    "description": "research",
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "search",
                    "parameters": {"type": "object"},
                },
            },
        ],
        handlers={
            "deepresearch": deepresearch_handler,
            "web_search": lambda args: pytest.fail("web_search should not run after deepresearch"),
        },
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=5)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "请调研俄乌局势"}])]

    assert tools.calls == []
    assert len(llm.calls) == 1
    batch = next(e for e in events if isinstance(e, ToolBatchEvent))
    assert [call.name for call in batch.tool_calls] == ["deepresearch"]


@pytest.mark.asyncio
async def test_multiple_tool_calls_are_preserved_in_one_external_batch():
    """AgentLoop preserves batch order and leaves execution to the harness."""

    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="tool_a", arguments={}),
                    ToolCall(id="c2", name="tool_b", arguments={}),
                ],
                stop_reason="tool_use",
                usage=ChatUsage(),
            ),
            ChatResponse(content="done", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    tools = FakeToolRegistry(
        schemas=[],
        handlers={
            "tool_a": lambda args: pytest.fail("AgentLoop dispatched tool_a"),
            "tool_b": lambda args: pytest.fail("AgentLoop dispatched tool_b"),
        },
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "go"}])]
    batch = next(e for e in events if isinstance(e, ToolBatchEvent))
    assert [call.name for call in batch.tool_calls] == ["tool_a", "tool_b"]
    assert tools.calls == []


@pytest.mark.asyncio
async def test_tool_exception_is_not_triggered_inside_agent_loop():
    def broken_tool(args):
        raise ValueError("kaboom")

    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="broken", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(),
            ),
            ChatResponse(content="recovered", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    tools = FakeToolRegistry(handlers={"broken": broken_tool})
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    assert any(isinstance(e, ToolBatchEvent) for e in events)
    assert tools.calls == []
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_tool_batch_is_a_pause_point_before_max_iterations():
    # LLM keeps asking for a tool forever.
    never_ending = [
        ChatResponse(
            content="",
            tool_calls=[ToolCall(id=f"call_{i}", name="ping", arguments={})],
            stop_reason="tool_use",
            usage=ChatUsage(),
        )
        for i in range(30)
    ]
    llm = FakeLLMRegistry(responses=never_ending)
    tools = FakeToolRegistry(handlers={"ping": lambda args: {"pong": True}})
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=3)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "loop"}])]
    assert isinstance(events[-1], ToolBatchEvent)
    assert len(llm.calls) == 1
    assert tools.calls == []


@pytest.mark.asyncio
async def test_budget_exceeded_aborts_with_error(tmp_path):
    budget = DailyBudget(cap_usd=0.001, state_path=tmp_path / "b.json")
    # Pre-seed with spend above cap.
    budget.add_usage("openai", "gpt-4o", ChatUsage(output_tokens=100_000))  # $1
    assert budget.check_allowed() is False

    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(content="unreachable", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        budget_checker=budget,
    )
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    assert isinstance(events[0], ErrorEvent)
    assert events[0].reason == "budget_exceeded"
    assert len(llm.calls) == 0  # MUST NOT invoke LLM when over budget


@pytest.mark.asyncio
async def test_llm_error_surfaces_as_error_event():
    llm = FakeLLMRegistry(
        responses=[LLMProviderError("all providers failed: gone")]
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=FakeToolRegistry())
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].reason == "llm_error"
    assert "all providers failed" in events[0].detail


@pytest.mark.asyncio
async def test_tool_batch_contains_canonical_assistant_tool_call_message():
    """The external harness receives canonical state needed for resume."""
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"x": 1})],
                stop_reason="tool_use",
                usage=ChatUsage(),
            ),
            ChatResponse(content="ok", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    tools = FakeToolRegistry(handlers={"echo": lambda args: {"seen": args}})
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    batch = next(e for e in events if isinstance(e, ToolBatchEvent))
    assistant = batch.canonical_messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert assistant["tool_calls"][0]["function"]["name"] == "echo"
    assert tools.calls == []
    assert len(llm.calls) == 1
