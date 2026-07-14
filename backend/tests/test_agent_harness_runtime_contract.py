# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import json
from typing import Any

import pytest

from agent.agent_loop import (
    AgentLoop,
    AssistantMessageEvent,
    FinalEvent,
    PipelineEvent,
    SubagentCompletionEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from deskpet.agent.evidence_gate import EvidenceGate
from deskpet.agent.subagent_registry import SubagentRegistry, SubagentRun
from llm.types import ChatResponse, ChatUsage, ToolCall


def _usage() -> ChatUsage:
    return ChatUsage(input_tokens=10, output_tokens=5)


def _tool_response(name: str, args: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        content="I need a tool.",
        stop_reason="tool_use",
        tool_calls=[ToolCall(id="tc-1", name=name, arguments=args)],
        usage=_usage(),
    )


def _final_response(content: str = "done") -> ChatResponse:
    return ChatResponse(
        content=content,
        stop_reason="end_turn",
        tool_calls=[],
        usage=_usage(),
    )


class ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "model": model,
                "kwargs": dict(kwargs),
            }
        )
        if not self._responses:
            raise AssertionError("ScriptedLLM exhausted")
        return self._responses.pop(0)


class V2Tools:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def schemas(self, enabled_toolsets=None) -> list[dict[str, Any]]:  # noqa: ANN001
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

    async def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        session_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "name": name,
                "args": dict(args),
                "session_id": session_id,
                "task_id": task_id,
            }
        )
        return {
            "ok": True,
            "result": json.dumps({"path": args.get("path"), "content": "secret=42"}),
            "error": None,
        }


class LegacyTools:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def schemas(self, enabled_toolsets=None) -> list[dict[str, Any]]:  # noqa: ANN001
        return [
            {
                "type": "function",
                "function": {
                    "name": "legacy_lookup",
                    "description": "legacy",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ]

    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> dict[str, Any]:
        self.calls.append({"name": name, "args": dict(args), "task_id": task_id})
        return {"legacy": True, "query": args.get("query")}


@pytest.mark.asyncio
async def test_runtime_golden_tool_result_is_fed_back_before_final() -> None:
    llm = ScriptedLLM(
        [
            _tool_response("read_file", {"path": "auth.py"}),
            _final_response("I checked auth.py."),
        ]
    )
    tools = V2Tools()
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=5)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "check auth"}],
            session_id="sid-runtime",
            task_id="task-runtime",
        )
    ]

    assert [event.type for event in events] == [
        "assistant_message",
        "tool_call",
        "tool_result",
        "assistant_message",
        "final",
    ]
    assert isinstance(events[0], AssistantMessageEvent)
    assert isinstance(events[1], ToolCallEvent)
    assert isinstance(events[2], ToolResultEvent)
    assert isinstance(events[-1], FinalEvent)
    assert tools.calls == [
        {
            "name": "read_file",
            "args": {"path": "auth.py"},
            "session_id": "sid-runtime",
            "task_id": "task-runtime",
        }
    ]

    second_messages = llm.calls[1]["messages"]
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second_messages)
    tool_messages = [m for m in second_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload["ok"] is True
    assert json.loads(payload["result"])["content"] == "secret=42"


@pytest.mark.asyncio
async def test_runtime_legacy_dispatch_result_is_json_fed_back_before_final() -> None:
    llm = ScriptedLLM(
        [
            _tool_response("legacy_lookup", {"query": "auth"}),
            _final_response("I checked the legacy registry."),
        ]
    )
    tools = LegacyTools()
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=5)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "legacy lookup"}],
            session_id="sid-legacy",
            task_id="task-legacy",
        )
    ]

    assert [event.type for event in events] == [
        "assistant_message",
        "tool_call",
        "tool_result",
        "assistant_message",
        "final",
    ]
    assert tools.calls == [
        {
            "name": "legacy_lookup",
            "args": {"query": "auth"},
            "task_id": "task-legacy",
        }
    ]
    tool_messages = [m for m in llm.calls[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert json.loads(tool_messages[0]["content"]) == {"legacy": True, "query": "auth"}


@pytest.mark.asyncio
async def test_evidence_gate_runtime_blocks_claim_then_allows_after_tool_evidence() -> None:
    llm = ScriptedLLM(
        [
            _final_response("Looks like auth is broken."),
            _tool_response("read_file", {"path": "auth.py"}),
            _final_response("After reading auth.py, auth is broken."),
        ]
    )
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=V2Tools(),
        max_iterations=6,
        evidence_gate=EvidenceGate(max_nudges=2),
        pipeline_needs_investigation=True,
        pipeline_problem_type="debug",
        pipeline_observability=True,
    )

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "debug auth"}],
            session_id="sid-evidence",
        )
    ]

    gate_events = [e for e in events if e.type == "chat_v2_evidence_gate"]
    assert len(gate_events) >= 2
    assert all(isinstance(e, PipelineEvent) for e in gate_events)
    assert all(isinstance(e.iteration, int) and e.iteration > 0 for e in gate_events)
    assert all(e.task_id for e in gate_events)
    assert gate_events[0].payload["blocked"] is True
    assert gate_events[-1].payload["blocked"] is False
    assert gate_events[-1].payload["reason"] == "evidence_present"
    assert {"blocked", "reason", "nudge_count"} <= set(gate_events[0].payload)
    assert any(isinstance(e, ToolResultEvent) and e.tool_name == "read_file" for e in events)
    assert isinstance(events[-1], FinalEvent)
    assert len(llm.calls) == 3
    nudge_messages = [m for m in llm.calls[1]["messages"] if m.get("role") == "system"]
    assert nudge_messages
    assert all(m.get("content") for m in nudge_messages)


@pytest.mark.asyncio
async def test_subagent_completion_is_emitted_once_before_parent_llm_call() -> None:
    reg = SubagentRegistry()
    reg.complete("missing", summary="ignored")
    reg.register(
        SubagentRun(
            run_id="r1",
            kind="research",
            task_id="subtask-1",
            status="completed",
            summary="child found the source",
        )
    )
    reg.complete("r1", summary="child found the source")
    llm = ScriptedLLM([_final_response("Parent used child result.")])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=V2Tools(),
        max_iterations=3,
        subagent_registry=reg,
    )
    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "use child"}],
            session_id="sid-subagent",
        )
    ]

    completions = [e for e in events if isinstance(e, SubagentCompletionEvent)]
    assert len(completions) == 1
    assert completions[0].run_id == "r1"
    assert reg.completion_queue.empty()
    assert "child found the source" in json.dumps(llm.calls[0]["messages"], ensure_ascii=False)
