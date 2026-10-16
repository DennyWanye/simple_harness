# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Gates a final (no tool call) reply passes: evidence, completion probe, session goal and external evaluation."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

from agent.workflow_trace import traced_call
from deskpet.workflows.trace import SpanKind
from llm.types import ChatResponse

from .core import AgentLoopCore
from .support import (
    _SELFCHECK_TIER3_AT,
    AgentEvent,
    ErrorEvent,
    FinalEvent,
    _append_loop_control,
    _append_loop_transcript,
    _context_fragment_id,
    _latest_context_anchor,
    _RunState,
    logger,
)

# ───────────────────── agent loop ─────────────────────


def _host_validated_terminal_event(
    loop: Any,
    response: ChatResponse,
    task_id: str,
    iteration: int,
    totals: dict[str, int],
) -> FinalEvent:
    """Finish a trusted zero-tool background Run after its host validation."""
    loop._gate.record_final_answer()
    if loop._tracer:
        loop._tracer.record(
            {
                "kind": "end",
                "iter": iteration,
                "reason": response.stop_reason or "end_turn",
                "gate_summary": loop._gate.summary(),
                "host_validated_zero_tool_terminal": True,
            }
        )
    return FinalEvent(
        type="final",
        task_id=task_id,
        iteration=iteration,
        content=response.content,
        reasoning_content=response.reasoning_content,
        stop_reason=response.stop_reason or "end_turn",
        total_input_tokens=totals["input"],
        total_output_tokens=totals["output"],
        total_cache_read_tokens=totals["cache_read"],
        total_cache_write_tokens=totals["cache_write"],
    )


async def _review_final_response_quality(
    loop: Any,
    response: ChatResponse,
    *,
    user_request: str | None,
    session_id: str,
    request_id: str,
    iteration: int,
    model: str,
    prepared_context: Any,
    current_tool_set: Any,
    totals: dict[str, int],
) -> str:
    """Apply the optional semantic concise-completeness gate outside AgentLoop."""
    final_content = response.content or ""
    gate = loop._response_quality_gate
    if gate is None or not user_request or not final_content:
        return final_content
    try:
        quality_messages = gate.build_messages(
            user_request=user_request,
            candidate_response=final_content,
        )
        quality_provider = getattr(
            loop.llm,
            "_provider",
            getattr(loop.llm, "_active_provider", loop.llm),
        )
        quality_response = await loop._run_context_attempt(
            provider=quality_provider,
            session_id=session_id,
            request_id=request_id,
            attempt_id=f"{request_id}:iter:{iteration}:response-quality",
            purpose="response_quality",
            messages=quality_messages,
            tools=None,
            fallback_model=model,
            prepared_context=prepared_context,
            current_tool_set=current_tool_set,
            invoke=lambda: traced_call(
                name="response_quality.check",
                kind=SpanKind.LLM,
                lifecycle_stage="llm",
                attributes={
                    "iteration": iteration,
                    "gate": "response_quality",
                    "model": model,
                },
                invoke=lambda: loop.llm.chat_with_fallback(
                    quality_messages,
                    tools=None,
                    model=model,
                    max_tokens=1024,
                    temperature=0.0,
                    response_format=gate.response_format(),
                ),
            ),
        )
        usage = getattr(quality_response, "usage", None)
        if usage is not None:
            totals["input"] += int(getattr(usage, "input_tokens", 0) or 0)
            totals["output"] += int(getattr(usage, "output_tokens", 0) or 0)
            totals["cache_read"] += int(getattr(usage, "cache_read_tokens", 0) or 0)
            totals["cache_write"] += int(getattr(usage, "cache_write_tokens", 0) or 0)
        decision = gate.resolve(
            original_response=final_content,
            review_response=str(getattr(quality_response, "content", "") or ""),
        )
        logger.info(
            "response_quality_checked sid=%s checked=%s revised=%s missing=%d reason=%s",
            session_id,
            decision.checked,
            decision.revised,
            len(decision.missing_items),
            decision.reason,
        )
        return decision.text
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "response_quality_check_failed sid=%s err=%s — preserving original response",
            session_id,
            str(exc)[:200],
            exc_info=True,
        )
        return final_content


class FinalGateMixin(AgentLoopCore):
    """Gates a final (no tool call) reply passes: evidence, completion probe, session goal and external evaluation."""

    __slots__ = ()

    async def _handle_final_response(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """模型没有发起工具调用（或停止原因不是工具调用）：逐个过收尾闸门，决定收尾还是再来一轮。"""

        # End of conversation — emit final and stop.
        if state.response.stop_reason != "tool_use" or not state.response.tool_calls:
            if state.host_validated_zero_tool_terminal:
                yield _host_validated_terminal_event(
                    self, state.response, state.tid, state.iteration, state.totals
                )
                state.flow = "stop"
                return
            # WI-1 §17.6 — verify_exhausted final-summary turn: when this
            # is the single forced "summarize then stop" turn granted after
            # verify-gate exhaustion (_verify_final_done set + force_finish
            # consumed this iteration), the model has now produced its
            # plain-text summary. Surface it as the terminal
            # ErrorEvent(verify_exhausted) (summary carried in detail) so the
            # failure is still reported while the user gets the summary.
            # Without this, the verify block's own entry guard
            # (verify_nudges_used < max) skips re-entry on this turn and the
            # run would silently end as a FinalEvent, hiding the failure.
            if state._verify_final_done and state._force_finish_next:
                if self._tracer:
                    self._tracer.record(
                        {
                            "kind": "end",
                            "iter": state.iteration,
                            "reason": "verify_exhausted",
                            "gate_summary": self._gate.summary(),
                        }
                    )
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=state.iteration,
                    reason="verify_exhausted",
                    detail=(state.response.content or "verify-gate: all retries exhausted"),
                )
                state.flow = "stop"
                return
            state.flow = None
            async with aclosing(self._evidence_gate_step(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow is not None:
                return

            state.flow = None
            await self._completion_probe_gate(state)
            if state.flow is not None:
                return

            state.flow = None
            async with aclosing(self._self_check_gate_step(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow is not None:
                return

            state.flow = None
            await self._session_goal_gate(state)
            if state.flow is not None:
                return

            state.flow = None
            async with aclosing(self._external_evaluation_gate(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow is not None:
                return
            state._final_content = await _review_final_response_quality(
                self,
                state.response,
                user_request=state.loop_user_request,
                session_id=state.session_id,
                request_id=state.context_request_id or state.tid,
                iteration=state.iteration,
                model=state.use_model or "",
                prepared_context=state.prepared_context,
                current_tool_set=state.current_tool_set,
                totals=state.totals,
            )
            # ─── Step7 收敛标记（自然收尾路径，仅观测；止损在循环顶部 2d-① 处理）。
            # plans/2026-06-24-...：模型自己 end_turn 正常收尾时 gate 尚未 terminate
            # （reason="running"），此处只发 converged 观测事件，**不触发止损**（止损只在触顶分支）。
            if self._convergence_controller is not None and self._pipeline_observability:
                state._v = self._convergence_controller.evaluate(
                    principal_resolved=True,
                    unverified_claims=0,
                    gate_summary=self._gate.summary(),
                )
                yield self._pipeline_event(
                    "chat_v2_convergence",
                    state.iteration,
                    {
                        "converged": state._v.converged,
                        "principal_resolved": state._v.principal_resolved,
                        "stop_reason": state._v.stop_reason,
                        "report": "",
                    },
                )

            # P6 Phase 6 — gate records the natural terminal state
            # before we emit the FinalEvent so consumers reading
            # gate.summary() after a run see SUCCESS / matching reason.
            self._gate.record_final_answer()
            if self._tracer:
                self._tracer.record(
                    {
                        "kind": "end",
                        "iter": state.iteration,
                        "reason": state.response.stop_reason or "end_turn",
                        "gate_summary": self._gate.summary(),
                    }
                )

            # WI-OH-4: 记忆 self-curation nudge — 在 FinalEvent 之后周期性
            # fire-and-forget 触发（curator None → BC，跳过整段；非 None 时
            # 每 _curation_every 回合调一次，异步不挡主回合）。
            self._maybe_fire_curation_nudge(state.session_id, state.working_messages)

            yield FinalEvent(
                type="final",
                task_id=state.tid,
                iteration=state.iteration,
                content=state._final_content,
                reasoning_content=state.response.reasoning_content,
                stop_reason=state.response.stop_reason or "end_turn",
                total_input_tokens=state.totals["input"],
                total_output_tokens=state.totals["output"],
                total_cache_read_tokens=state.totals["cache_read"],
                total_cache_write_tokens=state.totals["cache_write"],
            )
            state.flow = "stop"
            return

    async def _evidence_gate_step(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """取证闸门：需要调查却没取证就拦下。"""

        # ─── Step2 EvidenceGate（取证门控，核心闸①，plans/2026-06-24-...）。
        # needs_investigation 且本 run 尚未取证就想 end_turn → 拦截注入 <调查> nudge。
        # flag off 时 self._evidence_gate=None → 跳过整段（BC）。判定走 self._evidence_gathered
        # 布尔（dispatch 时按白名单+history 快照置位），对 compaction 整体替换 working_messages 免疫（R1）。
        if (
            state.iteration < _SELFCHECK_TIER3_AT
            and self._evidence_gate is not None
            and self._pipeline_needs_investigation
        ):
            state._ev_dec = await traced_call(
                name="evidence_gate.check",
                kind=SpanKind.GATE,
                lifecycle_stage="gate",
                attributes={"iteration": state.iteration, "gate": "evidence"},
                invoke=lambda: self._evidence_gate.check(
                    needs_investigation=self._pipeline_needs_investigation,
                    evidence_gathered=self._evidence_gathered,
                    nudges_used=self._evidence_nudges_used,
                ),
            )
            if self._pipeline_observability:
                yield self._pipeline_event(
                    "chat_v2_evidence_gate",
                    state.iteration,
                    {
                        "blocked": state._ev_dec.blocked,
                        "reason": state._ev_dec.reason,
                        "nudge_count": state._ev_dec.nudge_count,
                    },
                )
            if state._ev_dec.blocked:
                self._evidence_nudges_used = state._ev_dec.nudge_count
                state._evidence_anchor = _latest_context_anchor(
                    state.working_messages,
                    fallback=f"agent-loop:{state.tid}:iteration:{state.iteration}:response",
                )
                if state.response.content:
                    state._evidence_reply = _append_loop_transcript(
                        state.working_messages,
                        {"role": "assistant", "content": state.response.content},
                        metadata_enabled=state.context_metadata_enabled,
                        source="agent_loop.evidence_gate.trigger",
                        role="assistant",
                        fragment_id=(
                            f"agent-loop:{state.tid}:iteration:{state.iteration}:evidence-trigger"
                        ),
                    )
                    state._evidence_anchor = (
                        _context_fragment_id(state._evidence_reply) or state._evidence_anchor
                    )
                _append_loop_control(
                    state.working_messages,
                    {"role": "system", "content": state._ev_dec.nudge},
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.evidence_nudge",
                    anchor_after=state._evidence_anchor,
                    fragment_id=(
                        f"agent-loop:{state.tid}:iteration:{state.iteration}:"
                        f"evidence-nudge:{state._ev_dec.nudge_count}"
                    ),
                )
                logger.info(
                    "evidence_gate_nudge_injected sid=%s nudge=%d",
                    state.session_id,
                    state._ev_dec.nudge_count,
                )
                state.flow = "continue"
                return

    async def _completion_probe_gate(self, state: _RunState) -> None:
        """待办完成度探测闸门：还有没做完的待办就补提示。"""

        # P5-S2 Hook A: completion guard. Before truly finalizing,
        # ask the caller (via ``completion_probe``) whether session-
        # level work (todos) is actually finished. If the LLM said
        # "I'm done" but the SessionDB still has incomplete todos,
        # rebound with a system message reminding it to either
        # finish them or explicitly mark them cancelled. Capped at
        # ``max_completion_nudges`` so we can't loop forever when
        # the LLM digs in.
        if (
            state.iteration < _SELFCHECK_TIER3_AT
            and self.completion_probe is not None
            and self.max_completion_nudges > 0
            and state.completion_nudges_used < self.max_completion_nudges
        ):
            try:
                state.incomplete = await traced_call(
                    name="completion_probe.check",
                    kind=SpanKind.GATE,
                    lifecycle_stage="gate",
                    attributes={"iteration": state.iteration, "gate": "completion"},
                    invoke=lambda: self.completion_probe(state.session_id),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "p5s2_completion_probe_failed sid=%s err=%s",
                    state.session_id,
                    str(exc)[:200],
                    exc_info=True,
                )
                state.incomplete = []
            if state.incomplete:
                if self._tracer:
                    self._tracer.record(
                        {
                            "kind": "gate",
                            "iter": state.iteration,
                            "which": "completion",
                            "passed": False,
                            "reason": "incomplete_todos",
                        }
                    )
                state.completion_nudges_used += 1
                # Build the rebound system message. Keep it brief
                # — long prompts crowd the context window.
                state.bullet_list = "\n".join(
                    f"  - {(t.get('content') or '').strip()[:120]}" for t in state.incomplete[:8]
                )
                state.rebound = (
                    f"你声明已完成（stop_reason={state.response.stop_reason or 'end_turn'}），"
                    f"但 todos 里还有 {len(state.incomplete)} 项未完成：\n"
                    f"{state.bullet_list}\n\n"
                    "请继续执行剩余 todos。如果某项确实做不了 / 不该做，"
                    "请用 todo_write 把它标成 completed 并简述原因（或者改文案明确说明放弃理由）。"
                    "不要再次空口说『我做完了』。"
                )
                # Append the assistant message that triggered this
                # so the LLM sees its own prior end_turn output —
                # otherwise the rebound system message has no
                # context for "what did I just stop on".
                state._completion_anchor = _latest_context_anchor(
                    state.working_messages,
                    fallback=f"agent-loop:{state.tid}:iteration:{state.iteration}:response",
                )
                if state.response.content:
                    state._completion_reply = _append_loop_transcript(
                        state.working_messages,
                        {
                            "role": "assistant",
                            "content": state.response.content,
                        },
                        metadata_enabled=state.context_metadata_enabled,
                        source="agent_loop.completion_probe.trigger",
                        role="assistant",
                        fragment_id=(
                            f"agent-loop:{state.tid}:iteration:{state.iteration}:completion-trigger"
                        ),
                    )
                    state._completion_anchor = (
                        _context_fragment_id(state._completion_reply) or state._completion_anchor
                    )
                _append_loop_control(
                    state.working_messages,
                    {"role": "system", "content": state.rebound},
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.completion_nudge",
                    anchor_after=state._completion_anchor,
                    fragment_id=(
                        f"agent-loop:{state.tid}:iteration:{state.iteration}:"
                        f"completion-nudge:{state.completion_nudges_used}"
                    ),
                )
                logger.info(
                    "p5s2_completion_nudge_injected sid=%s nudge=%d/%d incomplete=%d",
                    state.session_id,
                    state.completion_nudges_used,
                    self.max_completion_nudges,
                    len(state.incomplete),
                )
                # Skip the final emission and re-iterate. The
                # next LLM call sees the nudge.
                state.flow = "continue"
                return
            if self._tracer:
                self._tracer.record(
                    {
                        "kind": "gate",
                        "iter": state.iteration,
                        "which": "completion",
                        "passed": True,
                        "reason": "",
                    }
                )

    async def _session_goal_gate(self, state: _RunState) -> None:
        """会话目标闸门：目标没完成就补提示再来一轮。"""

        # WI-B3 Companion+Code v1 — /goal end_turn rebound. Same
        # safe-fail pattern as completion_probe / verify_gate:
        # checker exception or done=False → inject a system
        # message and ``continue`` the loop so the next LLM call
        # sees the hint. Cap at ``goal.max_iterations`` so the
        # checker can't loop forever. flag-off (store / checker
        # = None, or no active goal, or goal.done already) →
        # skip the block entirely (BC).
        if (
            state.iteration < _SELFCHECK_TIER3_AT
            and self.session_goal_store is not None
            and self.goal_checker is not None
        ):
            state._goal = self.session_goal_store.get(state.session_id)
            if (
                state._goal is not None
                and not state._goal.done
                and state._goal.iterations_used < state._goal.max_iterations
            ):
                try:
                    state._done, state._hint = await traced_call(
                        name="goal_checker.check",
                        kind=SpanKind.GATE,
                        lifecycle_stage="gate",
                        attributes={"iteration": state.iteration, "gate": "goal"},
                        invoke=lambda: self.goal_checker.check(
                            state._goal.text,
                            state.working_messages,
                            **(
                                {
                                    "workload_context": self._provider_workload_context_factory(
                                        "agent.goal_check"
                                    )
                                }
                                if self._provider_workload_context_factory is not None
                                else {}
                            ),
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 — safe-fail
                    logger.warning(
                        "goal_checker raised (sid=%s): %s — skipping check",
                        state.session_id,
                        exc,
                    )
                    # R-T3 §15.4: 兜底也用 skipped 语义（check() 内已 safe-fail，
                    # 这里只作双重防护）。不默认 done=True。
                    state._done, state._hint = False, "goal_check=skipped"
                # metric emit (best-effort, not blocking)
                try:
                    from observability.metrics_sink import (  # noqa: PLC0415
                        record as _goal_metric,
                    )

                    _goal_metric(
                        "goal_checker_invoked",
                        {
                            "ok": bool(state._done),
                            "count": int(state._goal.iterations_used),
                        },
                    )
                except Exception:  # noqa: BLE001 - 指标记录失败不影响把关
                    logger.debug("goal_checker_metric_failed", exc_info=True)
                state.flow = None
                await self._apply_goal_check(state)
                if state.flow is not None:
                    return

    async def _apply_goal_check(self, state: _RunState) -> None:
        """按目标核对结果补提示或放行。"""

        # R-T3 §15.4: goal_check=skipped → checker 降级，
        # 无法正向确认 → 不 mark_done，但也不注入"未达成"nudge
        # （我们不知道目标是否真达成，不能给 LLM 假信号）。
        # 直接 fall-through 到 FinalEvent / evaluator gate。
        if state._hint == "goal_check=skipped":
            logger.info(
                "goal_checker.skipped sid=%s — "
                "checker degraded, proceeding without goal confirmation",
                state.session_id,
            )
            # 不 continue，让正常 FinalEvent 流程继续
        elif not state._done:
            if self._tracer:
                self._tracer.record(
                    {
                        "kind": "gate",
                        "iter": state.iteration,
                        "which": "goal",
                        "passed": False,
                        "reason": str(state._hint or "")[:200],
                    }
                )
            self.session_goal_store.increment_iteration(state.session_id)
            # T1：落库 iterations_used，重启不归零。safe-fail
            # 内置于 persist_iteration；getattr 兜底旧 store。
            state._pit = getattr(
                self.session_goal_store,
                "persist_iteration",
                None,
            )
            if state._pit is not None:
                await state._pit(state.session_id)
            # Append the assistant message that triggered
            # this so the LLM sees its own prior end_turn
            # output — symmetric with completion_probe /
            # verify_gate rebound shape.
            state._goal_anchor = _latest_context_anchor(
                state.working_messages,
                fallback=(f"agent-loop:{state.tid}:iteration:{state.iteration}:response"),
            )
            if state.response.content:
                state._goal_reply = _append_loop_transcript(
                    state.working_messages,
                    {
                        "role": "assistant",
                        "content": state.response.content,
                    },
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.goal_checker.trigger",
                    role="assistant",
                    fragment_id=(
                        f"agent-loop:{state.tid}:iteration:{state.iteration}:goal-trigger"
                    ),
                )
                state._goal_anchor = _context_fragment_id(state._goal_reply) or state._goal_anchor
            _append_loop_control(
                state.working_messages,
                {
                    "role": "system",
                    "content": (
                        f"[goal] 未达成 "
                        f"({state._goal.iterations_used}/{state._goal.max_iterations}): "
                        f"{state._hint}\n继续工作直到目标完成。"
                    ),
                },
                metadata_enabled=state.context_metadata_enabled,
                source="agent_loop.goal_nudge",
                anchor_after=state._goal_anchor,
                fragment_id=(
                    f"agent-loop:{state.tid}:iteration:{state.iteration}:"
                    f"goal-nudge:{state._goal.iterations_used}"
                ),
            )
            logger.info(
                "goal_checker_nudge_injected sid=%s iter=%d/%d",
                state.session_id,
                state._goal.iterations_used,
                state._goal.max_iterations,
            )
            state.flow = "continue"
            return
        else:
            if self._tracer:
                self._tracer.record(
                    {
                        "kind": "gate",
                        "iter": state.iteration,
                        "which": "goal",
                        "passed": True,
                        "reason": "",
                    }
                )
            self.session_goal_store.mark_done(state.session_id)
            # 落库 done 终态（与 increment 落库对称）：否则
            # load_persisted 只召回 status='active'，已完成目标
            # 重启会复活成 active。getattr 兜底旧 store。
            state._pd = getattr(
                self.session_goal_store,
                "persist_done",
                None,
            )
            if state._pd is not None:
                await state._pd(state.session_id)
            logger.info(
                "goal_checker.marked_done sid=%s",
                state.session_id,
            )

    async def _external_evaluation_gate(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """异体评分闸门（没有自检编排时）。"""

        # WI-2.4: external evaluator gate — BEFORE FinalEvent.
        # Only fires when:
        #   a) external_evaluator is wired (flag on), AND
        #   b) is_high_consequence_goal() returns True.
        # If evaluator returns verdict=revise → emit ErrorEvent
        # ("evaluator_revise") instead of FinalEvent so auto_resume
        # can spawn a replan. Runs at most ONCE per goal (here,
        # before FinalEvent — revise → replan → new run, no loop).
        # flag off (external_evaluator=None) → skip entirely (BC, 0 calls).
        # ⚠️ plans/2026-06-24-...：pipeline on（self._self_check_gate 非 None）时
        # 异体评分已由 Step6 SelfCheckGate 编排 → 此处跳过避免双重评分。
        if self.external_evaluator is not None and self._self_check_gate is None:
            try:
                from deskpet.agent.external_evaluator import (  # noqa: PLC0415
                    is_high_consequence_goal as _is_hcg,
                )

                state._eval_ledger = (
                    self.receipt_store.load_session(state.session_id)
                    if self.receipt_store is not None
                    else []
                )
                # Extract goal_text from session_goal_store if wired,
                # else fall back to first user message text.
                state._eval_goal_text = ""
                if self.session_goal_store is not None:
                    state._gt_fn = getattr(self.session_goal_store, "get_goal_text", None)
                    if callable(state._gt_fn):
                        state._eval_goal_text = state._gt_fn(state.session_id) or ""
                if not state._eval_goal_text:
                    # BC fallback: use first user message content
                    for state._m in state.working_messages:
                        if state._m.get("role") == "user":
                            state._eval_goal_text = str(state._m.get("content") or "")
                            break
                if _is_hcg(state._eval_goal_text, state._eval_ledger, []):
                    state._ev_result = await traced_call(
                        name="external_evaluator.evaluate",
                        kind=SpanKind.GATE,
                        lifecycle_stage="gate",
                        attributes={
                            "iteration": state.iteration,
                            "gate": "external_evaluator",
                        },
                        invoke=lambda: self.external_evaluator.evaluate(
                            original_goal=state._eval_goal_text,
                            produced_artifacts=[
                                getattr(r, "tool_name", "unknown") for r in state._eval_ledger
                            ],
                            objective_evidence=[
                                f"receipt ok: tool={getattr(r, 'tool_name', '?')}"
                                for r in state._eval_ledger
                                if getattr(r, "ok", True)
                            ],
                            conversation_summary=str(state.response.content or "")[:512],
                            workload_context=(
                                self._provider_workload_context_factory("agent.external_evaluator")
                                if self._provider_workload_context_factory is not None
                                else None
                            ),
                        ),
                    )
                    if (
                        state._ev_result.get("verdict") == "revise"
                        and state._ev_result.get("quality_score", 10) < 6
                    ):
                        logger.info(
                            "external_evaluator verdict=revise sid=%s "
                            "quality_score=%d issues=%d → emit evaluator_revise",
                            state.session_id,
                            state._ev_result["quality_score"],
                            len(state._ev_result.get("issues", [])),
                        )
                        try:
                            from observability.metrics_sink import (  # noqa: PLC0415
                                record as _eval_metric,
                            )

                            _eval_metric(
                                "evaluator_revise_triggered",
                                {
                                    "quality_score": state._ev_result["quality_score"],
                                    "issues_count": len(state._ev_result.get("issues", [])),
                                },
                            )
                        except Exception:  # noqa: BLE001 - 指标记录失败不影响把关
                            logger.debug("evaluator_revise_metric_failed", exc_info=True)
                        yield ErrorEvent(
                            type="error",
                            task_id=state.tid,
                            iteration=state.iteration,
                            reason="evaluator_revise",
                            detail=(
                                f"[external_evaluator] 质量不足 "
                                f"(score={state._ev_result['quality_score']}/10): "
                                + "; ".join(state._ev_result.get("issues", []))
                            ),
                        )
                        state.flow = "stop"
                        return
            except Exception as exc:  # noqa: BLE001 — safe-fail
                logger.warning(
                    "external_evaluator gate failed (sid=%s): %s — passing through",
                    state.session_id,
                    exc,
                )
