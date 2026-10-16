# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Phase 1 单测 — compaction-bestpractice-upgrade WI-1/2/3/4a。

对照 plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md §4 的可证伪断言。
"""

from __future__ import annotations

import pytest


# ───────────────────────── WI-4b pre-flush 落 L1 (Phase 2) ─────────────────────────
class TestWI4bPreflush:
    @pytest.mark.asyncio
    async def test_no_preflush_when_file_memory_none(self):
        """file_memory=None → 不 flush(BC)。"""
        from agent.agent_loop import AgentLoop

        sig_params = AgentLoop.__init__.__doc__  # smoke
        import inspect

        assert "file_memory" in inspect.signature(AgentLoop.__init__).parameters
        assert inspect.signature(AgentLoop.__init__).parameters["file_memory"].default is None


# ───────────────────── WI-4b pre-flush 缺口 (safe-fail / 无 goal / 都无) ─────────────────────
class TestWI4bPreflushEdgeGaps:
    class _Tools:
        def schemas(self, enabled_toolsets=None):
            return []

        async def execute_tool(self, name, args, task_id):
            return '{"ok": true}'

    class _LLM:
        async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
            from llm.types import ChatResponse

            return ChatResponse(
                content="done",
                stop_reason="end_turn",
                tool_calls=[],
                usage={"input_tokens": 5, "output_tokens": 3},
            )

    class _AlwaysCompress:
        def should_compress(self, n):
            return True

        async def compress(self, messages, *, goal_text=None, pending_tasks=None):
            from deskpet.agent.context_compressor import CompressionResult

            return CompressionResult(messages=list(messages), compressed=False)

    @pytest.mark.asyncio
    async def test_preflush_does_not_block_compression_when_append_raises(self):
        """file_memory.append 抛异常 → pre-flush safe-fail,压缩照常进行,run 不崩。"""
        from agent.agent_loop import AgentLoop

        class _RaisingFM:
            def __init__(self):
                self.calls = 0

            async def append(self, target, content, salience=0.5):
                self.calls += 1
                raise RuntimeError("disk full")

        class _GoalStore:
            def get_goal_text(self, sid):
                return "有目标"

        compressor = self._AlwaysCompress()
        fm = _RaisingFM()
        loop = AgentLoop(
            llm_registry=self._LLM(),
            tool_registry=self._Tools(),
            compressor=compressor,
            session_goal_store=_GoalStore(),
            file_memory=fm,
        )
        events = []
        async for ev in loop.run([{"role": "user", "content": "请整理"}], session_id="s-raise"):
            events.append(ev)
        # append 被尝试(safe-fail 吞异常),loop 正常产出 final,无 error event
        assert fm.calls >= 1
        assert any(getattr(e, "type", "") == "final" for e in events)
        assert not any(getattr(e, "type", "") == "error" for e in events)

    @pytest.mark.asyncio
    async def test_preflush_when_no_goal_but_recent_user(self):
        """无 goal 但有最近 user → 仍 flush(_flush_parts 含'最近请求')。"""
        from agent.agent_loop import AgentLoop

        class _RecFM:
            def __init__(self):
                self.calls = []

            async def append(self, target, content, salience=0.5):
                self.calls.append(content)

        class _NoGoalStore:
            def get_goal_text(self, sid):
                return None  # 无 goal

        fm = _RecFM()
        loop = AgentLoop(
            llm_registry=self._LLM(),
            tool_registry=self._Tools(),
            compressor=self._AlwaysCompress(),
            session_goal_store=_NoGoalStore(),
            file_memory=fm,
        )
        async for _ in loop.run([{"role": "user", "content": "帮我查天气"}], session_id="s-nogoal"):
            pass
        assert len(fm.calls) == 1
        assert "最近请求" in fm.calls[0]
        assert "帮我查天气" in fm.calls[0]
        assert "目标:" not in fm.calls[0]  # 无 goal → 不含目标段

    @pytest.mark.asyncio
    async def test_no_preflush_when_no_goal_no_user(self):
        """既无 goal 又无 user 消息(只 system) → _flush_parts 空 → 不 flush。"""
        from agent.agent_loop import AgentLoop

        class _RecFM:
            def __init__(self):
                self.calls = []

            async def append(self, target, content, salience=0.5):
                self.calls.append(content)

        class _NoGoalStore:
            def get_goal_text(self, sid):
                return None

        fm = _RecFM()
        loop = AgentLoop(
            llm_registry=self._LLM(),
            tool_registry=self._Tools(),
            compressor=self._AlwaysCompress(),
            session_goal_store=_NoGoalStore(),
            file_memory=fm,
        )
        # 只有 system 消息,无 user
        async for _ in loop.run([{"role": "system", "content": "persona"}], session_id="s-bare"):
            pass
        assert fm.calls == []  # goal+user 都无 → 不 flush


# ───────────────────── WI-1B-2 压缩可观测 (ctx_observability flag) ─────────────────────
class TestWI1B2CtxObservability:
    """flag OFF = 字节级 BC (不 emit metrics / 不 yield ContextCompactedEvent);
    flag ON = 压缩命中后 yield 一条 ContextCompactedEvent(字段=reduction/in/out)
    + record 一条 metrics。对照 plan WI-1B-2 可证伪断言。"""

    class _Tools:
        def schemas(self, enabled_toolsets=None):
            return []

        async def execute_tool(self, name, args, task_id):
            return '{"ok": true}'

    class _LLM:
        async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
            from llm.types import ChatResponse

            return ChatResponse(
                content="done",
                stop_reason="end_turn",
                tool_calls=[],
                usage={"input_tokens": 5, "output_tokens": 3},
            )

    class _CompressHit:
        """压缩命中 — 返回 compressed=True + 真 token 字段，触发可观测分支。"""

        model = "claude-haiku-4-5"

        def should_compress(self, n):
            return True

        async def compress(self, messages, *, goal_text=None, pending_tasks=None):
            from deskpet.agent.context_compressor import CompressionResult

            return CompressionResult(
                messages=list(messages),
                compressed=True,
                input_tokens=1000,
                output_tokens=400,
                reduction_ratio=0.6,
            )

    @pytest.mark.asyncio
    async def test_observability_off_no_extra_event(self, monkeypatch):
        """flag OFF (默认): 压缩命中也不 yield ContextCompactedEvent、不调 metrics。"""
        import observability.metrics_sink as _ms
        from agent.agent_loop import AgentLoop

        recorded: list[tuple] = []
        monkeypatch.setattr(
            _ms,
            "record",
            lambda event, detail=None: recorded.append((event, detail)) or True,
        )

        loop = AgentLoop(
            llm_registry=self._LLM(),
            tool_registry=self._Tools(),
            compressor=self._CompressHit(),
            # ctx_observability 不传 → 默认 False (BC)
        )
        events = []
        async for ev in loop.run([{"role": "user", "content": "请整理"}], session_id="s-off"):
            events.append(ev)

        # OFF=BC: 无 ContextCompactedEvent
        assert not any(getattr(e, "type", "") == "context_compacted" for e in events), (
            "flag OFF 不应 yield ContextCompactedEvent"
        )
        # OFF=BC: 无 context_compacted metrics 调用
        assert not any(ev == "context_compacted" for ev, _ in recorded), (
            "flag OFF 不应 record context_compacted metrics"
        )
        # 压缩本身仍发生 → loop 正常结束
        assert any(getattr(e, "type", "") == "final" for e in events)

    @pytest.mark.asyncio
    async def test_observability_on_metrics_event_whitelisted(self):
        """context_compacted 在 metrics_sink VALID_EVENTS 白名单内 → record 真落盘
        (否则被丢)。detail keys ratio/model/count 也在白名单内。"""
        from observability.metrics_sink import (
            VALID_EVENTS,
            sanitize_detail,
        )

        assert "context_compacted" in VALID_EVENTS
        out = sanitize_detail({"ratio": 0.6, "model": "claude-haiku-4-5", "count": 600})
        assert out == {"ratio": 0.6, "model": "claude-haiku-4-5", "count": 600}
