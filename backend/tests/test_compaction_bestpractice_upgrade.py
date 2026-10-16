# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Phase 1 单测 — compaction-bestpractice-upgrade WI-1/2/3/4a。

对照 plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md §4 的可证伪断言。
"""

from __future__ import annotations

import pytest


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
