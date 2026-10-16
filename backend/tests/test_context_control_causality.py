# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from agent.agent_loop import AgentLoop, ToolBatchEvent
from llm.context_messages import CONTEXT_MESSAGE_META_KEY, ContextMessageMeta, split_wire_messages
from llm.types import ChatResponse, ToolCall


class _ToolRegistry:
    def schemas(self, enabled_toolsets=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def execute_tool(self, name, args, task_id, session_id="default", **kwargs):
        return '{"ok": true, "result": "found"}'


class _ToolThenFinalLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat_with_fallback(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        if len(self.calls) == 1:
            return ChatResponse(
                content="checking",
                stop_reason="tool_use",
                tool_calls=[ToolCall(id="call-1", name="lookup", arguments={})],
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        return ChatResponse(
            content="done",
            stop_reason="end_turn",
            tool_calls=[],
            usage={"input_tokens": 20, "output_tokens": 5},
        )


class _ThreeToolsThenForcedFinalLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_with_fallback(self, messages, **kwargs):
        self.calls.append({"messages": copy.deepcopy(messages), **kwargs})
        if len(self.calls) <= 3:
            sequence = len(self.calls)
            return ChatResponse(
                content="",
                stop_reason="tool_use",
                tool_calls=[
                    ToolCall(
                        id=f"call-{sequence}",
                        name="lookup",
                        arguments={"sequence": sequence},
                    )
                ],
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        return ChatResponse(
            content="forced-final",
            stop_reason="end_turn",
            tool_calls=[],
            usage={"input_tokens": 20, "output_tokens": 5},
        )


def _metadata(message: dict) -> ContextMessageMeta:
    raw = message.get(CONTEXT_MESSAGE_META_KEY)
    assert isinstance(raw, dict)
    return ContextMessageMeta.from_mapping(raw)


@pytest.mark.asyncio
async def test_tool_batch_carries_canonical_assistant_causal_group() -> None:
    llm = _ToolThenFinalLLM()
    loop = AgentLoop(llm, _ToolRegistry(), max_iterations=3)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "look it up"}],
            task_id="task-causal",
            prepared_context=SimpleNamespace(tool_set=None),
        )
    ]

    batch = next(event for event in events if isinstance(event, ToolBatchEvent))
    canonical = list(batch.canonical_messages)
    assistant = next(message for message in canonical if message.get("tool_calls"))
    assistant_meta = _metadata(assistant)

    assert assistant_meta.placement == "transcript"
    assert assistant_meta.causal_group_id
    assert not any(message.get("role") == "tool" for message in canonical)
    assert len(llm.calls) == 1

    wire, aligned = split_wire_messages(canonical)
    assert len(wire) == len(aligned)
    assert all(CONTEXT_MESSAGE_META_KEY not in message for message in wire)
    assert assistant["tool_calls"] == next(
        message["tool_calls"] for message in wire if message.get("tool_calls")
    )


@pytest.mark.asyncio
async def test_context_os_returns_before_internal_force_finish_iteration() -> None:
    llm = _ThreeToolsThenForcedFinalLLM()
    loop = AgentLoop(llm, _ToolRegistry(), max_iterations=4)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "run three tools then finish"}],
            task_id="task-force-finish",
            prepared_context=SimpleNamespace(tool_set=None),
        )
    ]

    assert len(llm.calls) == 1
    assert llm.calls[0].get("tools")
    assert isinstance(events[-1], ToolBatchEvent)


class _CompletionNudgeLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat_with_fallback(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return ChatResponse(
            content="I am done",
            stop_reason="end_turn",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 3},
        )


@pytest.mark.asyncio
async def test_completion_control_anchors_after_triggering_assistant() -> None:
    llm = _CompletionNudgeLLM()

    async def incomplete(_session_id: str):
        return [{"content": "finish verification", "status": "pending"}]

    loop = AgentLoop(
        llm,
        _ToolRegistry(),
        completion_probe=incomplete,
        max_completion_nudges=1,
        max_iterations=3,
    )
    async for _ in loop.run(
        [{"role": "user", "content": "finish everything"}],
        task_id="task-control",
        prepared_context=SimpleNamespace(tool_set=None),
    ):
        pass

    second_call = llm.calls[1]
    trigger_index = next(
        index
        for index, message in enumerate(second_call)
        if _metadata(message).source == "agent_loop.completion_probe.trigger"
    )
    control_index = next(
        index
        for index, message in enumerate(second_call)
        if _metadata(message).source == "agent_loop.completion_nudge"
    )
    trigger_meta = _metadata(second_call[trigger_index])
    control_meta = _metadata(second_call[control_index])

    assert trigger_index < control_index
    assert control_meta.placement == "control"
    assert control_meta.anchor_after == trigger_meta.fragment_id


@pytest.mark.asyncio
async def test_context_os_off_keeps_canonical_batch_append_shape() -> None:
    llm = _ToolThenFinalLLM()
    loop = AgentLoop(llm, _ToolRegistry(), max_iterations=3)

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "look it up"}],
            task_id="task-off",
        )
    ]

    batch = next(event for event in events if isinstance(event, ToolBatchEvent))
    assert list(batch.canonical_messages) == [
        {"role": "user", "content": "look it up"},
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        },
    ]
    assert all(CONTEXT_MESSAGE_META_KEY not in message for message in batch.canonical_messages)
    assert len(llm.calls) == 1
