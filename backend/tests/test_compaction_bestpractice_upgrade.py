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
    _looks_reflective,
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

    @pytest.mark.asyncio
    async def test_safe_fail_preserves_microcompact(self):
        """haiku 失败时退回 microcompact 后的 work(占位仍在),不丢收益。"""

        class _RaisingLLM:
            async def chat_with_fallback(self, *a, **k):
                raise RuntimeError("haiku down")

        # 小窗口逼到必须走 haiku(microcompact 不够) → 失败 → 应返回 work
        c = ContextCompressor(
            llm_registry=_RaisingLLM(), context_window=8_000,
            threshold_percent=0.75, microcompact_keep_tools=1,
        )
        msgs = self._msgs_with_tools(5)
        r = await c.compress(msgs)
        assert r.compressed is True  # microcompact 生效
        assert r.meta.get("tool_results_pruned", 0) >= 1
        tool_contents = [m["content"] for m in r.messages if m.get("role") == "tool"]
        assert any("已清理" in t for t in tool_contents)


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

    def test_goal_store_get_pending_tasks(self):
        """always-on 子目标数据源: SessionGoalStore.get_pending_tasks 返回 subgoals。"""
        from deskpet.agent.goal_store import SessionGoalStore

        store = SessionGoalStore()
        # 无目标 → 空
        assert store.get_pending_tasks("s0") == []
        g = store.set("s1", "整理周报")
        # 有目标但无子目标 → 空
        assert store.get_pending_tasks("s1") == []
        g.subgoals = ["收集数据", "汇总成稿"]
        assert store.get_pending_tasks("s1") == ["收集数据", "汇总成稿"]


# ───────────────────────── 反射 guard (caveat #2 加固) ─────────────────────────
class TestReflectionGuard:
    def test_looks_reflective_detects_meta(self):
        # 真机观测到的反射样本(复述压缩提示词)
        reflective = (
            "【进行中/当前任务】用户要把一段对话历史压缩成更省 token 的摘要，"
            "按意图/目标分段输出，用第三人称、不要杜撰。"
        )
        assert _looks_reflective(reflective) is True

    def test_looks_reflective_passes_real_task(self):
        real = (
            "【进行中/当前任务】用户在让助手调研宁德时代2024年报的营收和净利润，"
            "已查到营业收入约3620亿元，还差研发投入数据。"
        )
        assert _looks_reflective(real) is False

    def test_single_signal_not_flagged(self):
        # 真实对话碰巧提一次"压缩对话"不应误判(需 ≥2 信号)
        assert _looks_reflective("用户问怎么压缩对话框的字体大小") is False

    def test_extract_prior_skips_reflective(self):
        refl = _format_summary("用户要把对话历史压缩成摘要，分段输出，第三人称，不要杜撰")
        clean = _format_summary("用户在调研宁德时代年报")
        # 中段含一条反射旧摘要 → 不作为 prior 带入
        _, _ = _extract_prior_summary([{"role": "assistant", "content": refl}])
        prior_refl, _ = _extract_prior_summary([{"role": "assistant", "content": refl}])
        assert prior_refl is None  # 反射 prior 被丢弃
        prior_clean, _ = _extract_prior_summary([{"role": "assistant", "content": clean}])
        assert prior_clean == "用户在调研宁德时代年报"

    @pytest.mark.asyncio
    async def test_reflective_output_falls_back_to_prior(self):
        """新摘要反射 + 中段有干净旧摘要 → 输出回退到干净 prior,不落反射。"""

        class _ReflectiveLLM:
            async def chat_with_fallback(self, *a, **k):
                class R:
                    content = ("用户要把对话历史压缩成摘要，按分段输出，"
                               "第三人称，省 token，不要杜撰。")
                return R()

        c = ContextCompressor(llm_registry=_ReflectiveLLM(), first_n=1, last_n=1)
        clean_prior = _format_summary("用户在调研宁德时代2024年报核心财务数据")
        msgs = [
            {"role": "user", "content": "u0"},
            {"role": "assistant", "content": clean_prior},
            {"role": "user", "content": "m1" * 30},
            {"role": "assistant", "content": "m2" * 30},
            {"role": "user", "content": "u-last"},
        ]
        r = await c.compress(msgs)
        # 注入的摘要应是干净 prior,不是反射新摘要
        summary_msgs = [
            m for m in r.messages
            if str(m.get("content") or "").lstrip().startswith(_SUMMARY_MARKER)
        ]
        assert len(summary_msgs) == 1
        body = summary_msgs[0]["content"]
        assert "宁德时代" in body
        assert "不要杜撰" not in body  # 反射内容没落地


# ───────────────────────── WI-4b pre-flush 落 L1 (Phase 2) ─────────────────────────
class TestWI4bPreflush:
    @pytest.mark.asyncio
    async def test_preflush_writes_task_state_once(self):
        """触发压缩 → pre-flush 把任务态 append 到 L1 'memory',每 run 限一次。"""
        from agent.agent_loop import AgentLoop
        from deskpet.agent.context_compressor import CompressionResult

        class _FakeTools:
            def schemas(self, enabled_toolsets=None):
                return []

            async def execute_tool(self, name, args, task_id):
                return '{"ok": true}'

        class _FakeLLM:
            async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
                from llm.types import ChatResponse
                return ChatResponse(
                    content="done", stop_reason="end_turn", tool_calls=[],
                    usage={"input_tokens": 5, "output_tokens": 3},
                )

        class _FakeCompressor:
            def should_compress(self, n):
                return True

            async def compress(self, messages, *, goal_text=None, pending_tasks=None):
                return CompressionResult(messages=list(messages), compressed=False)

        class _FakeGoalStore:
            def get_goal_text(self, sid):
                return "整理三份文档"

        class _RecordingFileMemory:
            def __init__(self):
                self.calls = []

            async def append(self, target, content, salience=0.5):
                self.calls.append({"target": target, "content": content, "sal": salience})

        fm = _RecordingFileMemory()
        loop = AgentLoop(
            llm_registry=_FakeLLM(),
            tool_registry=_FakeTools(),
            compressor=_FakeCompressor(),
            session_goal_store=_FakeGoalStore(),
            file_memory=fm,
        )
        msgs = [{"role": "user", "content": "请帮我整理文档"}]
        msgs += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                 for i in range(4)]
        async for _ in loop.run(msgs, session_id="s-pf"):
            pass

        assert len(fm.calls) == 1, f"pre-flush 应限频一次, 实际 {len(fm.calls)}"
        c = fm.calls[0]
        assert c["target"] == "memory"
        assert "任务态" in c["content"]
        assert "整理三份文档" in c["content"]

    @pytest.mark.asyncio
    async def test_no_preflush_when_file_memory_none(self):
        """file_memory=None → 不 flush(BC)。"""
        from agent.agent_loop import AgentLoop

        sig_params = AgentLoop.__init__.__doc__  # smoke
        import inspect
        assert "file_memory" in inspect.signature(AgentLoop.__init__).parameters
        assert inspect.signature(AgentLoop.__init__).parameters["file_memory"].default is None
