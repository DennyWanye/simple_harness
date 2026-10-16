# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Before and after the turn loop: run start, chain budget, turn state, initial coverage and the max-iterations finish."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from agent.context_report import estimate_selected_attempt_budget
from agent.task_id import new_task_id
from agent.termination import TerminationReason
from llm.context_messages import CONTEXT_MESSAGE_META_KEY, ContextMessageMeta, tag_message

from .core import AgentLoopCore
from .support import (
    AgentEvent,
    ErrorEvent,
    FinalEvent,
    _append_loop_control,
    _latest_context_anchor,
    _RunState,
    logger,
)


def _tag_run_input_messages(
    messages: list[dict[str, Any]], *, task_id: str
) -> list[dict[str, Any]]:
    """Add host-only identity to untagged prepared input without wire changes."""

    tagged: list[dict[str, Any]] = []
    open_group: str | None = None
    open_tool_ids: set[str] = set()
    for index, message in enumerate(messages):
        if CONTEXT_MESSAGE_META_KEY in message:
            tagged.append(message)
            continue
        role = str(message.get("role") or "user")
        fragment_id = f"agent-loop:{task_id}:input:{index}"
        if role == "assistant" and message.get("tool_calls"):
            open_group = f"agent-loop:{task_id}:input-group:{index}"
            open_tool_ids = {
                str(call.get("id"))
                for call in message.get("tool_calls") or ()
                if isinstance(call, dict) and call.get("id")
            }
        causal_group_id = (
            open_group
            if role == "assistant"
            or (role == "tool" and str(message.get("tool_call_id")) in open_tool_ids)
            else None
        )
        if role == "system":
            metadata = ContextMessageMeta(
                placement="prefix",
                lifetime="stable",
                source="agent_loop.prepared_input",
                protected=True,
                trim_policy="never",
                fragment_id=fragment_id,
            )
        else:
            metadata = ContextMessageMeta(
                placement="transcript",
                lifetime="history",
                source="agent_loop.prepared_input",
                trim_policy="summarize",
                fragment_id=fragment_id,
                causal_group_id=causal_group_id,
            )
        tagged.append(tag_message(message, metadata))
        if role == "tool":
            open_tool_ids.discard(str(message.get("tool_call_id")))
            if not open_tool_ids:
                open_group = None
    return tagged


class RunSetupMixin(AgentLoopCore):
    """Before and after the turn loop: run start, chain budget, turn state, initial coverage and the max-iterations finish."""

    __slots__ = ()

    async def _begin_run(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """整理入参：启动参数配对、外部反馈、工作消息、工具清单与提供方链检查（空链直接报错停下）。"""

        state.host_validated_zero_tool_terminal = bool(
            state.llm_kwargs.pop("host_validated_zero_tool_terminal", False)
        )
        if (state.launch_operation_id is None) != (state.provider_launch_snapshot is None):
            raise ValueError("launch operation id and snapshot must be paired")
        state.launch_call_kwargs = (
            {}
            if state.launch_operation_id is None
            else dict(
                launch_operation_id=state.launch_operation_id,
                provider_launch_snapshot=state.provider_launch_snapshot,
            )
        )
        state.scoped_evidence = state.llm_kwargs.pop("_scoped_evidence", None)
        state.external_feedback = dict(state.llm_kwargs.pop("_external_tool_feedback", {}) or {})
        state.tid = state.task_id or new_task_id()
        self._current_tid = state.tid  # 供 _pipeline_event 构造观测事件用（plans/2026-06-24-...）
        state.working_messages: list[dict[str, Any]] = list(state.messages)
        state.context_metadata_enabled = state.prepared_context is not None
        if state.context_metadata_enabled:
            state.working_messages = _tag_run_input_messages(
                state.working_messages, task_id=state.tid
            )
        # ⚠️ R1（round-2，plans/2026-06-24-...）：Step2 取证布尔累积标志，对 compaction 免疫
        # （compaction 整体替换 working_messages 不会改这个已置位的布尔）。run() 开头重设 +
        # 快照 history 注入的旧 tool name → 本 run 只认「新 dispatch 且不在 history 快照里」的取证。
        self._evidence_gathered = bool(state.external_feedback.get("evidence_gathered"))
        self._evidence_nudges_used = int(state.external_feedback.get("evidence_nudges_used", 0))
        self._history_tool_names = set(self._collect_tool_names(state.working_messages))
        state.current_tool_set = (
            getattr(state.prepared_context, "tool_set", None)
            if state.prepared_context is not None
            else None
        )
        if state.current_tool_set is not None:
            state.tool_schemas = list(state.current_tool_set.logical_schemas())
        else:
            state.tool_schemas = self.tools.schemas(enabled_toolsets=state.tools_filter)
        if state.tool_names_filter is not None:
            state.allowed_tool_names = set(state.tool_names_filter)
            state.tool_schemas = [
                schema
                for schema in state.tool_schemas
                if str(
                    (
                        schema.get("function", {})
                        if isinstance(schema.get("function"), dict)
                        else schema
                    ).get("name", "")
                )
                in state.allowed_tool_names
            ]
        state.totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        state.use_model = state.model or self.default_model

        # ──────────────── P5-S2 Phase 3: provider chain mode ────────────────
        #
        # When ``provider_chain`` is supplied, AgentLoop walks the chain
        # itself instead of delegating to ``self.llm.chat_with_fallback``.
        # Each provider is tried in order; LLMProviderError (transient)
        # falls through to the next; permanent errors (args_parse_error
        # etc. — surfaced via ChatResponse, NOT exceptions) short-circuit
        # the chain because the next provider would just see the same
        # broken request and fail identically.
        #
        # Empty chain is an actionable user-facing error — emit it once
        # at the top of run() so the loop never tries to call anything.
        state.chain_mode = state.provider_chain is not None
        if state.chain_mode and not state.provider_chain:
            yield ErrorEvent(
                type="error",
                task_id=state.tid,
                iteration=0,
                reason="no_provider_configured",
                detail=("未配置任何 LLM provider。请打开设置 → LLM Providers → 添加"),
            )
            state.flow = "stop"
            return
        # Detect streaming capability lazily — agent loop tests use a
        # mock registry that may only define chat_with_fallback.
        state.stream_capable = state.stream and callable(
            getattr(self.llm, "chat_with_fallback_stream", None)
        )

    async def _plan_chain_budget(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """提供方链模式：按链上最小窗口统一规划一次上下文（必要时先做覆盖压缩），放不下就停。"""

        # Context OS plans one common transcript against the smallest usable
        # provider window before the first transport attempt. This keeps
        # fallback reversible: a smaller backup never receives a request that
        # was only valid for the primary, and it does not terminate the chain
        # merely because its window was discovered late.
        state._chain_budget_rescue_pending = False
        if state.context_metadata_enabled and state.chain_mode and state.provider_chain:
            state.initial_budget = getattr(state.prepared_context, "request_budget", None)
            state.reserve = int(
                getattr(state.initial_budget, "generation_reserve", 0)
                or state.llm_kwargs.get("max_tokens", 0)
                or 0
            )
            state.attachment_tokens = int(
                getattr(state.prepared_context, "attachment_tokens", 0) or 0
            )
            state.candidate_budgets = [
                estimate_selected_attempt_budget(
                    provider=provider,
                    model_id=str(getattr(provider, "model", "") or state.use_model or ""),
                    messages=state.working_messages,
                    tools=state.tool_schemas or None,
                    generation_reserve=state.reserve,
                    attachment_tokens=state.attachment_tokens,
                )
                for provider in state.provider_chain
            ]
            state.safe_budget = min(
                state.candidate_budgets,
                key=lambda item: item.effective_input_budget,
            )
            try:
                state.replan_for_budget = getattr(state.prepared_context, "replan_for_budget", None)
                state.used_common_replan = callable(state.replan_for_budget)
                await self._replan_chain_for_budget(state)
                state.current_tool_set = getattr(state.prepared_context, "tool_set", None)
                if state.current_tool_set is not None:
                    state.tool_schemas = list(state.current_tool_set.logical_schemas())
                state.final_budgets = [
                    estimate_selected_attempt_budget(
                        provider=provider,
                        model_id=str(getattr(provider, "model", "") or state.use_model or ""),
                        messages=state.working_messages,
                        tools=state.tool_schemas or None,
                        generation_reserve=state.reserve,
                        attachment_tokens=state.attachment_tokens,
                    )
                    for provider in state.provider_chain
                ]
                state.coverage_still_pending = bool(
                    state.used_common_replan
                    and tuple(
                        getattr(
                            state.prepared_context,
                            "coverage_compaction_jobs",
                            (),
                        )
                        or ()
                    )
                )
                state.final_budget_fits = all(item.fits for item in state.final_budgets)
                if state.coverage_still_pending or not state.final_budget_fits:
                    logger.warning(
                        "provider_chain_common_budget_detail sid=%s tools=%d budgets=%s",
                        state.session_id,
                        len(state.tool_schemas or ()),
                        [
                            {
                                "planned": item.planned_input_tokens,
                                "effective": item.effective_input_budget,
                                "messages": item.messages_tokens,
                                "tools": item.tool_tokens,
                                "reserve": item.generation_reserve,
                            }
                            for item in state.final_budgets
                        ],
                    )
                    if state.coverage_still_pending or self.compressor is None:
                        raise RuntimeError("provider_chain_context_budget_exceeded")
                    # Replanning has already exhausted the lossless options.
                    # Let the normal in-loop compactor make exactly one lossy,
                    # goal-preserving rescue attempt before any provider send.
                    state._chain_budget_rescue_pending = True
                self._history_tool_names = set(self._collect_tool_names(state.working_messages))
            except Exception as exc:  # noqa: BLE001 - recoverable BLOCK
                logger.warning(
                    "provider_context_budget_blocked sid=%s request_id=%s error_type=%s error=%r",
                    state.session_id,
                    state.context_request_id or state.tid,
                    type(exc).__name__,
                    exc,
                )
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=0,
                    reason="provider_context_budget_exceeded",
                    detail=str(exc),
                )
                state.flow = "stop"
                return

    async def _replan_chain_for_budget(self, state: _RunState) -> None:
        """链式预算不够时让上层重排计划。"""

        if callable(state.replan_for_budget):
            state.replanned = await asyncio.wait_for(
                state.replan_for_budget(
                    context_window=state.safe_budget.context_window,
                    effective_pct=state.safe_budget.effective_pct,
                    generation_reserve=state.safe_budget.generation_reserve,
                ),
                timeout=10.0,
            )
            state.prepared_context = getattr(state.replanned, "prepared_context", state.replanned)
            if tuple(getattr(state.prepared_context, "coverage_compaction_jobs", ()) or ()):
                if self.compressor is None:
                    raise RuntimeError("coverage_compaction_owner_unavailable")
                state.chain_cycle_id = f"{state.context_request_id or state.tid}:chain-safe:1"
                state.resolution = self._resolve_compression_model(
                    state.provider_chain,
                    requested_model=self._requested_compression_model(),
                )
                await self._flush_context_snapshot(
                    cycle_id=state.chain_cycle_id,
                    session_id=state.session_id,
                    request_id=state.context_request_id or state.tid,
                    working_messages=_tag_run_input_messages(
                        list(state.prepared_context.messages), task_id=state.tid
                    ),
                    prepared_context=state.prepared_context,
                    current_tool_set=getattr(state.prepared_context, "tool_set", None),
                )
                await self._execute_coverage_compaction_jobs(
                    cycle_id=state.chain_cycle_id,
                    prepared_context=state.prepared_context,
                    resolution=state.resolution,
                )
                logger.info(
                    "coverage_replan_started sid=%s request_id=%s phase=chain_safe",
                    state.session_id,
                    state.context_request_id or state.tid,
                )
                state.replanned = await asyncio.wait_for(
                    state.replan_for_budget(
                        context_window=state.safe_budget.context_window,
                        effective_pct=state.safe_budget.effective_pct,
                        generation_reserve=state.safe_budget.generation_reserve,
                    ),
                    timeout=10.0,
                )
                logger.info(
                    "coverage_replan_completed sid=%s request_id=%s phase=chain_safe",
                    state.session_id,
                    state.context_request_id or state.tid,
                )
                state.prepared_context = getattr(
                    state.replanned, "prepared_context", state.replanned
                )
            state.working_messages = _tag_run_input_messages(
                list(state.prepared_context.messages), task_id=state.tid
            )

    async def _init_turn_state(self, state: _RunState) -> None:
        """本轮的计数器与闸门开关清零；目标锚定注入一次；准备冻结技能的重挂载。"""

        # P5-S2 Hook A: per-run nudge counter for the completion guard.
        # Reset every fresh ``run`` invocation so each chat turn gets a
        # fresh nudge budget — we don't want stale "already nudged 2x"
        # state leaking between turns.
        state.completion_nudges_used = 0
        # Some reasoning-capable OpenAI-compatible models can end a turn
        # after writing only hidden reasoning, without either user-visible
        # content or a structured tool call. Treat that as an incomplete
        # provider turn: give the model one focused repair attempt, then fail
        # structurally instead of silently completing with an empty answer.
        state.reasoning_only_nudges_used = 0
        # WI-T2.6: 同 completion_nudges_used 模式 — 本轮起算
        state.verify_nudges_used = 0
        # WI-2.2: track previous task_replanning for stagnation detection
        state._prev_task_replanning: str = ""
        state.tools_used_count = int(state.external_feedback.get("tools_used_count", 0))
        state._verify_final_done = False
        state._force_finish_queued = bool(state.external_feedback.get("force_finish_queued"))

        # P5-S2 B3: warn-once latch so we don't spam the WARN log every
        # iteration once we're in the 80-95% band.
        state._budget_warn_emitted = False

        # WI-4a 目标 always-on 单点注入(对标 Claude Code 钉死 CLAUDE.md)。
        # 唯一注入点 = 这里(循环前注一次),role=system → context_compressor._partition
        # 永久排除、永不被压;compress() 见到它就不再自注(去重),整轮恒 ≤1 条 [目标锚定]。
        # 取代了原"周期性 anchor(每 _GOAL_ANCHOR_EVERY 轮重注)"+"压缩后 _build_goal_anchor
        # 注入"两处冗余。目标 + 子目标都从 session_goal_store 取。
        if self.session_goal_store is not None:
            state._ga_fn = getattr(self.session_goal_store, "get_goal_text", None)
            state._always_goal = state._ga_fn(state.session_id) if callable(state._ga_fn) else None
            if state._always_goal:
                state._anchor_lines = [f"[目标锚定] 当前目标：{state._always_goal}"]
                state._pg_fn = getattr(self.session_goal_store, "get_pending_tasks", None)
                state._pending = state._pg_fn(state.session_id) if callable(state._pg_fn) else None
                if state._pending:
                    state._anchor_lines.append(f"[当前子目标] {state._pending[0]}")
                state._anchor_lines.append(
                    "请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏。"
                )
                _append_loop_control(
                    state.working_messages,
                    {"role": "system", "content": "\n".join(state._anchor_lines)},
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.goal_anchor",
                    anchor_after=_latest_context_anchor(
                        state.working_messages,
                        fallback=f"agent-loop:{state.tid}:run-start",
                    ),
                    fragment_id=f"agent-loop:{state.tid}:goal-anchor",
                    protected=True,
                    trim_policy="never",
                )
                logger.info("wi4a_goal_anchor_always_on sid=%s tid=%s", state.session_id, state.tid)

        # WI-4.0 compaction: warn-once latch so we don't spam the log every
        # iteration when the compressor fires (long multi-tool tasks may cross
        # the threshold repeatedly; we log the first fire and stay quiet after).
        state._compaction_warn_logged: bool = False

        # WI-4b pre-flush 限频 latch: 每个 run 最多把任务态 flush 进 L1 一次
        # (防长 agentic 任务反复触发压缩时刷爆 MEMORY.md 50KB cap → 驱逐真实记忆)。
        state._preflush_done: bool = False
        state._compaction_cycle_index = 0

        # WI-1B-3 自适应触发线: 本 run 累计工具调用数 ≥ 阈值 → 视为 "agentic"
        # (多工具长任务,提前压留 buffer);否则纯对话(延后压)。仅 adaptive_compact_pct
        # ON 时影响触发线,OFF 时只是个没人读的计数器(字节级 BC)。
        state._run_tool_calls: int = state.tools_used_count
        state._AGENTIC_TOOL_THRESHOLD = 3

        # FP-2 TC-2.1 第 3 刀: relay 真实 prompt_tokens 反馈回路。char-based
        # 估算对中文/markdown 系统性低估(真机 real 32.9k 时 estimate <24k),
        # 纯系数追不上内容分布 → 用上一轮 response.usage.input_tokens 兜底,
        # compaction 判定取 max(estimate, real)。0 = 本 run 还没有真实值。
        state._last_real_prompt_tokens: int = 0
        state._latest_compression_report: dict[str, Any] = {}

        self._skills_used_order = list(state.external_feedback.get("skills_used_order", ()))
        self._skills_used_this_run = set(self._skills_used_order)
        self._frozen_skill_remounts: dict[str, tuple[Any, str]] = {}
        await self._prepare_frozen_skill_remounts(
            state.working_messages,
            prepared_context=state.prepared_context,
            external_feedback=state.external_feedback,
        )
        state._skill_compaction_happened = False

    async def _run_initial_coverage(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """上下文规划带着覆盖压缩任务：先压缩、再按压缩结果重规划；覆盖不完整或预算不够就停（fail closed）。"""

        state.initial_coverage_jobs = tuple(
            getattr(state.prepared_context, "coverage_compaction_jobs", ()) or ()
        )
        if state.context_metadata_enabled and state.initial_coverage_jobs:
            try:
                if self.compressor is None:
                    raise RuntimeError("coverage_compaction_owner_unavailable")
                state._compaction_cycle_index += 1
                state.initial_cycle_id = f"{state.context_request_id or state.tid}:coverage:{state._compaction_cycle_index}"
                state.initial_resolution = self._resolve_compression_model(
                    state.provider_chain,
                    requested_model=self._requested_compression_model(),
                )
                await self._flush_context_snapshot(
                    cycle_id=state.initial_cycle_id,
                    session_id=state.session_id,
                    request_id=state.context_request_id or state.tid,
                    working_messages=state.working_messages,
                    prepared_context=state.prepared_context,
                    current_tool_set=state.current_tool_set,
                )
                await self._execute_coverage_compaction_jobs(
                    cycle_id=state.initial_cycle_id,
                    prepared_context=state.prepared_context,
                    resolution=state.initial_resolution,
                )
                state.replan = getattr(state.prepared_context, "replan_after_compaction", None)
                if not callable(state.replan):
                    raise RuntimeError("coverage_replan_unavailable")
                logger.info(
                    "coverage_replan_started sid=%s request_id=%s phase=initial",
                    state.session_id,
                    state.context_request_id or state.tid,
                )
                state.replanned = await asyncio.wait_for(state.replan(), timeout=10.0)
                logger.info(
                    "coverage_replan_completed sid=%s request_id=%s phase=initial",
                    state.session_id,
                    state.context_request_id or state.tid,
                )
                state.replanned_context = getattr(
                    state.replanned, "prepared_context", state.replanned
                )
                state.coverage = getattr(state.replanned_context, "coverage_report", None)
                if (
                    state.coverage is None
                    or not bool(getattr(state.coverage, "valid", False))
                    or tuple(getattr(state.coverage, "gaps", ()) or ())
                    or tuple(getattr(state.coverage, "overlaps", ()) or ())
                    or tuple(getattr(state.coverage, "stale_segment_ids", ()) or ())
                    or tuple(getattr(state.coverage, "broken_causal_groups", ()) or ())
                    or tuple(getattr(state.replanned_context, "coverage_compaction_jobs", ()) or ())
                ):
                    raise RuntimeError("session_history_coverage_invalid")
                state.request_budget = getattr(state.replanned_context, "request_budget", None)
                if state.request_budget is None or not bool(
                    getattr(state.request_budget, "fits", False)
                ):
                    raise RuntimeError("context_budget_exceeded_after_coverage")
                state.replanned_tool_set = getattr(state.replanned_context, "tool_set", None)
                if state.current_tool_set is not None and (
                    state.replanned_tool_set is None
                    or state.replanned_tool_set.scope_id != state.current_tool_set.scope_id
                    or state.replanned_tool_set.schema_fingerprint
                    != state.current_tool_set.schema_fingerprint
                ):
                    raise RuntimeError("coverage_replan_changed_tool_set")
                state.prepared_context = state.replanned_context
                state.working_messages = _tag_run_input_messages(
                    list(state.prepared_context.messages), task_id=state.tid
                )
                state.current_tool_set = state.replanned_tool_set
                if state.current_tool_set is not None:
                    state.tool_schemas = list(state.current_tool_set.logical_schemas())
                self._history_tool_names = set(self._collect_tool_names(state.working_messages))
            except Exception as coverage_exc:  # noqa: BLE001 - fail closed
                logger.warning(
                    "coverage_compaction_blocked sid=%s request_id=%s error=%s",
                    state.session_id,
                    state.context_request_id or state.tid,
                    str(coverage_exc),
                )
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=0,
                    reason="context_coverage_block",
                    detail=str(coverage_exc),
                )
                state.flow = "stop"
                return

    async def _finish_max_iterations(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """轮次用完：记警告；有收敛控制器时产出诚实止损报告，否则发 max_iterations 错误。"""

        # Hit max_iterations — spec §11.4 says emit warning + final.
        logger.warning("agent loop task %s hit max_iterations=%d", state.tid, self.max_iterations)
        # ─── Step7 止损（WI-2，plans/2026-06-24-...）：loop range 耗尽（每轮都 tool_use 跑满
        # max_iterations）也接 ConvergenceController，产出诚实止损报告，而非只发 error。
        # 此出口 gate 不一定 terminate()，summary()["reason"] 多半还是合成 "running" → 必须手动
        # 覆盖成 HARD_MAX_TURNS.value（"error_max_turns"），否则 resource_capped 判 False → 止损不触发。
        # pipeline off 时 self._convergence_controller=None → 保持原 warning+ErrorEvent（字节级 BC）。
        if self._convergence_controller is not None:
            state._cap_summary = dict(self._gate.summary())
            state._cap_summary["reason"] = TerminationReason.HARD_MAX_TURNS.value
            state._verdict = self._convergence_controller.evaluate(
                principal_resolved=False,  # 跑满 range 必是每轮都在调工具没收尾 → 未收敛
                unverified_claims=0,
                gate_summary=state._cap_summary,
            )
            if self._pipeline_observability:
                yield self._pipeline_event(
                    "chat_v2_convergence",
                    self.max_iterations,
                    {
                        "converged": state._verdict.converged,
                        "principal_resolved": state._verdict.principal_resolved,
                        "stop_reason": state._verdict.stop_reason,
                        "report": state._verdict.report,
                    },
                )
            if state._verdict.should_stop_loss and state._verdict.report:
                self._gate.record_final_answer()
                yield FinalEvent(
                    type="final",
                    task_id=state.tid,
                    iteration=self.max_iterations,
                    content=state._verdict.report,
                    stop_reason="stop_loss",
                )
                state.flow = "stop"
                return
        yield ErrorEvent(
            type="error",
            task_id=state.tid,
            iteration=self.max_iterations,
            reason="max_iterations",
            detail=f"exceeded {self.max_iterations} iterations without terminal stop_reason",
        )
