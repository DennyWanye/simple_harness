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

4. Inject the summary as a dynamic ``role=system`` history message positioned
   AFTER ``first_n`` and BEFORE ``last_n``. Keeping it out of the assistant
   role prevents the main model from treating the summary as an answer
   template, while its non-prefix placement keeps it out of frozen_system.

``should_compress`` compares token estimate against
``context_window * threshold_percent``. Threshold and model are
configurable via ``config.context`` section.
"""
from __future__ import annotations

import copy
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from agent.context_messages import (
    CONTEXT_MESSAGE_META_KEY,
    ContextMessageMeta,
    ProviderAttemptOptions,
    context_attempt_scope,
    tag_message,
)
from deskpet.execution.provider_workloads import ProviderWorkloadContext
from deskpet.execution.provider_workloads import invoke_explicit

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
        Fraction of the window at which compression triggers. Default 0.70.
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

    # 结构化摘要(优化 #2): 旧版只是一段 prose、只保"实体/时间/决策",压缩后
    # 常把"用户正在让我做的那件事"摘没了 → 桌宠"看不到上一轮任务"。新版强制
    # 用分段输出,**头等优先保活"当前进行中的任务 + 最近一条用户请求"**,任务连续性不丢。
    _SUMMARY_SYSTEM = (
        "你在压缩一段 DESKPET 对话历史(为省 token),但**绝不能丢掉任务连续性**。\n"
        "⚠️ 任务边界优先(最重要的纪律): 一段对话里用户可能**先后做过不同的任务**"
        "(例如先做'宁德时代年报',后来又转去做'小学教育 PPT')。【进行中/当前任务】"
        "**只能**反映**最近一条用户请求**所属的那个任务;凡是更早的、用户已经不再继续的"
        "任务,一律降级写进【早前已结束的任务】(每个一行带过即可、细节可丢),"
        "**绝不要**把旧任务当成'当前任务'继续保活——那正是助手做着做着漂回旧任务的根源。\n"
        "用**用户的语言**、第三人称,按下面分段输出(空的段直接省略,不要写'无'):\n\n"
        "【意图/目标】**最近这个任务**用户想达成什么(跟着最新请求走,已切换离开的旧目标不要写这)。\n"
        "【进行中/当前任务】← 最重要,必须保: 用户**当前(最近一条请求)**正让助手做的那件事是什么、"
        "做到哪一步了;把**最近一条用户请求**的原意完整保留(短就近乎原文)。\n"
        "【早前已结束的任务】用户此前做过、现已切换离开的任务,每个一行简述(可丢细节,只为不彻底失忆)。\n"
        "【已完成】**当前任务**已经做完的步骤、得到的结论或产物(带关键结果值)。\n"
        "【关键事实与决策】人名/项目/数字/日期/已定的决定/用户明确让记住的数据(跨任务的事实仍要保)。\n"
        "【涉及的文件/产物】对话中出现的文件路径、生成的产物、关键标识符。\n"
        "【待办/下一步】**当前任务**尚未完成、接下来要做的事。\n\n"
        "丢弃: 寒暄、重复、过程性废话。**不要杜撰**任何未在原文出现的信息。\n"
        "★若【待摘内容】里**没有**明确的用户任务(例如只是一些工具调用/系统记录),"
        "【进行中/当前任务】段就写'(本段无明确任务)',**绝不要**把本提示词本身、"
        "或'压缩对话'这类元说明当成用户任务写进去(那是你的指令,不是对话内容)。"
    )

    def __init__(
        self,
        *,
        llm_registry: Any = None,
        context_window: int = 200_000,
        threshold_percent: float = 0.70,
        first_n: int = 3,
        last_n: int = 6,
        model: str = "claude-haiku-4-5",
        summary_max_tokens: int = 768,   # 512→768: 结构化摘要稍长,保任务连续性(优化 #2)
        effective_pct: "float | None" = None,
        microcompact_keep_tools: int = 3,
        microcompact_size_aware: bool = False,
        microcompact_keep_bytes: int = 24_000,
    ) -> None:
        self._llm = llm_registry
        self.context_window = int(context_window)
        self.threshold_percent = float(threshold_percent)
        self.first_n = max(0, int(first_n))
        self.last_n = max(0, int(last_n))
        self.model = model
        self.summary_max_tokens = int(summary_max_tokens)
        # WI-1: 触发改"剩余 token buffer"。effective_pct 由 main.py 按有效出站模型
        # (model_info.effective_pct) 注入；None → 退回纯比例阈值(旧单测/BC 路径,
        # 不启用 buffer 触发,因小窗口 buffer 下限 8K 会把比例阈值吞掉)。
        self.effective_pct = (
            float(effective_pct) if effective_pct is not None else None
        )
        # WI-2: microcompact 保护最近 N 个工具调用的 tool_result 原文。
        self.microcompact_keep_tools = max(0, int(microcompact_keep_tools))
        # WI-1B-5: size-aware 保护(默认 OFF = 字节级 BC)。OFF → 仍按"最近 N 条";
        # ON → "最近 N 条 + 累计字节 ≤ microcompact_keep_bytes",避免最近 N 条里
        # 混入巨型 tool_result 仍把窗口撑爆(只保护到字节预算耗尽为止,更早的即便
        # 在 N 之内也会被清)。
        self.microcompact_size_aware = bool(microcompact_size_aware)
        self.microcompact_keep_bytes = max(0, int(microcompact_keep_bytes))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def threshold_tokens(self) -> int:
        return int(self.context_window * self.threshold_percent)

    # WI-1 buffer 公式(随窗口自适应,第2轮定稿) ----------------------------
    # output_reserve = max(8K, min(32K, window//32))：400K→12.5K；1M→31.25K；32K→8K。
    _OUTPUT_RESERVE_FLOOR = 8_000
    _OUTPUT_RESERVE_CEIL = 32_000

    def output_reserve(self) -> int:
        """留给"下一条输出/摘要"的 token buffer(纯公式,供 WI-1 单测断言)。"""
        return max(
            self._OUTPUT_RESERVE_FLOOR,
            min(self._OUTPUT_RESERVE_CEIL, self.context_window // 32),
        )

    def effective_window(self) -> int:
        """有效窗口 = context_window × effective_pct(effective_pct=None 时按 1.0)。"""
        pct = self.effective_pct if self.effective_pct is not None else 1.0
        return int(self.context_window * pct)

    def trigger_tokens(self) -> int:
        """WI-1 单一触发线。

        - effective_pct 未注入(BC/旧单测) → 纯比例阈值 ``threshold_tokens()``。
        - 注入了 → ``min(threshold_tokens(), effective_window − output_reserve)``，
          谁先到先压(大窗口比例先到、小窗口 buffer 先到)。若 buffer 比有效窗口还大
          (极小窗口) ⇒ ``eff_win − buffer`` 为负,忽略该项退回比例阈值。
        """
        base = self.threshold_tokens()
        if self.effective_pct is None:
            return base
        buffer_line = self.effective_window() - self.output_reserve()
        if buffer_line <= 0:
            return base
        return min(base, buffer_line)

    def should_compress(self, prompt_tokens: int) -> bool:
        """Return True when caller should call :meth:`compress`."""
        if self.context_window <= 0 or self.threshold_percent <= 0.0:
            return False
        return prompt_tokens >= self.trigger_tokens()

    def for_context(
        self,
        *,
        context_window: int,
        threshold_percent: float,
        effective_pct: "float | None",
    ) -> "ContextCompressor":
        """Return an isolated view for one Run's frozen model limits.

        The summary client is safe to share, but window limits are not: two
        Sessions may select different models.  Copying the compressor keeps the
        shared client while preventing cross-Session configuration mutation.
        """
        routed = copy.copy(self)
        routed.context_window = int(context_window)
        routed.threshold_percent = float(threshold_percent)
        routed.effective_pct = (
            float(effective_pct) if effective_pct is not None else None
        )
        return routed

    async def _call_summary_model(
        self,
        messages: list[dict[str, Any]],
        *,
        resolved_provider: Any = None,
        resolved_model: str | None = None,
        workload_context: ProviderWorkloadContext | None = None,
    ) -> Any:
        from agent.context_messages import provider_purpose_scope

        if resolved_provider is None:
            if self._llm is None:
                raise RuntimeError("compression_model_unavailable")
            if callable(self._llm):
                if workload_context is None:
                    raise RuntimeError("compression_workload_context_unavailable")
                prompt = "\n\n".join(
                    f"[{item.get('role', 'user')}] {item.get('content', '')}"
                    for item in messages
                )
                return {
                    "content": await invoke_explicit(
                        self._llm,
                        prompt,
                        workload_context=workload_context,
                    )
                }
            with provider_purpose_scope("compressor"):
                return await self._llm.chat_with_fallback(
                    messages,
                    model=resolved_model or self.model,
                    max_tokens=self.summary_max_tokens,
                    temperature=0.0,
                )
        direct = getattr(resolved_provider, "chat_with_tools", None)
        if not callable(direct):
            raise RuntimeError("compression_provider_not_callable")
        # One exact provider/model attempt.  Do not route through a registry
        # fallback when Context OS selected a dedicated compaction candidate.
        with provider_purpose_scope("compressor"):
            if workload_context is None:
                return await direct(
                    messages,
                    tools=None,
                    max_tokens=self.summary_max_tokens,
                    temperature=0.0,
                )
            with context_attempt_scope(
                ProviderAttemptOptions(
                    purpose="compressor",
                    session_id=workload_context.session_id,
                    root_run_id=workload_context.root_run_id,
                    request_id=workload_context.request_id,
                    attempt_id=f"aux:{workload_context.call_id}",
                    workload_class=workload_context.workload_class.value,
                    callsite_id=workload_context.callsite_id,
                    detached=workload_context.detached,
                )
            ):
                return await direct(
                    messages,
                    tools=None,
                    max_tokens=self.summary_max_tokens,
                    temperature=0.0,
                )

    async def compress_coverage_job(
        self,
        job: Any,
        *,
        resolved_provider: Any,
        resolved_model: str,
        compaction_cycle_id: str,
        workload_context: ProviderWorkloadContext | None = None,
    ) -> str:
        transcript = _render_transcript(list(job.messages))
        if not transcript.strip():
            raise RuntimeError("coverage_compaction_source_empty")
        response = await self._call_summary_model(
            [
                {"role": "system", "content": self._SUMMARY_SYSTEM},
                {"role": "user", "content": transcript},
            ],
            resolved_provider=resolved_provider,
            resolved_model=resolved_model,
            workload_context=workload_context,
        )
        summary = _response_content(response).strip()
        if not summary:
            raise RuntimeError("coverage_compaction_summary_empty")
        logger.info(
            "coverage_compaction_completed",
            cycle_id=compaction_cycle_id,
            first_message_id=int(job.first_message_id),
            last_message_id=int(job.last_message_id),
            source_hash=str(job.source_hash),
            provider=getattr(resolved_provider, "id", type(resolved_provider).__name__),
            model=resolved_model,
        )
        return summary

    async def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        goal_text: "str | None" = None,
        pending_tasks: "list[str] | None" = None,
        resolved_provider: Any = None,
        resolved_model: str | None = None,
        compaction_cycle_id: str | None = None,
        workload_context: ProviderWorkloadContext | None = None,
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

        # ── WI-2 microcompact: 先廉价清陈旧 tool_result(不调模型、不动语义)。
        # 命中后若整体已降到触发线下 → 直接返回,省一次 haiku 调用(最高频生效层)。
        work, n_micro = _microcompact_tool_results(
            messages,
            self.microcompact_keep_tools,
            keep_bytes=(
                self.microcompact_keep_bytes
                if self.microcompact_size_aware else None
            ),
        )
        if n_micro > 0:
            try:
                from .tokens import count_messages_tokens as _cmt
                if _cmt(work) < self.trigger_tokens():
                    logger.info(
                        "context_microcompact_only",
                        tool_results_pruned=n_micro,
                        window=self.context_window,
                    )
                    return CompressionResult(
                        messages=_sanitize_tool_pairs(work),
                        compressed=True,
                        latency_ms=(time.monotonic() - start) * 1000.0,
                        meta={
                            "reason": "microcompact_only",
                            "tool_results_pruned": n_micro,
                        },
                    )
            except Exception:  # noqa: BLE001
                pass

        # Normalize previous compaction formats before choosing new cut points.
        # A summary may have moved into the preserved head/tail, so extracting
        # only the middle can create nested summaries on the next pass.
        partition_work = [
            message for message in work if not _is_compaction_handoff(message)
        ]
        prior_summary, partition_work = _extract_prior_summary(partition_work)
        system_msgs, first_chunk, middle_chunk, last_chunk = _partition(
            partition_work, self.first_n, self.last_n
        )

        if not middle_chunk:
            if prior_summary:
                summary_message = _summary_message(
                    prior_summary, source_messages=work
                )
                new_messages = _sanitize_tool_pairs(
                    list(system_msgs)
                    + list(first_chunk)
                    + [summary_message]
                    + list(last_chunk)
                )
                if _compression_preserves_request_boundary(work, new_messages):
                    return CompressionResult(
                        messages=new_messages,
                        compressed=True,
                        latency_ms=(time.monotonic() - start) * 1000.0,
                        meta={
                            "reason": "prior_summary_passthrough",
                            "tool_results_pruned": n_micro,
                        },
                    )
            # Nothing to compress — window is too short.
            return CompressionResult(
                messages=_sanitize_tool_pairs(work) if n_micro > 0 else list(work),
                compressed=n_micro > 0,
                latency_ms=(time.monotonic() - start) * 1000.0,
                meta={
                    "reason": "no_middle_to_summarize",
                    "tool_results_pruned": n_micro,
                },
            )

        if self._llm is None and resolved_provider is None:
            return CompressionResult(
                messages=_sanitize_tool_pairs(work) if n_micro > 0 else list(work),
                compressed=n_micro > 0,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error=None if n_micro > 0 else "no_llm_registry",
                meta={
                    "reason": "microcompact_no_llm" if n_micro > 0 else "llm_registry_missing",
                    "tool_results_pruned": n_micro,
                },
            )

        # ── WI-3 锚定增量: 上次 [压缩摘要] 已在切分前从任意动态位置抽出，
        # 不混进待摘 transcript，防止摘要套娃 drift。
        middle_for_summary = middle_chunk

        # 边界: 中段去掉旧摘要后为空(只剩旧摘要+噪声) → 透传 prior,不丢(不调模型)。
        middle_text = _render_transcript(middle_for_summary)
        if not middle_text.strip():
            if prior_summary:
                summary_message = _summary_message(
                    prior_summary, source_messages=work
                )
                has_anchor = any(
                    m.get("role") == "system"
                    and str(m.get("content") or "").startswith("[目标锚定]")
                    for m in system_msgs
                )
                anchor_msgs = (
                    []
                    if has_anchor
                    else _build_goal_anchor(
                        goal_text,
                        pending_tasks,
                        metadata_enabled=_metadata_enabled(work),
                        anchor_after=_latest_fragment_id(system_msgs),
                    )
                )
                new_messages = _sanitize_tool_pairs(
                    list(system_msgs)
                    + anchor_msgs
                    + list(first_chunk)
                    + [summary_message]
                    + list(last_chunk)
                )
                if not _compression_preserves_request_boundary(work, new_messages):
                    return CompressionResult(
                        messages=(
                            _sanitize_tool_pairs(work)
                            if n_micro > 0
                            else list(messages)
                        ),
                        compressed=n_micro > 0,
                        latency_ms=(time.monotonic() - start) * 1000.0,
                        error="request_boundary_changed",
                        meta={"tool_results_pruned": n_micro},
                    )
                return CompressionResult(
                    messages=new_messages,
                    compressed=True,
                    latency_ms=(time.monotonic() - start) * 1000.0,
                    meta={"reason": "prior_summary_passthrough", "tool_results_pruned": n_micro},
                )
            return CompressionResult(
                messages=_sanitize_tool_pairs(work) if n_micro > 0 else list(work),
                compressed=n_micro > 0,
                latency_ms=(time.monotonic() - start) * 1000.0,
                meta={"reason": "empty_middle_after_microcompact", "tool_results_pruned": n_micro},
            )

        input_tokens = _approx_tokens(middle_text)

        summary_system = self._SUMMARY_SYSTEM
        if prior_summary:
            summary_system = (
                self._SUMMARY_SYSTEM
                + "\n\n【已有摘要(在此基础上增量更新)】\n"
                + prior_summary
                + "\n\n⚠️ 增量规则(打破'任务棘轮'): 保留已有摘要里的【关键事实与决策】"
                "【涉及的文件/产物】;但【进行中/当前任务】**以最新对话为准**——"
                "若最新请求已切换到新任务,就把已有摘要里那个旧的'当前任务'移到"
                "【早前已结束的任务】(一行带过),**不要**再把它当当前任务保活。"
            )

        try:
            response = await self._call_summary_model(
                [
                    {"role": "system", "content": summary_system},
                    {"role": "user", "content": middle_text},
                ],
                resolved_provider=resolved_provider,
                resolved_model=resolved_model,
                workload_context=workload_context,
            )
        except Exception as exc:
            logger.warning(
                "context_compressor.llm_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # safe-fail: 退回上一级 = microcompact 后的 work(保住占位收益),
            # 而非最原始 messages(否则 microcompact 白做)。与 no_middle/no_llm 一致。
            return CompressionResult(
                messages=_sanitize_tool_pairs(work) if n_micro > 0 else list(messages),
                compressed=n_micro > 0,
                input_tokens=input_tokens,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error=str(exc),
                meta={"tool_results_pruned": n_micro},
            )

        summary_text = _response_content(response).strip()
        if not summary_text:
            return CompressionResult(
                messages=_sanitize_tool_pairs(work) if n_micro > 0 else list(messages),
                compressed=n_micro > 0,
                input_tokens=input_tokens,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error="empty_summary",
                meta={"tool_results_pruned": n_micro},
            )

        # 反射后处理闸(issue #46602): 新摘要疑似复述压缩提示词本身(把元指令当用户
        # 任务)→ 不让它落地。优先回退到上一条**干净** prior(_extract_prior_summary
        # 已保证 prior 非反射);无干净 prior 时只能保留并告警(WI-4a 目标 always-on
        # system 段仍护任务连续性,反射只是让本条摘要质量差,不致命)。
        reflective = _looks_reflective(summary_text)
        if reflective:
            logger.warning(
                "context_compressor.reflective_summary_detected",
                preview=summary_text[:120],
                fell_back_to_prior=bool(prior_summary),
            )
            if prior_summary:
                summary_text = prior_summary

        output_tokens = _approx_tokens(summary_text)
        summary_message = _summary_message(summary_text, source_messages=work)

        # WI-4a 去重: 整合路径里 agent_loop 已注入 always-on [目标锚定] system,
        # 它经 _partition 进 system_msgs。若已存在就**不再**自注第二条(满足
        # "[目标锚定] system 恒 ≤1 条" 的 DoD);独立调用(无 always-on,如单测)
        # 仍按 goal_text 注一条,保 BC。
        has_existing_anchor = any(
            m.get("role") == "system"
            and str(m.get("content") or "").startswith("[目标锚定]")
            for m in system_msgs
        )
        anchor_msgs = (
            []
            if has_existing_anchor
            else _build_goal_anchor(
                goal_text,
                pending_tasks,
                metadata_enabled=_metadata_enabled(work),
                anchor_after=_latest_fragment_id(system_msgs),
            )
        )
        new_messages = _sanitize_tool_pairs(
            list(system_msgs)
            + anchor_msgs
            + list(first_chunk)
            + [summary_message]
            + list(last_chunk)
        )
        if not _compression_preserves_request_boundary(work, new_messages):
            logger.error(
                "context_compressor.request_boundary_guard_failed",
                summarized_msgs=len(middle_chunk),
            )
            fallback = _sanitize_tool_pairs(work) if n_micro > 0 else list(messages)
            return CompressionResult(
                messages=fallback,
                compressed=n_micro > 0,
                input_tokens=input_tokens,
                latency_ms=(time.monotonic() - start) * 1000.0,
                error="request_boundary_changed",
                meta={"tool_results_pruned": n_micro},
            )

        # Reduction ratio is measured on the rolled-up middle only —
        # preserves the "40% reduction" post-condition in §13.5.
        reduction = 0.0
        if input_tokens > 0:
            reduction = max(0.0, 1.0 - (output_tokens / input_tokens))

        # 可观测(优化 #4): 压缩命中落一条结构化锚点,便于排查"何时压了/压了多少"。
        # grep `context_compacted` → stderr → tauri dev log。
        logger.info(
            "context_compacted",
            middle_tokens_in=input_tokens,
            summary_tokens_out=output_tokens,
            reduction=round(reduction, 3),
            summarized_msgs=len(middle_chunk),
            kept_head=len(first_chunk),
            kept_tail=len(last_chunk),
            model=resolved_model or self.model,
            window=self.context_window,
            threshold_pct=self.threshold_percent,
            summary_preview=summary_text[:300],
        )

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
def _response_content(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("content") or "")
    return str(getattr(response, "content", "") or "")


def _message_metadata(message: dict[str, Any]) -> dict[str, Any] | None:
    metadata = message.get(CONTEXT_MESSAGE_META_KEY)
    return metadata if isinstance(metadata, dict) else None


def _causal_group_id(message: dict[str, Any]) -> str | None:
    metadata = _message_metadata(message)
    group_id = metadata.get("causal_group_id") if metadata else None
    return str(group_id) if group_id else None


def _latest_fragment_id(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        metadata = _message_metadata(message)
        fragment_id = metadata.get("fragment_id") if metadata else None
        if fragment_id:
            return str(fragment_id)
    return None


def _metadata_enabled(messages: list[dict[str, Any]]) -> bool:
    return any(_message_metadata(message) is not None for message in messages)


def _summary_message(
    summary_text: str, *, source_messages: list[dict[str, Any]]
) -> dict[str, Any]:
    message = {
        "role": "system",
        "content": _format_summary(summary_text),
    }
    if not _metadata_enabled(source_messages):
        return message
    digest = hashlib.sha256(summary_text.encode("utf-8")).hexdigest()[:16]
    return tag_message(
        message,
        ContextMessageMeta(
            placement="control",
            lifetime="history",
            source="context_compressor.summary",
            trim_policy="summarize",
            fragment_id=f"context-compressor:summary:{digest}",
            reason="compressed_middle",
        ),
    )


def _partition(
    messages: list[dict[str, Any]], first_n: int, last_n: int
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Split messages by the §13.3 rule.

    System messages are pulled out and returned as a group. The rest are
    then split into ``first_n + middle + last_n``. If the non-system
    count is ≤ first_n+last_n the middle is empty.

    切割点会对齐 tool 配对边界(2026-06-12 真机 400 修复)：OpenAI 协议
    要求 ``role:"tool"`` 必须紧跟在带 ``tool_calls`` 的 assistant 之后。
    纯按位置切会把配对切断 —— tail 开头留下孤儿 tool(配对 assistant 被
    压进 middle)、head 尾部留下悬空 tool_calls(响应被压进 middle)，上游
    直接 400 Bad Request。对齐规则：
      * head 从尾部收缩：最后一条是带 tool_calls 的 assistant 且其 tool
        响应不全在 head → 该 assistant 挪进 middle(连同其后已在 head 的
        tool 响应)。
      * tail 从头部收缩：开头的 tool 消息(配对在 middle)挪进 middle。
    """
    def _is_prefix_system(message: dict[str, Any]) -> bool:
        if message.get("role") != "system":
            return False
        # Rolling summaries are dynamic history even for legacy callers that
        # do not attach Context OS metadata. Never promote them into the frozen
        # prefix on a later compression pass.
        if str(message.get("content") or "").lstrip().startswith(_SUMMARY_MARKER):
            return False
        metadata = message.get(CONTEXT_MESSAGE_META_KEY)
        if not isinstance(metadata, dict):
            return True
        return metadata.get("placement") == "prefix"

    system_msgs = [m for m in messages if _is_prefix_system(m)]
    non_system = [m for m in messages if not _is_prefix_system(m)]
    n = len(non_system)
    if n <= first_n + last_n:
        # Keep a useful head/tail boundary for prior-summary passthrough even
        # though ordinary no-summary callers will return the original list.
        cut = min(first_n, max(0, n - last_n))
        if (
            0 < cut < n
            and non_system[cut - 1].get("role") == "user"
            and non_system[cut].get("role") == "assistant"
        ):
            cut -= 1
        return system_msgs, non_system[:cut], [], non_system[cut:]

    cut_head = first_n
    cut_tail = n - last_n if last_n > 0 else n

    # Preserve ordinary user/assistant turns just as strictly as tool groups.
    # A message-count cut such as [..., user] | [assistant, ...] makes the
    # retained tail start with an orphan answer, which can bias the next model
    # response and loses the question/answer causal unit.
    if (
        0 < cut_head < n
        and non_system[cut_head - 1].get("role") == "user"
        and non_system[cut_head].get("role") == "assistant"
    ):
        cut_head -= 1
    if (
        0 < cut_tail < n
        and non_system[cut_tail].get("role") == "assistant"
        and non_system[cut_tail - 1].get("role") == "user"
    ):
        cut_tail -= 1

    # head 尾部不停在「悬空 tool_calls」上：若 head 最后一条是带
    # tool_calls 的 assistant(它的 tool 响应在切割点之后) → 收缩。
    while cut_head > 0:
        last = non_system[cut_head - 1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            cut_head -= 1
            continue
        if last.get("role") == "tool":
            # head 以 tool 结尾本身合法(配对在更前面),但若同组 tool 响应
            # 跨越切割点(下一条还是 tool) → 整组连同 assistant 一起收缩。
            if cut_head < n and non_system[cut_head].get("role") == "tool":
                cut_head -= 1
                continue
        break

    # tail 开头不以孤儿 tool 起步：配对 assistant 在 middle 已被压缩。
    while cut_tail < n and non_system[cut_tail].get("role") == "tool":
        cut_tail += 1

    # Context OS sidecars make the causal unit explicit.  Keep every tagged
    # assistant+tool-result group on one side of either cut even if a future
    # provider introduces a non-tool message shape inside that group.
    while cut_head > 0 and cut_head < n:
        left_group = _causal_group_id(non_system[cut_head - 1])
        if not left_group or left_group != _causal_group_id(non_system[cut_head]):
            break
        cut_head -= 1
    while cut_tail > 0 and cut_tail < n:
        left_group = _causal_group_id(non_system[cut_tail - 1])
        if not left_group or left_group != _causal_group_id(non_system[cut_tail]):
            break
        cut_tail += 1

    if cut_tail <= cut_head:
        # 对齐后没有可压缩的 middle — 放弃压缩(调用方按 no_middle 处理)。
        return system_msgs, non_system, [], []

    head = non_system[:cut_head]
    middle = non_system[cut_head:cut_tail]
    tail = non_system[cut_tail:]
    return system_msgs, head, middle, tail


def _sanitize_tool_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """输出前的协议兜底清洗：删孤儿 tool 消息、剥悬空 tool_calls。

    任何来源的非法序列(不只 compressor 自己)到这里都被修齐,保证发给
    OpenAI 兼容上游的消息序列永远合法。纯函数,不改入参。
    """
    out: list[dict[str, Any]] = []
    open_ids: set = set()  # 上一条 assistant.tool_calls 中尚未见到响应的 id
    for m in messages:
        role = m.get("role")
        if role == "tool":
            if m.get("tool_call_id") in open_ids:
                out.append(m)
                continue
            # 孤儿 tool：配对 assistant 已被压缩 → 丢弃(内容在摘要里)。
            continue
        if role == "assistant" and m.get("tool_calls"):
            open_ids = {
                tc.get("id") for tc in (m.get("tool_calls") or []) if tc.get("id")
            }
            out.append(m)
            continue
        if role != "tool":
            open_ids = set()
        out.append(m)

    # 二遍：剥掉「响应不全」的 assistant.tool_calls(悬空) — 倒序找每个
    # assistant 的后续 tool 响应是否齐全。
    for i, m in enumerate(out):
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        want = {tc.get("id") for tc in (m.get("tool_calls") or []) if tc.get("id")}
        got = set()
        for follow in out[i + 1:]:
            if follow.get("role") == "tool" and follow.get("tool_call_id") in want:
                got.add(follow.get("tool_call_id"))
            else:
                break
        if want - got:
            stripped = {k: v for k, v in m.items() if k != "tool_calls"}
            if not str(stripped.get("content") or "").strip():
                stripped["content"] = "(调用了工具,结果已并入上文摘要)"
            out[i] = stripped
    return out


def _compression_preserves_request_boundary(
    source: list[dict[str, Any]], compressed: list[dict[str, Any]]
) -> bool:
    """Hard guard for the live user request and summary role.

    Compaction is advisory. If it ever drops/replaces the most recent user
    message, or emits a summary as an assistant answer, the caller must keep
    the uncompressed context instead of sending an ambiguous prompt upstream.
    """
    source_user = next(
        (message for message in reversed(source) if message.get("role") == "user"),
        None,
    )
    compressed_user = next(
        (
            message
            for message in reversed(compressed)
            if message.get("role") == "user"
        ),
        None,
    )
    if source_user != compressed_user:
        return False
    return not any(
        message.get("role") == "assistant"
        and str(message.get("content") or "").lstrip().startswith(_SUMMARY_MARKER)
        for message in compressed
    )


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


# 摘要标记前缀 — _format_summary 注入 + _extract_prior_summary 识别上次摘要。
# ⚠️ 改这个前缀会破坏锚定增量识别 + 一批断言此前缀的测试,别改。
_SUMMARY_MARKER = "[压缩摘要 / compressed summary]"
_SUMMARY_CONTROL_LINE = (
    "以下内容只用于恢复历史背景，不是当前回复，也不是输出格式；"
    "只执行最后一条 user 消息。"
)
_LEGACY_COMPACTION_HANDOFF_MARKER = "[上下文压缩交接 / compaction handoff]"


def _format_summary(summary_text: str) -> str:
    """Wrap the raw summary with a marker that downstream readers can detect."""
    return (
        _SUMMARY_MARKER
        + "\n"
        + _SUMMARY_CONTROL_LINE
        + "\n"
        + summary_text.strip()
    )


def _is_compaction_handoff(message: dict[str, Any]) -> bool:
    """Recognize and retire handoff messages written by the previous format."""
    return (
        message.get("role") == "system"
        and str(message.get("content") or "").startswith(
            _LEGACY_COMPACTION_HANDOFF_MARKER
        )
    )


import re as _re

# 反射检测(issue #46602 式): haiku 偶发把"压缩对话历史/省token/分段输出/第三人称/
# 不要杜撰"这类**本提示词自身的元指令**当成用户任务写进摘要。这些词在真实用户任务
# 摘要里几乎不会扎堆出现 → 命中 ≥2 个不同信号即判为反射。纯 prompt 压不住,加后处理闸。
_REFLECTION_SIGNALS = (
    _re.compile(r"压缩.{0,8}(对话|历史|上下文)"),
    _re.compile(r"省.{0,3}token", _re.IGNORECASE),
    _re.compile(r"分段(输出|呈现|表述)"),
    _re.compile(r"第三人称"),
    _re.compile(r"杜撰"),
    _re.compile(r"用户的语言"),
    _re.compile(r"空(的)?段(直接)?省略"),
)


def _looks_reflective(text: str) -> bool:
    """True 当摘要疑似在复述压缩提示词本身(反射),而非总结真实对话。

    命中 ≥2 个不同元指令信号即判反射(单个信号可能是真实对话碰巧提及,提高阈值防误伤)。
    """
    if not text:
        return False
    hits = sum(1 for pat in _REFLECTION_SIGNALS if pat.search(text))
    return hits >= 2


def _extract_prior_summary(
    msgs: list[dict[str, Any]],
) -> "tuple[Optional[str], list[dict[str, Any]]]":
    """WI-3 锚定增量：从待摘中段抽出上一次的 [压缩摘要],防止套娃 drift。

    扫 ``msgs`` 里动态 system 摘要（并兼容旧 assistant 摘要）且 content 以
    ``_SUMMARY_MARKER`` 开头的消息:
      * 取**最后一条**的正文(剥掉前缀行)作为 ``prior_summary``;
      * 把**所有**这类消息从列表里剔除(它们不该再被当待摘 transcript 重摘)。
    返回 ``(prior_summary, msgs_without_prior)``。纯函数,不改入参。

    无历史摘要 → 返回 ``(None, list(msgs))``。
    """
    prior: Optional[str] = None
    kept: list[dict[str, Any]] = []
    for m in msgs:
        content = m.get("content")
        if (
            m.get("role") in {"system", "assistant"}
            and isinstance(content, str)
            and content.lstrip().startswith(_SUMMARY_MARKER)
        ):
            # 剥掉前缀行,余下即上次摘要正文。多条则保留最后一条。
            body = content.lstrip()[len(_SUMMARY_MARKER):].strip()
            if body.startswith(_SUMMARY_CONTROL_LINE):
                body = body[len(_SUMMARY_CONTROL_LINE):].strip()
            # 防 drift 传播: 反射的旧摘要**不**作为 prior-state 带入(否则反射会被
            # 增量更新永久延续);但仍从待摘列表剔除(不重新摘旧摘要)。
            if not _looks_reflective(body):
                prior = body
            continue  # 从待摘列表剔除(反射与否都剔,不重摘旧摘要)
        kept.append(m)
    return prior, kept


def _microcompact_tool_results(
    messages: list[dict[str, Any]],
    keep_recent_tools: int,
    *,
    keep_bytes: "int | None" = None,
) -> "tuple[list[dict[str, Any]], int]":
    """WI-2 microcompact：把陈旧 tool_result 正文换占位串(不调模型、不动语义)。

    保护**最近 keep_recent_tools 个** role=="tool" 消息的 content 原文;更早的
    tool 消息 content 换成占位,但**保留整条消息壳 + tool_call_id**(绝不删整条,
    否则破坏 assistant.tool_calls↔tool 配对计数,见 _sanitize_tool_pairs)。
    assistant.tool_calls 一律不动。纯函数,不改入参。

    WI-1B-5 size-aware(``keep_bytes`` 非 None 时启用): 在"最近 N 条"基础上再叠
    "累计 content 字节 ≤ keep_bytes"——从最近往旧累加,字节预算耗尽即停止保护
    (避免最近 N 条里混入巨型 tool_result 仍把窗口撑爆)。``keep_bytes=None``
    (默认)→ 纯按"最近 N 条",与旧行为字节级一致(BC)。

    返回 ``(new_messages, n_compacted)``。``n_compacted`` = 实际被换占位的条数。
    """
    _PLACEHOLDER = "[旧工具结果已清理 / stale tool result pruned]"
    # 收集所有 role=="tool" 的下标,最近 keep_recent_tools 个受保护。
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if not tool_idxs:
        return list(messages), 0
    recent_idxs = tool_idxs[-keep_recent_tools:] if keep_recent_tools > 0 else []
    if keep_bytes is None:
        # BC 路径: 纯"最近 N 条"。
        protected = set(recent_idxs)
    else:
        # size-aware: 在最近 N 条里从新到旧累加字节,超预算即不再保护更旧的。
        protected = set()
        used = 0
        for idx in reversed(recent_idxs):  # 最近的先入,旧的后入
            body_len = len(str(messages[idx].get("content") or "").encode("utf-8"))
            if used + body_len > keep_bytes:
                # 预算耗尽: 当前及更旧的(更靠前)一律不再保护 → 纳入压缩判断。
                break
            protected.add(idx)
            used += body_len
    out: list[dict[str, Any]] = []
    n_compacted = 0
    for i, m in enumerate(messages):
        if (
            m.get("role") == "tool"
            and i not in protected
            and str(m.get("content") or "").strip()
            and m.get("content") != _PLACEHOLDER
        ):
            new_m = dict(m)
            new_m["content"] = _PLACEHOLDER
            out.append(new_m)
            n_compacted += 1
        else:
            out.append(m)
    return out, n_compacted


def _approx_tokens(text: str) -> int:
    """Token estimate — 统一走 tokens.count_text_tokens(CJK-aware + 可选 tiktoken,
    与 budget/assembler 同口径,消除旧 ``len//4`` 不一致,优化 #1+#3)。"""
    if not text:
        return 0
    from .tokens import count_text_tokens
    return count_text_tokens(text)


def _build_goal_anchor(
    goal_text: "str | None",
    pending_tasks: "list[str] | None",
    *,
    metadata_enabled: bool = False,
    anchor_after: str | None = None,
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
    message = {"role": "system", "content": content}
    if metadata_enabled:
        message = tag_message(
            message,
            ContextMessageMeta(
                placement="control",
                lifetime="current",
                source="context_compressor.goal_anchor",
                protected=True,
                trim_policy="never",
                fragment_id="context-compressor:goal-anchor",
                anchor_after=anchor_after,
                reason="post_compaction_goal_reanchor",
            ),
        )
    return [message]
