# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Phase 1 单测 — compaction-bestpractice-upgrade WI-1/2/3/4a。

对照 plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md §4 的可证伪断言。
"""
from __future__ import annotations

import pytest

from deskpet.agent.context_compressor import (
    ContextCompressor,
    _extract_prior_summary,
    _microcompact_tool_results,
    _format_summary,
    _SUMMARY_MARKER,
)


# ───────────────────────────── WI-1 buffer 触发 ─────────────────────────────
class TestWI1BufferTrigger:
    @pytest.mark.parametrize(
        "window,eff_pct,compact_pct,exp_reserve,exp_trigger",
        [
            # gpt-5.5 400K: reserve=12500, trigger=min(320000, 380000-12500)=320000
            (400_000, 0.95, 0.80, 12_500, 320_000),
            # deepseek 1M: reserve=31250, trigger=min(750000, 950000-31250)=750000
            (1_000_000, 0.95, 0.75, 31_250, 750_000),
            # _default 32K: reserve=8000, trigger=min(25600, 28800-8000)=20800
            (32_000, 0.90, 0.80, 8_000, 20_800),
        ],
    )
    def test_buffer_formula_and_trigger_line(
        self, window, eff_pct, compact_pct, exp_reserve, exp_trigger
    ):
        c = ContextCompressor(
            context_window=window,
            threshold_percent=compact_pct,
            effective_pct=eff_pct,
        )
        assert c.output_reserve() == exp_reserve
        assert c.trigger_tokens() == exp_trigger

    def test_trigger_boundary(self):
        # _default 32K → trigger 20800
        c = ContextCompressor(
            context_window=32_000, threshold_percent=0.80, effective_pct=0.90
        )
        assert c.should_compress(20_799) is False
        assert c.should_compress(20_800) is True

    def test_bc_when_no_effective_pct(self):
        """effective_pct=None → 纯比例阈值(旧单测/BC),buffer 不参与。"""
        c = ContextCompressor(context_window=1000, threshold_percent=0.75)
        assert c.trigger_tokens() == 750
        assert c.should_compress(749) is False
        assert c.should_compress(750) is True

    def test_tiny_window_falls_back_to_ratio(self):
        """极小窗口 eff_win-buffer<0 → 忽略 buffer 项,退回比例阈值(不会全触发)。"""
        c = ContextCompressor(
            context_window=1000, threshold_percent=0.75, effective_pct=0.95
        )
        # eff_win=950, reserve=8000 → buffer_line 负 → 退回 750
        assert c.trigger_tokens() == 750
        assert c.should_compress(749) is False


# ───────────────────────────── WI-2 microcompact ─────────────────────────────
class TestWI2Microcompact:
    def _msgs_with_tools(self, n_tools=5):
        out = [{"role": "system", "content": "sys"}]
        for i in range(n_tools):
            out.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": f"tc{i}", "function": {"name": "web_fetch"}}
                    ],
                }
            )
            out.append(
                {"role": "tool", "tool_call_id": f"tc{i}", "content": "RESULT" * 2000}
            )
        out.append({"role": "user", "content": "继续"})
        return out

    def test_prunes_stale_keeps_recent_and_shell(self):
        msgs = self._msgs_with_tools(5)
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=2)
        assert n == 3  # 5 个 tool,最近 2 个保护 → 清 3 个
        tools = [m for m in out if m.get("role") == "tool"]
        assert len(tools) == 5  # 整条壳保留,绝不删整条
        # 最近 2 个原文保留
        assert tools[-1]["content"] == "RESULT" * 2000
        assert tools[-2]["content"] == "RESULT" * 2000
        # 更早 3 个换占位,但 tool_call_id + role 保留
        for t in tools[:3]:
            assert "已清理" in t["content"]
            assert t["tool_call_id"].startswith("tc")
            assert t["role"] == "tool"

    def test_no_tools_noop(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ho"}]
        out, n = _microcompact_tool_results(msgs, keep_recent_tools=3)
        assert n == 0
        assert out == msgs

    def test_idempotent_placeholder_not_recounted(self):
        msgs = self._msgs_with_tools(5)
        out1, n1 = _microcompact_tool_results(msgs, keep_recent_tools=2)
        out2, n2 = _microcompact_tool_results(out1, keep_recent_tools=2)
        assert n1 == 3
        assert n2 == 0  # 占位串不再被重复清理

    @pytest.mark.asyncio
    async def test_compress_microcompact_only_skips_haiku(self):
        """microcompact 后已降到触发线下 → 不调 haiku(mock 计数=0)。"""

        class _CountingLLM:
            def __init__(self):
                self.calls = 0

            async def chat_with_fallback(self, *a, **k):
                self.calls += 1

                class R:
                    content = "S"

                return R()

        llm = _CountingLLM()
        # window 大到 microcompact 后总量 < trigger
        c = ContextCompressor(
            llm_registry=llm, context_window=200_000, threshold_percent=0.75,
            microcompact_keep_tools=1,
        )
        msgs = self._msgs_with_tools(5)
        r = await c.compress(msgs)
        assert r.compressed is True
        assert r.meta.get("reason") == "microcompact_only"
        assert llm.calls == 0  # 没调模型

    @pytest.mark.asyncio
    async def test_microcompact_output_no_orphan_tool(self):
        from deskpet.agent.context_compressor import _sanitize_tool_pairs

        msgs = self._msgs_with_tools(5)
        out, _ = _microcompact_tool_results(msgs, keep_recent_tools=2)
        sanitized = _sanitize_tool_pairs(out)
        # 占位后每个 tool 仍有配对 assistant → 不产孤儿(条数不减)
        assert len([m for m in sanitized if m.get("role") == "tool"]) == 5


# ───────────────────────── WI-3 结构化摘要 + 锚定增量 ─────────────────────────
class TestWI3StructuredSummaryAndAnchoring:
    def test_summary_system_has_full_schema(self):
        s = ContextCompressor._SUMMARY_SYSTEM
        for seg in ["意图", "进行中", "已完成", "关键事实", "文件", "待办", "下一步"]:
            assert seg in s, f"missing segment: {seg}"

    def test_extract_prior_summary_pure(self):
        prior_msg = {"role": "assistant", "content": _format_summary("旧摘要内容")}
        msgs = [
            {"role": "user", "content": "a"},
            prior_msg,
            {"role": "assistant", "content": "b"},
        ]
        prior, kept = _extract_prior_summary(msgs)
        assert prior == "旧摘要内容"
        # 旧摘要从待摘列表剔除
        assert all(_SUMMARY_MARKER not in str(m.get("content")) for m in kept)
        assert len(kept) == 2

    def test_extract_prior_none_when_absent(self):
        msgs = [{"role": "user", "content": "x"}]
        prior, kept = _extract_prior_summary(msgs)
        assert prior is None
        assert kept == msgs

    @pytest.mark.asyncio
    async def test_summary_transcript_excludes_old_marker(self):
        """喂 haiku 的待摘 transcript 不含旧 [压缩摘要] 前缀(防套娃)。"""

        captured = {}

        class _LLM:
            async def chat_with_fallback(self, messages, **k):
                captured["user"] = messages[1]["content"]
                captured["system"] = messages[0]["content"]

                class R:
                    content = "NEW SUMMARY"

                return R()

        c = ContextCompressor(llm_registry=_LLM(), first_n=1, last_n=1)
        old = {"role": "assistant", "content": _format_summary("PRIOR-STATE-XYZ")}
        msgs = [{"role": "user", "content": "u0"}]
        msgs += [{"role": "assistant", "content": f"mid{i}" * 30} for i in range(3)]
        msgs.insert(2, old)
        msgs += [{"role": "user", "content": "u-last"}]
        await c.compress(msgs)
        assert _SUMMARY_MARKER not in captured["user"]
        # prior 作为独立段拼进 system
        assert "PRIOR-STATE-XYZ" in captured["system"]
        assert "已有摘要" in captured["system"]

    @pytest.mark.asyncio
    async def test_no_nesting_single_summary_after_two_compressions(self):
        class _LLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = "SUMMARY-CONTENT"

                return R()

        c = ContextCompressor(llm_registry=_LLM(), first_n=1, last_n=1)
        msgs = [{"role": "user", "content": "u0"}]
        msgs += [{"role": "assistant", "content": f"m{i}" * 40} for i in range(8)]
        msgs += [{"role": "user", "content": "u-last"}]
        r1 = await c.compress(msgs)
        # 第二次压缩(把第一次产物再压)
        more = list(r1.messages) + [
            {"role": "assistant", "content": f"x{i}" * 40} for i in range(6)
        ] + [{"role": "user", "content": "u-last2"}]
        r2 = await c.compress(more)
        n_summary = sum(
            1 for m in r2.messages
            if str(m.get("content") or "").lstrip().startswith(_SUMMARY_MARKER)
        )
        assert n_summary <= 1, f"摘要套娃: {n_summary} 条 [压缩摘要]"


# ───────────────────────────── WI-4a 目标 always-on ─────────────────────────────
class TestWI4aGoalAnchor:
    @pytest.mark.asyncio
    async def test_compress_dedups_when_anchor_already_present(self):
        """system 段已有 [目标锚定] → compress 不再注第二条(≤1 DoD)。"""

        class _LLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = "S"

                return R()

        c = ContextCompressor(llm_registry=_LLM())
        msgs = [
            {"role": "system", "content": "[目标锚定] 当前目标：已存在"},
        ]
        msgs += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" * 50}
                 for i in range(30)]
        r = await c.compress(msgs, goal_text="新目标会想注入但应被去重")
        anchors = [
            m for m in r.messages
            if m.get("role") == "system" and str(m.get("content")).startswith("[目标锚定]")
        ]
        assert len(anchors) == 1
        assert "已存在" in anchors[0]["content"]

    @pytest.mark.asyncio
    async def test_compress_still_injects_when_no_existing_anchor(self):
        """无 always-on 锚(独立调用,如 CLI/单测) → 仍按 goal_text 注一条(BC)。"""

        class _LLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = "S"

                return R()

        c = ContextCompressor(llm_registry=_LLM())
        msgs = [{"role": "system", "content": "sys"}]
        msgs += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" * 50}
                 for i in range(30)]
        r = await c.compress(msgs, goal_text="独立目标")
        anchors = [
            m for m in r.messages
            if m.get("role") == "system" and str(m.get("content")).startswith("[目标锚定]")
        ]
        assert len(anchors) == 1
        assert "独立目标" in anchors[0]["content"]
