# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Context compressor — rolling summary when prompt tokens near cap (P4-S8).

Implements the agent-loop Context Engine hook described in
``openspec/changes/p4-poseidon-agent-harness/specs/agent-loop/spec.md``:

    Agent MUST 在每轮 LLM 调用前调用
    `context_engine.should_compress(prompt_tokens)`, 若 true 则先
    `context_engine.compress(messages)` 再调用 LLM.

Algorithm (§13.3):

1. Split ``messages`` into:

   - ``system``   — every ``role=system`` message (kept verbatim, order preserved)
   - ``first_n``  — the first N non-system messages (default 3)
   - ``middle``   — everything between ``first_n`` and the last ``last_n``
   - ``last_n``   — the last N non-system messages (default 6)

2. If ``middle`` is empty → no-op (return original messages).

3. Ask ``claude-haiku-4-5`` to compress the middle into a single prose
   summary that preserves names / times / decisions. On failure, log a
   warning and return the original messages so the turn still makes
   progress.

4. Inject the summary as a single ``role=assistant`` message positioned
   AFTER ``first_n`` and BEFORE ``last_n``. The summary lives in the
   dynamic section — we do NOT mutate the frozen system prompt (§13.4).

``should_compress`` compares token estimate against
``context_window * threshold_percent``. Threshold and model are
configurable via ``config.context`` section.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Soft cap for injected goal-anchor system message (~1500 tokens × 4 chars/token).
_MAX_SYSTEM_INJECT_TOKENS = 1500
_MAX_SYSTEM_INJECT_CHARS = _MAX_SYSTEM_INJECT_TOKENS * 4  # 6000 chars


# ---------------------------------------------------------------------------
# Result / stats
# ---------------------------------------------------------------------------
@dataclass
class CompressionResult:
    """Outcome of :meth:`ContextCompressor.compress`."""

    messages: list[dict[str, Any]]
    compressed: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    reduction_ratio: float = 0.0
    summary_preview: str = ""
    latency_ms: float = 0.0
    error: Optional[str] = None
    # Count of middle messages rolled into the summary (for telemetry UI).
    messages_summarized: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ContextCompressor
# ---------------------------------------------------------------------------
class ContextCompressor:
    """Rolling-summary compressor for long conversations.

    Parameters
    ----------
    llm_registry:
        Anything with ``async chat_with_fallback(messages, ...)`` returning an
        object with ``.content`` attribute. ``None`` → :meth:`compress` is a no-op.
    context_window:
        Underlying model's context window in tokens. Default 200_000 (Claude 4.5).
    threshold_percent:
        Fraction of the window at which compression triggers. Default 0.75.
    first_n:
        How many leading non-system messages to preserve unchanged.
        Default 3 — typically the initial user request + first reply pair.
    last_n:
        How many trailing non-system messages to preserve unchanged.
        Default 6 — roughly the last 3 turns (u/a pairs).
    model:
        Haiku model used for the summary call. Default ``claude-haiku-4-5``.
    summary_max_tokens:
        Cap on the compressed output. Default 512.
    """

    _SUMMARY_SYSTEM = (
        "You summarise a DESKPET conversation segment for prompt-cache "
        "efficiency. Preserve: named entities (people, projects), dates "
        "and times, explicit decisions / commitments, and any data the "
        "user asked to remember. Drop filler, repetition, and social "
        "pleasantries. Output MUST be a neutral third-person prose "
        "paragraph in the user's language. Do NOT hallucinate."
    )

    def __init__(
        self,
        *,
        llm_registry: Any = None,
        context_window: int = 200_000,
        threshold_percent: float = 0.75,
        first_n: int = 3,
        last_n: int = 6,
        model: str = "claude-haiku-4-5",
        summary_max_tokens: int = 512,
    ) -> None:
        self._llm = llm_registry
        self.context_window = int(context_window)
        self.threshold_percent = float(threshold_percent)
        self.first_n = max(0, int(first_n))
        self.last_n = max(0, int(last_n))
        self.model = model
        self.summary_max_tokens = int(summary_max_tokens)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def threshold_tokens(self) -> int:
        return int(self.context_window * self.threshold_percent)

    def should_compress(self, prompt_tokens: int) -> bool:
        """Return True when caller should call :meth:`compress`."""
        if self.context_window <= 0 or self.threshold_percent <= 0.0:
            return False
        return prompt_tokens >= self.threshold_tokens()

    async def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        goal_text: "str | None" = None,
        pending_tasks: "list[str] | None" = None,
    ) -> CompressionResult:
        """Produce a compressed messages list.

        Parameters
        ----------
        goal_text:
            When non-empty, a re-anchor system message is appended to the
            system segment of the output (after existing system messages,
            before first_chunk). Content is soft-capped at
            ``_MAX_SYSTEM_INJECT_TOKENS`` tokens.  ``None`` or empty string →
            no injection; output is byte-identical to the old single-param
            behaviour.
        pending_tasks:
            Optional list of pending sub-task titles. The first item is
            included in the anchor as ``[当前子目标]``. Only used when
            ``goal_text`` is non-empty.

        Never raises. On any failure returns the original messages
        with ``compressed=False`` and ``error`` populated.
        """
        start = time.monotonic()
        if not messages:
            return CompressionResult(messages=[])

        system_msgs, first_chunk, middle_chunk, last_chunk = _partition(
            messages, self.first_n, self.last_n
        )

        if not middle_chunk:
            # Nothing to compress — window is too short.
            return CompressionResult(
                messages=list(messages),
                compressed=False,
                latency_ms=(time.monotonic() - start) * 1000.0,
                meta={"reason": "no_middle_to_summarize"},
            )

        if self._llm is None:
            return CompressionResult(
                messages=list(messages),
                compressed=False,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error="no_llm_registry",
                meta={"reason": "llm_registry_missing"},
            )

        middle_text = _render_transcript(middle_chunk)
        input_tokens = _approx_tokens(middle_text)

        try:
            response = await self._llm.chat_with_fallback(
                [
                    {"role": "system", "content": self._SUMMARY_SYSTEM},
                    {"role": "user", "content": middle_text},
                ],
                model=self.model,
                max_tokens=self.summary_max_tokens,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning(
                "context_compressor.llm_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return CompressionResult(
                messages=list(messages),
                compressed=False,
                input_tokens=input_tokens,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error=str(exc),
            )

        summary_text = str(getattr(response, "content", "") or "").strip()
        if not summary_text:
            return CompressionResult(
                messages=list(messages),
                compressed=False,
                input_tokens=input_tokens,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error="empty_summary",
            )

        output_tokens = _approx_tokens(summary_text)
        summary_message = {
            "role": "assistant",
            "content": _format_summary(summary_text),
        }

        anchor_msgs = _build_goal_anchor(goal_text, pending_tasks)
        new_messages = (
            list(system_msgs)
            + anchor_msgs
            + list(first_chunk)
            + [summary_message]
            + list(last_chunk)
        )

        # Reduction ratio is measured on the rolled-up middle only —
        # preserves the "40% reduction" post-condition in §13.5.
        reduction = 0.0
        if input_tokens > 0:
            reduction = max(0.0, 1.0 - (output_tokens / input_tokens))

        return CompressionResult(
            messages=new_messages,
            compressed=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reduction_ratio=reduction,
            summary_preview=summary_text[:200],
            latency_ms=(time.monotonic() - start) * 1000.0,
            messages_summarized=len(middle_chunk),
            meta={
                "first_n": len(first_chunk),
                "last_n": len(last_chunk),
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _partition(
    messages: list[dict[str, Any]], first_n: int, last_n: int
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Split messages by the §13.3 rule.

    System messages are pulled out and returned as a group. The rest are
    then split into ``first_n + middle + last_n``. If the non-system
    count is ≤ first_n+last_n the middle is empty.
    """
    system_msgs: list[dict[str, Any]] = [
        m for m in messages if (m.get("role") == "system")
    ]
    non_system: list[dict[str, Any]] = [
        m for m in messages if (m.get("role") != "system")
    ]
    n = len(non_system)
    if n <= first_n + last_n:
        return system_msgs, non_system, [], []

    head = non_system[:first_n]
    tail = non_system[n - last_n :] if last_n > 0 else []
    middle = non_system[first_n : n - last_n] if last_n > 0 else non_system[first_n:]
    return system_msgs, head, middle, tail


def _render_transcript(messages: list[dict[str, Any]]) -> str:
    """Render a message list into a flat transcript for the summariser."""
    lines: list[str] = []
    for m in messages:
        role = str(m.get("role") or "?")
        content = m.get("content")
        if isinstance(content, list):
            # Multi-part (tool-calls) — keep only text parts.
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    parts.append(part)
            content_str = "\n".join(parts)
        else:
            content_str = str(content or "")
        content_str = content_str.strip()
        if not content_str:
            # Still emit tool-call pseudo lines so the summariser knows
            # an action happened — otherwise it silently drops a turn.
            if m.get("tool_calls"):
                names = ", ".join(
                    tc.get("function", {}).get("name", "?") if isinstance(tc, dict) else "?"
                    for tc in m["tool_calls"]
                )
                content_str = f"(invoked tools: {names})"
            else:
                continue
        lines.append(f"[{role}] {content_str}")
    return "\n".join(lines)


def _format_summary(summary_text: str) -> str:
    """Wrap the raw summary with a marker that downstream readers can detect."""
    return (
        "[压缩摘要 / compressed summary]\n" + summary_text.strip()
    )


def _approx_tokens(text: str) -> int:
    """Coarse token estimate — same 4-char heuristic as the assembler."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _build_goal_anchor(
    goal_text: "str | None",
    pending_tasks: "list[str] | None",
) -> list[dict]:
    """Build zero or one system-role anchor message for goal re-anchoring.

    Returns an empty list when ``goal_text`` is None or empty (BC path).
    The content is soft-capped at ``_MAX_SYSTEM_INJECT_TOKENS`` tokens
    with priority: goal_text > 子目标 > remaining pending tasks.
    """
    if not goal_text:
        return []

    budget_chars = _MAX_SYSTEM_INJECT_CHARS  # ~6000

    # --- Priority 1: goal_text (never dropped, but truncated if enormous) ---
    # Reserve at least a few chars for the fixed template lines.
    _TEMPLATE_OVERHEAD = 120  # rough bytes for the fixed Chinese strings
    goal_chars = min(len(goal_text), budget_chars - _TEMPLATE_OVERHEAD)
    goal_chars = max(goal_chars, 0)
    truncated_goal = goal_text[:goal_chars]

    # --- Priority 2: first pending task (子目标) ---
    first_task: str = ""
    remaining_tasks: list[str] = []
    if pending_tasks:
        first_task = pending_tasks[0] if pending_tasks else ""
        remaining_tasks = list(pending_tasks[1:])

    # Account for goal in budget
    used = len(truncated_goal) + _TEMPLATE_OVERHEAD
    remaining_budget = max(budget_chars - used, 0)

    # Truncate first_task if needed
    if first_task:
        first_task = first_task[:remaining_budget]
        remaining_budget -= len(first_task)

    # Build content
    lines: list[str] = [
        f"[目标锚定] 当前目标：{truncated_goal}",
    ]
    if first_task:
        lines.append(f"[当前子目标] {first_task}")
    lines.append("请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏。")

    content = "\n".join(lines)
    return [{"role": "system", "content": content}]
