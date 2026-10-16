# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Construction and small helpers shared by every phase."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from agent.context_manager import ContextManager
from agent.skill_remount import (
    frozen_skill_scope,
    prepare_frozen_skill_remounts,
    remount_skills,
    skill_scope_candidates,
)
from agent.termination import GateConfig, TerminationGate
from deskpet.workflows.trace import TraceStore
from llm.budget import DailyBudget
from llm.types import ChatResponse

from .support import AgentEvent, _RunState, logger

# ───────────────────── P5-S2 Phase 3 constants ─────────────────────


# Repeat-detection threshold: if the LLM emits the SAME (tool_name,
# args_hash) signature ``>= _REPEAT_THRESHOLD`` times in a row, the
# loop short-circuits the dispatch and injects a system message asking
# the LLM to look at the prior tool_result hint or change tactics.
# Keep at 3 to match the OpenSpec proposal default.
_REPEAT_THRESHOLD = 3


@dataclass
class PipelineEvent(AgentEvent):
    """七步问题处理流水线观测事件（plans/2026-06-24-...）。

    type ∈ {chat_v2_intent, chat_v2_contradiction, chat_v2_evidence_gate,
    chat_v2_selfcheck, chat_v2_convergence}。仅 pipeline_observability=True 时 yield；
    main.py 据 .type 直接 send_json。flag off 时**永不构造**（零开销，BC）。"""

    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # type 由构造方显式给（多种事件复用本类），不在此覆盖。
        pass


# ───────────────────── protocols for caller dependencies ─────────────────────


class _LLMRegistryProto(Protocol):
    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse: ...


class _ToolRegistryProto(Protocol):
    def schemas(self, enabled_toolsets: list[str] | None = None) -> list[dict[str, Any]]: ...
    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any: ...


class AgentLoopCore:
    """Construction and small helpers shared by every phase."""

    if TYPE_CHECKING:

        async def _compact_context(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]: ...
        async def _execute_coverage_compaction_jobs(
            self, *, cycle_id: str, prepared_context: Any, resolution: Any
        ) -> None: ...
        async def _flush_context_snapshot(
            self,
            *,
            cycle_id: str,
            session_id: str,
            request_id: str,
            working_messages: list[dict[str, Any]],
            prepared_context: Any,
            current_tool_set: Any,
        ) -> Any: ...
        async def _handle_final_response(
            self, state: _RunState
        ) -> AsyncGenerator[AgentEvent, None]: ...
        async def _iterate_context_attempt(
            self,
            *,
            iterator: Callable[[], AsyncIterator[Any]],
            provider: Any,
            session_id: str,
            request_id: str,
            attempt_id: str,
            purpose: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None,
            fallback_model: str,
            prepared_context: Any,
            current_tool_set: Any,
            compression: Mapping[str, Any] | None = ...,
        ) -> AsyncIterator[Any]: ...
        async def _periodic_checks(self, state: _RunState) -> None: ...
        def _requested_compression_model(self) -> str: ...
        def _resolve_compression_model(
            self, provider_chain: Any, *, requested_model: str | None = ...
        ) -> Any: ...
        async def _run_context_attempt(
            self,
            *,
            invoke: Callable[[], Awaitable[Any]],
            provider: Any,
            session_id: str,
            request_id: str,
            attempt_id: str,
            purpose: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None,
            fallback_model: str,
            prepared_context: Any,
            current_tool_set: Any,
            compression: Mapping[str, Any] | None = ...,
        ) -> Any: ...
        async def _self_check_gate_step(
            self, state: _RunState
        ) -> AsyncGenerator[AgentEvent, None]: ...

    def __init__(
        self,
        llm_registry: _LLMRegistryProto,
        tool_registry: _ToolRegistryProto,
        *,
        max_iterations: int = 20,
        budget_checker: DailyBudget | None = None,
        default_model: str | None = None,
        completion_probe: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
        max_completion_nudges: int = 2,
        activity_store: Any | None = None,
        signature_repeat_threshold: int | None = None,
        termination_gate: TerminationGate | None = None,
        context_manager: ContextManager | None = None,
        # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门 + ReceiptStore 拿 ledger
        verify_gate: Any | None = None,  # deskpet.agent.verify_gate.VerifyGate
        receipt_store: Any | None = None,  # deskpet.tools.receipt_store.ReceiptStore
        max_verify_nudges: int = 2,  # PRD D6: 3 次失败强退
        # WI-B3 Companion+Code v1: /goal command 末轮 LLM-judged rebound.
        # 两个都 None (默认) → BC，跳过整段 goal-check 块。
        session_goal_store: Any | None = None,  # deskpet.agent.goal_store.SessionGoalStore
        goal_checker: Any | None = None,  # deskpet.agent.goal_checker.GoalChecker
        # WI-2.1 structured reflection: when True, _REFLECTION_INSTRUCTION is
        # appended to verify-gate rebound + selfcheck tier2/tier3 system msgs.
        # Default False = BC (flag off → byte-identical behaviour to pre-WI-2.1).
        structured_reflection: bool = False,
        # WI-2.4 external evaluator: cross-persona quality judge for high-consequence
        # goals. None (default) = BC (0 extra LLM calls, skip entirely).
        external_evaluator: Any | None = None,  # deskpet.agent.external_evaluator.ExternalEvaluator
        # WI-4.0 compaction: ContextCompressor to call when prompt tokens near cap.
        # None (default) = BC (compressor not injected → zero new behaviour).
        # When non-None, should_compress() and compress() are called in the loop.
        compressor: Any | None = None,  # deskpet.agent.context_compressor.ContextCompressor
        context_projector: Any | None = None,
        context_snapshot_store: Any | None = None,
        context_segment_store: Any | None = None,
        context_attempt_store: Any | None = None,
        compression_model_resolver: Any | None = None,
        compression_model: str = "follow_session",
        compression_model_provider: Callable[[], str] | None = None,
        # WI-1B-2 压缩可观测 (features.ctx_observability). False (默认) = 字节级 BC:
        # 压缩成功路径不 emit metrics、不 yield ContextCompactedEvent。True 时压缩
        # 命中额外 record 一条 metrics + yield 一条轻量事件供 main.py 转 ws → 前端 toast。
        ctx_observability: bool = False,
        # WI-4.2 skill remount: inject SkillLoader + SkillMatcher for post-compaction
        # skill body re-inline.  Both default None → BC (no remount, zero overhead).
        # When non-None and compaction fires, _remount_skills() is called to re-insert
        # the bodies of skills used this run as a single role=system block.
        skill_loader: Any | None = None,  # deskpet.skills.loader.SkillLoader
        skill_matcher: Any | None = None,  # deskpet.skills.skill_matcher.SkillMatcher
        # WI-4b pre-flush: 压缩真正摘掉中段前,把"当前任务态"写进 L1 文件记忆,
        # 跨 session 记住任务(frozen-snapshot → 只对下个 session 生效)。None (默认)
        # → 不 flush(BC)。每个 run 最多 flush 一次(限频,防刷爆 MEMORY.md 50KB cap)。
        file_memory: Any | None = None,  # FileMemory（记忆 SDK 未集成）
        force_finish_via_tool_choice: bool = True,
        tracer: Any | None = None,
        trace_store: TraceStore | None = None,
        workflow_service: Any | None = None,
        session_todo_getter: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
        # WI-OH-4 记忆 self-curation nudge: MemoryCurator + 频率门控。
        # curator None (默认) → 不调 nudge（BC，零行为变更）。非 None 时每
        # curation_nudge_every_n_turns 个回合在 FinalEvent 后 fire-and-forget
        # 一次 nudge（异步，不挡主回合，照 vector_worker 模式）。
        memory_curator: Any | None = None,  # MemoryCurator（记忆 SDK 未集成）
        curation_nudge_every_n_turns: int = 8,
        provider_workload_context_factory: Callable[[str], Any] | None = None,
        provider_workload_router: Any | None = None,
        # ─── 七步问题处理流水线 IN-LOOP 三闸（plans/2026-06-24-problem-pipeline）。
        # 全 None/False → 跳过所有 pipeline 分支（kill-switch 回退到今天的链路，BC）。
        evidence_gate: Any | None = None,  # deskpet.agent.evidence_gate.EvidenceGate
        self_check_gate: Any
        | None = None,  # deskpet.agent.self_check_gate.SelfCheckGate（build_agent 内构造后传）
        convergence_report_on_stop: bool = False,  # Step7：True → __init__ 末尾用 self._gate 自建 ConvergenceController
        pipeline_problem_type: str | None = None,  # Step1 产出的 problem_type（喂 Step6 选档）
        pipeline_needs_investigation: bool = False,  # IntentCard.needs_investigation（喂 Step2）
        pipeline_observability: bool = False,  # 发 chat_v2_evidence_gate / _selfcheck / _convergence 事件
        response_quality_gate: Any | None = None,
    ) -> None:
        self.llm = llm_registry
        self.tools = tool_registry
        # WI-OH-4 记忆自策展 nudge（BC: None → 不调 nudge）。每 session 轮次
        # 计数器现挂在 curator 单例上（见 curation.py bump_turn）——本 loop 每回合
        # 重建，计数器若挂这里会每回合归零、永不到阈值（真测抓出）。阈值仍留这里。
        self._memory_curator = memory_curator
        self._curation_every = max(1, int(curation_nudge_every_n_turns or 8))
        self._provider_workload_context_factory = provider_workload_context_factory
        self._provider_workload_router = provider_workload_router
        self.max_iterations = max_iterations
        self.budget_checker = budget_checker
        self.default_model = default_model
        # P5-S2 Hook A: completion guard. If supplied, ``completion_probe``
        # is called when the LLM tries to finalize (stop_reason ≠ tool_use)
        # to check whether session-level work (todos) is actually done.
        # The probe receives ``session_id`` and returns the list of
        # incomplete todo dicts ({content, activeForm, status, ...}).
        # If non-empty AND we still have nudges in budget, the loop
        # injects a system message reminding the LLM and re-runs the
        # iteration instead of finalizing. ``max_completion_nudges``
        # caps the rebound count to prevent infinite loops when the LLM
        # genuinely refuses to continue. Set to 0 to disable the hook.
        self.completion_probe = completion_probe
        self.max_completion_nudges = max_completion_nudges
        # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门
        self.verify_gate = verify_gate
        self.receipt_store = receipt_store
        self.max_verify_nudges = max_verify_nudges
        # WI-B3 Companion+Code v1: /goal store + checker (BC: 两者皆 None → skip)
        self.session_goal_store = session_goal_store
        self.goal_checker = goal_checker
        # WI-2.1 structured reflection flag (BC: False → no injection)
        self.structured_reflection = structured_reflection
        # WI-2.4 external evaluator: cross-persona quality judge for high-consequence
        # goals (BC: None → skip entirely, 0 extra LLM calls).
        self.external_evaluator = external_evaluator
        # WI-4.0 compaction: ContextCompressor (BC: None → skip entirely).
        # When non-None, loop calls should_compress() + compress() after budget check.
        self.compressor = compressor
        self.context_projector = context_projector
        self.context_snapshot_store = context_snapshot_store
        self.context_segment_store = context_segment_store
        self.context_attempt_store = context_attempt_store
        self.compression_model_resolver = compression_model_resolver
        self.compression_model = str(compression_model or "follow_session")
        self.compression_model_provider = compression_model_provider
        # WI-1B-2 压缩可观测 flag (BC: False → 压缩路径零额外行为)。
        self.ctx_observability = bool(ctx_observability)
        # WI-4.2 skill remount (BC: both None → skip entirely).
        # When non-None and compaction fires, _remount_skills() re-inlines skill bodies.
        self.skill_loader = skill_loader
        self.skill_matcher = skill_matcher
        # WI-4b pre-flush L1 句柄（BC: None → 不 flush）。
        self.file_memory = file_memory
        self.force_finish_via_tool_choice = force_finish_via_tool_choice
        self._tracer = tracer
        self._trace_store = trace_store
        self._workflow_service = workflow_service
        self.session_todo_getter = session_todo_getter
        # P5-S2 Phase 3.3: same-(name, args) repeat detection. When set,
        # the loop checks the activity store's per-session
        # ``tool_signature_window`` BEFORE dispatching each tool_call —
        # if the same signature already shows ``>= _REPEAT_THRESHOLD - 1``
        # consecutive prior calls, we suppress the dispatch and inject a
        # system nudge so the LLM gets a chance to change tactics.
        # Pre-Phase-3 callers leave this as None and the branch is a
        # no-op (verified by ``test_no_activity_store_means_no_repeat_detection``).
        self.activity_store = activity_store
        # P5-S2 Phase 6: per-instance override for the consecutive
        # same-(name, args) repeat threshold. Defaults to module
        # constant ``_REPEAT_THRESHOLD`` (3) so legacy callers keep
        # the existing behaviour. main.py wires
        # ``[supervisor].tool_signature_repeat_threshold`` here.
        self._signature_repeat_threshold = (
            int(signature_repeat_threshold)
            if signature_repeat_threshold is not None
            else _REPEAT_THRESHOLD
        )
        # P6 Phase 6 — TerminationGate is always wired in. Callers may
        # inject an explicit gate; otherwise a default gate is created
        # with max_turns = self.max_iterations. The legacy ``_gate is
        # None`` branch is gone — every code path goes through the gate.
        #
        # The soft selfcheck tier messages (_SELFCHECK_*) are kept in
        # place — they are complementary to the gate, not replaced by it.
        # The gate provides HARD termination semantics on top of those
        # nudges. The legacy _TOOL_BUDGET_HARD_MSG soft cap was removed
        # in Phase 6 (gate handles tool budget hard cap directly).
        self._gate: TerminationGate = (
            termination_gate
            if termination_gate is not None
            else TerminationGate(GateConfig(max_turns=self.max_iterations))
        )

        # P6 Phase 6 — ContextManager is always wired in. Same shape as
        # _gate above: caller may inject one, otherwise a default is
        # created. The tool-result write site delegates truncation to
        # ctx.record_tool_result (which honours skip_truncation_for_tools
        # — the G1 fix). The legacy ``_ctx is None`` branch is gone.
        self._ctx: ContextManager = (
            context_manager if context_manager is not None else ContextManager()
        )

        # ─── 七步问题处理流水线 IN-LOOP 三闸赋值（plans/2026-06-24-problem-pipeline）。
        # 必须在 self._gate 构造（上方）之后——ConvergenceController 依赖 self._gate。
        self._evidence_gate = evidence_gate
        self._self_check_gate = self_check_gate
        self._pipeline_problem_type = pipeline_problem_type
        self._pipeline_needs_investigation = bool(pipeline_needs_investigation)
        self._pipeline_observability = bool(pipeline_observability)
        self._response_quality_gate = response_quality_gate
        self._evidence_nudges_used = 0  # Step2 nudge 计数
        # ⚠️ R1（round-2）：弃用绝对长度切片基线 → 改布尔累积标志（对 compaction 免疫）。
        # run() 开头重设；本 run dispatch 出取证工具 → 置 True。
        self._evidence_gathered = False
        self._history_tool_names: set[str] = set()
        # Step7 ConvergenceController 自建（依赖 self._gate）：
        self._convergence_controller = None
        if convergence_report_on_stop:
            from deskpet.agent.convergence_controller import ConvergenceController  # noqa: PLC0415

            self._convergence_controller = ConvergenceController(
                self._gate,
                report_on_stop=True,
            )

    # ------------------------------------------------------------------
    # WI-OH-4 — memory self-curation nudge (fire-and-forget)
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_tool_names(messages: list[dict[str, Any]]) -> list[str]:
        """收集 working_messages 里 tool 调用名（role=='tool' 的 name）。run() 开头用它快照
        history 旧 tool（含 bundle.history 注入的带 name 旧 tool 消息），喂 self._history_tool_names。
        plans/2026-06-24-... §M2 改动 2b-1。"""
        return [m.get("name", "") for m in messages if m.get("role") == "tool"]

    @staticmethod
    def _extract_goal_text(working_messages: list[dict[str, Any]]) -> str:
        """取首个 user 消息作 goal_text（复用 external_evaluator 块的 BC fallback 逻辑）。"""
        for m in working_messages:
            if m.get("role") == "user":
                return str(m.get("content") or "")
        return ""

    @staticmethod
    def _prepared_toolset_summary(current_tool_set: Any) -> dict[str, Any]:
        if current_tool_set is None:
            return {"adapter_state": "canonical"}
        direct = tuple(getattr(current_tool_set, "direct", ()) or ())
        activated = tuple(getattr(current_tool_set, "activated", ()) or ())
        all_caps = (*direct, *activated)
        return {
            "direct_names": [str(cap.ref.name) for cap in direct],
            "activated_names": [str(cap.ref.name) for cap in activated],
            "schema_hashes": {str(cap.ref.name): str(cap.ref.schema_hash) for cap in all_caps},
            "persisted_tool_scope_revision": int(getattr(current_tool_set, "revision", 0) or 0),
            "registry_revision": int(getattr(current_tool_set, "registry_revision", 0) or 0),
            "policy_fingerprint": str(getattr(current_tool_set, "policy_fingerprint", "") or ""),
            "schema_fingerprint": str(getattr(current_tool_set, "schema_fingerprint", "") or ""),
            "adapter_state": "canonical",
        }

    def _pipeline_event(self, ev_type: str, iteration: int, payload: dict) -> PipelineEvent:
        """构造七步流水线 WS 观测事件（plans/2026-06-24-... §M2 改动 2f）。"""
        return PipelineEvent(
            type=ev_type,
            task_id=getattr(self, "_current_tid", None) or "",
            iteration=iteration,
            payload=payload,
        )

    def _maybe_fire_curation_nudge(
        self,
        session_id: str,
        working_messages: list[dict[str, Any]],
    ) -> None:
        """Periodically kick off a memory self-curation nudge.

        BC: ``self._memory_curator is None`` → return immediately (zero
        behaviour change). When a curator is wired, a per-session turn counter
        is bumped each terminal turn; once it hits ``_curation_every`` the
        nudge is scheduled as a fire-and-forget asyncio task so it never blocks
        the FinalEvent / user turn (照 vector_worker 异步模式).

        Any failure here is swallowed — the curation path must never affect the
        chat turn.
        """
        curator = self._memory_curator
        if curator is None:
            return
        try:
            # WI-OH-4 fix (2026-06-23, real-E2E caught): the per-session count
            # lives on the curator SINGLETON, not on this _AgentLoop. main.py
            # rebuilds a fresh loop per chat turn (build_agent in _run_chat),
            # so an in-loop counter reset every turn and never reached the
            # threshold in production (the unit test passed only because it
            # reused one loop instance). The threshold itself stays here (from
            # config). Defensive getattr: curators without bump_turn (old/mock)
            # fall back to firing every terminal turn rather than never.
            _bump = getattr(curator, "bump_turn", None)
            count = _bump(session_id) if callable(_bump) else self._curation_every
            if count % self._curation_every != 0:
                return
            # Snapshot the conversation so the background task reads a stable
            # list even if `working_messages` keeps mutating after we return.
            recent = list(working_messages)

            async def _run_curation() -> None:
                try:
                    decisions = await curator.nudge(
                        recent,
                        **(
                            {
                                "workload_context": self._provider_workload_context_factory(
                                    "memory.curation"
                                )
                            }
                            if self._provider_workload_context_factory is not None
                            else {}
                        ),
                    )
                    logger.info(
                        "oh4_curation_nudge sid=%s turn=%d decisions=%d remembered=%d",
                        session_id,
                        count,
                        len(decisions),
                        sum(1 for d in decisions if getattr(d, "should_remember", False)),
                    )
                except Exception as exc:  # noqa: BLE001 — never escape bg task
                    logger.debug("oh4_curation_nudge_failed sid=%s: %s", session_id, exc)

            task = asyncio.create_task(_run_curation())
            if (
                self._provider_workload_router is not None
                and self._provider_workload_context_factory is not None
            ):
                self._provider_workload_router.track_task(
                    self._provider_workload_context_factory("memory.curation"),
                    task,
                )
        except Exception as exc:  # noqa: BLE001 — scheduling must not break turn
            logger.debug("oh4_curation_schedule_failed sid=%s: %s", session_id, exc)

    # ------------------------------------------------------------------
    # WI-4.2 — post-compaction skill body remount
    # ------------------------------------------------------------------

    _REMOUNT_MARKER = "[已重挂技能 / remounted skills]"
    _REMOUNT_TOKEN_BUDGET = 25_000  # chars (proxy for tokens; 1 token ≈ 4 chars)

    _frozen_skill_scope = staticmethod(frozen_skill_scope)
    _skill_scope_candidates = staticmethod(skill_scope_candidates)

    async def _prepare_frozen_skill_remounts(
        self,
        messages: list[dict[str, Any]],
        *,
        prepared_context: Any,
        external_feedback: Mapping[str, Any],
    ) -> None:
        await prepare_frozen_skill_remounts(
            messages,
            loader=self.skill_loader,
            prepared_context=prepared_context,
            external_feedback=external_feedback,
            target=self._frozen_skill_remounts,
        )

    def _remount_skills(
        self, messages: list[dict], session_id: str, *, prepared_context: Any = None
    ) -> list[dict]:
        return remount_skills(self, messages, session_id, prepared_context=prepared_context)
