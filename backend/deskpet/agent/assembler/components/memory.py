# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Memory component (P4-S7 task 12.5).

Wraps the three-layer :class:`~deskpet.memory.manager.MemoryManager`. On
every turn this component asks the manager for:

1. L1 frozen snapshot (`{memory, user}` raw file text) — optional.
2. L2 recent session messages.
3. L3 RRF hybrid recall hits.

It then packages the results into a single markdown-ish block that the
LLM can consume naturally. L1 is treated as ``"frozen"`` bucket (it only
changes when the user edits their MEMORY.md / USER.md — cache-friendly),
while L2 + L3 go into ``"dynamic"`` because they shift every turn.

Core memory (L1) MAY NEVER be removed by policy (spec D9). The component
honours ``policy.memory.l1 == "off"`` by skipping the *block* but still
adds L1 content to the frozen bucket — just with empty strings.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext
from deskpet.memory.entity_extractor import _STOPWORDS

logger = structlog.get_logger(__name__)


_L2_CONTEXT_LABEL = "以下为较早的对话记录，可能涉及其他话题，仅供背景参考。"
_CURRENT_REQUEST_NUDGE = (
    "当前请求是本轮唯一任务；先前对话仅为背景，若与当前请求冲突，以当前请求为准。"
)
_ANAPHORA_PREFIXES = (
    "它",
    "他",
    "她",
    "其",
    "这个",
    "那个",
    "这些",
    "那些",
    "这",
    "那",
    "继续",
    "然后",
    "what",
    "why",
    "how",
    "which",
    "this",
    "that",
    "these",
    "those",
)
_ANAPHORA_WORDS = {"it", "they", "them", "he", "she", "his", "her", "their"}


class MemoryComponent:
    """Provides L1 snapshot + L2/L3 recall results."""

    name: str = "memory"

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        mm = ctx.memory_manager
        if mm is None:
            # No memory manager wired — graceful empty. The assembler
            # never "fails" a turn just because L3 is offline.
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=100,
                bucket="frozen",
                meta={"status": "no_memory_manager"},
            )

        policy_memory = ctx.policy.memory
        call_policy = {
            "l1": policy_memory.l1,
            "l2_top_k": policy_memory.l2_top_k,
            "l3_top_k": policy_memory.l3_top_k,
            "session_id": ctx.session_id,
        }

        try:
            result = await mm.recall(ctx.user_message, policy=call_policy)
        except Exception as exc:  # defensive — manager is supposed to be safe
            return Slice(
                component_name=self.name,
                text_content="",
                priority=100,
                bucket="frozen",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        l2_rows = result.get("l2") or []
        l3_hits = result.get("l3") or []

        topic_shift_gate = bool(getattr(policy_memory, "topic_shift_gate", False))
        relabel_l2 = bool(getattr(policy_memory, "relabel_l2", False))
        anchor_current = bool(getattr(policy_memory, "anchor_current", False))
        topic_shift_threshold = float(
            getattr(policy_memory, "topic_shift_threshold", 0.35)
        )
        l2_keep_on_shift = int(getattr(policy_memory, "l2_keep_on_shift", 1))
        topic_shift_min_len = int(getattr(policy_memory, "topic_shift_min_len", 16))

        l2_count_in = len(l2_rows)
        gate_sim: float | None = None
        l2_truncated = False
        if l2_rows and topic_shift_gate:
            emb = _get_embedder(mm)
            l2_concat = "\n".join(
                (row.get("content") or "").strip()
                for row in l2_rows
                if isinstance(row, dict) and (row.get("content") or "").strip()
            )
            gate_sim = await _topic_similarity(emb, ctx.user_message, l2_concat)
            is_shift = (
                gate_sim is not None
                and gate_sim < topic_shift_threshold
                and len(ctx.user_message.strip()) >= topic_shift_min_len
                and not _starts_with_anaphora(ctx.user_message)
            )
            if is_shift:
                keep = max(0, l2_keep_on_shift)
                l2_rows = l2_rows[-keep:] if keep else []
                l2_truncated = True

        frozen_text = _render_l1(result.get("l1"))
        # P4-S21 #16 fix: L2 raw rows are now promoted to bundle.history
        # by the assembler stitcher (see meta["l2_history"] below). The
        # text rendering only carries L3 semantic recall — keeping L2
        # *also* in the system memory_block would just double-charge tokens
        # AND risk the LLM ignoring it as instruction noise. Old behaviour
        # was `_render_l2_l3(...)`; we now emit L3 only.
        dynamic_text = _render_l3_only(l3_hits)

        # Frozen slice for L1
        combined = frozen_text
        if dynamic_text:
            # Two sections merged; the frozen half comes first so the
            # whole block stays cache-friendly as long as L1 doesn't
            # change. When L2/L3 shift we only invalidate the tail.
            combined = f"{frozen_text}\n\n{dynamic_text}".strip()

        tokens = _approx_tokens(combined)
        elapsed_ms = (time.monotonic() - start) * 1000.0

        # Promote L2 raw rows to OpenAI message format. The assembler's
        # `_stitch` reads meta["l2_history"] and assigns to bundle.history.
        l2_history: list[dict[str, Any]] = []
        if l2_rows and relabel_l2:
            l2_history.append({"role": "system", "content": _L2_CONTEXT_LABEL})
        for row in l2_rows:
            role = row.get("role")
            content = (row.get("content") or "").strip()
            # System summaries (is_summary=1) live in messages too — keep
            # them as system-role hints so the LLM treats them as context
            # without confusing "assistant" turn boundaries.
            if not content or role not in ("user", "assistant", "system"):
                continue
            entry: dict[str, Any] = {"role": role, "content": content}
            # P4-S24: thinking-mode round-trip. If a prior assistant
            # message stored a reasoning_content (DeepSeek V4 Pro /
            # Qwen3 thinking / GLM-4.5), echo it back into history so
            # the LLM doesn't 400 with "reasoning_content must be
            # passed back". NULL/empty for non-thinking models — skip
            # the field entirely so plain Ollama / GPT-4o payloads
            # stay clean.
            if role == "assistant":
                rc = row.get("reasoning_content")
                if rc:
                    entry["reasoning_content"] = rc
            l2_history.append(entry)

        # Task-drift observability anchor (grep `task_drift_context_gate` in
        # the tauri-dev stderr log). Makes Tier 1 (relabel/anchor) and Tier 2
        # (topic-shift truncation) verifiable in real E2E without a prompt
        # dump — and doubles as production telemetry for drift incidents.
        logger.info(
            "task_drift_context_gate",
            session_id=ctx.session_id,
            relabel_applied=bool(l2_rows and relabel_l2),
            anchor_applied=anchor_current,
            topic_shift_gate=topic_shift_gate,
            l2_truncated=l2_truncated,
            gate_sim=(round(gate_sim, 4) if gate_sim is not None else None),
            l2_count_in=l2_count_in,
            l2_count_out=len(l2_rows),
        )

        return Slice(
            component_name=self.name,
            text_content=combined,
            tokens=tokens,
            priority=100,
            bucket="dynamic" if dynamic_text else "frozen",
            meta={
                "l1_bytes": len(frozen_text),
                "l2_count": len(l2_rows),
                "l3_count": len(l3_hits),
                "latency_ms": round(elapsed_ms, 2),
                # NEW: assembler picks this up and assigns to bundle.history
                "l2_history": l2_history,
                **(
                    {"late_system_nudge": _CURRENT_REQUEST_NUDGE}
                    if anchor_current
                    else {}
                ),
            },
        )


def _get_embedder(mm: Any) -> Any:
    r = getattr(mm, "_retriever", None)
    return getattr(r, "_embedder", None) if r is not None else None


async def _topic_similarity(emb: Any, current: str, l2_concat: str) -> float | None:
    if emb is None:
        return None
    try:
        if not emb.is_ready() or emb.is_mock():
            return None
        vecs = await asyncio.wait_for(
            emb.encode([current, l2_concat]), timeout=0.3
        )
    except Exception:
        return None
    if getattr(vecs, "shape", [0])[0] < 2:
        return None
    return float(vecs[0] @ vecs[1])


def _starts_with_anaphora(current: str) -> bool:
    text = current.strip()
    if not text:
        return False
    if len(text) < 10:
        return True

    lower = text.lower()
    if any(lower.startswith(prefix) for prefix in _ANAPHORA_PREFIXES):
        return True

    first = _first_token(text)
    if not first:
        return False
    first_lower = first.lower()
    return (
        first_lower in _ANAPHORA_WORDS
        or first in _STOPWORDS
        or first_lower.capitalize() in _STOPWORDS
    )


def _first_token(text: str) -> str:
    cjk = re.match(r"[\u4e00-\u9fff]+", text)
    if cjk:
        span = cjk.group(0)
        candidates = sorted(_STOPWORDS, key=len, reverse=True)
        for word in candidates:
            if span.startswith(word):
                return word
        return span[:2]

    word = re.match(r"[A-Za-z]+", text)
    return word.group(0) if word else text[:1]


# ---------------------------------------------------------------------------
# Renderers — keep layout stable for prompt caching
# ---------------------------------------------------------------------------
def _render_l1(l1: Any) -> str:
    """Render L1 snapshot into a stable block.

    Returns empty string if l1 is missing / empty — avoids inserting a
    blank section that would still occupy tokens in the prompt.
    """
    if not isinstance(l1, dict):
        return ""
    memory = (l1.get("memory") or "").strip()
    user = (l1.get("user") or "").strip()
    if not memory and not user:
        return ""

    parts: list[str] = ["## 记忆档案 (L1, frozen)"]
    if memory:
        parts.append("### MEMORY.md\n" + memory)
    if user:
        parts.append("### USER.md\n" + user)
    return "\n\n".join(parts)


def _render_l3_only(l3_hits: list[dict[str, Any]]) -> str:
    """Render only L3 (RRF semantic recall) into a system-prompt-friendly block.

    L2 (recent conversation) used to be rendered here too, but as of P4-S21
    #16 it's promoted to ``bundle.history`` (real OpenAI message turns).
    Keeping L2 in this block as well would double-charge tokens AND risk
    the LLM ignoring it as instruction noise — see the "VPN bug" in
    `plans/2026-05-07-msi-known-issues.md` #16.

    L3 stays as text because it pulls semantically-similar bits from
    *anywhere* (other sessions, archived summaries, etc.) — those don't
    belong in the conversation history per se, they're "retrieved
    memories" the LLM should consult.
    """
    if not l3_hits:
        return ""

    parts: list[str] = ["## 相关记忆片段 (L3, RRF recall)"]
    lines = []
    for hit in l3_hits:
        text = (hit.get("text") or "").strip()
        if not text:
            continue
        if len(text) > 240:
            text = text[:240] + "…"
        score = hit.get("score")
        src = hit.get("source", "?")
        score_str = (
            f"{score:.3f}" if isinstance(score, (int, float)) else "?"
        )
        lines.append(f"- [{src} {score_str}] {text}")
    if not lines:
        return ""
    parts.append("\n".join(lines))
    return "\n\n".join(parts)


# Kept under the old name for any out-of-tree caller that imports it.
# Internally everything routes through `_render_l3_only` now.
def _render_l2_l3(
    l2_rows: list[dict[str, Any]], l3_hits: list[dict[str, Any]]
) -> str:
    """Deprecated — kept for backwards compatibility with tests. Use
    ``_render_l3_only`` for new code. L2 rendering is intentionally a
    no-op now; L2 lives in ``bundle.history`` as real messages."""
    del l2_rows  # ignored; see docstring
    return _render_l3_only(l3_hits)


def _approx_tokens(text: str) -> int:
    """Very coarse token estimate (1 token ≈ 4 chars for mixed Chinese+English).

    Accurate enough for budget allocation. The real tokeniser is
    provider-specific; we avoid importing tiktoken here to keep the
    hot path fast.
    """
    if not text:
        return 0
    if not text:
        return 0
    from deskpet.agent.tokens import count_text_tokens
    return count_text_tokens(text)


# For structural typing / Protocol conformance checking in tests.
_ASSERT_PROTOCOL: Component = MemoryComponent()  # noqa: E501
