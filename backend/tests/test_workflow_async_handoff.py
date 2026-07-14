from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent.agent_loop import (
    AgentLoop,
    AsyncHandoffEvent,
    DispatchOutcome,
    FinalEvent,
)
from llm.types import ChatResponse, ChatUsage, ToolCall


def _usage() -> ChatUsage:
    return ChatUsage(input_tokens=4, output_tokens=3)


def _tool_response(*calls: ToolCall) -> ChatResponse:
    return ChatResponse(
        content="Starting the requested work.",
        stop_reason="tool_use",
        tool_calls=list(calls),
        usage=_usage(),
    )


def _final_response() -> ChatResponse:
    return ChatResponse(
        content="Please retry the workflow tool alone.",
        stop_reason="end_turn",
        tool_calls=[],
        usage=_usage(),
    )


class ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_fallback(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(list(messages))
        return self.responses.pop(0)


class SemanticTools:
    def __init__(self, *, accepted: bool = True) -> None:
        self.calls: list[str] = []
        self.accepted = accepted

    def schemas(self, enabled_toolsets=None):  # noqa: ANN001
        return []

    def get(self, name: str):  # noqa: ANN201
        semantics = "accepted_async" if name == "deepresearch" else "sync"
        return SimpleNamespace(name=name, completion_semantics=semantics)

    async def execute_tool(self, name, args, session_id, task_id):  # noqa: ANN001
        self.calls.append(name)
        if not self.accepted:
            return {
                "ok": True,
                "result": json.dumps({"ok": False, "error": "workflow_start_failed"}),
                "error": None,
            }
        return {
            "ok": True,
            "result": json.dumps(
                {
                    "accepted": True,
                    "run_id": "run-async-1",
                    "request_id": "request-1",
                    "turn_id": "turn-1",
                    "accepted_event_id": "event-accepted-1",
                }
            ),
            "error": None,
        }


class WorkflowServiceSpy:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def deliver_event_once(self, event_id: str) -> dict[str, Any]:
        self.events.append(event_id)
        return {"event_id": event_id, "delivered": True}


class MustNotRunGate:
    mode = "strict"

    def check(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("completion/verify gates must not run after async handoff")


@pytest.mark.asyncio
async def test_single_accepted_async_call_dispatches_once_and_ends_with_handoff() -> None:
    llm = ScriptedLLM(
        [
            _tool_response(
                ToolCall(
                    id="call-async",
                    name="deepresearch",
                    arguments={"question": "trace durable workflows"},
                )
            )
        ]
    )
    tools = SemanticTools()
    workflow_service = WorkflowServiceSpy()
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        workflow_service=workflow_service,
        verify_gate=MustNotRunGate(),
        max_iterations=4,
    )

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "research this"}],
            session_id="session-async",
        )
    ]

    assert [event.type for event in events] == [
        "assistant_message",
        "tool_call",
        "tool_result",
        "async_handoff",
    ]
    assert tools.calls == ["deepresearch"]
    assert len(llm.calls) == 1
    assert not any(isinstance(event, FinalEvent) for event in events)
    handoff = events[-1]
    assert isinstance(handoff, AsyncHandoffEvent)
    assert handoff.dispatch_outcome is DispatchOutcome.ASYNC_HANDOFF
    assert handoff.run_id == "run-async-1"
    assert handoff.event_id == "event-accepted-1"
    assert workflow_service.events == ["event-accepted-1"]


@pytest.mark.asyncio
async def test_mixed_async_batch_is_rejected_before_any_tool_executes() -> None:
    llm = ScriptedLLM(
        [
            _tool_response(
                ToolCall(
                    id="call-async",
                    name="deepresearch",
                    arguments={"question": "research this"},
                ),
                ToolCall(
                    id="call-sync",
                    name="read_file",
                    arguments={"path": "notes.md"},
                ),
            ),
            _final_response(),
        ]
    )
    tools = SemanticTools()
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=3)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "research and read"}],
            session_id="session-mixed",
        )
    ]

    assert tools.calls == []
    assert len(llm.calls) == 2
    assert not any(isinstance(event, AsyncHandoffEvent) for event in events)
    assert isinstance(events[-1], FinalEvent)
    rejected_results = [
        json.loads(event.result)
        for event in events
        if event.type == "tool_result"
    ]
    assert len(rejected_results) == 2
    assert {result["error"] for result in rejected_results} == {
        "accepted_async_must_be_single"
    }
    second_turn_tool_messages = [
        message for message in llm.calls[1] if message.get("role") == "tool"
    ]
    assert len(second_turn_tool_messages) == 2


@pytest.mark.asyncio
async def test_failed_async_start_returns_to_llm_instead_of_handing_off() -> None:
    llm = ScriptedLLM(
        [
            _tool_response(
                ToolCall(
                    id="call-async",
                    name="deepresearch",
                    arguments={"question": "research this"},
                )
            ),
            _final_response(),
        ]
    )
    tools = SemanticTools(accepted=False)
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=3)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "research this"}],
            session_id="session-start-failed",
        )
    ]

    assert tools.calls == ["deepresearch"]
    assert len(llm.calls) == 2
    assert not any(isinstance(event, AsyncHandoffEvent) for event in events)
    assert isinstance(events[-1], FinalEvent)
