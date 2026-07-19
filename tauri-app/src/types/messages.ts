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

// --- Emotion / action events (S1) ---
// Emitted by backend pipeline when LLM output contains
// [emotion:xxx] or [action:xxx] tags. Frontend drives Live2D accordingly.

export interface EmotionChangeMessage {
  type: "emotion_change";
  payload: { value: string };
}

export interface ActionTriggerMessage {
  type: "action_trigger";
  payload: { value: string };
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
  PermissionRequest,
  PPTOutlineProposed,
  PPTOutlineResolved,
  ToolUseEvent,
} from "./skillPlatform";

// P4-S22 — Code mode IPC payloads.
export interface CodeModeStateMessage {
  type: "code_mode_state";
  payload: {
    enabled: boolean;
    project_root?: string;
    project_name?: string;
    code_session_id?: string;
    error?: string;
  };
}

export interface CodeTodoUpdateMessage {
  type: "code_todo_update";
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

export interface CodeModeSuggestMessage {
  type: "code_mode_suggest";
  payload: {
    trigger_text: string;
    reason?: string;
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

export type IncomingMessage =
  | ChatResponse
  | PongMessage
  | ErrorMessage
  | LipSyncMessage
  | EmotionChangeMessage
  | ActionTriggerMessage
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
  | PermissionsPendingListResponse
  | PermissionRequest
  | ClarificationRequest
  | PPTOutlineProposed
  | PPTOutlineResolved
  | ToolUseEvent
  | CodeModeStateMessage
  | CodeTodoUpdateMessage
  | CodeModeSuggestMessage
  | WorkflowRunsListResponse
  | WorkflowRunDetailResponse
  | WorkflowCheckpointForkResponse
  | WorkflowDecisionResolveResponse
  | WorkflowEvaluationSubmitResponse
  | WorkflowDeliveryMutationResponse
  | WorkflowRunRetryFromStartResponse
  | WorkflowLifecycleEvent
  | WorkflowIPCErrorResponse
  | ContextCompactedMessage;

export type AudioMessage = VADEvent | TranscriptMessage | TTSEndMessage | TTSBargeInMessage | ErrorMessage;
