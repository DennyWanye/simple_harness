// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

export interface ControlMessage {
  type: string;
  request_id?: string;
  expected_version?: number;
  payload?: Record<string, unknown>;
}

export interface ChatResponse {
  type: "chat_response";
  payload: {
    text: string;
    /** Which provider actually served this response: "cloud" | "local". */
    provider?: "cloud" | "local";
    // P2-1-S8: set by backend when BudgetHook refused the cloud call.
    // Frontend shows a toast and keeps the fallback echo text.
    budget_exceeded?: boolean;
    budget_reason?: string;
  };
}

// P2-1-S8: daily budget snapshot returned by control WS `budget_status`
// request. SettingsPanel polls this to render the "今日使用" widget.
export interface DailyBudgetStatus {
  spent_today_cny: number;
  daily_budget_cny: number;
  remaining_cny: number;
  percent_used: number;
}

export interface BudgetStatusMessage {
  type: "budget_status";
  payload: DailyBudgetStatus;
}

export interface ChatTurnTimeoutResponse {
  type: "chat_turn_timeout_response";
  request_id?: string;
  payload: { minutes: number };
}

export interface PermissionAutoModeResponse {
  type: "permission_auto_mode_response";
  payload: { enabled: boolean };
}

// WI-1B-2 压缩可观测 — 上下文压缩命中时后端推送（仅 features.ctx_observability
// ON；OFF=BC 不发）。前端浮 toast「已压缩，省 N token」（N = tokens_in - tokens_out）。
export interface ContextCompactedMessage {
  type: "context_compacted";
  payload: {
    reduction: number;
    tokens_in: number;
    tokens_out: number;
    model: string;
    session_id: string;
  };
}

export type ContextUsageSource = "measured" | "compacted" | "binding_only";
export type ContextUsageAvailability = "available" | "unavailable" | "unknown";

/** Durable per-Session Context Usage read model.  Version is monotonic and
 * lets reconnect/live events share one last-write authority without letting
 * a late old event overwrite a recovered state. */
export interface ContextUsageSnapshot {
  schema_version?: 2;
  session_id: string;
  source?: ContextUsageSource;
  sample_id?: string | null;
  /** Frozen prepared SDK request inspected by the public breakdown. */
  snapshot_id?: string | null;
  snapshot_fingerprint?: string | null;
  version?: number;
  binding_epoch?: number;
  availability?: ContextUsageAvailability;
  provider_id?: string | null;
  model: string;
  model_id?: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  context_window: number;
  effective_ceiling: number;
  compact_at: number;
  recall_sweet: number;
  based_on_sample_id?: string | null;
  has_measurement?: boolean;
  legacy_incomplete?: boolean;
  updated_at: number;
  attempts?: ContextAttemptSnapshot[];
}

export interface PongMessage {
  type: "pong";
}

export interface ErrorMessage {
  type: "error";
  payload: { message: string };
}

// --- Audio channel message types ---

export interface VADEvent {
  type: "vad_event";
  payload: { status: "speech_start" | "speech_end" };
}

export interface TranscriptMessage {
  type: "transcript";
  // provider 仅在 role=assistant 时可能出现 —— 语音链路把实际服务本轮的
  // 路由（local / cloud）捎带过来，用于驱动右上角指示灯颜色。
  payload: {
    text: string;
    role: "user" | "assistant";
    provider?: "cloud" | "local";
  };
}

export interface LipSyncMessage {
  type: "lip_sync";
  payload: { chunk_index: number; amplitude: number };
}

export interface TTSEndMessage {
  type: "tts_end";
  payload: Record<string, never>;
}

export interface TTSBargeInMessage {
  type: "tts_barge_in";
  payload: { reason: "vad_speech_detected" };
}

export interface RunEventMessage {
  type: "run_event";
  payload: {
    session_id?: string;
    run_id: string;
    kind?: string;
    status: string;
    driver_kind?: string;
    [key: string]: unknown;
  };
}

export type HarnessInspectorStatus =
  | "created"
  | "starting"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled"
  | "claimed"
  | "unknown"
  | string;

export type PublicRunOutcomeStatus =
  | "running"
  | "waiting"
  | "blocked"
  | "completed"
  | "completed_with_recovery"
  | "failed"
  | "cancelled"
  | "unknown";

export type PublicRunPhaseStatus = Exclude<PublicRunOutcomeStatus, "blocked">;

export type PublicRunPhaseTaxonomy =
  | "understand"
  | "prepare"
  | "delegate"
  | "execute"
  | "verify_repair"
  | "wait_user"
  | "deliver";

export interface PublicRunAggregateOutcome {
  status: PublicRunOutcomeStatus;
  explanation_code: string;
  evidence_refs: string[];
  started_at?: number | null;
  ended_at?: number | null;
  child_warnings: Array<{
    run_id: string;
    status: string;
    evidence_refs: string[];
  }>;
}

export interface PublicRunWorkflowStep {
  workflow_step_id: string;
  label?: string | null;
  status?: PublicRunPhaseStatus | string | null;
  step_index?: number | null;
  step_total?: number | null;
}

export interface PublicRunSemanticPhase {
  phase_id: string;
  taxonomy: PublicRunPhaseTaxonomy;
  title: string;
  status: PublicRunPhaseStatus;
  mapping_reason: string;
  evidence_refs: string[];
  tool_refs: string[];
  child_refs: string[];
  workflow_steps: PublicRunWorkflowStep[];
  items: PublicRunPhaseItem[];
  current_step?: number | null;
  total_steps?: number | null;
  order_key: [number, number, number, string];
}

export interface PublicRunPhaseItem {
  stable_id: string;
  kind: "narration" | "tool" | "child" | "fact" | string;
  narration_code?: string | null;
  safe_text?: string | null;
  status?: string | null;
  action_code?: string | null;
  tool_name?: string | null;
  public_name?: string | null;
  safe_target_label?: string | null;
  detail_ref?: string | null;
  created_at?: number | null;
  context_visibility?: PublicContextVisibility;
}

/** Allowlisted tool data. These fields are safe for direct user display. */
export type PublicContextVisibility = "exclude";

export interface ToolPublicView {
  tool_ref: string;
  stable_id: string;
  phase_id?: string | null;
  public_name: string;
  action_label: string;
  safe_target_label?: string | null;
  status: string;
  duration_ms?: number | null;
  public_input?: unknown;
  public_result?: unknown;
  result_available?: boolean;
  details_available?: boolean;
  truncated?: boolean;
  unavailable_reason?: string | null;
  detail_ref?: string | null;
  created_at?: number | null;
  /** Transport fixtures may omit this for legacy V3; normalization supplies `exclude`. */
  context_visibility?: PublicContextVisibility;
}

export interface PublicRunMessageView {
  message_id: string;
  stable_id: string;
  phase_id?: string | null;
  text: string;
  created_at: number;
  /** Transport fixtures may omit this for legacy V3; normalization supplies `exclude`. */
  context_visibility?: PublicContextVisibility;
}

export interface PublicRunActivityItem {
  stable_id: string;
  kind: string;
  title: string;
  status: string;
  phase_id?: string | null;
  action_code?: string | null;
  tool_name?: string | null;
  safe_text?: string | null;
  safe_target_label?: string | null;
  detail_ref?: string | null;
  public_input?: unknown;
  public_result?: unknown;
  duration_ms?: number | null;
  created_at?: number | null;
  truncated?: boolean;
  context_visibility?: PublicContextVisibility;
}

export interface PublicRunSnapshotV3 {
  schema_version: "3";
  projection_id: string;
  session_id: string;
  root_run_id: string;
  aggregate_outcome: PublicRunAggregateOutcome;
  semantic_phases: PublicRunSemanticPhase[];
  tool_public_views: ToolPublicView[];
  public_messages: PublicRunMessageView[];
  activity_items?: PublicRunActivityItem[];
  context_visibility?: PublicContextVisibility;
  totals: {
    workflow_facts: number;
    content_facts: number;
    provider_details: number;
    tool_details: number;
  };
  projection_complete: boolean;
  diagnostics: string[];
  read_cut?: {
    workflow?: { captured_at: number; data_version: number; complete: boolean };
    state?: { captured_at: number; data_version: number; complete: boolean };
    unmatched_refs?: string[];
  };
  legacy_fallback?: boolean;
}

export interface HarnessInspectorSnapshot {
  schema_version: 1 | 2;
  refreshed_at: number;
  session_id: string;
  activity?: {
    phase: string;
    summary: string;
    waiting_reason?: string | null;
    last_progress_at: number;
    stalled_for_seconds: number;
    is_stalled: boolean;
    last_error?: {
      source: string;
      code?: string | null;
      type?: string | null;
      message?: string | null;
    } | null;
    artifact_count: number;
  };
  run: {
    run_id: string;
    root_run_id: string;
    request_id: string;
    turn_id: string;
    trace_id: string;
    driver_kind: string;
    profile_key: string;
    persistence_level: string;
    status: HarnessInspectorStatus;
    version: number;
    durable_seq: number;
    owner_kind: string;
    owner_generation: number;
    created_at: number;
    started_at?: number | null;
    updated_at: number;
    ended_at?: number | null;
  };
  projection?: {
    task_scope_id: string;
    ui_state: string;
    version: number;
    created_at: number;
    updated_at: number;
  } | null;
  prepared_context: {
    available: boolean;
    start_fingerprint?: string | null;
    prepared_ref_count: number;
    terminal_delivery_count: number;
    created_at?: number | null;
    steps?: Array<{
      step: string;
      started_at: number;
      ended_at: number;
      duration_ms: number;
      input?: unknown;
      output?: unknown;
    }>;
    details?: unknown;
  };
  continuation?: {
    iteration: number;
    version: number;
    pending_decision_id?: string | null;
    pending_call_count: number;
    pending_tool_names: string[];
    updated_at: number;
  } | null;
  goal?: {
    goal_id: string;
    task_scope_id: string;
    status: string;
    version: number;
    plan_version?: number | null;
    trigger_failure_set_id?: string | null;
    created_at: number;
    updated_at: number;
    ended_at?: number | null;
  } | null;
  lineage: Array<{
    run_id: string;
    parent_run_id?: string | null;
    driver_kind: string;
    profile_key: string;
    status: HarnessInspectorStatus;
    version: number;
    created_at: number;
    started_at?: number | null;
    updated_at: number;
    ended_at?: number | null;
  }>;
  provider_invocations: Array<{
    run_id: string;
    invocation_id: string;
    provider_id: string;
    model_id: string;
    adapter_id: string;
    iteration?: number | null;
    attempt_ordinal: number;
    status: HarnessInspectorStatus;
    claimed_at: number;
    dispatch_started_at?: number | null;
    updated_at: number;
    duration_ms: number;
    audit_reason?: string | null;
    request_hash?: string;
    policy?: unknown;
    input?: unknown;
    output?: unknown;
    error_type?: string | null;
    error_message?: string | null;
  }>;
  action_batches: Array<{
    batch_id: string;
    provider_turn_id: string;
    pending_call_count: number;
    failure_set_id?: string | null;
    status: HarnessInspectorStatus;
    observed_status?: HarnessInspectorStatus | null;
    version: number;
    created_at: number;
    updated_at: number;
    settled_at?: number | null;
  }>;
  tool_calls: Array<{
    call_record_id: string;
    batch_id: string;
    order: number;
    call_id: string;
    tool_name: string;
    admission_state: HarnessInspectorStatus;
    outcome_status?: HarnessInspectorStatus | null;
    error_code?: string | null;
    error_message?: string | null;
    terminal_outcome_ref?: string | null;
    details?: unknown;
    version: number;
    created_at: number;
    updated_at: number;
  }>;
  effects: Array<{
    effect_id: string;
    run_id: string;
    call_id: string;
    tool_name: string;
    effect_type: string;
    status: HarnessInspectorStatus;
    handoff_state: string;
    completion_disposition: string;
    receipt_ref?: string | null;
    details?: unknown;
    created_at: number;
    updated_at: number;
    ended_at?: number | null;
  }>;
  /** Durable tool effects emitted by child workflow Runs. */
  workflow_effects?: Array<{
    effect_id: string;
    run_id: string;
    status: HarnessInspectorStatus;
    workflow_step_id?: string | null;
    receipt_ref?: string | null;
    details?: {
      prepared?: unknown;
      outcome?: unknown;
      artifact_refs?: unknown;
    };
    created_at: number;
    updated_at: number;
    ended_at?: number | null;
  }>;
  /** User-readable durable plan projected from child workflow checkpoints. */
  workflow_plans?: Array<{
    run_id: string;
    plan_id?: string | null;
    active_step_id?: string | null;
    steps: Array<{
      workflow_step_id: string;
      index: number;
      title: string;
      status: HarnessInspectorStatus;
    }>;
    public_messages: Array<{
      message_id: string;
      text: string;
      tool_call_ids: string[];
      workflow_step_id?: string | null;
    }>;
  }>;
  attempts: Array<{
    attempt_id: string;
    run_id: string;
    provider_turn_id: string;
    batch_id: string;
    plan_version: number;
    trigger_failure_set_id?: string | null;
    supersedes_attempt_id?: string | null;
    status: HarnessInspectorStatus;
    continuation_status?: HarnessInspectorStatus | null;
    version: number;
    created_at: number;
    updated_at: number;
    ended_at?: number | null;
  }>;
  failures: Array<{
    report_ref: string;
    run_id: string;
    attempt_id: string;
    plan_version: number;
    provider_call_id?: string | null;
    child_run_id?: string | null;
    failed_call_id?: string | null;
    failed_effect_id?: string | null;
    failed_step: string;
    error_class: string;
    error_code: string;
    error_message?: string | null;
    source_kind: string;
    source_identity: string;
    created_at: number;
  }>;
  events: Array<{
    event_id: string;
    run_id: string;
    seq: number;
    kind: string;
    status: HarnessInspectorStatus;
    driver_kind: string;
    failure_layer?: string | null;
    failure_code?: string | null;
    error_code?: string | null;
    error_message?: string | null;
    payload?: unknown;
    correlation?: unknown;
    created_at: number;
  }>;
}

export interface HarnessInspectorSnapshotResponse {
  type: "harness_inspector_snapshot_response";
  request_id: string;
  ok: true;
  snapshot?: PublicRunSnapshotV3;
  payload?: HarnessInspectorSnapshot | PublicRunSnapshotV3;
}

export interface HarnessInspectorErrorResponse {
  type: "harness_inspector_error";
  request_id: string;
  ok: false;
  code?: string;
  message?: string;
  payload?: {
    session_id?: string;
    run_id?: string;
    root_run_id?: string;
    code: string;
    detail?: string;
    message?: string;
  };
}

export interface HarnessInspectorDetailsResponse {
  type: "harness_inspector_details_response";
  request_id: string;
  projection_id: string;
  query_kind: "workflow_facts" | "content_facts" | "provider_details" | "tool_details";
  items: unknown[];
  next_cursor?: string | null;
  total?: number;
  projection_complete?: boolean;
}

// --- S14 memory management (control channel) ---

export interface StoredTurn {
  id: number;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
}

export interface SessionSummary {
  session_id: string;
  turn_count: number;
  last_message_at: number;
}

export interface MemoryListResponse {
  type: "memory_list_response";
  payload: {
    scope: "session" | "all";
    session_id: string | null;
    turns: StoredTurn[];
  };
}

export interface MemoryDeleteAck {
  type: "memory_delete_ack";
  payload: { id: number; deleted: boolean };
}

// 记忆系统升级 WI-M1.1：评估反馈回路。点 👍/👎 后端落 memory_user_feedback。
export interface MemoryThumbsUpResponse {
  type: "memory_thumbs_up_response";
  payload: { ok: boolean; feedback_id?: number; reason?: string };
}

export interface MemoryClearAck {
  type: "memory_clear_ack";
  payload: { scope: "session" | "all"; session_id?: string; removed?: number };
}

export interface MemoryExportResponse {
  type: "memory_export_response";
  payload: {
    exported_at: number;
    sessions: SessionSummary[];
    turns: StoredTurn[];
  };
}

// --- P2-1-S3 settings / provider test ----------------------------------------

/** Outgoing: SettingsPanel「测试连接」button. Candidate creds travel on the
 * already-authenticated control channel; nothing is persisted backend-side. */
export interface ProviderTestConnectionRequest {
  type: "provider_test_connection";
  payload: { base_url: string; api_key: string; model: string };
}

/** Incoming: backend reply to the request above. */
export interface ProviderTestConnectionResult {
  type: "provider_test_connection_result";
  payload: {
    ok: boolean;
    tested_url?: string;
    /** Present when ok=false; short human-readable reason. */
    error?: string;
  };
}

/**
 * P2-1-S3 <-> P2-1-S8 cross-slice contract: the shape SettingsPanel's
 * 今日使用 section consumes. S3 ships a stub `fetchDailyBudget`; S8 replaces
 * it with the real control-WS roundtrip.
 *
 * Fields are snake_case to match the eventual backend payload verbatim —
 * no translation layer needed when S8 lands.
 */
export interface DailyBudgetStatus {
  spent_today_cny: number;
  daily_budget_cny: number;
  remaining_cny: number;
  /** 0..100. Precomputed by the backend so the UI doesn't have to guard
   * against division-by-zero or stale limit values. */
  percent_used: number;
}

// --- P4-S11 MemoryPanel + ContextTrace (L1 / L3 / Skills / Decisions) --------
//
// Five new request→response pairs rendered on top of the existing control WS.
// Backend handlers live in `backend/p4_ipc.py`; all of them degrade gracefully
// (empty list + `reason`) when the underlying service hasn't been wired yet
// so the UI ships independent of S12.

export interface SkillDescriptor {
  name: string;
  description?: string;
  version?: string;
  author?: string;
  /** "builtin" | "user" — lets the panel group custom skills separately. */
  source?: "builtin" | "user" | string;
  path?: string;
}

export interface SkillsListResponse {
  type: "skills_list_response";
  payload: {
    skills: SkillDescriptor[];
    /** Present when SkillLoader isn't registered yet (pre-S12 wire-in). */
    reason?: string;
  };
}

/**
 * WI-TG-2 — one in-flight permission prompt. Mirrors the
 * `permission_request` payload so the ApprovalCenterPanel can reuse the
 * same fields. Read-only snapshot from PermissionGate.list_pending().
 */
export interface PendingPermissionItem {
  request_id: string;
  category: string;
  summary: string;
  params: Record<string, unknown>;
  default_action: "allow" | "prompt" | "deny";
  dangerous: boolean;
  session_id: string;
}

/** WI-TG-2 — backend → frontend: current session's pending prompts. */
export interface PermissionsPendingListResponse {
  type: "permissions_pending_list_response";
  payload: {
    pending: PendingPermissionItem[];
    /** Present when PermissionGate isn't registered (v2 init failed). */
    reason?: string;
  };
}

export interface DecisionRecord {
  /** ISO8601 or epoch seconds — UI formats defensively. */
  timestamp?: string | number;
  /** Which router branch fired ("local" / "cloud" / "echo" etc.). */
  classifier_path?: string;
  /** End-to-end latency in ms for this turn. */
  latency_ms?: number;
  /** Total tokens consumed (prompt + completion). */
  total_tokens?: number;
  /** Per-section token budget breakdown for the bar chart. */
  token_breakdown?: Record<string, number>;
  /** Short one-line justification. */
  reason?: string;
  /** Session that produced the decision, if known. */
  session_id?: string;
}

export interface ContextAttemptSnapshot {
  session_id: string;
  request_id: string;
  attempt_id: string;
  purpose: string;
  state: "planned" | "sent" | "succeeded" | "failed" | "cancelled" | "cancelled_before_send";
  provider_id: string;
  model_id: string;
  adapter_id: string;
  adapter_version: string;
  message_hash: string;
  logical_tool_hash: string;
  wire_tool_hash: string;
  schema_fingerprint: string;
  policy_fingerprint: string;
  registry_revision: number;
  tool_scope_revision: number;
  direct_tool_count: number;
  activated_tool_count: number;
  deferred_tool_count: number;
  schema_tokens_by_name: Array<[string, number]>;
  selection_reasons: string[];
  fragments: Array<{
    fragment_id: string;
    action: "loaded" | "trimmed" | "omitted";
    reason: string;
    estimated_tokens: number;
    cache_scope: string;
    cache_hash: string;
  }>;
  coverage_entries: Array<{
    kind: string;
    message_ids: number[];
    segment_id: string;
    source_hash: string;
  }>;
  coverage_valid?: boolean | null;
  coverage_gaps: number[];
  coverage_overlaps: number[];
  coverage_stale_segment_ids: string[];
  coverage_broken_causal_groups: string[];
  coverage_page_in_refs: number;
  tool_tokens: number;
  message_tokens: number;
  attachment_tokens: number;
  reserve_tokens: number;
  effective_input_budget: number;
  context_window: number;
  planned_tokens: number;
  estimate_method: string;
  cache_boundary?: number | null;
  cache_fingerprint: string;
  actual_input_tokens?: number | null;
  actual_output_tokens?: number | null;
  actual_cache_read_tokens?: number | null;
  actual_cache_write_tokens?: number | null;
  transport_retry_count: number;
  requested_compression_model: string;
  resolved_compression_model: string;
  actual_compression_model: string;
  compression_provider: string;
  compression_source: string;
  compression_failure: string;
  reasons: string[];
  created_at: number;
  updated_at: number;
}

export interface DecisionsListResponse {
  type: "decisions_list_response";
  payload: {
    decisions: DecisionRecord[];
    attempts?: ContextAttemptSnapshot[];
    reason?: string;
  };
}

export interface MemoryHit {
  text: string;
  score: number;
  source?: string;
  created_at?: string | number | null;
  session_id?: string | null;
}

export interface MemorySearchResponse {
  type: "memory_search_response";
  payload: {
    query: string;
    hits: MemoryHit[];
    reason?: string;
    error?: string;
  };
}

export type L1Target = "memory" | "user";

export interface L1Entry {
  index: number;
  text: string;
  salience: number;
}

export interface MemoryL1ListResponse {
  type: "memory_l1_list_response";
  payload: {
    target: L1Target;
    entries: L1Entry[];
    reason?: string;
  };
}

export interface MemoryL1DeleteAck {
  type: "memory_l1_delete_ack";
  payload: {
    target: L1Target;
    index: number;
    deleted: boolean;
    reason?: string;
  };
}

// --- WI-S2.1b: facts view（事实 tab） + 🗑 + 5s undo --------------------
//
// 后端 ws 路由 `memory_facts_list` / `memory_forget` / `memory_forget_undo`
// 由 backend/p4_ipc.py 实现。embedding 列在后端已剥离（JSON 不接 bytes），
// 因此 FactItem 不含 embedding 字段。
export interface FactItem {
  id: number;
  category: string;
  subject: string;
  key: string;
  value: string;
  confidence: number;
  source_msg_id: number | null;
  created_at: number;
  updated_at: number;
  evidence: string | null;
  is_active: number;
  decay_rate: number;
  last_recalled: number | null;
  superseded_by?: number | null;
  forgotten_at?: number | null;
}

export interface MemoryFactsListResponse {
  type: "memory_facts_list_response";
  payload: { facts: FactItem[]; reason?: string };
}

export interface MemoryForgetResponse {
  type: "memory_forget_response";
  payload: {
    status: "ok" | "error" | "skipped" | "not_found";
    op_id?: string;
    forgotten_ids?: number[];
    reason?: string;
    candidates?: number[];
  };
}

export interface MemoryForgetUndoResponse {
  type: "memory_forget_undo_response";
  payload: {
    status: "ok" | "expired" | "error";
    restored_ids: number[];
    reason?: string;
  };
}

// --- P4-S16 Embedder status (SettingsPanel BGE-M3 卡片) ---------------------
//
// 让用户在前端直接看见当前 BGE-M3 是真模型还是 mock。后端 handler 在
// backend/p4_ipc.py::_handle_embedder_status；service_context._p4_embedder
// 缺失或抛错会带 reason 回传，UI 据此渲染降级状态。

export interface EmbedderStatusResponse {
  type: "embedder_status_response";
  payload: {
    /** Embedder.warmup() 是否已完成（mock 也算 ready）。 */
    is_ready: boolean;
    /** True = 当前走 mock 路径（语义搜索能力受限）。 */
    is_mock: boolean;
    /** Embedder 期望的模型路径（绝对路径，已脱敏不含密码）。 */
    model_path: string;
    /** 仅在异常态出现："embedder_not_registered" / "embedder_error: ..." */
    reason?: string;
  };
}

// --- Option A (2026-06-05) 首启模型下载进度 ------------------------------
//
// 瘦包不内嵌模型 → backend 首启从 hf-mirror 下载缺失的 bge-m3/faster-whisper。
// 前端轮询 `model_provision_status`，后端 p4_ipc::_handle_model_provision_status
// 回传进度。state="ready"(或服务未注册) 时前端不显示进度条。
export interface ModelProvisionStatusResponse {
  type: "model_provision_status_response";
  payload: {
    /** idle | checking | downloading | ready | error */
    state: string;
    /** 正在下载的模型子目录名（downloading 时） */
    current?: string | null;
    /** 1-based：当前是第几个模型 */
    index?: number;
    /** 本次需下载的模型总数（0 = 无需下载） */
    total?: number;
    /** 当前模型已下载字节（按磁盘实时大小估） */
    downloaded_bytes?: number;
    /** 当前模型预估总字节（0 = 未知） */
    total_bytes?: number;
    /** 仅 error 态：失败原因 */
    error?: string | null;
  };
}

// --- Phase 1.1.6 模型上下文配置卡片（context-1m-rearch）-------------------
//
// SettingsPanel「模型上下文」卡片 ←→ backend p4_ipc.py。
//   model_context_get  → model_context_get_response（当前 resolve 结果 +
//                         builtin 全表，渲染来源链 + 下拉）
//   model_context_set  → model_context_set_ack（写回 global/project TOML）
// 字段 snake_case 与后端 payload 逐字对齐，无翻译层。

export interface ModelContextResolved {
  context_window: number;
  effective_pct: number;
  compact_at_pct: number;
  recall_sweet_tokens: number;
  /** 解析链尾：哪一层最终决定了值。 */
  source: "builtin" | "global" | "project" | string;
}

export interface ModelContextBuiltinEntry {
  context_window: number;
  effective_pct: number;
  compact_at_pct: number;
  recall_sweet_tokens: number;
}

export interface ModelContextGetRequest {
  type: "model_context_get";
  payload: { model?: string; project_root?: string };
}

export interface ModelContextGetResponse {
  type: "model_context_get_response";
  payload: {
    model: string;
    resolved: ModelContextResolved | Record<string, never>;
    builtin: Record<string, ModelContextBuiltinEntry>;
    /** 仅异常态："resolve_error: ..."。 */
    reason?: string;
  };
}

export interface ModelContextSetRequest {
  type: "model_context_set";
  payload: {
    scope: "global" | "project";
    model: string;
    fields: Partial<ModelContextBuiltinEntry>;
    /** scope="project" 时必填。 */
    project_root?: string;
  };
}

export interface ModelContextSetAck {
  type: "model_context_set_ack";
  payload: {
    ok: boolean;
    scope?: string;
    model?: string;
    /** ok=false 时的原因。 */
    reason?: string;
  };
}

// P4-S20: skill platform — re-exported from skillPlatform.ts to keep
// the wire-contract definition close to the rest of the platform types.
import type {
  ClarificationRequest,
  ExternalWaitRequest,
  PermissionRequest,
  PPTOutlineProposed,
  PPTOutlineResolved,
  ToolUseEvent,
} from "./skillPlatform";

export interface SessionTodoUpdateMessage {
  type: "session_todo_update";
  payload: {
    session_id?: string;
    items: {
      content: string;
      activeForm: string;
      status: "pending" | "in_progress" | "completed";
      sort_order?: number;
    }[];
  };
}

export interface ContextCompactionGetRequest {
  type: "context_compaction_get";
  payload: Record<string, never>;
}

export interface ContextCompactionGetResponse {
  type: "context_compaction_get_response";
  payload: {
    model: string;
    default_model: "follow_session";
    available_models: string[];
  };
}

export interface ContextCompactionSetRequest {
  type: "context_compaction_set";
  payload: { model: string };
}

export interface ContextCompactionSetAck {
  type: "context_compaction_set_ack";
  payload: { ok: boolean; model?: string; reason?: string };
}

export interface WorkflowRunsListResponse {
  type: "workflow_runs_list_response";
  request_id: string;
  ok: true;
  payload: {
    runs: Array<{
      run_id: string;
      workflow_name: string;
      workflow_version: string;
      status: "created" | "running" | "waiting" | "retryable" | "cancel_requested" | "cancelling" | "blocked" | "completed" | "failed" | "cancelled";
      created_at: number;
      updated_at: number;
      active_nodes: string[];
      run_version?: number;
      trace_id?: string | null;
      recovery_action?: string | null;
      next_retry_at?: number | null;
      error?: unknown;
    }>;
    next_cursor?: string | null;
  };
}

export interface WorkflowRunDetailResponse {
  type: "workflow_run_detail_response";
  request_id: string;
  ok: true;
  payload: {
    run_id: string;
    run_version?: number;
    run?: WorkflowRunsListResponse["payload"]["runs"][number];
    nodes: Array<{ id: string; label: string; status: string; attempt?: number; error?: string | null; recovery_action?: string | null; next_retry_at?: number | null; started_at?: number | null; ended_at?: number | null }>;
    edges: Array<{ source: string; target: string; label?: string }>;
    spans: Array<{ span_id: string; parent_span_id?: string | null; name: string; kind: string; status: string; duration_ms?: number | null; error?: string | null; input_summary?: string | null; output_summary?: string | null; redacted?: boolean }>;
    checkpoints: Array<{ checkpoint_id: string; checkpoint_ns?: string; node_id?: string | null; created_at: number; status: string; can_fork?: boolean; fork_reason?: string | null; requires_effect_confirmation?: boolean; effect_summary?: string | null }>;
    evaluations: Array<{ evaluation_id: string; evaluator_name: string; evaluator_version: string; verdict: string; score?: number | null; explanation?: string | null; labels?: string[]; comment?: string | null; experiment_version?: string | null; created_at?: number | null }>;
    decisions?: Array<{
      decision_id: string;
      run_id: string;
      kind: string;
      status: "open" | "expired" | "resolved" | "cancelled" | "abandoned";
      prompt: unknown;
      nonce: string;
      version: number;
      expires_at?: number | null;
      created_at: number;
      options?: Array<{ label: string; value: unknown; description?: string | null; dangerous?: boolean }>;
    }>;
    deliveries?: Array<{ delivery_id: string; run_id?: string; channel?: string | null; status: string; attempts?: number; version: number; next_attempt_at?: number | null; last_error?: string | null }>;
    delivery_aggregate?: WorkflowDeliveryAggregate | null;
  };
}

export interface WorkflowDeliveryAggregate {
  schema_version: 1;
  run_id: string;
  manifest_ref: string;
  status: "queued" | "delivering" | "delivered" | "retrying" | "fenced" | "failed";
  required_total: number;
  pending: number;
  delivering: number;
  delivered: number;
  retrying: number;
  fenced: number;
  failed: number;
  updated_at: number;
}

export interface WorkflowCheckpointForkResponse {
  type: "workflow_checkpoint_fork_response";
  request_id: string;
  ok: true;
  payload: {
    source_run_id?: string;
    checkpoint_id?: string;
    run_id?: string;
    audit?: string | null;
  };
}

export interface WorkflowDecisionResolveResponse {
  type: "workflow_decision_resolve_response";
  request_id: string;
  ok: true;
  payload: {
    run_id?: string;
    decision_id: string;
    status?: string;
    version?: number;
    audit?: string | null;
  };
}

export interface WorkflowEvaluationSubmitResponse {
  type: "workflow_evaluation_submit_response";
  request_id: string;
  ok: true;
  payload: {
    evaluation_id: string;
    trace_id: string;
    run_id?: string | null;
    evaluator_name: string;
    evaluator_version: string;
    verdict: string;
    score?: number | null;
    explanation?: string | null;
    labels?: string[];
    created_at?: number | null;
    audit?: string | null;
  };
}

export interface WorkflowDeliveryMutationResponse {
  type: "workflow_delivery_retry_response" | "workflow_delivery_discard_response";
  request_id: string;
  ok: true;
  payload: { delivery_id: string; run_id?: string; status: string; version: number; audit?: string | null };
}

export interface WorkflowRunRetryFromStartResponse {
  type: "workflow_run_retry_from_start_response";
  request_id: string;
  ok: true;
  payload: {
    run_id: string;
    source_run_id: string;
    created: boolean;
    accepted?: boolean;
  };
}

export interface WorkflowLifecycleEvent {
  type: "workflow_event" | "workflow_final";
  payload: {
    event_id: string;
    event_type: string;
    run_id: string;
    seq: number;
    session_id: string;
    delivery_aggregate?: WorkflowDeliveryAggregate | null;
    payload: {
      kind?: string;
      status?: string;
      text?: string;
      payload?: { text?: string; [key: string]: unknown };
      card?: Record<string, unknown>;
      [key: string]: unknown;
    };
  };
}

export interface WorkflowIPCErrorResponse {
  type: "workflow_ipc_error";
  request_type?: string | null;
  request_id?: string | null;
  ok: false;
  error: { code: string; message: string; retryable: boolean; current_version?: number; details?: Record<string, unknown> };
  payload: { error: WorkflowIPCErrorResponse["error"] };
}

// --- Human-anchored Companion projections ---------------------------------

export interface CompanionOwnerFence {
  profile_id: string;
  profile_generation: number;
}

export type CompanionDecisionStatus =
  | "open"
  | "confirmed"
  | "rejected"
  | "resolved"
  | "expired"
  | "rolled_back"
  | "forgotten"
  | "stale";

interface CompanionDecisionBase {
  status?: CompanionDecisionStatus;
  expires_at: string;
}

export interface CompanionEvaluationAuthorizationDecision
  extends CompanionDecisionBase {
  kind: "evaluation_authorization";
  candidate_id: string;
  candidate_revision: number;
  candidate_package_hash: string;
  candidate_code_digest?: string;
  suite_hash: string;
  runner_policy_hash: string;
  nonce: string;
  decision_version: number;
}

export interface CompanionActivationDecision extends CompanionDecisionBase {
  kind: "activation";
  candidate_id: string;
  pack_id: string;
  candidate_version: string;
  candidate_package_hash: string;
  candidate_code_digest?: string;
  evaluation_report_hash: string;
  risk_assessment_hash: string;
  scope: "run" | "project" | "user";
  scope_key: string;
  owner_key: string;
  expected_binding_generation: number;
  nonce: string;
  decision_version: number;
  activation_risk_ack?: "persistent_local_code_no_os_sandbox";
}

export interface CompanionActionConfirmationDecision
  extends CompanionDecisionBase {
  kind: "action_confirmation";
  decision_id: string;
  run_id: string;
  call_id: string;
  effect_id: string;
  tool_name: string;
  redacted_target_summary: string;
  args_hash: string;
  capability_hash: string;
  scope_hash: string;
}

export type CompanionDecision =
  | CompanionEvaluationAuthorizationDecision
  | CompanionActivationDecision
  | CompanionActionConfirmationDecision;

/** The deliberately small durable payload stored in SessionDB. */
export interface CompanionNotificationEnvelope {
  notification_id: string;
  kind: string;
  summary: string;
  detail_ref: string;
  detail_version: string;
  available_actions: string[];
}

/**
 * History and live delivery use this identical owner-fenced envelope.
 * Large evidence, diffs, reports and audit rows are available only through
 * `companion_detail_get`.
 */
export interface CompanionEvent {
  event_id: string;
  profile_id: string;
  profile_generation: number;
  session_id: string;
  seq: number;
  route_version?: number;
  redaction_version?: number;
  importance?: "normal" | "important";
  occurred_at?: string;
  received_at?: number;
  notification: CompanionNotificationEnvelope;
  decision?: CompanionDecision;
  tombstone?: boolean;
}

export interface CompanionEventMessage {
  type: "companion_event";
  payload: CompanionEvent;
}

export interface CompanionProjectionRetractedMessage {
  type: "companion_projection_retracted";
  payload: Omit<CompanionEvent, "notification"> & {
    notification_id: string;
    redaction_version: number;
    notification?: CompanionNotificationEnvelope;
  };
}

export type CompanionDetailSection =
  | "overview"
  | "evidence"
  | "diff"
  | "evaluation"
  | "decision"
  | "operation_receipt"
  | "current_binding"
  | "audit";

export interface CompanionDetailGetRequest {
  type: "companion_detail_get";
  request_id: string;
  payload: {
    notification_id: string;
    section: CompanionDetailSection;
    cursor: string | null;
    page_size: number;
    expected_detail_version: string;
  };
}

export interface CompanionDetailItem {
  id: string;
  title?: string;
  summary?: string;
  status?: string;
  hash?: string;
  occurred_at?: string;
  [key: string]: unknown;
}

export interface CompanionDetailResponse {
  type: "companion_detail_response";
  request_id: string;
  payload: {
    notification_id: string;
    section: CompanionDetailSection;
    detail_version: string;
    items: CompanionDetailItem[];
    next_cursor: string | null;
  };
}

export interface CompanionDetailErrorResponse {
  type: "companion_detail_error";
  request_id: string;
  payload: {
    code: "unavailable" | "detail_changed" | "cursor_invalid";
    message?: string;
  };
}

export type IncomingMessage =
  | ChatResponse
  | PongMessage
  | ErrorMessage
  | LipSyncMessage
  | MemoryListResponse
  | MemoryDeleteAck
  | MemoryThumbsUpResponse
  | MemoryClearAck
  | MemoryExportResponse
  | ProviderTestConnectionResult
  | BudgetStatusMessage
  | ChatTurnTimeoutResponse
  | PermissionAutoModeResponse
  | SkillsListResponse
  | DecisionsListResponse
  | MemorySearchResponse
  | MemoryL1ListResponse
  | MemoryL1DeleteAck
  | MemoryFactsListResponse
  | MemoryForgetResponse
  | MemoryForgetUndoResponse
  | EmbedderStatusResponse
  | ModelProvisionStatusResponse
  | ModelContextGetResponse
  | ModelContextSetAck
  | ContextCompactionGetResponse
  | ContextCompactionSetAck
  | HarnessInspectorSnapshotResponse
  | HarnessInspectorErrorResponse
  | PermissionsPendingListResponse
  | PermissionRequest
  | ExternalWaitRequest
  | ClarificationRequest
  | PPTOutlineProposed
  | PPTOutlineResolved
  | ToolUseEvent
  | SessionTodoUpdateMessage
  | WorkflowRunsListResponse
  | WorkflowRunDetailResponse
  | WorkflowCheckpointForkResponse
  | WorkflowDecisionResolveResponse
  | WorkflowEvaluationSubmitResponse
  | WorkflowDeliveryMutationResponse
  | WorkflowRunRetryFromStartResponse
  | WorkflowLifecycleEvent
  | WorkflowIPCErrorResponse
  | CompanionEventMessage
  | CompanionProjectionRetractedMessage
  | CompanionDetailResponse
  | CompanionDetailErrorResponse
  | ContextCompactedMessage
  | { type: "context_usage"; payload: ContextUsageSnapshot };

export type AudioMessage =
  | VADEvent
  | TranscriptMessage
  | TTSEndMessage
  | TTSBargeInMessage
  | RunEventMessage
  | ErrorMessage;
