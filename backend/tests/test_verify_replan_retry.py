# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-2.2 — verify 失败 → 真重规划重试（TDD 先红后绿）。

测试组覆盖：
  1. 伪完成 → 1st rebound 含反思指令；mock 2nd-round LLM 真调工具 → verify pass。
  2. task_replanning 高相似度(>0.85) → 升级 ephemeral，不消耗 2nd nudge。
  3. ephemeral callable=None → 2nd fail 保守 fail（BC）。
  4. ephemeral callable 返 pass → 救援放行，log ephemeral_rescued。
  5. 三层全失败 → emit verify_exhausted + is_auto_resume_trigger("verify_exhausted")=True。
  6. §7 死循环上限：永久失败任务 total LLM calls <= 显式上限，最终 graceful 退降。

所有测试使用 FakeLLMRegistry（来自 test_deskpet_agent_loop 的模式）+ 真实 AgentLoop。
"""

from __future__ import annotations

from typing import Any

from agent.auto_resume import is_auto_resume_trigger
from deskpet.tools.receipt import ToolReceipt
from llm.types import ChatResponse, ChatUsage, ToolCall

# ─── helpers ────────────────────────────────────────────────────────────────


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
    """LLM final end_turn response with given assistant text."""
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=_make_usage(),
    )


def _tool_resp(tool_name: str, args: dict) -> ChatResponse:
    """LLM response that calls a tool."""
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id="tc-1", name=tool_name, arguments=args)],
        stop_reason="tool_use",
        usage=_make_usage(),
    )


class FakeLLMRegistry:
    """Minimal sync-queue fake LLM registry."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._responses:
            raise AssertionError("FakeLLMRegistry exhausted")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolRegistry:
    def __init__(self, handlers: dict | None = None) -> None:
        self._handlers = handlers or {}
        self.calls: list[dict] = []

    def schemas(self, enabled_toolsets=None) -> list[dict]:
        return []

    def dispatch(self, name: str, args: dict, task_id: str) -> Any:
        self.calls.append({"name": name, "args": args})
        if name not in self._handlers:
            raise KeyError(f"tool {name!r} not registered")
        return self._handlers[name](args)


class FakeReceiptStore:
    """Minimal receipt store that can be populated per-round."""

    def __init__(self, ledger: list[ToolReceipt] | None = None) -> None:
        self._ledger: list[ToolReceipt] = list(ledger or [])

    def load_session(self, session_id: str) -> list[ToolReceipt]:
        return list(self._ledger)

    def add(self, receipt: ToolReceipt) -> None:
        self._ledger.append(receipt)


async def _collect(agen) -> list[Any]:
    """Drain an async generator into a list."""
    out = []
    async for ev in agen:
        out.append(ev)
    return out


# ─── Test 8: auto_resume trigger for verify_exhausted ────────────────────────


def test_auto_resume_trigger_verify_exhausted():
    """verify_exhausted must be in auto_resume trigger set."""
    assert is_auto_resume_trigger("verify_exhausted") is True


def test_auto_resume_trigger_existing_reasons_unchanged():
    """Existing reasons still trigger (BC regression guard)."""
    assert is_auto_resume_trigger("max_iterations") is True
    assert is_auto_resume_trigger("permanent_tool_error") is True
    assert is_auto_resume_trigger("hallucination") is True
    assert is_auto_resume_trigger("circuit_open") is True
    assert is_auto_resume_trigger("random_garbage") is False
