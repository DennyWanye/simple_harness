"""构造 Agent：组装主循环、校验门、工具与上下文（整段搬家，拆函数在后面的阶段）"""

from __future__ import annotations

import logging as _log
import uuid
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any

import paths as _paths
from agent.agent_loop import AgentLoop as _AgentLoop
from agent.trace import IterationTracer
from deskpet.agent.verify_gate import (
    RegexExtractor,
    VerifyGate,
    load_claim_patterns,
    make_ephemeral_verifier,
)

from host.context_projection import (
    _build_context_task_projector,
    _configured_context_compaction_model,
    _live_context_compaction_model,
)
from host.llm_calls import _make_session_aware_llm_call
from host.runtime import logger, service_context
from host.stores_bootstrap import _file_memory


@dataclass
class _AgentBuildState:
    """build_agent 的运行状态：原函数的局部变量与参数（切段用）。"""

    result: Any = None
    flow: Any = None
    _EE: Any = None
    _SCG: Any = None
    _agent_cfg: Any = None
    _build_pipeline_external_evaluator: Any = None
    _build_pipeline_verify_gate: Any = None
    _cfg_raw: Any = None
    _compression_model: Any = None
    _compression_model_provider: Any = None
    _compression_model_resolver_for_loop: Any = None
    _context_projector: Any = None
    _context_segment_store_for_loop: Any = None
    _context_snapshot_store_for_loop: Any = None
    _ctx_observability: Any = None
    _ephemeral_llm: Any = None
    _ephemeral_subagent: Any = None
    _ev_llm_call: Any = None
    _external_evaluator: Any = None
    _features_cfg: Any = None
    _ff: Any = None
    _pp: Any = None
    _sc_external: Any = None
    _sc_hetero: Any = None
    _sc_verify_gate: Any = None
    _scm: Any = None
    _self_check_gate: Any = None
    _services: Any = None
    _trace_enabled: Any = None
    _tracer: Any = None
    _use_external_evaluator: Any = None
    _verify_metric: Any = None
    cfg: Any = None
    completion_probe: Any = None
    compressor: Any = None
    context_attempt_store: Any = None
    context_manager: Any = None
    convergence_report_on_stop: Any = None
    enable_verify_gate: Any = None
    evidence_gate: Any = None
    goal_checker: Any = None
    llm_registry: Any = None
    max_completion_nudges: Any = None
    max_iterations: Any = None
    memory_curator: Any = None
    nudges: Any = None
    patterns: Any = None
    patterns_path: Any = None
    pipeline_needs_investigation: Any = None
    pipeline_observability: Any = None
    pipeline_problem_type: Any = None
    provider_workload_context_factory: Any = None
    provider_workload_router: Any = None
    receipt_store: Any = None
    receipt_store_getter: Any = None
    response_quality_gate: Any = None
    session_goal_store: Any = None
    session_todo_getter: Any = None
    signature_repeat_threshold: Any = None
    skill_loader: Any = None
    skill_matcher: Any = None
    tool_registry: Any = None
    trace_store: Any = None
    use_structured_reflection: Any = None
    verifier_cfg: Any = None
    verify_gate: Any = None
    workflow_service: Any = None


def build_agent(
    cfg,
    *,
    llm_registry,
    tool_registry,
    context_manager,
    receipt_store_getter,
    # ★v3 round2 P0-6 补 4 个参数（main.py:4161 现场调用需要）
    max_iterations: int = 16,
    completion_probe=None,
    session_todo_getter=None,
    max_completion_nudges: int = 2,
    signature_repeat_threshold=None,
    # Companion+Code v1 — WI-B3 goal_mode 接电
    session_goal_store=None,
    goal_checker=None,
    # WI-4.0 compaction — pre-built ContextCompressor or None (flag off = BC)
    compressor=None,
    # FP-5 缺口 5a (2026-06-06) — WI-4.2 remount + 自动披露接电。两者 None (默认)
    # → _AgentLoop 跳过 _remount_skills + auto-disclosure（字节级 BC）。
    skill_loader=None,
    skill_matcher=None,
    # WI-OH-4 — 记忆 self-curation nudge。None (默认) → agent_loop 不调 nudge
    # （字节级 BC）。非 None 时每 N 回合 fire-and-forget 触发一次。
    memory_curator=None,
    provider_workload_context_factory=None,
    provider_workload_router=None,
    # ─── 七步问题处理流水线 IN-LOOP 闸透传（plans/2026-06-24-...）。全默认 None/False = BC。
    # self_check_gate / convergence_controller 不由 caller 传——前者 build_agent 内构造（B3，
    # 依赖本函数的 verify_gate/external_evaluator），后者 AgentLoop 内自建（依赖 self._gate）。
    evidence_gate=None,
    pipeline_problem_type=None,
    pipeline_needs_investigation=False,
    pipeline_observability=False,
    convergence_report_on_stop=False,
    response_quality_gate=None,
    trace_store=None,
    context_attempt_store=None,
    enable_verify_gate: bool = True,
):
    """Build a wired _AgentLoop with optional VerifyGate + ReceiptStore.

    Behavior matrix:
      - cfg.tools.verifier.verify_gate_mode == "off" (default): verify_gate=None,
        agent_loop 跳过 verify check（BC，与现状字节级一致）
      - mode in ("shadow", "strict"): 构造 RegexExtractor(load_claim_patterns(yaml))
        + VerifyGate(extractor, mode)；patterns yaml 缺失或 parse 失败 → 退化
        verify_gate=None + warn（**不抛**，避免 backend 启动崩）
      - receipt_store_getter() 失败 → receipt_store=None + warn

    Args:
        cfg: AppConfig 实例（需有 cfg.tools.verifier 子段）
        llm_registry / tool_registry / context_manager: AgentLoop 必需
        receipt_store_getter: 0-arg callable，返回 ReceiptStore 或 None
        max_iterations: AgentLoop 迭代上限
        completion_probe: 完成探针 (P5-S2 Hook A)
        session_todo_getter: session todo 快照读取器 (WI-4 Focus Chain)
        max_completion_nudges: 完成探针 nudge 上限
        signature_repeat_threshold: 死循环抑制阈值 (P5-S2 Phase 6)

    Returns:
        _AgentLoop 实例（带 verify_gate / receipt_store / max_verify_nudges 接电）
    """

    # Build VerifyGate from cfg (mode != "off" only — flag OFF stays BC).
    st = _AgentBuildState(
        cfg=cfg,
        llm_registry=llm_registry,
        tool_registry=tool_registry,
        context_manager=context_manager,
        receipt_store_getter=receipt_store_getter,
        max_iterations=max_iterations,
        completion_probe=completion_probe,
        session_todo_getter=session_todo_getter,
        max_completion_nudges=max_completion_nudges,
        signature_repeat_threshold=signature_repeat_threshold,
        session_goal_store=session_goal_store,
        goal_checker=goal_checker,
        compressor=compressor,
        skill_loader=skill_loader,
        skill_matcher=skill_matcher,
        memory_curator=memory_curator,
        provider_workload_context_factory=provider_workload_context_factory,
        provider_workload_router=provider_workload_router,
        evidence_gate=evidence_gate,
        pipeline_problem_type=pipeline_problem_type,
        pipeline_needs_investigation=pipeline_needs_investigation,
        pipeline_observability=pipeline_observability,
        convergence_report_on_stop=convergence_report_on_stop,
        response_quality_gate=response_quality_gate,
        trace_store=trace_store,
        context_attempt_store=context_attempt_store,
        enable_verify_gate=enable_verify_gate,
    )
    _prepare_verify_gate(st)

    # Pull receipt_store (None when emit_receipts=False).
    st.receipt_store = None
    try:
        st.receipt_store = st.receipt_store_getter()
    except Exception as exc:  # noqa: BLE001
        logger.warning("receipt_store getter failed: %s — verify_gate disabled", exc, exc_info=True)
        st.receipt_store = None
        st.verify_gate = None  # gate without ledger = blocks every end_turn

    st.nudges = 2
    try:
        st.nudges = int(getattr(st.verifier_cfg, "max_verify_nudges", 2)) if st.verifier_cfg else 2
    except Exception:  # noqa: BLE001
        st.nudges = 2

    # WI-2.1: read structured_reflection flag from verifier config (default False = BC)
    st.use_structured_reflection = (
        bool(getattr(st.verifier_cfg, "structured_reflection", False)) if st.verifier_cfg else False
    )

    # WI-1B-2 压缩可观测: 从 cfg.features.ctx_observability 读 flag (默认 False = BC)。
    # OFF 时 _AgentLoop 压缩路径不 emit metrics / 不 yield ContextCompactedEvent。
    st._features_cfg = getattr(st.cfg, "features", None)
    st._ctx_observability = (
        bool(getattr(st._features_cfg, "ctx_observability", False)) if st._features_cfg else False
    )

    _prepare_external_evaluator(st)

    _prepare_agent_flags_and_tracer(st)

    _define_pipeline_builders(st)

    _prepare_self_check_gate(st)

    if st.trace_store is None and getattr(
        getattr(st.cfg, "workflows", None), "trace_enabled", False
    ):
        st.workflow_service = service_context.get("workflow_service")
        st.trace_store = getattr(st.workflow_service, "trace_store", None)

    _prepare_context_os(st)

    st.flow = None
    _construct_agent_loop(st)
    if st.flow == "stop":
        return st.result


def _prepare_agent_flags_and_tracer(st: _AgentBuildState) -> None:
    """读 agent 配置开关；开启时建逐轮追踪器。"""

    st._cfg_raw = getattr(st.cfg, "raw", None)
    st._agent_cfg = st._cfg_raw.get("agent", {}) if isinstance(st._cfg_raw, dict) else {}
    st._ff = bool(st._agent_cfg.get("force_finish_tool_choice", True))
    # WI-2: 用 iteration_trace_enabled（而非 trace_enabled）避免与
    # [context.assembler].trace_enabled（Context Trace UI / P4-S11）命名碰撞。
    st._trace_enabled = bool(st._agent_cfg.get("iteration_trace_enabled", False))
    st._tracer = None
    if st._trace_enabled:
        try:
            st._tracer = IterationTracer(
                trace_dir=_paths.user_data_dir() / "traces",
                session_id="",
                task_id=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("iteration_tracer_init_failed: %s", exc, exc_info=True)
            st._tracer = None


def _prepare_verify_gate(st: _AgentBuildState) -> None:
    """按配置建核验门；失败时关闭并记日志。"""

    st.verify_gate = None
    try:
        st.verifier_cfg = getattr(getattr(st.cfg, "tools", None), "verifier", None)
        if (
            st.enable_verify_gate
            and st.verifier_cfg is not None
            and st.verifier_cfg.verify_gate_mode != "off"
        ):
            st.patterns_path = _Path(st.verifier_cfg.claim_patterns_file)
            if not st.patterns_path.is_absolute():
                # Resolve relative to backend/ (where the default yaml lives)
                st.patterns_path = _Path(__file__).parent.parent / st.patterns_path
            st.patterns = load_claim_patterns(st.patterns_path)
            # The verifier is part of this Root, so its model authority is the
            # same frozen Session/Root route as every other auxiliary call.
            st._ephemeral_llm = _make_session_aware_llm_call(max_tokens=256)
            st._ephemeral_subagent = make_ephemeral_verifier(st._ephemeral_llm)
            st.verify_gate = VerifyGate(
                extractor=RegexExtractor(st.patterns),
                mode=st.verifier_cfg.verify_gate_mode,
                ephemeral_subagent=st._ephemeral_subagent,
            )
            logger.info(
                "verify_gate_init mode=%s patterns=%d path=%s",
                st.verifier_cfg.verify_gate_mode,
                len(st.patterns),
                st.patterns_path,
            )
            try:
                # 按需导入：测试替身（observability.metrics_sink）
                from observability.metrics_sink import record as _verify_metric

                st._verify_metric = _verify_metric

                st._verify_metric(
                    "verify_gate_init",
                    {
                        "mode": st.verifier_cfg.verify_gate_mode,
                        "patterns_loaded": len(st.patterns),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("verify_gate init failed: %s — disabled", exc, exc_info=True)
        st.verify_gate = None


def _prepare_external_evaluator(st: _AgentBuildState) -> None:
    """按配置建外部评审。"""

    # WI-2.4: construct ExternalEvaluator when flag on + provider available.
    # flag off (default) OR provider=None → None (BC, 0 extra LLM calls).
    st._external_evaluator = None
    st._use_external_evaluator = (
        bool(getattr(st.verifier_cfg, "external_evaluator", False)) if st.verifier_cfg else False
    )
    if st._use_external_evaluator:
        try:
            from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415

            st._EE = _EE

            st._ev_llm_call = _make_session_aware_llm_call(max_tokens=512)
            # FP-3 R-T3 接线：evaluator 仅对高后果目标触发（is_high_consequence_goal
            # 门控），故超时/错误时应保守拦截（返 revise）而非放行 —— 高后果场景
            # 漏放代价远大于误拦。生产构造启用 conservative_on_error（之前默认 False
            # → R-T3「高后果 evaluator 超时保守拦」分支生产从不触发）。
            st._external_evaluator = st._EE(
                llm_call=st._ev_llm_call,
                conservative_on_error=True,
            )
        except Exception as exc:  # noqa: BLE001 — safe-fail
            _log.getLogger(__name__).warning(
                "external_evaluator construction failed: %s — skipping", exc
            )


def _define_pipeline_builders(st: _AgentBuildState) -> None:
    """定义流水线用的核验门与外部评审构造函数。"""

    # ─── Step6 SelfCheckGate 装配（plans/2026-06-24-..., 红队 B3）。
    # 关键：SelfCheckGate **不依赖** tools.verifier 两 flag——用户单开 problem_pipeline.self_check
    # 不开 tools.verifier 时，强制自建一套 verify_gate(mode=strict) + external_evaluator(异体 fresh-context)
    # 供 SelfCheckGate，避免"自检空门"。二者皆不可得 → 降级 pass **但启动期 warning**，不静默空门。
    def _build_pipeline_verify_gate(_scm: str):
        """为 SelfCheckGate 自建 strict 对账门（抽自上方 :979 构造逻辑；不绑 verifier_cfg.mode）。"""
        try:
            _vc = getattr(getattr(st.cfg, "tools", None), "verifier", None)
            _pf = _Path(
                getattr(_vc, "claim_patterns_file", "verify/claim_patterns.yaml")
                if _vc
                else "verify/claim_patterns.yaml"
            )
            if not _pf.is_absolute():
                _pf = _Path(__file__).parent.parent / _pf
            _patterns = load_claim_patterns(_pf)
            _sub = make_ephemeral_verifier(_make_session_aware_llm_call(max_tokens=256))
            return VerifyGate(
                extractor=RegexExtractor(_patterns), mode="strict", ephemeral_subagent=_sub
            )
        except Exception as _e:  # noqa: BLE001
            logger.warning("pipeline_build_verify_gate_failed err=%s", str(_e)[:200], exc_info=True)
            return None

    st._build_pipeline_verify_gate = _build_pipeline_verify_gate

    def _build_pipeline_external_evaluator(_scm: str):
        """异体评分子代理：复用 frozen Root route，但保持独立 evaluator persona。"""
        try:
            from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415

            _evc = _make_session_aware_llm_call(max_tokens=512)
            return _EE(llm_call=_evc, conservative_on_error=True)
        except Exception as _e:  # noqa: BLE001
            logger.warning(
                "pipeline_build_external_evaluator_failed err=%s", str(_e)[:200], exc_info=True
            )
            return None

    st._build_pipeline_external_evaluator = _build_pipeline_external_evaluator


def _prepare_self_check_gate(st: _AgentBuildState) -> None:
    """问题流水线开启时建自检门。"""

    st._self_check_gate = None
    st._pp = getattr(getattr(st.cfg, "features", None), "problem_pipeline", None)
    if (
        st.enable_verify_gate
        and st._pp is not None
        and getattr(st._pp, "enabled", False)
        and getattr(st._pp, "self_check", False)
    ):
        try:
            from deskpet.agent.self_check_gate import SelfCheckGate as _SCG  # noqa: PLC0415

            st._SCG = _SCG

            st._scm = getattr(st._pp, "self_check_model", "") or ""
            st._sc_hetero = bool(getattr(st._pp, "self_check_heterogeneous", True))
            st._sc_verify_gate = st.verify_gate  # tools.verifier 已开 → 复用
            if st._sc_verify_gate is None and st.receipt_store is not None:
                st._sc_verify_gate = st._build_pipeline_verify_gate(st._scm)
            st._sc_external = (
                st._external_evaluator
            )  # tools.verifier.external_evaluator 已开 → 复用
            if st._sc_external is None and st._sc_hetero:
                st._sc_external = st._build_pipeline_external_evaluator(st._scm)
            if st._sc_verify_gate is None and st._sc_external is None:
                logger.warning(
                    "self_check_degraded reason=no_verify_no_evaluator sid_scope=build_agent"
                )
            st._self_check_gate = st._SCG(
                verify_gate=st._sc_verify_gate,
                external_evaluator=st._sc_external,
                heterogeneous_enabled=st._sc_hetero,
            )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("self_check_gate build failed: %s", _exc, exc_info=True)
            st._self_check_gate = None


def _prepare_context_os(st: _AgentBuildState) -> None:
    """上下文系统开关打开时备好投影器、快照与片段存储、压缩模型。"""

    st._context_projector = None
    st._context_snapshot_store_for_loop = None
    st._context_segment_store_for_loop = None
    st._compression_model_resolver_for_loop = None
    st._compression_model = "follow_session"
    st._compression_model_provider = None
    if bool(getattr(getattr(st.cfg, "features", None), "context_os_v1", False)):
        try:
            st._services = service_context
            st._context_snapshot_store_for_loop = st._services.get("context_snapshot_store")
            st._context_segment_store_for_loop = st._services.get("context_segment_store")
            st._compression_model_resolver_for_loop = st._services.get("compression_model_resolver")
            st._context_projector = _build_context_task_projector(
                session_goal_store=st.session_goal_store,
                receipt_store=st.receipt_store,
            )
            st._compression_model = _configured_context_compaction_model(st.cfg)
            st._compression_model_provider = _live_context_compaction_model
        except Exception as _context_os_exc:  # noqa: BLE001
            raise RuntimeError(
                f"context_os_compaction_wiring_failed:{_context_os_exc}"
            ) from _context_os_exc


def _construct_agent_loop(st: _AgentBuildState) -> None:
    """用已备好的部件构造 AgentLoop。"""

    st.result = _AgentLoop(
        llm_registry=st.llm_registry,
        tool_registry=st.tool_registry,
        max_iterations=st.max_iterations,
        completion_probe=st.completion_probe,
        session_todo_getter=st.session_todo_getter,
        max_completion_nudges=st.max_completion_nudges,
        signature_repeat_threshold=st.signature_repeat_threshold,
        context_manager=st.context_manager,
        # ─── WI-T2.1 v3 真接电（修 last-mile P0-1）───
        verify_gate=st.verify_gate,
        receipt_store=st.receipt_store,
        max_verify_nudges=st.nudges,
        # ─── Companion+Code v1 WI-B3 真接电 ───
        # flag OFF 时 store/checker=None → agent_loop 跳过 goal-check（BC）
        session_goal_store=st.session_goal_store,
        goal_checker=st.goal_checker,
        # ─── WI-2.1 结构化反思（flag off = BC）───
        structured_reflection=st.use_structured_reflection,
        # ─── WI-2.4 外部评估器（flag off = BC, None = 0 calls）───
        external_evaluator=st._external_evaluator,
        # ─── WI-4.0 compaction（flag off = compressor=None = BC）───
        compressor=st.compressor,
        context_projector=st._context_projector,
        context_snapshot_store=st._context_snapshot_store_for_loop,
        context_segment_store=st._context_segment_store_for_loop,
        compression_model_resolver=st._compression_model_resolver_for_loop,
        compression_model=st._compression_model,
        compression_model_provider=st._compression_model_provider,
        # ─── WI-1B-2 压缩可观测（flag off = False = BC，压缩路径零额外行为）───
        ctx_observability=st._ctx_observability,
        # ─── FP-5 缺口 5a：WI-4.2 remount + 自动披露（flag off = None = BC）───
        skill_loader=st.skill_loader,
        skill_matcher=st.skill_matcher,
        # ─── WI-OH-4：记忆 self-curation nudge（curator None = BC，不调 nudge）───
        memory_curator=st.memory_curator,
        provider_workload_context_factory=st.provider_workload_context_factory,
        provider_workload_router=st.provider_workload_router,
        trace_store=st.trace_store,
        context_attempt_store=st.context_attempt_store,
        curation_nudge_every_n_turns=int(
            getattr(
                getattr(getattr(st.cfg, "memory", None), "v2", None),
                "curation_nudge_every_n_turns",
                8,
            )
            or 8
        ),
        # ─── WI-4b pre-flush：压缩前把任务态落 L1(跨 session 记任务)。模块级
        # _file_memory(L1048)在 build_agent 调用时已就绪；try 失败则 None(BC)。───
        file_memory=_file_memory,
        force_finish_via_tool_choice=st._ff,
        tracer=st._tracer,
        # ─── 子代理并发驱动 WI-3.3：非阻塞子代理 completion queue（回合边界 drain）。
        # 从 module-global service_context 取（flag subagent_nonblocking OFF 时槽为
        # None → agent_loop 不 drain，BC）。───
        # ─── 七步问题处理流水线 IN-LOOP 闸（plans/2026-06-24-...）。flag off → 全 None/False = BC。
        evidence_gate=st.evidence_gate,
        self_check_gate=st._self_check_gate,
        convergence_report_on_stop=st.convergence_report_on_stop,
        pipeline_problem_type=st.pipeline_problem_type,
        pipeline_needs_investigation=st.pipeline_needs_investigation,
        pipeline_observability=st.pipeline_observability,
        response_quality_gate=st.response_quality_gate,
    )
    st.flow = "stop"
    return
