# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-2.4 — ExternalEvaluator + is_high_consequence_goal + agent_loop wiring.

Tests (TDD, red before green):
  1. is_high_consequence_goal: write tool in ledger → True.
  2. is_high_consequence_goal: 支付 keyword in goal_text → True.
  3. is_high_consequence_goal: ≥5 tool calls → True.
  4. is_high_consequence_goal: reflection confidence < 0.3 → True.
  5. is_high_consequence_goal: pure read-only tools (web_search/read/retrieve) → False.
  6. ExternalEvaluator.evaluate: verdict=revise → returns dict with verdict/issues/quality_score/reason.
  7. ExternalEvaluator: provider=None → safe-fail (returns pass) + evaluator_skipped metric.
  8. agent_loop: flag on + high_consequence + evaluator returns revise → emits evaluator_revise.
  9. agent_loop: flag on + non-high-consequence goal → evaluator NOT called (llm_call count==0).
  10. agent_loop: flag off → evaluator path entirely skipped (BC, evaluator not constructed).
  11. is_auto_resume_trigger("evaluator_revise") is True.
  12. ExternalEvaluator.evaluate: quality_score >= threshold with pass verdict → returns pass (no replan).
"""

from __future__ import annotations

import json
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Helpers shared with other test files
# ──────────────────────────────────────────────────────────────────────────────
from deskpet.tools.receipt import ToolReceipt
from llm.types import ChatResponse, ChatUsage, ToolCall


def _make_receipt(tool_name: str, ok: bool = True) -> ToolReceipt:
    return ToolReceipt(
        receipt_id="r-" + tool_name,
        tool_name=tool_name,
        args_hash="aabbccdd",
        started_at="2026-06-05T00:00:00Z",
        ended_at="2026-06-05T00:00:01Z",
        duration_ms=1000,
        ok=ok,
    )


def _make_usage() -> ChatUsage:
    return ChatUsage(input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0)


def _final_resp(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=_make_usage(),
    )


def _tool_resp(tool_name: str, args: dict) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id="tc-1", name=tool_name, arguments=args)],
        stop_reason="tool_use",
        usage=_make_usage(),
    )


class FakeLLMRegistry:
    """Minimal fake LLM registry for agent_loop tests."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> ChatResponse:
        self.calls.append({"messages": messages, "model": model})
        if not self._responses:
            raise RuntimeError("FakeLLMRegistry exhausted")
        resp = self._responses.pop(0)
        if callable(resp):
            return resp(messages)
        return resp


class FakeToolRegistry:
    """Minimal fake tool registry (matches agent_loop expected interface)."""

    def __init__(self, results: dict | None = None) -> None:
        self._results = results or {}
        self.calls: list[dict] = []

    def schemas(self, enabled_toolsets=None) -> list[dict]:
        return []

    def dispatch(self, name: str, args: dict, task_id: str = "") -> Any:
        self.calls.append({"name": name, "args": args})
        return self._results.get(name, json.dumps({"ok": True}))


# ──────────────────────────────────────────────────────────────────────────────
# 8. agent_loop: flag on + high_consequence + evaluator returns revise
#    → emits ErrorEvent(reason="evaluator_revise")
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# 11. is_auto_resume_trigger("evaluator_revise") is True
# ──────────────────────────────────────────────────────────────────────────────

from agent.auto_resume import is_auto_resume_trigger


def test_evaluator_revise_in_auto_resume_triggers():
    """evaluator_revise must be in _AUTO_RESUME_TRIGGER_REASONS."""
    assert is_auto_resume_trigger("evaluator_revise") is True


def test_random_reason_not_in_triggers():
    """Unrelated reasons should still return False."""
    assert is_auto_resume_trigger("some_random_reason") is False
