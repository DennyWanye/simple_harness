"""构造 Agent：组装主循环、校验门、工具与上下文（整段搬家，拆函数在后面的阶段）"""

from __future__ import annotations

import uuid

import paths as _paths

from host.context_projection import (
    _build_context_task_projector,
    _configured_context_compaction_model,
    _live_context_compaction_model,
)
from host.llm_calls import _make_session_aware_llm_call
from host.runtime import logger, service_context
from host.stores_bootstrap import _file_memory


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
    from agent.agent_loop import AgentLoop as _AgentLoop

    # Build VerifyGate from cfg (mode != "off" only — flag OFF stays BC).
    verify_gate = None
    try:
        verifier_cfg = getattr(getattr(cfg, "tools", None), "verifier", None)
        if (
            enable_verify_gate
            and verifier_cfg is not None
            and verifier_cfg.verify_gate_mode != "off"
        ):
            from pathlib import Path as _Path

            from deskpet.agent.verify_gate import (
                RegexExtractor,
                VerifyGate,
                load_claim_patterns,
                make_ephemeral_verifier,
            )

            patterns_path = _Path(verifier_cfg.claim_patterns_file)
            if not patterns_path.is_absolute():
                # Resolve relative to backend/ (where the default yaml lives)
                patterns_path = _Path(__file__).parent / patterns_path
            patterns = load_claim_patterns(patterns_path)
            # The verifier is part of this Root, so its model authority is the
            # same frozen Session/Root route as every other auxiliary call.
            _ephemeral_llm = _make_session_aware_llm_call(max_tokens=256)
            _ephemeral_subagent = make_ephemeral_verifier(_ephemeral_llm)
            verify_gate = VerifyGate(
                extractor=RegexExtractor(patterns),
                mode=verifier_cfg.verify_gate_mode,
                ephemeral_subagent=_ephemeral_subagent,
            )
            logger.info(
                "verify_gate_init mode=%s patterns=%d path=%s",
                verifier_cfg.verify_gate_mode,
                len(patterns),
                patterns_path,
            )
            try:
                from observability.metrics_sink import record as _verify_metric

                _verify_metric(
                    "verify_gate_init",
                    {
                        "mode": verifier_cfg.verify_gate_mode,
                        "patterns_loaded": len(patterns),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("verify_gate init failed: %s — disabled", exc)
        verify_gate = None

    # Pull receipt_store (None when emit_receipts=False).
    receipt_store = None
    try:
        receipt_store = receipt_store_getter()
    except Exception as exc:  # noqa: BLE001
        logger.warning("receipt_store getter failed: %s — verify_gate disabled", exc)
        receipt_store = None
        verify_gate = None  # gate without ledger = blocks every end_turn

    nudges = 2
    try:
        nudges = int(getattr(verifier_cfg, "max_verify_nudges", 2)) if verifier_cfg else 2
    except Exception:  # noqa: BLE001
        nudges = 2

    # WI-2.1: read structured_reflection flag from verifier config (default False = BC)
    use_structured_reflection = (
        bool(getattr(verifier_cfg, "structured_reflection", False)) if verifier_cfg else False
    )

    # WI-1B-2 压缩可观测: 从 cfg.features.ctx_observability 读 flag (默认 False = BC)。
    # OFF 时 _AgentLoop 压缩路径不 emit metrics / 不 yield ContextCompactedEvent。
    _features_cfg = getattr(cfg, "features", None)
    _ctx_observability = (
        bool(getattr(_features_cfg, "ctx_observability", False)) if _features_cfg else False
    )

    # WI-2.4: construct ExternalEvaluator when flag on + provider available.
    # flag off (default) OR provider=None → None (BC, 0 extra LLM calls).
    _external_evaluator = None
    _use_external_evaluator = (
        bool(getattr(verifier_cfg, "external_evaluator", False)) if verifier_cfg else False
    )
    if _use_external_evaluator:
        try:
            from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415

            _ev_llm_call = _make_session_aware_llm_call(max_tokens=512)
            # FP-3 R-T3 接线：evaluator 仅对高后果目标触发（is_high_consequence_goal
            # 门控），故超时/错误时应保守拦截（返 revise）而非放行 —— 高后果场景
            # 漏放代价远大于误拦。生产构造启用 conservative_on_error（之前默认 False
            # → R-T3「高后果 evaluator 超时保守拦」分支生产从不触发）。
            _external_evaluator = _EE(
                llm_call=_ev_llm_call,
                conservative_on_error=True,
            )
        except Exception as exc:  # noqa: BLE001 — safe-fail
            import logging as _log

            _log.getLogger(__name__).warning(
                "external_evaluator construction failed: %s — skipping", exc
            )

    _cfg_raw = getattr(cfg, "raw", None)
    _agent_cfg = _cfg_raw.get("agent", {}) if isinstance(_cfg_raw, dict) else {}
    _ff = bool(_agent_cfg.get("force_finish_tool_choice", True))
    # WI-2: 用 iteration_trace_enabled（而非 trace_enabled）避免与
    # [context.assembler].trace_enabled（Context Trace UI / P4-S11）命名碰撞。
    _trace_enabled = bool(_agent_cfg.get("iteration_trace_enabled", False))
    _tracer = None
    if _trace_enabled:
        try:
            from agent.trace import IterationTracer

            _tracer = IterationTracer(
                trace_dir=_paths.user_data_dir() / "traces",
                session_id="",
                task_id=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("iteration_tracer_init_failed: %s", exc)
            _tracer = None

    # ─── Step6 SelfCheckGate 装配（plans/2026-06-24-..., 红队 B3）。
    # 关键：SelfCheckGate **不依赖** tools.verifier 两 flag——用户单开 problem_pipeline.self_check
    # 不开 tools.verifier 时，强制自建一套 verify_gate(mode=strict) + external_evaluator(异体 fresh-context)
    # 供 SelfCheckGate，避免"自检空门"。二者皆不可得 → 降级 pass **但启动期 warning**，不静默空门。
    def _build_pipeline_verify_gate(_scm: str):
        """为 SelfCheckGate 自建 strict 对账门（抽自上方 :979 构造逻辑；不绑 verifier_cfg.mode）。"""
        try:
            from pathlib import Path as _Path

            from deskpet.agent.verify_gate import (
                RegexExtractor,
                VerifyGate,
                load_claim_patterns,
                make_ephemeral_verifier,
            )

            _vc = getattr(getattr(cfg, "tools", None), "verifier", None)
            _pf = _Path(
                getattr(_vc, "claim_patterns_file", "verify/claim_patterns.yaml")
                if _vc
                else "verify/claim_patterns.yaml"
            )
            if not _pf.is_absolute():
                _pf = _Path(__file__).parent / _pf
            _patterns = load_claim_patterns(_pf)
            _sub = make_ephemeral_verifier(_make_session_aware_llm_call(max_tokens=256))
            return VerifyGate(
                extractor=RegexExtractor(_patterns), mode="strict", ephemeral_subagent=_sub
            )
        except Exception as _e:  # noqa: BLE001
            logger.warning("pipeline_build_verify_gate_failed err=%s", str(_e)[:200])
            return None

    def _build_pipeline_external_evaluator(_scm: str):
        """异体评分子代理：复用 frozen Root route，但保持独立 evaluator persona。"""
        try:
            from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415

            _evc = _make_session_aware_llm_call(max_tokens=512)
            return _EE(llm_call=_evc, conservative_on_error=True)
        except Exception as _e:  # noqa: BLE001
            logger.warning("pipeline_build_external_evaluator_failed err=%s", str(_e)[:200])
            return None

    _self_check_gate = None
    _pp = getattr(getattr(cfg, "features", None), "problem_pipeline", None)
    if (
        enable_verify_gate
        and _pp is not None
        and getattr(_pp, "enabled", False)
        and getattr(_pp, "self_check", False)
    ):
        try:
            from deskpet.agent.self_check_gate import SelfCheckGate as _SCG  # noqa: PLC0415

            _scm = getattr(_pp, "self_check_model", "") or ""
            _sc_hetero = bool(getattr(_pp, "self_check_heterogeneous", True))
            _sc_verify_gate = verify_gate  # tools.verifier 已开 → 复用
            if _sc_verify_gate is None and receipt_store is not None:
                _sc_verify_gate = _build_pipeline_verify_gate(_scm)
            _sc_external = _external_evaluator  # tools.verifier.external_evaluator 已开 → 复用
            if _sc_external is None and _sc_hetero:
                _sc_external = _build_pipeline_external_evaluator(_scm)
            if _sc_verify_gate is None and _sc_external is None:
                logger.warning(
                    "self_check_degraded reason=no_verify_no_evaluator sid_scope=build_agent"
                )
            _self_check_gate = _SCG(
                verify_gate=_sc_verify_gate,
                external_evaluator=_sc_external,
                heterogeneous_enabled=_sc_hetero,
            )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("self_check_gate build failed: %s", _exc)
            _self_check_gate = None

    if trace_store is None and getattr(getattr(cfg, "workflows", None), "trace_enabled", False):
        workflow_service = service_context.get("workflow_service")
        trace_store = getattr(workflow_service, "trace_store", None)

    _context_projector = None
    _context_snapshot_store_for_loop = None
    _context_segment_store_for_loop = None
    _compression_model_resolver_for_loop = None
    _compression_model = "follow_session"
    _compression_model_provider = None
    if bool(getattr(getattr(cfg, "features", None), "context_os_v1", False)):
        try:
            _services = service_context
            _context_snapshot_store_for_loop = _services.get("context_snapshot_store")
            _context_segment_store_for_loop = _services.get("context_segment_store")
            _compression_model_resolver_for_loop = _services.get("compression_model_resolver")
            _context_projector = _build_context_task_projector(
                session_goal_store=session_goal_store,
                receipt_store=receipt_store,
            )
            _compression_model = _configured_context_compaction_model(cfg)
            _compression_model_provider = _live_context_compaction_model
        except Exception as _context_os_exc:  # noqa: BLE001
            raise RuntimeError(
                f"context_os_compaction_wiring_failed:{_context_os_exc}"
            ) from _context_os_exc

    return _AgentLoop(
        llm_registry=llm_registry,
        tool_registry=tool_registry,
        max_iterations=max_iterations,
        completion_probe=completion_probe,
        session_todo_getter=session_todo_getter,
        max_completion_nudges=max_completion_nudges,
        signature_repeat_threshold=signature_repeat_threshold,
        context_manager=context_manager,
        # ─── WI-T2.1 v3 真接电（修 last-mile P0-1）───
        verify_gate=verify_gate,
        receipt_store=receipt_store,
        max_verify_nudges=nudges,
        # ─── Companion+Code v1 WI-B3 真接电 ───
        # flag OFF 时 store/checker=None → agent_loop 跳过 goal-check（BC）
        session_goal_store=session_goal_store,
        goal_checker=goal_checker,
        # ─── WI-2.1 结构化反思（flag off = BC）───
        structured_reflection=use_structured_reflection,
        # ─── WI-2.4 外部评估器（flag off = BC, None = 0 calls）───
        external_evaluator=_external_evaluator,
        # ─── WI-4.0 compaction（flag off = compressor=None = BC）───
        compressor=compressor,
        context_projector=_context_projector,
        context_snapshot_store=_context_snapshot_store_for_loop,
        context_segment_store=_context_segment_store_for_loop,
        compression_model_resolver=_compression_model_resolver_for_loop,
        compression_model=_compression_model,
        compression_model_provider=_compression_model_provider,
        # ─── WI-1B-2 压缩可观测（flag off = False = BC，压缩路径零额外行为）───
        ctx_observability=_ctx_observability,
        # ─── FP-5 缺口 5a：WI-4.2 remount + 自动披露（flag off = None = BC）───
        skill_loader=skill_loader,
        skill_matcher=skill_matcher,
        # ─── WI-OH-4：记忆 self-curation nudge（curator None = BC，不调 nudge）───
        memory_curator=memory_curator,
        provider_workload_context_factory=provider_workload_context_factory,
        provider_workload_router=provider_workload_router,
        trace_store=trace_store,
        context_attempt_store=context_attempt_store,
        curation_nudge_every_n_turns=int(
            getattr(
                getattr(getattr(cfg, "memory", None), "v2", None), "curation_nudge_every_n_turns", 8
            )
            or 8
        ),
        # ─── WI-4b pre-flush：压缩前把任务态落 L1(跨 session 记任务)。模块级
        # _file_memory(L1048)在 build_agent 调用时已就绪；try 失败则 None(BC)。───
        file_memory=_file_memory,
        force_finish_via_tool_choice=_ff,
        tracer=_tracer,
        # ─── 子代理并发驱动 WI-3.3：非阻塞子代理 completion queue（回合边界 drain）。
        # 从 module-global service_context 取（flag subagent_nonblocking OFF 时槽为
        # None → agent_loop 不 drain，BC）。───
        # ─── 七步问题处理流水线 IN-LOOP 闸（plans/2026-06-24-...）。flag off → 全 None/False = BC。
        evidence_gate=evidence_gate,
        self_check_gate=_self_check_gate,
        convergence_report_on_stop=convergence_report_on_stop,
        pipeline_problem_type=pipeline_problem_type,
        pipeline_needs_investigation=pipeline_needs_investigation,
        pipeline_observability=pipeline_observability,
        response_quality_gate=response_quality_gate,
    )
