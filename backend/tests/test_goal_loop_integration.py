# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B3 — AgentLoop x SessionGoalStore + GoalChecker wiring tests.

Mirrors the shape of ``test_agent_loop_verify_wiring.py`` — checks the
__init__ signature, BC default (None / None → skip), the rebound
behaviour when ``goal.done is False``, and the ``mark_done`` path.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from agent.agent_loop import AgentLoop, FinalEvent
from llm.types import ChatResponse, ChatUsage

# ───────────────────── fake LLM / tools (copied from existing loop tests) ─


class FakeLLMRegistry:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        if not self._responses:
            raise AssertionError("FakeLLMRegistry ran out of programmed responses")
        return self._responses.pop(0)


class FakeToolRegistry:
    def schemas(self, enabled_toolsets: list[str] | None = None) -> list[dict[str, Any]]:
        return []

    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any:
        raise KeyError(name)


class FakeGoalChecker:
    """Records every check call and returns the queued (done, hint) tuple."""

    def __init__(self, results: list[tuple[bool, str]]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def check(
        self,
        goal_text: str,
        working_msgs: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        self.calls.append({"goal_text": goal_text, "n_msgs": len(working_msgs)})
        if not self._results:
            raise AssertionError("FakeGoalChecker ran out of results")
        return self._results.pop(0)


# ───────────────────── tests ─────────────────────────────────────────────


def test_agent_loop_signature_accepts_goal_kwargs():
    """新 kwargs BC: 默认 None。"""
    sig = inspect.signature(AgentLoop.__init__)
    params = sig.parameters
    assert "session_goal_store" in params
    assert params["session_goal_store"].default is None
    assert "goal_checker" in params
    assert params["goal_checker"].default is None


@pytest.mark.asyncio
async def test_no_goal_store_skips_block_bc():
    """store=None → goal-check block 完全跳过 (BC, 现有 callers 不影响)."""
    llm = FakeLLMRegistry(
        [
            ChatResponse(
                content="all done.",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=1, output_tokens=1),
                model="x",
            )
        ]
    )
    checker = FakeGoalChecker([])  # 不会被调
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=None,  # 显式 BC
        goal_checker=checker,
    )
    events = [
        e
        async for e in loop.run(
            messages=[{"role": "user", "content": "hi"}],
            session_id="sid-1",
        )
    ]
    assert any(isinstance(e, FinalEvent) for e in events)
    assert checker.calls == []  # 未调用
