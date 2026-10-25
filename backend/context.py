# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

_VALID_SERVICES = frozenset(
    {
        "llm_engine",
        "agent_engine",
        "tool_router",
        # P2-1-S8: BillingLedger registered so per-session handlers can read the
        # daily-budget ledger without re-plumbing config.
        "billing_ledger",
        # --- P4 Poseidon agent harness (S12 wire-in) -----------------------------
        # Optional: registered only when `config.agent.enabled=true` (ContextAssembler,
        # SkillLoader, MemoryManager) or `config.mcp.enabled=true` (MCPManager).
        # p4_ipc.py handlers tolerate any of these being None via graceful fallback.
        "context_assembler",  # ContextAssembler instance (recent_decisions, assemble)
        "skill_loader",  # SkillLoader with hot-reload + builtin skills
        # 2026-09-10 删记忆 SDK：memory_manager / memory_recall_query /
        # memory_recall_scope_resolver / memory_facts_surface /
        # human_memory_v7_runtime / sdk_evidence_authority /
        # sdk_memory_analysis_executor / sdk_memory_ingestion_outbox 八个槽位
        # 已无任何 register/get，一并从白名单与 ServiceContext 字段里删除。
        # 仍保留的记忆相关名字（embedder / vector_worker / memory_identity_* /
        # memory_display_invalidation / prospective_occurrence_coordinator /
        # sdk_typed_context_use_authority；2026-10-17 阶段 8K 又删了只读不写的
        # file_memory、memory_curator、memory_store、facts_store、preference_memory、
        # conversation_memory 等槽位）
        # 仍有 register 或 get 调用点（多数是恒 None 的占位），删掉会让 get()/
        # register() 抛 "Unknown service"（2026-05-30 / 2026-06-05 两次事故的形态）。
        "sdk_context_staging",  # durable private provider-context staging authority
        "sdk_context_source_repository",  # immutable non-Memory Context source authority
        "memory_identity_resolver",  # trusted AgentIdentity session binding
        "memory_identity_authority",  # validated product auth -> AgentIdentity
        "frozen_skill_instruction_resolver",  # Run-catalog Skill projection
        "legacy_frozen_skill_instruction_resolver",  # legacy-only fallback
        "managed_skill_discovery_projection",  # discovery-only managed Skill view
        "mcp_manager",  # MCPManager (stdio/sse/streamable_http clients)
        # --- 公开化（之前挂在 _p4_* 私有属性上）---------------------------
        # Embedder / VectorWorker / SessionDB 在 S15 wire-in 时是直接挂私有属性，
        # 这里转成正式 register 路径，避免 getattr(sc, "_p4_xxx") 这种隐式约定。
        "embedder",  # BGE-M3 Embedder (with mock fallback)
        "vector_worker",  # VectorWorker draining embedding queue → vec0
        "session_db",  # P4 canonical L2 SessionDB (state.db)
        # --- Pet Supervisor ------------------------------------------------
        "session_activity",  # SessionActivityStore — per-sid recent events + sig window
        "watchdog",  # WatchdogLoop — periodic supervisor scan
        "supervisor",  # SupervisorAgent — LLM-based diagnosis dispatcher
        # --- Self-Healing Harness ------------------------------------------
        "tool_circuit_breaker",  # ToolCircuitBreaker — per-(sid, tool) 3-state breaker
        # --- multi-provider-management ------------------------------------
        "provider_registry",  # LLMProviderRegistry — owns [[llm.providers]] list
        "provider_routing_readiness",  # fail-closed startup latch
        # --- 子代理并发驱动 (plans/2026-06-21-subagent-concurrency-driver/) -------
        # flag OFF（默认）时 lifespan 不 register（None 占位无需），但加进白名单
        # 防 get()/register() 抛 "Unknown service"。
        "subagent_scheduler",  # SubagentScheduler — lane-aware 有界并发调度
        # --- WI-TG-2 审批 UX 聚合 (plans/2026-06-22-context-and-agent-optimization) ---
        # PermissionGate 注册成 service 让 p4_ipc 的只读「列 pending」接口拿到它，
        # 供 ApprovalCenterPanel 聚合展示。只读 — 不改 gate 决策路径。
        "permission_gate",  # PermissionGate — 只读 list_pending 供审批聚合面板
        # 执行账本（workflow.db 的 execution_* / capability_* 表），全产品共用。
        "execution_uow",
        "run_execution_fence_acquirer",
        "provider_invocation_coordinator",
        "provider_workload_router",
        "provider_workload_audit",
        "terminal_operation_audit",
        "terminal_operation_audit_status",
        "harness_public_read_service",
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
        "sdk_run_context_authority",
        "sdk_typed_context_use_authority",
        "prospective_occurrence_coordinator",
        "sdk_runtime_decision_sink",
        "sdk_task_execution_authority",
        # S5b: EffectGate (Task 1), closure instruction reader (Task 3 review F-2) and
        # the Host↔Memory async face (Task 4) are explicit composition slots too.
        "sdk_effect_gate",
        "sdk_closure_instruction_reader",
        # MM-D2（2026-09-09）：绑定挑战签发/决定落库后广播 content-free 显示失效，供 PrimaryWorkspaceBindings 刷新。
        "memory_display_invalidation",
        # F-Z1（2026-09-09）：读工具调用期门（WorkspaceReadGate）。
        "sdk_workspace_read_gate",
        "human_memory_host_service_factory",
        "human_memory_binding_append_authority",
        "human_memory_recovery_lifecycle",
        "human_memory_foreground_scheduler_wake",
        "human_memory_foreground_runtime_execution_authority",
        # Capability-pack execution state, including the complete Project-scoped
        # Skill-install aggregate, is owned by the execution/Manager database.
        # ProductState contributes only the immutable authorization receipt.
        "capability_store",
        "capability_platform",
        "project_skill_install_service",
        "project_skill_discovery_service",
        "skill_install_runtime_verifier",
        "skill_install_verification_driver_factory",
        "capability_center",
        "capability_builder_host",
        "authorization_runtime",
        "admission_task_grant_runtime",
        "capability_refresh_snapshots",
        "capability_refresh_staging",
        "capability_refresh_service",
        # Shared Project-scoped Skill install application service. Capability
        # bootstrap publishes it before the SDK Runtime freezes its tool catalog.
        "project_skill_install_service",
        "project_skill_install_settings_authorizer",
        "skill_install_runtime_verifier",
        # Process-owned async retrieval gateway shared by web_search and research.
        "search_gateway",
        # --- Context OS V1 ------------------------------------------------------
        "tool_capability_scope_store",
        "tool_capability_resolver",
        "context_page_in_store",
        "project_binding_service",
        "session_creation_service",
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
        "companion_notification_service",
        "window_control_credential_verifier",
        # 2026-09-11 Agent 编排接入（plans/2026-09-11-orchestrator-host-integration，用户
        # Phase3 P3.1）：编排服务与 mission_changed 推送。未登记会让 register 抛
        # "Unknown service" 并使启动失败（2026-09-09 事故形态）。
        "orchestration",
    }
)


@dataclass
class ServiceContext:
    llm_engine: Any | None = None
    agent_engine: Any | None = None
    tool_router: Any | None = None
    billing_ledger: Any | None = None
    # --- P4 Poseidon slots ---------------------------------------------------
    context_assembler: Any | None = None
    skill_loader: Any | None = None
    sdk_context_staging: Any | None = None
    sdk_context_source_repository: Any | None = None
    memory_identity_resolver: Any | None = None
    memory_identity_authority: Any | None = None
    frozen_skill_instruction_resolver: Any | None = None
    legacy_frozen_skill_instruction_resolver: Any | None = None
    managed_skill_discovery_projection: Any | None = None
    mcp_manager: Any | None = None
    # --- 公开化 -------------------------------------------------------
    embedder: Any | None = None
    vector_worker: Any | None = None
    session_db: Any | None = None
    # --- Pet Supervisor ------------------------------------------------
    session_activity: Any | None = None
    watchdog: Any | None = None
    supervisor: Any | None = None
    # --- Self-Healing Harness ------------------------------------------
    tool_circuit_breaker: Any | None = None
    # --- multi-provider-management ------------------------------------
    provider_registry: Any | None = None
    provider_routing_readiness: Any | None = None
    # --- 子代理并发驱动 (plans/2026-06-21-subagent-concurrency-driver/) -------
    subagent_scheduler: Any | None = None
    # --- WI-TG-2 审批 UX 聚合 -------------------------------------------------
    permission_gate: Any | None = None
    execution_uow: Any | None = None
    run_execution_fence_acquirer: Any | None = None
    provider_invocation_coordinator: Any | None = None
    provider_workload_router: Any | None = None
    provider_workload_audit: Any | None = None
    terminal_operation_audit: Any | None = None
    terminal_operation_audit_status: str | None = None
    harness_public_read_service: Any | None = None
    sdk_runtime_ready: Any | None = None
    sdk_runtime_catalog: Any | None = None
    sdk_provider_binding_resolver: Any | None = None
    sdk_tool_authority_registry: Any | None = None
    sdk_runtime_tool_inventory: Any | None = None
    sdk_prepared_authorization_policy: Any | None = None
    sdk_run_context_authority: Any | None = None
    sdk_typed_context_use_authority: Any | None = None
    prospective_occurrence_coordinator: Any | None = None
    sdk_runtime_decision_sink: Any | None = None
    sdk_task_execution_authority: Any | None = None
    sdk_effect_gate: Any | None = None
    sdk_closure_instruction_reader: Any | None = None
    human_memory_host_service_factory: Any | None = None
    human_memory_binding_append_authority: Any | None = None
    human_memory_recovery_lifecycle: Any | None = None
    human_memory_foreground_scheduler_wake: Any | None = None
    human_memory_foreground_runtime_execution_authority: Any | None = None
    capability_store: Any | None = None
    capability_platform: Any | None = None
    # Project-scoped Skill installation is composed during capability-runtime
    # startup. Keep these live collaborators explicit so the fail-closed
    # registry validates production wiring instead of rejecting valid startup.
    project_skill_install_service: Any | None = None
    project_skill_discovery_service: Any | None = None
    skill_install_runtime_verifier: Any | None = None
    skill_install_verification_driver_factory: Any | None = None
    capability_center: Any | None = None
    capability_builder_host: Any | None = None
    authorization_runtime: Any | None = None
    admission_task_grant_runtime: Any | None = None
    capability_refresh_snapshots: Any | None = None
    capability_refresh_staging: Any | None = None
    capability_refresh_service: Any | None = None
    project_skill_install_service: Any | None = None
    project_skill_install_settings_authorizer: Any | None = None
    skill_install_runtime_verifier: Any | None = None
    search_gateway: Any | None = None
    tool_capability_scope_store: Any | None = None
    tool_capability_resolver: Any | None = None
    context_page_in_store: Any | None = None
    project_binding_service: Any | None = None
    session_creation_service: Any | None = None
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
    companion_notification_service: Any | None = None
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

        return MappingProxyType({name: getattr(self, name, None) for name in _VALID_SERVICES})
