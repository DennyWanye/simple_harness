# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-4.0 — AgentLoop compaction wiring tests.

Verifies that:
1. AgentLoop accepts ``compressor=`` param (BC: default None).
2. compressor=None → no compress() call ever (flag off BC).
3. should_compress True → compress() called → working_messages replaced.
4. skill_prelude (role=system) survives compaction (_partition keeps system).
5. goal_text is passed to compress → [目标锚定] anchor in post-compaction msgs.
"""

from __future__ import annotations

from agent.agent_loop import AgentLoop

# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class _FakeToolRegistry:
    """Minimal ToolRegistry — no tool calls, just enough for AgentLoop init."""

    def schemas(self, enabled_toolsets=None):
        return []

    async def execute_tool(self, name, args, task_id):
        return '{"ok": true, "result": "noop"}'


class _FakeLLM:
    """Mock LLM that returns an end_turn response immediately."""

    def __init__(self):
        self.calls = 0

    async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
        self.calls += 1
        from llm.types import ChatResponse

        return ChatResponse(
            content="done",
            stop_reason="end_turn",
            tool_calls=[],
            usage={"input_tokens": 100, "output_tokens": 10},
        )


class _FakeContextManager:
    """Minimal ContextManager facade with a per-session compaction threshold."""

    def __init__(self, *, estimated_tokens: int, compact_at_tokens: int):
        from agent.token_budget import BudgetCheck, BudgetCheckResult

        self.result = BudgetCheckResult(
            verdict=BudgetCheck.OK,
            estimated_tokens=estimated_tokens,
            context_window=32000,
            ratio=estimated_tokens / 32000,
        )

        class _Cfg:
            pass

        self.config = _Cfg()
        self.config.compact_at_tokens = compact_at_tokens

    def check_budget(self, messages, *, model: str, real_prompt_tokens_floor: int = 0):
        from agent.token_budget import BudgetCheckResult

        estimated = max(self.result.estimated_tokens, int(real_prompt_tokens_floor or 0))
        return BudgetCheckResult(
            verdict=self.result.verdict,
            estimated_tokens=estimated,
            context_window=self.result.context_window,
            ratio=estimated / self.result.context_window,
        )

    def record_tool_result(self, *, tool_name: str, result: str):
        return result, None


def _make_msgs(n_non_system: int = 4, with_system: bool = True):
    """Build a short message list with optional system prefix."""
    out: list[dict] = []
    if with_system:
        out.append({"role": "system", "content": "[skill_prelude] instructions here"})
    for i in range(n_non_system):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"msg {i}"})
    return out


# ---------------------------------------------------------------------------
# BC: new param exists with default None
# ---------------------------------------------------------------------------


def test_agent_loop_accepts_compressor_param():
    """AgentLoop.__init__ accepts compressor= kwarg with default None (BC)."""
    import inspect

    sig = inspect.signature(AgentLoop.__init__)
    params = sig.parameters
    assert "compressor" in params, "compressor param missing from AgentLoop.__init__"
    assert params["compressor"].default is None, "compressor default must be None"


def test_agent_loop_compressor_none_by_default():
    """When compressor not passed, self.compressor is None."""
    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
    )
    assert loop.compressor is None


# ---------------------------------------------------------------------------
# config.py: compaction_enabled flag exists
# ---------------------------------------------------------------------------


def test_config_has_no_dead_compaction_flag():
    """NEXT-TG-1.0 §9（2026-09-28）：``compaction_enabled`` 没有任何读取方（压缩总是开），
    写着"开"却不生效的假开关已删除。"""
    from config import AppConfig

    cfg = AppConfig()
    assert not hasattr(cfg.features, "compaction_enabled")


# ---------------------------------------------------------------------------
# build_agent: compressor wired when flag on, None when off
# ---------------------------------------------------------------------------


def test_context_compressor_in_valid_services():
    """FP-5 WI-4.0 真机回归: main.py 无条件 register('context_compressor'),
    缺白名单会致启动 register 失败 + chat get 抛 'Unknown service' → code-mode 全崩。"""
    from context import _VALID_SERVICES

    assert "context_compressor" in _VALID_SERVICES


# ---------------------------------------------------------------------------
# FP-2 TC-2.1 第 3 刀 — relay 真实 prompt_tokens 反馈回路。
# 真机实测两轮系数校准后估算仍低估(real 32.9k 时 estimate <24k 不触发)。
# 修法: compaction 判定用 max(estimate, 上一轮 response.usage.input_tokens)。
# ---------------------------------------------------------------------------


class _HighUsageTwoTurnLLM:
    """第 1 轮回 tool_use 且 usage.input_tokens=30000(模拟 relay 真实值),
    第 2 轮 end_turn。compaction 检查在第 2 轮 LLM 调用前 → 应收到 ≥30000。"""

    def __init__(self):
        self.calls = 0

    async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
        self.calls += 1
        from llm.types import ChatResponse, ToolCall

        if self.calls == 1:
            return ChatResponse(
                content="",
                stop_reason="tool_use",
                tool_calls=[ToolCall(id="t1", name="noop", arguments={})],
                usage={"input_tokens": 30000, "output_tokens": 10},
            )
        return ChatResponse(
            content="done",
            stop_reason="end_turn",
            tool_calls=[],
            usage={"input_tokens": 30100, "output_tokens": 5},
        )
