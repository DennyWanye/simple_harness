# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Start of each turn: iteration flags, termination gate, budget check, post-compaction recovery and periodic checks."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing

from agent.termination import TerminationReason
from agent.token_budget import BudgetCheck as _BudgetCheck
from agent.workflow_trace import traced_call
from deskpet.agent.reflection import _REFLECTION_INSTRUCTION
from deskpet.workflows.trace import SpanKind

from .core import AgentLoopCore
from .support import (
    _SELFCHECK_TIER3_AT,
    AgentEvent,
    ErrorEvent,
    FinalEvent,
    _append_loop_control,
    _latest_context_anchor,
    _RunState,
    logger,
)

# ───────────────────── P5-S2 Phase 7: in-loop self-check ─────────────
#
# Every ``_SELFCHECK_EVERY`` iterations, BEFORE the LLM call, we inject
# a system message. The message ESCALATES based on iteration count:
#
#   tier 1 (iter=10): gentle reflection — "consider committing to a finish"
#   tier 2 (iter=20): firm — "you MUST justify each remaining tool call"
#   tier 3 (iter=30+): forced — "your NEXT response MUST be stop_reason=end_turn"
#
# Empirical: tier 1 alone wasn't enough — the LLM sees "considered" and
# keeps going. Forced tier at 30 reliably breaks "infinite content
# generation" patterns where each iteration writes another section,
# fix, refactor, etc.
#
# P6 Phase 6: the legacy soft tool-budget injection (``_TOOL_BUDGET_HARD_MSG``
# + the >= _TOOL_BUDGET_HARD branch) was removed — the TerminationGate
# now enforces tool_budget_hard with a HARD break that emits
# ErrorEvent(error_tool_budget). Only the iteration-based selfcheck
# tiers remain as soft nudges.

_SELFCHECK_EVERY = 10  # base interval — tier 1 fires here
_SELFCHECK_TIER2_AT = 20  # iter >= this → escalate
_TODO_SYNC_EVERY = 8
_TODO_SYNC_MSG = "[当前任务进度]\n{body}\n请优先完成未完成项，勿遗漏。"

_SELFCHECK_TIER1 = (
    "[self-check 第 1 级 / 第 {iter}/{max_iter} 轮 — 已用 {tools_used} 次工具调用]\n"
    "你已经跑了 {iter} 轮 ReAct 循环。在下一轮回复中先反思 3 行：\n"
    "1. 用户原问题的核心需求是什么？\n"
    "2. 你已经完成了哪些核心成果（具体改了什么文件、跑了什么命令、产出了什么结果）？\n"
    "3. 剩余工作是否真的必须？若不必须立即 stop_reason=end_turn 把可改进点写进 todo_write。\n"
    "记住：用户期望答完原问题，不是追求完美。剩 {budget} 轮预算。"
)

_SELFCHECK_TIER2 = (
    "[self-check 第 2 级 — 你已经跑了 {iter} 轮，使用了 {tools_used} 次工具调用]\n"
    "**警告**：你已经超过了正常任务的迭代量。不再允许『探索式』的工具调用。\n"
    "下一轮你**必须**做以下其中之一：\n"
    "  (A) 立即 stop_reason=end_turn，给用户一段总结：你做完了什么、剩余什么留给下次。\n"
    "  (B) 如果**真的**必须继续，先用一句话写清楚『为什么再来一轮就能收尾』，"
    "然后这一轮必须是**最后一轮**——只调一个工具，下下轮一定 end_turn。\n"
    "如果你又想『再写一节/再修一处/再扫一遍』——选 (A)，把它写进 todo 留给下次。"
)

_SELFCHECK_TIER3 = (
    "[STRICT STOP — 第 {iter}/{max_iter} 轮，已用 {tools_used} 次工具调用]\n"
    "你已经达到迭代预算的临界点。**禁止任何新的 tool_call**。\n"
    "你的下一轮回复**必须**：\n"
    "  1. 不包含任何 tool_calls 字段（即 stop_reason=end_turn）\n"
    "  2. 用 markdown 段落直接给用户写：\n"
    "     - 已完成的工作清单（带文件路径、改动概要）\n"
    "     - 已知未完成 / 待改进事项（用户可以下次让我做）\n"
    "  3. 用 todo_write 之前已经做过的，不要重复写\n"
    "如果你违反这条规则继续 tool_call，下一轮 supervisor 会强制中断你。"
)


def _build_selfcheck_message(iteration: int, max_iter: int, tools_used: int) -> str:
    """Choose the right self-check tier based on iteration count.

    Tier escalation handles the "LLM ignores gentle reminder" problem:
    we get progressively harsher until the LLM has no choice but to
    stop_reason=end_turn.
    """
    budget = max(0, max_iter - iteration)
    fmt = {
        "iter": iteration,
        "max_iter": max_iter,
        "budget": budget,
        "tools_used": tools_used,
    }
    if iteration >= _SELFCHECK_TIER3_AT:
        return _SELFCHECK_TIER3.format(**fmt)
    if iteration >= _SELFCHECK_TIER2_AT:
        return _SELFCHECK_TIER2.format(**fmt)
    return _SELFCHECK_TIER1.format(**fmt)


class TurnPrepMixin(AgentLoopCore):
    """Start of each turn: iteration flags, termination gate, budget check, post-compaction recovery and periodic checks."""

    __slots__ = ()

    async def _prepare_turn(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """本轮准备：开头标记、终止闸门、预算检查、上下文压缩与压缩后补救。"""

        state.flow = None
        async with aclosing(self._prepare_iteration(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            state.flow = "stop"
            return
        state.flow = None
        async with aclosing(self._termination_gate(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            state.flow = "stop"
            return

        state.flow = None
        async with aclosing(self._check_budget(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            state.flow = "stop"
            return

        async with aclosing(self._compact_context(state)) as _phase:
            async for _event in _phase:
                yield _event

        state.flow = None
        async with aclosing(self._after_compaction(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            state.flow = "stop"
            return

    async def _prepare_iteration(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """本轮开头：工具集、强制收尾标记与追踪。"""

        if state.current_tool_set is not None:
            state._scope_store = getattr(self.tools, "capability_scope_store", None)
            if state._scope_store is None or not hasattr(self.tools, "validate_prepared_tool_set"):
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=state.iteration,
                    reason="tool_capability_runtime_unavailable",
                    detail="Context OS requires a capability-aware registry",
                )
                state.flow = "stop"
                return
            state._record = state._scope_store.get(
                state.current_tool_set.scope_id,
                session_id=state.session_id,
                request_id=state.context_request_id or state.tid,
            )
            if state._record is None:
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=state.iteration,
                    reason="tool_capability_scope_expired",
                    detail="Context OS capability scope is missing or expired",
                )
                state.flow = "stop"
                return
            try:
                self.tools.validate_prepared_tool_set(
                    state.current_tool_set,
                    eligibility=state._record.eligibility,
                )
            except Exception as _stale_exc:  # noqa: BLE001
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=state.iteration,
                    reason=(
                        "tool_policy_unavailable"
                        if "tool_policy_unavailable" in str(_stale_exc)
                        else "tool_catalog_stale"
                    ),
                    detail=str(_stale_exc),
                )
                state.flow = "stop"
                return
        state._force_finish_next = False
        if state._force_finish_queued:
            state._force_finish_next = True
            state._force_finish_queued = False
        # Context OS requests reserve the last loop iteration for an
        # honest, tool-free final response once at least one tool has
        # already run.  Without this reservation, a tool-using model can
        # consume the final iteration with another tool call and the loop
        # exits through max_iterations without ever getting a chance to
        # summarize the completed work.  Keep the legacy path byte-for-byte
        # compatible: callers that do not provide PreparedContext retain
        # the historical max_iterations error/stop-loss behavior.
        if (
            not state._force_finish_next
            and state.prepared_context is not None
            and self.force_finish_via_tool_choice
            and state.tools_used_count > 0
            and state.iteration == self.max_iterations
        ):
            state._force_finish_next = True
            logger.info(
                "context_os_final_iteration_force_finish sid=%s tid=%s iter=%d tools_used=%d",
                state.session_id,
                state.tid,
                state.iteration,
                state.tools_used_count,
            )
        if self._tracer:
            self._tracer.record(
                {
                    "kind": "iter_start",
                    "iter": state.iteration,
                    "msg_count": len(state.working_messages),
                }
            )

    async def _termination_gate(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """终止闸门：不放行就收尾。"""

        # P6 Phase 6 — TerminationGate.allows_call() is always run.
        # Checks hard limits (turns, wall-clock, cost) BEFORE we burn
        # another LLM call. The gate is the single source of truth
        # for "should this loop keep going?".
        state._ok, state._reason = await traced_call(
            name="termination_gate.allows_call",
            kind=SpanKind.GATE,
            lifecycle_stage="gate",
            attributes={"iteration": state.iteration, "gate": "termination"},
            invoke=self._gate.allows_call,
        )
        if not state._ok:
            # ─── Step7 止损（闸③，plans/2026-06-24-...）：硬上限/资源触顶 → 不硬撑，
            # 产出诚实止损报告（桌宠交代"卡在哪+建议"，而非只发 error）。
            # pipeline off 时 self._convergence_controller=None → 走原 ErrorEvent return（字节级 BC）。
            if self._convergence_controller is not None:
                # ⚠️ allows_call() 返回 (False, reason) 但**不调 terminate()**（已核实 termination.py:142）
                # → 此刻 gate.summary()["reason"] 仍是合成 "running"。真实触顶 reason 在 _reason 里，
                # 必须用它覆盖 summary 的 reason，否则 resource_capped 判 False → 止损永不触发。
                state._cap_summary = dict(self._gate.summary())
                if state._reason is not None:
                    state._cap_summary["reason"] = state._reason.value
                state._verdict = self._convergence_controller.evaluate(
                    principal_resolved=False,  # 触顶即未收敛
                    unverified_claims=0,
                    gate_summary=state._cap_summary,
                )
                if self._pipeline_observability:
                    yield self._pipeline_event(
                        "chat_v2_convergence",
                        state.iteration,
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
                        iteration=state.iteration,
                        content=state._verdict.report,
                        stop_reason="stop_loss",
                    )
                    state.flow = "stop"
                    return
            yield ErrorEvent(
                type="error",
                task_id=state.tid,
                iteration=state.iteration,
                reason=(state._reason.value if state._reason is not None else "unknown"),
                detail="Termination gate blocked LLM call",
            )
            state.flow = "stop"
            return

    async def _check_budget(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """预算补救标记与预算检查。"""

        # P5-S2 B3: token budget guard. Runs BEFORE the LLM call so
        # we can surface an actionable error (BLOCK) or pre-emptive
        # warning (WARN) instead of waiting for the model to choke
        # on context_length_exceeded.
        #
        # P6 Phase 6 — always delegate to self._ctx.check_budget so
        # chat handler + AgentLoop share one budget evaluator.
        state._budget_block_pending = state._chain_budget_rescue_pending
        state._chain_budget_rescue_pending = False
        try:
            # Use the FIRST provider's model as the context-window
            # reference (chain mode). In single-provider mode fall
            # back to default_model.
            state._budget_model = state.use_model
            if state.chain_mode and state.provider_chain:
                state._first = state.provider_chain[0]
                state._budget_model = getattr(state._first, "model", None) or state.use_model
            state._resolved_model = state._budget_model or "unknown"
            # Risk-2 fix: floor the estimate with the last real prompt
            # size (response.usage.input_tokens) so the gate accounts for
            # the fixed system/tool-schema base that estimate_tokens(
            # working_messages) misses — otherwise a genuinely over-window
            # prompt (real 113%) reads as ~16% and never BLOCKs. Default
            # floor 0 on iter-0 → BC. Mirrors the compaction trigger.
            state._budget = self._ctx.check_budget(
                state.working_messages,
                model=state._resolved_model,
                real_prompt_tokens_floor=state._last_real_prompt_tokens,
            )
            if state._budget.verdict is _BudgetCheck.BLOCK:
                logger.error(
                    "p5s2_token_budget_block sid=%s tid=%s iter=%d tokens=%d window=%d ratio=%.2f",
                    state.session_id,
                    state.tid,
                    state.iteration,
                    state._budget.estimated_tokens,
                    state._budget.context_window,
                    state._budget.ratio,
                )
                if self.compressor is not None and (
                    state.context_metadata_enabled or state._budget_block_pending
                ):
                    # Context OS owns the only lossy recovery path.  Defer
                    # the terminal BLOCK until snapshot flush + compact +
                    # re-estimate have had one chance to recover.
                    state._budget_block_pending = True
                else:
                    # Legacy/OFF behavior remains byte-for-byte identical.
                    self._gate.record_error(TerminationReason.CONTEXT_BUDGET_BLOCK)
                    yield ErrorEvent(
                        type="error",
                        task_id=state.tid,
                        iteration=state.iteration,
                        reason="context_budget_block",
                        detail=state._budget.advice,
                    )
                    state.flow = "stop"
                    return
            elif state._budget.verdict is _BudgetCheck.WARN and not state._budget_warn_emitted:
                logger.warning(
                    "p5s2_token_budget_warn sid=%s tid=%s iter=%d tokens=%d window=%d ratio=%.2f",
                    state.session_id,
                    state.tid,
                    state.iteration,
                    state._budget.estimated_tokens,
                    state._budget.context_window,
                    state._budget.ratio,
                )
                state._budget_warn_emitted = True
        except Exception as _b_exc:  # noqa: BLE001
            # Budget check is advisory — never fail the loop on it.
            logger.debug("token_budget_check_failed err=%s", str(_b_exc)[:100], exc_info=True)

        # Budget gate BEFORE the call — can't take back tokens after the fact.
        if self.budget_checker is not None and not self.budget_checker.check_allowed():
            yield ErrorEvent(
                type="error",
                task_id=state.tid,
                iteration=state.iteration,
                reason="budget_exceeded",
                detail=f"daily budget cap reached (${self.budget_checker.cap_usd:.2f})",
            )
            state.flow = "stop"
            return

    async def _after_compaction(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """预算拦截后的补救与压缩后重新挂载已用技能。"""

        if state._budget_block_pending:
            try:
                state._post_budget = self._ctx.check_budget(
                    state.working_messages,
                    model=state._resolved_model,
                    real_prompt_tokens_floor=0,
                )
                state._still_blocked = state._post_budget.verdict is _BudgetCheck.BLOCK
                state._block_detail = state._post_budget.advice
            except Exception as _post_exc:  # noqa: BLE001
                state._still_blocked = True
                state._block_detail = f"context re-budget failed: {_post_exc}"
            if state._still_blocked:
                self._gate.record_error(TerminationReason.CONTEXT_BUDGET_BLOCK)
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=state.iteration,
                    reason="context_budget_block",
                    detail=state._block_detail,
                )
                state.flow = "stop"
                return

        if state._skill_compaction_happened and self._skills_used_this_run:
            state.working_messages = self._remount_skills(
                state.working_messages,
                state.session_id,
                prepared_context=state.prepared_context,
            )

    async def _periodic_checks(self, state: _RunState) -> None:
        """定期自检提示与会话待办同步。"""

        if state.iteration > 0 and state.iteration % _SELFCHECK_EVERY == 0:
            state.budget_left = self.max_iterations - state.iteration
            state.msg = _build_selfcheck_message(
                state.iteration, self.max_iterations, state.tools_used
            )
            # WI-2.1: append reflection instruction at tier2/tier3 only
            # (zero overhead on normal tier1 path; flag off = BC).
            if self.structured_reflection and state.iteration >= _SELFCHECK_TIER2_AT:
                state.msg = state.msg + _REFLECTION_INSTRUCTION
            _append_loop_control(
                state.working_messages,
                {"role": "system", "content": state.msg},
                metadata_enabled=state.context_metadata_enabled,
                source="agent_loop.self_check",
                anchor_after=_latest_context_anchor(
                    state.working_messages,
                    fallback=f"agent-loop:{state.tid}:iteration:{state.iteration}:start",
                ),
                fragment_id=(f"agent-loop:{state.tid}:iteration:{state.iteration}:self-check"),
            )
            state.tier = (
                3
                if state.iteration >= _SELFCHECK_TIER3_AT
                else 2
                if state.iteration >= _SELFCHECK_TIER2_AT
                else 1
            )
            logger.info(
                "p5s2_selfcheck_injected sid=%s tid=%s iter=%d budget=%d "
                "tier=%d tools_used=%d structured_reflection=%s",
                state.session_id,
                state.tid,
                state.iteration,
                state.budget_left,
                state.tier,
                state.tools_used,
                self.structured_reflection,
            )
            if self.force_finish_via_tool_choice and state.iteration >= _SELFCHECK_TIER3_AT:
                state._force_finish_next = True

        if (
            self.session_todo_getter is not None
            and state.iteration % _TODO_SYNC_EVERY == 0
            and state.iteration < _SELFCHECK_TIER3_AT
        ):
            try:
                state.todos = await self.session_todo_getter(state.session_id)
            except Exception:
                state.todos = []
            if state.todos:
                state.lines = []
                for state.t in state.todos:
                    state.content = (state.t.get("content") or "")[:80]
                    state.status = (state.t.get("status") or "").lower()
                    state.mark = {"completed": "✓", "in_progress": "🔄"}.get(state.status, "⏳")
                    state.lines.append(f"  {state.mark} {state.content}")
                _append_loop_control(
                    state.working_messages,
                    {
                        "role": "system",
                        "content": _TODO_SYNC_MSG.format(body="\n".join(state.lines)),
                    },
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.todo_sync",
                    anchor_after=_latest_context_anchor(
                        state.working_messages,
                        fallback=(f"agent-loop:{state.tid}:iteration:{state.iteration}:start"),
                    ),
                    fragment_id=(f"agent-loop:{state.tid}:iteration:{state.iteration}:todo-sync"),
                )
                logger.info(
                    "wi4_todo_sync sid=%s iter=%d n=%d",
                    state.session_id,
                    state.iteration,
                    len(state.todos),
                )
