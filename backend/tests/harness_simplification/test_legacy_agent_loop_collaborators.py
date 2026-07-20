from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from agent.agent_loop import AgentLoop
from agent.legacy_agent_loop_tool_runtime import (
    LegacyAgentLoopToolRuntime,
    LegacyToolRoundState,
)
from agent.legacy_subagent_bridge import LegacySubagentBridge
from llm.types import ChatResponse, ChatUsage, ToolCall


ROOT = Path(__file__).resolve().parents[3]


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    async def chat_with_fallback(self, messages, **kwargs):
        return self.responses.pop(0)


class Tools:
    def __init__(self):
        self.calls = []

    def schemas(self, enabled_toolsets=None):
        return [{"type": "function", "function": {"name": "read"}}]

    def dispatch(self, name, args, task_id):
        self.calls.append((name, args, task_id))
        return {"value": "ok"}


def _tool_turn():
    return ChatResponse(
        content="checking",
        stop_reason="tool_use",
        tool_calls=[ToolCall(id="call-1", name="read", arguments={"path": "a"})],
        usage=ChatUsage(input_tokens=2, output_tokens=1),
    )


def _final_turn():
    return ChatResponse(
        content="done",
        stop_reason="end_turn",
        usage=ChatUsage(input_tokens=2, output_tokens=1),
    )


def test_structure_budget_and_stateless_default_composition():
    loop_source = (ROOT / "backend/agent/agent_loop.py").read_text(encoding="utf-8")
    tree = ast.parse(loop_source)
    agent_loop = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AgentLoop"
    )
    assert agent_loop.end_lineno - agent_loop.lineno + 1 <= 3_800

    legacy_paths = [
        ROOT / "backend/agent/legacy_agent_loop_tool_runtime.py",
        ROOT / "backend/agent/legacy_subagent_bridge.py",
    ]
    assert sum(len(path.read_text(encoding="utf-8").splitlines()) for path in legacy_paths) <= 900

    loop = AgentLoop(ScriptedLLM([_final_turn()]), Tools())
    assert isinstance(loop._legacy_tool_runtime, LegacyAgentLoopToolRuntime)
    assert isinstance(loop._legacy_subagent_bridge, LegacySubagentBridge)
    assert not hasattr(loop._legacy_tool_runtime, "__dict__")
    assert not hasattr(loop._legacy_subagent_bridge, "__dict__")
    assert "__dict__" not in LegacyToolRoundState.__slots__


def test_legacy_modules_import_in_isolated_process_without_agent_loop_preload():
    code = (
        "import sys; assert 'agent.agent_loop' not in sys.modules; "
        "import agent.legacy_agent_loop_tool_runtime, agent.legacy_subagent_bridge; "
        "assert 'agent.agent_loop' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT / "backend",
        check=True,
    )


@pytest.mark.asyncio
async def test_real_runtime_preserves_tool_event_order_golden():
    tools = Tools()
    loop = AgentLoop(ScriptedLLM([_tool_turn(), _final_turn()]), tools)
    events = [event async for event in loop.run([{"role": "user", "content": "read"}])]

    assert [event.type for event in events] == [
        "assistant_message",
        "tool_call",
        "tool_result",
        "assistant_message",
        "final",
    ]
    assert [name for name, _args, _task in tools.calls] == ["read"]


class FailingRuntime:
    def __init__(self, exc: BaseException):
        self.exc = exc
        self.finalized = False

    async def run_round(self, loop, state):
        try:
            raise self.exc
            yield  # pragma: no cover
        finally:
            self.finalized = True


@pytest.mark.asyncio
async def test_collaborator_exception_propagates_and_finally_runs():
    loop = AgentLoop(ScriptedLLM([_tool_turn()]), Tools())
    runtime = FailingRuntime(RuntimeError("dispatch boundary failed"))
    loop._legacy_tool_runtime = runtime
    iterator = loop.run([{"role": "user", "content": "read"}])

    assert (await anext(iterator)).type == "assistant_message"
    with pytest.raises(RuntimeError, match="dispatch boundary failed"):
        await anext(iterator)
    assert runtime.finalized is True


class BlockingRuntime:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finalized = False

    async def run_round(self, loop, state):
        try:
            self.entered.set()
            await self.release.wait()
            yield  # pragma: no cover
        finally:
            self.finalized = True


@pytest.mark.asyncio
async def test_collaborator_cancellation_propagates_and_finally_runs():
    loop = AgentLoop(ScriptedLLM([_tool_turn()]), Tools())
    runtime = BlockingRuntime()
    loop._legacy_tool_runtime = runtime
    iterator = loop.run([{"role": "user", "content": "read"}])
    assert (await anext(iterator)).type == "assistant_message"

    pending = asyncio.create_task(anext(iterator))
    await runtime.entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert runtime.finalized is True
