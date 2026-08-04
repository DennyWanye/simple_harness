from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent.agent_loop import AgentLoop, ToolBatchEvent
from llm.types import ChatResponse, ChatUsage, ToolCall


ROOT = Path(__file__).resolve().parents[3]


class ScriptedLLM:
    async def chat_with_fallback(self, messages, **kwargs):
        return ChatResponse(
            content="checking",
            stop_reason="tool_use",
            tool_calls=[ToolCall(
                id="call-1", name="read", arguments={"path": "a"}
            )],
            usage=ChatUsage(input_tokens=2, output_tokens=1),
        )


class Tools:
    def __init__(self):
        self.calls = []

    def schemas(self, enabled_toolsets=None):
        return [{"type": "function", "function": {"name": "read"}}]

    def dispatch(self, name, args, task_id):
        self.calls.append((name, args, task_id))
        raise AssertionError("AgentLoop must not execute tools")


def test_internal_tool_runtime_is_deleted_and_agent_loop_stays_bounded():
    source = (ROOT / "backend/agent/agent_loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    loop = next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "AgentLoop")

    assert loop.end_lineno - loop.lineno + 1 <= 3_800
    assert not (ROOT / "backend/agent/legacy_agent_loop_tool_runtime.py").exists()
    assert "LegacyAgentLoopToolRuntime" not in source
    assert "external_tool_dispatch" not in source


@pytest.mark.asyncio
async def test_agent_loop_emits_one_external_batch_without_dispatching():
    tools = Tools()
    events = [event async for event in AgentLoop(
        ScriptedLLM(), tools
    ).run([{"role": "user", "content": "read"}])]

    assert [event.type for event in events] == ["assistant_message", "tool_batch"]
    batch = events[-1]
    assert isinstance(batch, ToolBatchEvent)
    assert [call.name for call in batch.tool_calls] == ["read"]
    assert batch.canonical_messages[-1]["role"] == "assistant"
    assert tools.calls == []
