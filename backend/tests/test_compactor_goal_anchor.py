# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FP-2 Task 2 — WI-1.3 TDD: history_compactor goal_text anchor + agent_loop
decision-point anchor.

Test matrix
-----------
1. test_compact_bc_when_goal_none
   compact_messages(goal_text=None) → output byte-identical to omitting kwarg.

2. test_compact_injects_anchor
   compact_messages(goal_text="整理纪要") → output contains a system message
   with "[目标锚定]" and "整理纪要".

3. test_compact_anchor_not_injected_when_should_not_compact
   Below threshold + goal_text set → early return with no anchor (BC path).

4. test_compact_anchor_not_injected_on_empty_range
   Above threshold but nothing to compact (all recent) + goal_text → no anchor.

5. test_compact_anchor_not_injected_on_summarize_fail
   summarize_fn raises + goal_text → returns original (BC safe-fail).

6. test_compact_anchor_not_injected_on_empty_summary
   summarize_fn returns "" + goal_text → returns original (BC safe-fail).

7. test_compact_anchor_message_format
   Verify exact message prefix "[目标锚定] 当前目标：" and suffix text.

8. test_compact_anchor_is_appended_after_inject_summary
   The anchor system message is the LAST message in the returned list.

9. test_agent_loop_goal_anchor_module_const
   _GOAL_ANCHOR_EVERY = 5 exists in agent_loop module.

10. test_agent_loop_injects_anchor_at_anchor_every_iteration
    Run a stub AgentLoop: verify a system message containing "[目标锚定]"
    appears in working_messages at iterations that are multiples of
    _GOAL_ANCHOR_EVERY when an active goal exists.

11. test_agent_loop_no_anchor_when_no_goal
    Same loop, no goal → no "[目标锚定]" message injected (BC).

12. test_agent_loop_anchor_deduped_same_iteration
    _last_anchor_iter bookkeeping: anchor message injected exactly once per
    qualifying iteration, not twice.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent.history_compactor import (
    DEFAULT_MESSAGE_THRESHOLD,
    compact_messages,
)

# ─────────────────────── helpers ───────────────────────


def _big_messages(
    *,
    count: int = DEFAULT_MESSAGE_THRESHOLD + 5,
    chars_each: int = 10,
    extra_system: int = 1,
) -> list[dict[str, Any]]:
    """Build a conversation that exceeds message threshold.

    Structure: ``extra_system`` system messages at head, then alternating
    user/assistant to fill ``count`` non-system messages.
    """
    msgs: list[dict[str, Any]] = []
    for i in range(extra_system):
        msgs.append({"role": "system", "content": f"system-{i}"})
    roles = ["user", "assistant"]
    for i in range(count):
        msgs.append({"role": roles[i % 2], "content": "x" * chars_each + f"-{i}"})
    return msgs


async def _fake_summarize(text: str) -> str:
    return "SUMMARY"


async def _raising_summarize(text: str) -> str:
    raise RuntimeError("LLM down")


async def _empty_summarize(text: str) -> str:
    return ""


# ─────────────────────── 1. BC when goal_text=None ───────────────────────


@pytest.mark.asyncio
async def test_compact_bc_when_goal_none():
    """goal_text=None must produce byte-identical output to omitting the kwarg."""
    msgs = _big_messages()
    result_with_none = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text=None,
    )
    result_without = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
    )
    # Should be structurally identical
    assert result_with_none == result_without


# ─────────────────────── 2. Anchor injected when goal_text set ───────────────────────


@pytest.mark.asyncio
async def test_compact_injects_anchor():
    """goal_text='整理纪要' → output has a system message with [目标锚定] + goal text."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="整理纪要",
    )
    anchor_msgs = [
        m for m in result if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs, "Expected at least one [目标锚定] system message"
    anchor_content = anchor_msgs[0]["content"]
    assert "整理纪要" in anchor_content


# ─────────────────────── 3. No anchor on early-return (should_compact=False) ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_when_should_not_compact():
    """Below threshold → should_compact=False → early return, no anchor injected."""
    # Only 2 messages — well below DEFAULT_MESSAGE_THRESHOLD
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="some goal",
    )
    anchor_msgs = [
        m for m in result if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs == [], "No anchor on below-threshold early-return"


# ─────────────────────── 4. No anchor on empty compact range ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_on_empty_range():
    """Above threshold but select_compactable_range returns empty → no anchor."""
    # All recent — keep_recent=999 means start>=end → no anchor either
    msgs = _big_messages(count=DEFAULT_MESSAGE_THRESHOLD + 1)
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="goal",
        keep_recent=1000,  # huge keep_recent → nothing to compact
    )
    anchor_msgs = [
        m for m in result if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs == [], "No anchor when compact range is empty"


# ─────────────────────── 5. No anchor on summarize failure ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_on_summarize_fail():
    """summarize_fn raises → returns original list unchanged (no anchor)."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_raising_summarize,
        goal_text="goal on failure",
    )
    # Should be a shallow copy of original (no modifications)
    assert result == list(msgs)
    anchor_msgs = [m for m in result if "[目标锚定]" in str(m.get("content", ""))]
    assert anchor_msgs == []


# ─────────────────────── 6. No anchor on empty summary ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_on_empty_summary():
    """summarize_fn returns '' → returns original list unchanged (no anchor)."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_empty_summarize,
        goal_text="goal on empty summary",
    )
    assert result == list(msgs)
    anchor_msgs = [m for m in result if "[目标锚定]" in str(m.get("content", ""))]
    assert anchor_msgs == []


# ─────────────────────── 7. Exact anchor message format ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_message_format():
    """Anchor message must start with '[目标锚定] 当前目标：' and contain goal text."""
    goal = "完成报告"
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text=goal,
    )
    anchor_msgs = [
        m for m in result if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs, "anchor message must exist"
    content = anchor_msgs[0]["content"]
    assert content.startswith("[目标锚定] 当前目标："), (
        f"Expected prefix '[目标锚定] 当前目标：', got: {content[:80]!r}"
    )
    assert goal in content
    # Must also contain the "don't drift" reminder text
    assert "不要被中间步骤带偏" in content


# ─────────────────────── 8. Anchor is LAST message ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_is_appended_after_inject_summary():
    """The anchor system message must be the LAST element of the returned list."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="tail goal",
    )
    last_msg = result[-1]
    assert last_msg.get("role") == "system"
    assert "[目标锚定]" in str(last_msg.get("content", ""))


# ─────────────────────── agent_loop helpers ───────────────────────


def _make_final_response():
    from llm.types import ChatResponse, ChatUsage

    return ChatResponse(
        content="done",
        stop_reason="end_turn",
        usage=ChatUsage(input_tokens=5, output_tokens=3),
        model="test-model",
    )


def _make_tool_response(tool_name: str = "my_tool"):
    from llm.types import ChatResponse, ChatUsage, ToolCall

    return ChatResponse(
        content="",
        stop_reason="tool_use",
        tool_calls=[
            ToolCall(id="tc1", name=tool_name, arguments={"x": 1}),
        ],
        usage=ChatUsage(input_tokens=5, output_tokens=3),
        model="test-model",
    )


class _RecordingLLM:
    """LLM that records all messages it receives and returns programmed responses."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.all_messages: list[list[dict]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ):
        self.all_messages.append(list(messages))
        if not self._responses:
            raise AssertionError("_RecordingLLM ran out of responses")
        return self._responses.pop(0)


class _NullToolRegistry:
    def schemas(self, enabled_toolsets=None):
        return []

    def dispatch(self, name: str, args: dict, task_id: str) -> str:
        return '{"ok": true}'
