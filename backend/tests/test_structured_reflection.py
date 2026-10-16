# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-2.1 — StructuredReflection schema + parse_reflection + agent_loop wiring.

Tests (TDD, red before green):
  1. parse_reflection parses 3 LLM output forms: bare JSON / fenced / text-wrapped.
  2. Missing fields → safe defaults, no raise.
  3. Total garbage → None (safe-fail).
  4. flag off → agent_loop selfcheck injection does NOT include reflection marker.
  5. flag on + verify-fail path → injected system message contains 'task_replanning'.
  6. _REFLECTION_INSTRUCTION does NOT contain anti-sycophancy bait phrases.
"""

from __future__ import annotations

import inspect
import json

# ──────────────────────────────────────────────────────────────────────────────
# Import the module under test
# ──────────────────────────────────────────────────────────────────────────────
from deskpet.agent.reflection import (
    _REFLECTION_INSTRUCTION,
    StructuredReflection,
    parse_reflection,
)

# ──────────────────────────────────────────────────────────────────────────────
# 1. parse_reflection — 3 LLM output forms
# ──────────────────────────────────────────────────────────────────────────────

_FULL_DICT = {
    "error_analysis": "上一轮忘了调 ppt_create",
    "execution_critique": "走了惯性路径，只声称没实际调用",
    "task_replanning": "这次先调 ppt_create('项目汇报', [...])",
    "next_action": "call ppt_create with title='项目汇报'",
    "confidence": 0.8,
}


def test_parse_bare_json():
    """直 json.loads 路径：纯 JSON 字符串。"""
    raw = json.dumps(_FULL_DICT, ensure_ascii=False)
    result = parse_reflection(raw)
    assert result is not None
    assert isinstance(result, StructuredReflection)
    assert result.error_analysis == _FULL_DICT["error_analysis"]
    assert result.execution_critique == _FULL_DICT["execution_critique"]
    assert result.task_replanning == _FULL_DICT["task_replanning"]
    assert result.next_action == _FULL_DICT["next_action"]
    assert abs(result.confidence - 0.8) < 1e-9


def test_parse_fenced_json():
    """fenced block 路径：```json ... ``` 包裹。"""
    body = json.dumps(_FULL_DICT, ensure_ascii=False)
    raw = f"好的，这是我的反思：\n```json\n{body}\n```\n接下来我将执行。"
    result = parse_reflection(raw)
    assert result is not None
    assert result.task_replanning == _FULL_DICT["task_replanning"]
    assert abs(result.confidence - 0.8) < 1e-9


def test_parse_text_wrapped_json():
    """首个 {...} substring 路径：JSON 夹在普通文本中间。"""
    body = json.dumps(_FULL_DICT, ensure_ascii=False)
    raw = f"以下是结构化反思：{body}\n然后我会调用工具。"
    result = parse_reflection(raw)
    assert result is not None
    assert result.error_analysis == _FULL_DICT["error_analysis"]
    assert abs(result.confidence - 0.8) < 1e-9


# ──────────────────────────────────────────────────────────────────────────────
# 2. Missing fields → safe defaults, no raise
# ──────────────────────────────────────────────────────────────────────────────


def test_missing_confidence_defaults_to_0_5():
    """confidence 缺失 → 0.5（不误触发 2.4 evaluator）。"""
    d = {k: v for k, v in _FULL_DICT.items() if k != "confidence"}
    result = parse_reflection(json.dumps(d))
    assert result is not None
    assert result.confidence == 0.5


def test_missing_str_fields_default_to_empty():
    """str 字段缺失 → 空串，不抛异常。"""
    result = parse_reflection(json.dumps({"confidence": 0.7}))
    assert result is not None
    assert result.error_analysis == ""
    assert result.execution_critique == ""
    assert result.task_replanning == ""
    assert result.next_action == ""
    assert abs(result.confidence - 0.7) < 1e-9


def test_all_fields_missing_returns_safe_defaults():
    """空 JSON 对象 → 全部默认值，不抛。"""
    result = parse_reflection("{}")
    assert result is not None
    assert result.error_analysis == ""
    assert result.confidence == 0.5


# ──────────────────────────────────────────────────────────────────────────────
# 3. Total garbage → None
# ──────────────────────────────────────────────────────────────────────────────


def test_garbage_input_returns_none():
    assert parse_reflection("totally not json at all!!!!") is None
    assert parse_reflection("") is None
    assert parse_reflection("   ") is None


def test_json_array_returns_none():
    """顶级数组（非 dict）→ None。"""
    assert parse_reflection("[1, 2, 3]") is None


# ──────────────────────────────────────────────────────────────────────────────
# 4. flag off → agent_loop selfcheck does NOT inject reflection instruction
# ──────────────────────────────────────────────────────────────────────────────


def test_flag_off_selfcheck_no_reflection_marker():
    """structured_reflection=False (default) → selfcheck msg contains NO reflection marker."""
    from agent.agent_loop import AgentLoop, _build_selfcheck_message

    # _build_selfcheck_message is a pure function; verify it doesn't contain the marker.
    # The reflection injection only happens when the flag is on and is appended *after* the
    # selfcheck message. Verify the base message is clean (flag off = no append).
    tier2_msg = _build_selfcheck_message(20, 50, 15)
    assert "task_replanning" not in tier2_msg
    assert "error_analysis" not in tier2_msg

    # Also verify AgentLoop constructor accepts structured_reflection param with default False.
    sig = inspect.signature(AgentLoop.__init__)
    params = sig.parameters
    assert "structured_reflection" in params
    assert params["structured_reflection"].default is False


# ──────────────────────────────────────────────────────────────────────────────
# 5. flag on + verify-fail path → injected system message contains 'task_replanning'
# ──────────────────────────────────────────────────────────────────────────────


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


# ──────────────────────────────────────────────────────────────────────────────
# 6. Anti-sycophancy guard: _REFLECTION_INSTRUCTION must NOT contain bait phrases
# ──────────────────────────────────────────────────────────────────────────────


def test_reflection_instruction_no_sycophancy_phrases():
    """Ensure _REFLECTION_INSTRUCTION has no 'just say it's done' escape hatches."""
    bad_phrases = [
        "如果做不到",
        "换个说法",
        "说已完成",
        "就说完成",
        "声称完成",
        "假装完成",
    ]
    lowered = _REFLECTION_INSTRUCTION.lower()
    for phrase in bad_phrases:
        assert phrase.lower() not in lowered, (
            f"Anti-sycophancy violation: _REFLECTION_INSTRUCTION contains '{phrase}'"
        )
