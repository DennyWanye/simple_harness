# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from agent.agent_loop import AgentLoop
from agent.context_messages import (
    CONTEXT_MESSAGE_META_KEY,
    ContextMessageMeta,
    append_control,
    append_transcript,
    split_wire_messages,
    tag_message,
)
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

    async def execute_tool(
        self, name, args, task_id, session_id="default", **kwargs
    ):
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
async def test_assistant_and_all_tool_results_share_one_causal_group() -> None:
    llm = _ToolThenFinalLLM()
    loop = AgentLoop(llm, _ToolRegistry(), max_iterations=3)

    async for _ in loop.run(
        [{"role": "user", "content": "look it up"}],
        task_id="task-causal",
        prepared_context=SimpleNamespace(tool_set=None),
    ):
        pass

    second_call = llm.calls[1]
    assistant = next(message for message in second_call if message.get("tool_calls"))
    tool = next(message for message in second_call if message.get("role") == "tool")
    assistant_meta = _metadata(assistant)
    tool_meta = _metadata(tool)

    assert assistant_meta.placement == "transcript"
    assert tool_meta.placement == "transcript"
    assert assistant_meta.causal_group_id == tool_meta.causal_group_id
    assert assistant_meta.causal_group_id

    wire, aligned = split_wire_messages(second_call)
    assert len(wire) == len(aligned)
    assert all(CONTEXT_MESSAGE_META_KEY not in message for message in wire)
    assert assistant["tool_calls"] == next(
        message["tool_calls"] for message in wire if message.get("tool_calls")
    )
    assert tool["tool_call_id"] == next(
        message["tool_call_id"] for message in wire if message.get("role") == "tool"
    )


@pytest.mark.asyncio
async def test_context_os_reserves_last_iteration_for_tool_free_finish() -> None:
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

    assert len(llm.calls) == 4
    assert all(call.get("tools") for call in llm.calls[:3])
    assert llm.calls[3].get("tools") is None
    assert llm.calls[3].get("tool_choice") == "none"
    assert events[-1].type == "final"
    assert events[-1].content == "forced-final"


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


def test_control_survives_deepcopy_and_stays_at_causal_position() -> None:
    messages: list[dict] = []
    append_transcript(
        messages,
        "request",
        source="test.user",
        fragment_id="user-1",
    )
    append_transcript(
        messages,
        {"role": "assistant", "content": "stopped"},
        source="test.assistant",
        role="assistant",
        fragment_id="assistant-1",
    )
    append_control(
        messages,
        "continue",
        source="test.nudge",
        fragment_id="control-1",
        anchor_after="assistant-1",
    )

    cloned = copy.deepcopy(messages)
    assert [_metadata(message) for message in cloned] == [
        _metadata(message) for message in messages
    ]

    from deskpet.agent.context_compressor import _partition

    system, head, middle, tail = _partition(cloned, first_n=1, last_n=1)
    rebuilt = system + head + middle + tail
    assert [message["content"] for message in rebuilt] == [
        "request",
        "stopped",
        "continue",
    ]
    control = rebuilt[-1]
    assert _metadata(control).anchor_after == "assistant-1"


@pytest.mark.asyncio
async def test_context_os_off_keeps_original_append_shape() -> None:
    llm = _ToolThenFinalLLM()
    loop = AgentLoop(llm, _ToolRegistry(), max_iterations=3)

    async for _ in loop.run(
        [{"role": "user", "content": "look it up"}],
        task_id="task-off",
    ):
        pass

    assert llm.calls[1] == [
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
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "lookup",
            "content": '"{\\"ok\\": true, \\"result\\": \\"found\\"}"',
        },
    ]
    assert all(
        CONTEXT_MESSAGE_META_KEY not in message for message in llm.calls[1]
    )


def test_partition_never_splits_explicit_causal_group() -> None:
    group = "group-1"
    assistant = tag_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1"}],
        },
        ContextMessageMeta(
            placement="transcript",
            lifetime="history",
            source="test.assistant",
            causal_group_id=group,
            fragment_id="assistant-1",
        ),
    )
    tool = tag_message(
        {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ContextMessageMeta(
            placement="transcript",
            lifetime="history",
            source="test.tool",
            causal_group_id=group,
            fragment_id="tool-1",
        ),
    )
    messages = [
        {"role": "user", "content": "head"},
        assistant,
        tool,
        {"role": "user", "content": "tail"},
    ]

    from deskpet.agent.context_compressor import _partition

    _, head, middle, tail = _partition(messages, first_n=2, last_n=1)
    placements = [head, middle, tail]
    containing = [part for part in placements if assistant in part or tool in part]
    assert len(containing) == 1
    assert assistant in containing[0]
    assert tool in containing[0]
