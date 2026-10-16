# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for S6 security primitives: sensitive-info redaction + tool confirmation."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from agent.providers.tool_using import ToolUsingAgent
from tools.base import ToolSpec
from tools.registry import ToolRegistry

# --- RedactingMemoryStore ---


class _FakeStore:
    def __init__(self):
        self.appended: list[tuple[str, str, str]] = []

    async def get_recent(self, session_id, limit=10):
        return []

    async def append(self, session_id, role, content):
        self.appended.append((session_id, role, content))

    async def clear(self, session_id):
        self.appended = [t for t in self.appended if t[0] != session_id]


# --- Tool confirmation ---


class _StubAgent:
    """Yields a reply that invokes a single tool."""

    def __init__(self, text: str):
        self._text = text

    async def chat_stream(self, messages, *, session_id="default") -> AsyncIterator[str]:
        yield self._text


class _StubTool:
    def __init__(self, spec: ToolSpec, result: str = "ok"):
        self.spec = spec
        self.result = result
        self.invoked = False

    async def invoke(self, **kwargs):
        self.invoked = True
        return self.result


@pytest.mark.asyncio
async def test_high_risk_tool_denied_by_default():
    """Fail-closed: requires_confirmation=True + no callback → refused."""
    registry = ToolRegistry()
    tool = _StubTool(
        ToolSpec(
            name="delete_file",
            description="removes a file",
            requires_confirmation=True,
        )
    )
    registry.register(tool)

    agent = ToolUsingAgent(_StubAgent("please run <tool>delete_file</tool>"), registry)
    chunks = [c async for c in agent.chat_stream([{"role": "user", "content": "go"}])]
    out = "".join(chunks)

    assert "[tool refused:" in out
    assert not tool.invoked


@pytest.mark.asyncio
async def test_high_risk_tool_runs_when_confirm_approves():
    registry = ToolRegistry()
    tool = _StubTool(
        ToolSpec(
            name="open_url",
            description="opens a URL",
            requires_confirmation=True,
        )
    )
    registry.register(tool)

    async def approve(name, session_id):  # noqa: ARG001
        return True

    agent = ToolUsingAgent(
        _StubAgent("<tool>open_url</tool>"),
        registry,
        confirm=approve,
    )
    out = "".join([c async for c in agent.chat_stream([{"role": "user", "content": "go"}])])

    assert "[tool:open_url] ok" in out
    assert tool.invoked


@pytest.mark.asyncio
async def test_low_risk_tool_bypasses_confirmation():
    """requires_confirmation=False (default) must NOT consult the callback."""
    registry = ToolRegistry()
    tool = _StubTool(ToolSpec(name="get_time", description="returns time"))
    registry.register(tool)

    called = {"n": 0}

    async def track(name, session_id):  # noqa: ARG001
        called["n"] += 1
        return True

    agent = ToolUsingAgent(
        _StubAgent("<tool>get_time</tool>"),
        registry,
        confirm=track,
    )
    out = "".join([c async for c in agent.chat_stream([{"role": "user", "content": "go"}])])

    assert tool.invoked
    assert "[tool:get_time] ok" in out
    assert called["n"] == 0  # callback untouched for low-risk


@pytest.mark.asyncio
async def test_sync_confirm_callback_also_works():
    registry = ToolRegistry()
    tool = _StubTool(
        ToolSpec(
            name="reboot",
            description="reboots the machine",
            requires_confirmation=True,
        )
    )
    registry.register(tool)

    agent = ToolUsingAgent(
        _StubAgent("<tool>reboot</tool>"),
        registry,
        confirm=lambda name, sid: True,  # sync
    )
    out = "".join([c async for c in agent.chat_stream([{"role": "user", "content": "go"}])])
    assert tool.invoked
    assert "[tool:reboot] ok" in out
