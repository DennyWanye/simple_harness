# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations
import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

_VALID_SERVICES = frozenset({
    "llm_engine", "asr_engine", "tts_engine",
    "vad_engine", "agent_engine", "memory_store", "tool_router",
    # P2-1-S8: BillingLedger registered so per-session handlers can read the
    # daily-budget ledger without re-plumbing config.
    "billing_ledger",
    # --- P4 Poseidon agent harness (S12 wire-in) -----------------------------
    # Optional: registered only when `config.agent.enabled=true` (ContextAssembler,
    # SkillLoader, MemoryManager) or `config.mcp.enabled=true` (MCPManager).
    # p4_ipc.py handlers tolerate any of these being None via graceful fallback.
    "context_assembler",   # ContextAssembler instance (recent_decisions, assemble)
    "skill_loader",        # SkillLoader with hot-reload + builtin skills
    "memory_manager",      # L1+L2+L3 MemoryManager facade
    "memory_recall_query",  # owner-scoped zero-write Retriever port
    "memory_recall_scope_resolver",  # durable Run -> owner memory scope
    "conversation_memory",  # Memory SDK conversation query/sink adapter
    "sdk_context_staging",  # durable private provider-context staging authority
    "sdk_context_source_repository",  # immutable non-Memory Context source authority
    "memory_identity_resolver",  # trusted AgentIdentity session binding
    "memory_identity_authority",  # validated product auth -> AgentIdentity
    "frozen_skill_instruction_resolver",  # Run-catalog Skill projection
    "managed_skill_discovery_projection",  # discovery-only managed Skill view
    "file_memory",         # Direct L1 handle (also reachable via manager.file_memory)
    "mcp_manager",         # MCPManager (stdio/sse/streamable_http clients)
    # --- P4-S16 公开化（之前挂在 _p4_* 私有属性上）---------------------------
    # Embedder / VectorWorker / SessionDB 在 S15 wire-in 时是直接挂私有属性，
    # 这里转成正式 register 路径，避免 getattr(sc, "_p4_xxx") 这种隐式约定。
    "embedder",            # BGE-M3 Embedder (with mock fallback)
    "vector_worker",       # VectorWorker draining embedding queue → vec0
    "image_worker",        # ImageGenerationWorker (async generate_image)
    "session_db",          # P4 canonical L2 SessionDB (state.db)
    # --- P4-S22 Code Mode ----------------------------------------------------
    "code_mode",           # CodeModeManager — per-base-session enable map
    # --- P5-S1 Pet Supervisor ------------------------------------------------
    "session_activity",    # SessionActivityStore — per-sid recent events + sig window
    "watchdog",            # WatchdogLoop — periodic supervisor scan
    "nudge_queue",         # NudgeQueue — supervisor hint injection queue
    "supervisor",          # SupervisorAgent — LLM-based diagnosis dispatcher
    # --- P5-S2 Self-Healing Harness ------------------------------------------
    "auto_resume",         # AutoResumeOrchestrator — closes supervisor loop
    "tool_circuit_breaker",  # ToolCircuitBreaker — per-(sid, tool) 3-state breaker
    # --- P5-S2 multi-provider-management Phase 2 -----------------------------
    "provider_registry",   # LLMProviderRegistry — owns [[llm.providers]] list
    "provider_routing_readiness",  # fail-closed startup latch
    # --- Stage 2 WI-S2.1a / E3 v2 — MemoryPanel facts view 桥接 ---------------
    "facts_store",         # FactsStore — list_active / mark_forgotten / restore_from_undo
    # --- Companion+Code v1 — SessionGoal + GoalChecker (main.py:1068-1069) ---
    # 2026-05-30 bug fix：之前缺这两项导致 register() 抛 ValueError → boot
    # warning `p4_services_registration_failed` + production UI 弹出红色错误条
    # "Unknown service 'session_goal_store'"。flag `features.goal_mode` 默认
    # OFF 时仍 register(None) 占位（main.py:1068 没 try/except 包）→ 必须在
    # whitelist 里。
    "session_goal_store",  # SessionGoalStore — companion+code goal tracking
    "goal_checker",        # GoalChecker — LLM-based goal completion check
    # --- goal-completion FP-5 WI-4.0 — compaction 接通 AgentLoop ---------------
    # 2026-06-05 真机 bug fix：main.py:1189 无条件 register("context_compressor")，
    # 缺白名单 → 启动 register 抛 ValueError(被 boot try/except 吞成 warning)，
    # 但 chat 派发处 get("context_compressor") 抛 "Unknown service" → code-mode
    # 任务全崩。flag OFF 时仍 register(None) 占位，故必须在白名单里。
    "context_compressor",  # ContextCompressor — WI-4.0 loop 内 compaction
    # --- goal-completion FP-5 接线修复 (2026-06-06) — 5 处跨层 wiring 断裂 -----
    # 独立验收发现 codify hook (main.py:5836/5838/5859) + remount 接线引用了 3 个
    # 未白名单的 service → get() 抛 ValueError("Unknown service") → 被 boot/chat
    # try/except 吞成 debug log → WI-4.1/4.2/4.3 真机完全 no-op。flag OFF（默认）
    # 时 lifespan 仍 register(None) 占位（保 BC + 让 get() 不抛），故必须在白名单里。
    "skill_matcher",       # SkillMatcher (WI-4.1) — embedding 相似度披露 + remount
    # --- superpowers Layer 1B — 偏好记忆（计划/意图，BGE-M3 语义匹配）---------
    "preference_memory",   # PreferenceMemory — plan-confirm 自动确认 + 意图记忆
    # --- Option A (2026-06-05) — 瘦包首启模型下载 ----------------------------
    "model_provisioner",   # ModelProvisioner — 首启从 hf-mirror 下载缺失模型
    # --- 子代理并发驱动 (plans/2026-06-21-subagent-concurrency-driver/) -------
    # flag OFF（默认）时 lifespan 不 register 这些（None 占位无需），但加进白名单
    # 防 get()/register() 抛 "Unknown service"（仿 session_goal_store 注释 :48）。
    "subagent_scheduler",  # SubagentScheduler — lane-aware 有界并发调度
    "team_store",          # TeamStore — spawn_team 共享任务池/mailbox/permission
    "task_graph_store",    # TaskGraphStore — DAG 依赖排序任务图
    # --- WI-OH-4 记忆 self-curation nudge (plans/2026-06-21-ppt-deepresearch-pro) ---
    # flag memory.v2.curation_nudge OFF（默认）时 lifespan 不 register（None 占位
    # 无需），但加进白名单防 get()/register() 抛 "Unknown service"（仿上方注释）。
    "memory_curator",      # MemoryCurator — agent 主动判断该不该长期记
    # --- WI-TG-2 审批 UX 聚合 (plans/2026-06-22-context-and-agent-optimization) ---
    # PermissionGate 注册成 service 让 p4_ipc 的只读「列 pending」接口拿到它，
    # 供 ApprovalCenterPanel 聚合展示。只读 — 不改 gate 决策路径。
    "permission_gate",     # PermissionGate — 只读 list_pending 供审批聚合面板
    # --- 七步问题处理流水线 (plans/2026-06-24-problem-handling-pipeline-maoxuan/) ---
    # flag features.problem_pipeline.enabled OFF 时 lifespan 仍 register(None)
    # 占位（见 main.py lifespan 的 else 分支）—— 否则 get()/register() 抛
    # "Unknown service"（仿 session_goal_store 注释 :48 / context_compressor :57 的占位约定）。
    "problem_pipeline",                  # ProblemHandlingPipeline（PRE-LOOP 编排器）
    "pipeline_evidence_gate",            # EvidenceGate（Step2 取证门，build_agent caller 透传）
    "pipeline_self_check_gate",          # 预留：第一期由 build_agent 内构造，service 仅占位
    "pipeline_convergence_controller",   # 预留：第一期由 AgentLoop 内构造，service 仅占位
    # Durable graph runtime and unified Trace/Replay/Eval facade.
    "workflow_service",
    "run_execution_fence_acquirer",
    "provider_invocation_coordinator",
    "provider_workload_router",
    "provider_workload_audit",
    "harness_public_read_service",
    "session_terminal_projection_gate",
    # Slice B: the sole immutable publication for the closed-ingress SDK stack.
    "sdk_runtime_ready",
    # SDK Context-authority cutover: immutable process publications assembled
    # before the runtime starts.  These are explicit ServiceContext slots so a
    # cold boot cannot fail while publishing an otherwise valid SDK stack.
    "sdk_runtime_catalog",
    "sdk_provider_binding_resolver",
    "sdk_tool_authority_registry",
    "sdk_runtime_tool_inventory",
    "sdk_prepared_authorization_policy",
    # Capability-pack, policy, TaskGrant, and authorization saga state are
    # product-owned in data/product_state.db.  They must never bind the SDK
    # execution database; the platform separately owns local worker lifetimes.
    "capability_store",
    "capability_platform",
    "capability_center",
    "capability_builder_host",
    "authorization_runtime",
    "admission_task_grant_runtime",
    "capability_refresh_snapshots",
    "capability_refresh_staging",
    "capability_refresh_service",
    # Process-owned async retrieval gateway shared by web_search and research.
    "search_gateway",
    # --- Context OS V1 ------------------------------------------------------
    "tool_capability_scope_store",
    "tool_capability_resolver",
    "context_request_planner",
    "context_snapshot_store",
    "context_segment_store",
    "context_page_in_store",
    "context_attempt_store",
    "compression_model_resolver",
    # Host-owned Context OS task projection hooks used by ProductTurnPreparer.
    # Omitting either slot aborts the P4 bootstrap block and leaves the
    # assembler unavailable while context_os_v1 remains enabled.
    "project_initial_context_snapshot",
    "attach_task_snapshot_to_request",
    # Companion Task 2: host-owned gate; absent/unready means retryable chat
    # rejection rather than guessing a previously active human identity.
    "companion_identity_gate",
    "companion_profile_coordinator",
    "companion_preference_resolver_dormant",
    "companion_clock",
    "companion_runtime",
    "companion_ingress_dispatcher",
    "companion_growth_pipeline",
    "companion_reminder_service",
    "companion_reminder_authority_selector",
    "companion_reminder_registry_reconciler",
    "growth_authority_cutover",
    # Task 12: durable notification projection/detail query and the exact
    # trusted action-decision service seams used by the main message window.
    "companion_detail_query",
    "companion_notification_service",
    "companion_projection_visibility",
    "companion_growth_action_decision_service",
    "companion_growth_evaluation_decision_service",
    "companion_growth_activation_decision_service",
    "companion_rollback_service",
    "companion_forget_service",
    "window_control_credential_verifier",
})

@dataclass
class ServiceContext:
    llm_engine: Any | None = None
    asr_engine: Any | None = None
    tts_engine: Any | None = None
    vad_engine: Any | None = None
    agent_engine: Any | None = None
    memory_store: Any | None = None
    tool_router: Any | None = None
    billing_ledger: Any | None = None
    # --- P4 Poseidon slots ---------------------------------------------------
    context_assembler: Any | None = None
    skill_loader: Any | None = None
    memory_manager: Any | None = None
    conversation_memory: Any | None = None
    sdk_context_staging: Any | None = None
    sdk_context_source_repository: Any | None = None
    memory_identity_resolver: Any | None = None
    memory_identity_authority: Any | None = None
    memory_recall_query: Any | None = None
    memory_recall_scope_resolver: Any | None = None
    frozen_skill_instruction_resolver: Any | None = None
    managed_skill_discovery_projection: Any | None = None
    file_memory: Any | None = None
    mcp_manager: Any | None = None
    # --- P4-S16 公开化 -------------------------------------------------------
    embedder: Any | None = None
    vector_worker: Any | None = None
    session_db: Any | None = None
    # --- P4-S22 Code Mode ----------------------------------------------------
    code_mode: Any | None = None
    # --- P5-S1 Pet Supervisor ------------------------------------------------
    session_activity: Any | None = None
    watchdog: Any | None = None
    nudge_queue: Any | None = None
    supervisor: Any | None = None
    # --- P5-S2 Self-Healing Harness ------------------------------------------
    auto_resume: Any | None = None
    tool_circuit_breaker: Any | None = None
    # --- P5-S2 multi-provider-management Phase 2 -----------------------------
    provider_registry: Any | None = None
    provider_routing_readiness: Any | None = None
    # --- Stage 2 WI-S2.1a / E3 v2 -------------------------------------------
    facts_store: Any | None = None
    # --- Companion+Code v1 --------------------------------------------------
    session_goal_store: Any | None = None
    goal_checker: Any | None = None
    # --- superpowers Layer 1B ------------------------------------------------
    preference_memory: Any | None = None
    # --- Option A — 瘦包首启模型下载 -----------------------------------------
    model_provisioner: Any | None = None
    # --- goal-completion FP-5 接线修复 (2026-06-06) -------------------------
    skill_matcher: Any | None = None
    # --- 子代理并发驱动 (plans/2026-06-21-subagent-concurrency-driver/) -------
    subagent_scheduler: Any | None = None
    team_store: Any | None = None
    task_graph_store: Any | None = None
    # --- WI-OH-4 记忆 self-curation nudge ------------------------------------
    memory_curator: Any | None = None
    # --- WI-TG-2 审批 UX 聚合 -------------------------------------------------
    permission_gate: Any | None = None
    # --- 七步问题处理流水线 (plans/2026-06-24-problem-handling-pipeline-maoxuan/) ---
    problem_pipeline: Any | None = None
    pipeline_evidence_gate: Any | None = None
    pipeline_self_check_gate: Any | None = None
    pipeline_convergence_controller: Any | None = None
    workflow_service: Any | None = None
    run_execution_fence_acquirer: Any | None = None
    provider_invocation_coordinator: Any | None = None
    provider_workload_router: Any | None = None
    provider_workload_audit: Any | None = None
    harness_public_read_service: Any | None = None
    session_terminal_projection_gate: Any | None = None
    sdk_runtime_ready: Any | None = None
    sdk_runtime_catalog: Any | None = None
    sdk_provider_binding_resolver: Any | None = None
    sdk_tool_authority_registry: Any | None = None
    sdk_runtime_tool_inventory: Any | None = None
    sdk_prepared_authorization_policy: Any | None = None
    capability_store: Any | None = None
    capability_platform: Any | None = None
    capability_center: Any | None = None
    capability_builder_host: Any | None = None
    authorization_runtime: Any | None = None
    admission_task_grant_runtime: Any | None = None
    capability_refresh_snapshots: Any | None = None
    capability_refresh_staging: Any | None = None
    capability_refresh_service: Any | None = None
    search_gateway: Any | None = None
    tool_capability_scope_store: Any | None = None
    tool_capability_resolver: Any | None = None
    context_request_planner: Any | None = None
    context_snapshot_store: Any | None = None
    context_segment_store: Any | None = None
    context_page_in_store: Any | None = None
    context_attempt_store: Any | None = None
    compression_model_resolver: Any | None = None
    project_initial_context_snapshot: Any | None = None
    attach_task_snapshot_to_request: Any | None = None
    companion_identity_gate: Any | None = None
    companion_profile_coordinator: Any | None = None
    companion_preference_resolver_dormant: Any | None = None
    companion_clock: Any | None = None
    companion_runtime: Any | None = None
    companion_ingress_dispatcher: Any | None = None
    companion_growth_pipeline: Any | None = None
    companion_reminder_service: Any | None = None
    companion_reminder_authority_selector: Any | None = None
    companion_reminder_registry_reconciler: Any | None = None
    growth_authority_cutover: Any | None = None
    companion_detail_query: Any | None = None
    companion_notification_service: Any | None = None
    companion_projection_visibility: Any | None = None
    companion_growth_action_decision_service: Any | None = None
    companion_growth_evaluation_decision_service: Any | None = None
    companion_growth_activation_decision_service: Any | None = None
    companion_rollback_service: Any | None = None
    companion_forget_service: Any | None = None
    window_control_credential_verifier: Any | None = None

    def register(self, name: str, provider: Any) -> None:
        if name not in _VALID_SERVICES:
            raise ValueError(f"Unknown service '{name}'. Valid: {sorted(_VALID_SERVICES)}")
        setattr(self, name, provider)

    def create_session(self) -> ServiceContext:
        return copy.deepcopy(self)

    def get(self, name: str) -> Any | None:
        if name not in _VALID_SERVICES:
            raise ValueError(f"Unknown service '{name}'. Valid: {sorted(_VALID_SERVICES)}")
        return getattr(self, name, None)

    def snapshot(self) -> MappingProxyType:
        """Freeze the current public service bindings for one product turn."""

        return MappingProxyType({
            name: getattr(self, name, None)
            for name in _VALID_SERVICES
        })
