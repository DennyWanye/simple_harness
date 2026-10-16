# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Self-check pipeline and the verify gate: nudges, structured reflection, ephemeral re-check and forced finish."""

from __future__ import annotations

import difflib as _difflib
from collections.abc import AsyncGenerator
from contextlib import aclosing

from agent.workflow_trace import traced_call
from deskpet.agent.reflection import _REFLECTION_INSTRUCTION, parse_reflection
from deskpet.workflows.trace import SpanKind

from .core import AgentLoopCore
from .support import (
    _SELFCHECK_TIER3_AT,
    AgentEvent,
    ErrorEvent,
    _append_loop_control,
    _append_loop_transcript,
    _context_fragment_id,
    _latest_context_anchor,
    _RunState,
    logger,
)


class VerifyGateMixin(AgentLoopCore):
    """Self-check pipeline and the verify gate: nudges, structured reflection, ephemeral re-check and forced finish."""

    __slots__ = ()

    async def _self_check_gate_step(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """自检编排闸门（核验、反思、异体评分；未开编排时走原核验闸门）。"""

        # ─── Step6 SelfCheckGate（整合 verify_gate + reflection + 异体评分，
        # plans/2026-06-24-...）。pipeline on 时走编排；off 时落到下方 elif 原 verify_gate 块（BC）。
        # ⚠️ R2：补 `and response.content` 守卫，与原 verify_gate 块准入对齐。
        if (
            state.iteration < _SELFCHECK_TIER3_AT
            and self._self_check_gate is not None
            and self._pipeline_problem_type is not None
            and state.response.content
        ):
            state._ledger = (
                self.receipt_store.load_session(state.session_id)
                if self.receipt_store is not None
                else []
            )
            if state.scoped_evidence is not None:
                state._ledger = list(state.scoped_evidence.records)
            state._sc = await traced_call(
                name="self_check_gate.check",
                kind=SpanKind.GATE,
                lifecycle_stage="gate",
                attributes={"iteration": state.iteration, "gate": "self_check"},
                invoke=lambda: self._self_check_gate.check(
                    problem_type=self._pipeline_problem_type,
                    assistant_text=state.response.content or "",
                    ledger=state._ledger,
                    goal_text=self._extract_goal_text(state.working_messages),
                    failure_count=state.verify_nudges_used,
                    produced_artifacts=[getattr(r, "tool_name", "?") for r in state._ledger],
                    objective_evidence=[
                        f"receipt ok: tool={getattr(r, 'tool_name', '?')}"
                        for r in state._ledger
                        if getattr(r, "ok", True)
                    ],
                    workload_context=(
                        self._provider_workload_context_factory("agent.external_evaluator")
                        if self._provider_workload_context_factory is not None
                        else None
                    ),
                ),
            )
            if self._pipeline_observability:
                yield self._pipeline_event(
                    "chat_v2_selfcheck",
                    state.iteration,
                    {
                        "passed": state._sc.passed,
                        "mode": state._sc.mode,
                        "heterogeneous": state._sc.heterogeneous,
                        "claims_unverified": state._sc.claims_unverified,
                    },
                )
            state.flow = None
            async with aclosing(self._on_self_check_failed(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow is not None:
                return
            # passed → 落到下面（不再走旧 verify_gate 块）

        # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门（PRD §3 D6）。
        # 同 completion_probe 模式 — 守门返回 outcome.passed=False 时
        # 回灌 D8 schema system message + continue；max_verify_nudges
        # 控制重试上限（PRD: failure_count==3 时调度 ephemeral → 仍
        # fail 才强退）。flag-off 时 verify_gate=None 跳过整段（BC）。
        # ⚠️ pipeline on 时上方 SelfCheckGate if 已处理 → 此 elif 跳过；off 时走原块（BC）。
        elif (
            state.iteration < _SELFCHECK_TIER3_AT
            and self.verify_gate is not None
            and getattr(self.verify_gate, "mode", "off") != "off"
            and state.verify_nudges_used < self.max_verify_nudges
            and state.response.content
        ):
            state.flow = None
            async with aclosing(self._verify_gate_step(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow is not None:
                return

    async def _on_self_check_failed(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """自检没通过：按剩余次数补提示，或强制收尾。"""

        if not state._sc.passed and state.verify_nudges_used < self.max_verify_nudges:
            state.verify_nudges_used += 1
            state._self_check_anchor = _latest_context_anchor(
                state.working_messages,
                fallback=f"agent-loop:{state.tid}:iteration:{state.iteration}:response",
            )
            if state.response.content:
                state._self_check_reply = _append_loop_transcript(
                    state.working_messages,
                    {"role": "assistant", "content": state.response.content},
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.self_check_gate.trigger",
                    role="assistant",
                    fragment_id=(
                        f"agent-loop:{state.tid}:iteration:{state.iteration}:self-check-trigger"
                    ),
                )
                state._self_check_anchor = (
                    _context_fragment_id(state._self_check_reply) or state._self_check_anchor
                )
            _append_loop_control(
                state.working_messages,
                {
                    "role": "system",
                    "content": (
                        "<自检> 自检未通过：声明与凭据不符。" + state._sc.reflection_instruction
                    ),
                },
                metadata_enabled=state.context_metadata_enabled,
                source="agent_loop.self_check_nudge",
                anchor_after=state._self_check_anchor,
                fragment_id=(
                    f"agent-loop:{state.tid}:iteration:{state.iteration}:"
                    f"self-check-nudge:{state.verify_nudges_used}"
                ),
            )
            logger.info(
                "self_check_nudge_injected sid=%s mode=%s nudge=%d",
                state.session_id,
                state._sc.mode,
                state.verify_nudges_used,
            )
            state.flow = "continue"
            return
        # ─── 耗尽分支（MINOR①：复用 verify-exhausted 终态，勿静默放过）。
        if (
            not state._sc.passed
            and state.verify_nudges_used >= self.max_verify_nudges
            and self.force_finish_via_tool_choice
            and not state._verify_final_done
        ):
            state._verify_final_done = True
            state._force_finish_queued = True
            _append_loop_control(
                state.working_messages,
                {
                    "role": "system",
                    "content": (
                        "自检多次未通过（声明与凭据不符），本轮必须 end_turn："
                        "向用户如实总结做了什么、哪些未能验证、建议下一步。不要再调用任何工具。"
                    ),
                },
                metadata_enabled=state.context_metadata_enabled,
                source="agent_loop.self_check_exhausted",
                anchor_after=_latest_context_anchor(
                    state.working_messages,
                    fallback=(f"agent-loop:{state.tid}:iteration:{state.iteration}:response"),
                ),
                fragment_id=(
                    f"agent-loop:{state.tid}:iteration:{state.iteration}:self-check-exhausted"
                ),
                protected=True,
            )
            logger.warning(
                "self_check_exhausted sid=%s nudge=%d/%d → force_finish",
                state.session_id,
                state.verify_nudges_used,
                self.max_verify_nudges,
            )
            state.flow = "continue"
            return
        # force_finish_via_tool_choice=False 时无强制总结轮 → 直发 verify_exhausted 终态。
        if not state._sc.passed and state.verify_nudges_used >= self.max_verify_nudges:
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
                detail=(
                    state.response.content
                    or f"self-check: all retries exhausted after "
                    f"{state.verify_nudges_used} nudge(s)"
                ),
            )
            state.flow = "stop"
            return

    async def _verify_gate_step(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """原核验闸门（未开自检编排时）：核验不过就补提示，次数用完强制收尾。"""

        try:
            # 拉取本 session 的 sig-filtered ledger（N1 信任面已在
            # ReceiptStore.load_session 内强制 hmac_verify）
            state.ledger = (
                self.receipt_store.load_session(state.session_id)
                if self.receipt_store is not None
                else []
            )
            # WI-2.3: thread goal_text into verify_gate.check so
            # GoalAlignment is populated when an active goal exists.
            # BC: goal_text=None (no store / no active goal) → byte-identical
            # to pre-WI-2.3 (check ignores None goal_text).
            state._vg_goal_text: str | None = None
            if self.session_goal_store is not None:
                state._gt_fn = getattr(self.session_goal_store, "get_goal_text", None)
                if callable(state._gt_fn):
                    state._vg_goal_text = state._gt_fn(state.session_id)
            state.v_outcome = await traced_call(
                name="verify_gate.check",
                kind=SpanKind.GATE,
                lifecycle_stage="gate",
                attributes={"iteration": state.iteration, "gate": "verify"},
                invoke=lambda: self.verify_gate.check(
                    assistant_text=state.response.content,
                    ledger=state.ledger,
                    goal_text=state._vg_goal_text,
                    **(
                        {"scoped_evidence": state.scoped_evidence}
                        if state.scoped_evidence is not None
                        else {}
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "verify_gate.check failed (sid=%s): %s — passing through",
                state.session_id,
                exc,
            )
            state.v_outcome = None

        if (
            state.v_outcome is not None
            and not state.v_outcome.passed
            and state.v_outcome.unmatched_claims
        ):
            if self._tracer:
                self._tracer.record(
                    {
                        "kind": "gate",
                        "iter": state.iteration,
                        "which": "verify",
                        "passed": False,
                        "reason": "unmatched_claims",
                    }
                )
            state.verify_nudges_used += 1

            # WI-2.2: stagnation detection — if 2nd+ rebound and
            # task_replanning text is nearly identical to previous
            # round (difflib ratio > 0.85), the LLM is stuck in
            # copy-paste instead of genuinely replanning. Skip to
            # ephemeral immediately (don't waste the 2nd nudge).
            state._stagnant = False
            self._verify_structured_reflection(state)

            # 失败计数达 max 或 stagnation → 调 ephemeral 救援
            state.ephemeral_pass = False
            if state._stagnant or state.verify_nudges_used >= self.max_verify_nudges:
                try:
                    state.ephemeral_pass = await self.verify_gate.consult_ephemeral_subagent(
                        ledger=state.ledger,
                        failed_claims=state.v_outcome.unmatched_claims,
                        assistant_text=state.response.content,
                        workload_context=(
                            self._provider_workload_context_factory("agent.verify_ephemeral")
                            if self._provider_workload_context_factory is not None
                            else None
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ephemeral consult failed: %s", exc)
                # WI-HM-1: emit ephemeral 救援判定到 metrics.jsonl
                # （之前只 logger.info，未计数化 → 监控看不到救援率）.
                try:
                    from observability.metrics_sink import (  # noqa: PLC0415
                        record as _eph_metric,
                    )

                    _eph_metric(
                        "ephemeral_rescued" if state.ephemeral_pass else "ephemeral_pass",
                        {
                            "nudge_count": int(state.verify_nudges_used),
                            "ok": bool(state.ephemeral_pass),
                        },
                    )
                except Exception:  # noqa: BLE001 — metric 失败不阻 dispatch
                    pass

            state.flow = None
            async with aclosing(self._verify_gate_exhausted(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow is not None:
                return
        elif self._tracer and state.v_outcome is not None:
            self._tracer.record(
                {
                    "kind": "gate",
                    "iter": state.iteration,
                    "which": "verify",
                    "passed": True,
                    "reason": "",
                }
            )

    def _verify_structured_reflection(self, state: _RunState) -> None:
        """核验连续不过时的结构化反思提示。"""

        if state.verify_nudges_used >= 2 and self.structured_reflection:
            try:
                state._refl = parse_reflection(state.response.content or "")
                state._cur_replan = str(state._refl.task_replanning) if state._refl else ""
                if (
                    state._prev_task_replanning
                    and state._cur_replan
                    and _difflib.SequenceMatcher(
                        None, state._prev_task_replanning, state._cur_replan
                    ).ratio()
                    > 0.85
                ):
                    state._stagnant = True
                    logger.info(
                        "verify_replan_stagnant sid=%s nudge=%d "
                        "ratio=%.2f — escalating to ephemeral",
                        state.session_id,
                        state.verify_nudges_used,
                        _difflib.SequenceMatcher(
                            None, state._prev_task_replanning, state._cur_replan
                        ).ratio(),
                    )
                    try:
                        # 按需导入：测试替身（observability.metrics_sink）
                        from observability.metrics_sink import (
                            record as _vg_metric,
                        )

                        _vg_metric(
                            "verify_replan_stagnant",
                            {
                                "nudge_count": int(state.verify_nudges_used),
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
                if state._refl:
                    state._prev_task_replanning = state._cur_replan
            except Exception:  # noqa: BLE001 — safe-fail
                pass

    async def _verify_gate_exhausted(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """核验补提示次数用完：临时会话再核验一次，仍不过就强制收尾。"""

        if not state.ephemeral_pass:
            state.flow = None
            async with aclosing(self._verify_force_finish(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow is not None:
                return

            # Still have nudges: inject rebound + 回灌 D8 schema
            state.unmatched_lines = "\n".join(
                f"  {i + 1}. [unmatched_claim] {c.raw_text!r} — no receipt matched ({c.reason})"
                for i, c in enumerate(state.v_outcome.unmatched_claims[:5])
            )
            state.rebound = (
                f"[verify-gate] iteration={state.iteration} blocked end_turn.\n"
                f"Failures:\n{state.unmatched_lines}\n"
                f"Classification: unmatched_claim\n"
                f"Next: please call the missing tool to actually "
                f"perform the action you claimed, then end_turn again."
            )
            # WI-2.3: append goal alignment context to rebound
            # when goal_text was provided and GoalAlignment is
            # populated. goal_text=None → rebound unchanged (BC).
            if (
                state.v_outcome.goal_alignment is not None
                and not state.v_outcome.goal_alignment.aligned
            ):
                state.ga = state.v_outcome.goal_alignment
                state._evidence_str = (
                    "\n".join(f"  - {e}" for e in state.ga.objective_evidence[:5])
                    or "  (无客观证据)"
                )
                state.rebound = (
                    state.rebound
                    + f"\n原始目标: {state.ga.goal_text}\n"
                    + f"客观证据: {state._evidence_str}\n"
                    + f"缺口: {state.ga.gap}\n"
                    + "→ 请重规划（结构化反思）后补齐，"
                    + "使产物真满足原目标，而非换个说法过关。"
                )
            # WI-2.1: append reflection instruction when flag on.
            # Flag off (default) → rebound string unchanged (BC).
            if self.structured_reflection:
                state.rebound = state.rebound + _REFLECTION_INSTRUCTION
            state._verify_anchor = _latest_context_anchor(
                state.working_messages,
                fallback=(f"agent-loop:{state.tid}:iteration:{state.iteration}:response"),
            )
            if state.response.content:
                state._verify_reply = _append_loop_transcript(
                    state.working_messages,
                    {
                        "role": "assistant",
                        "content": state.response.content,
                    },
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.verify_gate.trigger",
                    role="assistant",
                    fragment_id=(
                        f"agent-loop:{state.tid}:iteration:{state.iteration}:verify-trigger"
                    ),
                )
                state._verify_anchor = (
                    _context_fragment_id(state._verify_reply) or state._verify_anchor
                )
            _append_loop_control(
                state.working_messages,
                {"role": "system", "content": state.rebound},
                metadata_enabled=state.context_metadata_enabled,
                source="agent_loop.verify_nudge",
                anchor_after=state._verify_anchor,
                fragment_id=(
                    f"agent-loop:{state.tid}:iteration:{state.iteration}:"
                    f"verify-nudge:{state.verify_nudges_used}"
                ),
            )
            logger.info(
                "verify_gate_nudge_injected sid=%s nudge=%d/%d unmatched=%d ephemeral_pass=%s",
                state.session_id,
                state.verify_nudges_used,
                self.max_verify_nudges,
                len(state.v_outcome.unmatched_claims),
                state.ephemeral_pass,
            )
            # WI-T2.1 v3：真 emit 到 metrics.jsonl（MR-T-8 拦截
            # 硬证据 — 用户 goal "verify_* event in metrics.jsonl"
            # 把 fake-completion 拦截事件计数化，供监控面板看健康率）
            try:
                # 按需导入：测试替身（observability.metrics_sink）
                from observability.metrics_sink import (
                    record as _verify_metric,
                )

                _verify_metric(
                    "verify_gate_nudge_injected",
                    {
                        "nudge_count": int(state.verify_nudges_used),
                        "count": int(len(state.v_outcome.unmatched_claims)),
                        "ok": bool(state.ephemeral_pass),
                    },
                )
            except Exception:  # noqa: BLE001 — metric 失败不阻 dispatch
                pass
            state.flow = "continue"
            return
        else:
            logger.info(
                "verify_gate.ephemeral_rescued sid=%s",
                state.session_id,
            )

    async def _verify_force_finish(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """核验停滞或次数用完：强制收尾。"""

        # WI-2.2: verify_exhausted — all layers (nudges +
        # ephemeral) exhausted → emit terminal error + return.
        # Only when we've either stagnated or used all nudges
        # AND ephemeral failed.
        if state._stagnant or state.verify_nudges_used >= self.max_verify_nudges:
            logger.warning(
                "verify_exhausted sid=%s nudge=%d/%d stagnant=%s",
                state.session_id,
                state.verify_nudges_used,
                self.max_verify_nudges,
                state._stagnant,
            )
            try:
                # 按需导入：测试替身（observability.metrics_sink）
                from observability.metrics_sink import (
                    record as _vg_metric,
                )

                _vg_metric(
                    "verify_exhausted",
                    {
                        "nudge_count": int(state.verify_nudges_used),
                        "stagnant": bool(state._stagnant),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            if self.force_finish_via_tool_choice and not state._verify_final_done:
                state._verify_final_done = True
                state._force_finish_queued = True
                _append_loop_control(
                    state.working_messages,
                    {
                        "role": "system",
                        "content": (
                            "verify 多次未对齐 ledger，本轮必须 end_turn："
                            "向用户如实总结做了什么、哪些未能验证、建议下一步。"
                            "不要再调用任何工具。"
                        ),
                    },
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.verify_exhausted",
                    anchor_after=_latest_context_anchor(
                        state.working_messages,
                        fallback=(f"agent-loop:{state.tid}:iteration:{state.iteration}:response"),
                    ),
                    fragment_id=(
                        f"agent-loop:{state.tid}:iteration:{state.iteration}:verify-exhausted"
                    ),
                    protected=True,
                )
                state.flow = "continue"
                return
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
                detail=(
                    f"verify-gate: all retries exhausted after "
                    f"{state.verify_nudges_used} nudge(s). "
                    f"stagnant={state._stagnant}"
                ),
            )
            state.flow = "stop"
            return
