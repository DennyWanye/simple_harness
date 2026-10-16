# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from .openai_compatible_domains.chat import ChatMixin
from .openai_compatible_domains.streaming import StreamingToolMixin

# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
from .openai_compatible_domains.support import _extract_cached_tokens as _extract_cached_tokens
from .openai_compatible_domains.support import _stabilize_prefix as _stabilize_prefix
from .openai_compatible_domains.support import _stamp_cache_control as _stamp_cache_control


class OpenAICompatibleProvider(ChatMixin, StreamingToolMixin):
    """LLM provider speaking OpenAI's /v1/chat/completions SSE protocol.

    Works against any compatible endpoint:
      - Local Ollama on /v1 (api_key "ollama", ignored server-side).
      - DashScope compatible-mode /v1 (real bearer token).
      - Any other OpenAI-compatible gateway.

    Implements the `LLMProvider` Protocol in providers/base.py.
    """


# ──────────────────────────────────────────────────────────────────────
# Phase 1.3 — 4-breakpoint prompt cache + 前缀稳定纪律
# (OpenSpec 2026-05-15-context-1m-rearch · design.md D4)
#
# OpenAI-compat（the relay / deepseek-v4-pro）走 **prefix cache**：命中条件
# 是 messages 历史前缀的字节流跨轮稳定。命中条件不是我们打什么标记，而
# 是"前缀别变"。所以这一段做两件事：
#   1. `_stabilize_prefix`：抹平上游历史里会抖动的字段（tool-call-only
#      assistant 的 content 缺失/None；reasoning_content 空串 vs 无 key）
#      —— 抄 DeepSeek-TUI 的纪律"绝不改写历史前缀"。
#   2. `_cache_breakpoint_order` / `_extract_cached_tokens` 是纯函数，
#      给单测断言装配顺序契约 + 防御式取回包 cached_tokens。
#
# Anthropic `cache_control`（tasks 1.3.3）由 `llm/anthropic_adapter.py`
# 完整实现（最后一个 system block + 最后一个 tool 打 ephemeral，已读
# cache_read/creation tokens）—— 此处不重复，详见 evidence。
# ──────────────────────────────────────────────────────────────────────


def _cache_breakpoint_order(messages: list[dict], *, has_tools: bool) -> list[str]:
    """返回这批 messages 的 4-breakpoint 逻辑断点序（纯函数，给单测）。

    design.md D4 规定的稳定→易变装配顺序：

        [0]    system (persona + 静态指令)        ← breakpoint 1
        [1]    tools schema                       ← breakpoint 2
        [2]    memory / repo-map block            ← breakpoint 3
        [3..]  对话历史 + tool_results            ← 易变
        [last] 最近 user turn 之前                ← breakpoint 4

    实际装配在上游（ContextAssembler / chat handler）；provider 只在
    边界给出契约序，让单测能断言"上游没把易变块插进前缀"。

    - `tools` 不是一条 message（是 payload 顶层字段），由 `has_tools`
      决定是否占断点位。
    - `memory` 只有真有 memory/repo-map block（system 消息里含
      ``<memory_block>`` 或第 2 条 system）才占位，否则不硬塞，
      避免浪费 breakpoint。
    """
    order: list[str] = []
    has_system = any(m.get("role") == "system" for m in (messages or []))
    if has_system:
        order.append("system")
    if has_tools:
        order.append("tools")
    # memory/repo-map 约定：要么显式 <memory_block> 标记，要么是
    # 第二条 system 消息（ContextAssembler 产出独立 system block）。
    sys_msgs = [m for m in (messages or []) if m.get("role") == "system"]
    has_memory = len(sys_msgs) >= 2 or any(
        isinstance(m.get("content"), str) and "<memory_block>" in m.get("content", "")
        for m in sys_msgs
    )
    if has_memory:
        order.append("memory")
    order.append("history")
    order.append("last-user-pre")
    return order
