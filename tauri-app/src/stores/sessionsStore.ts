// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S23 — Multi-session zustand store.
 *
 * Single source of truth for everything Code-mode UI renders. The
 * companion-pet window AND the code-panel webview both subscribe to
 * the same store via Tauri's broadcast WS — events stamped with
 * `payload.session_id` get fanned out to the matching slice here.
 *
 * Design note: we deliberately keep this minimal. Session state lives
 * in this in-memory store + on backend's SessionDB; we don't try to
 * persist scrollback to localStorage (browser quota) — backend's
 * memory hierarchy already handles long-term retrieval.
 */
import { create } from "zustand";

import type { PPTOutlineHistoryItem } from "../types/skillPlatform";
import type { ContextAttemptSnapshot, WorkflowDeliveryAggregate } from "../types/messages";

export type MessageRole =
  | "user"
  | "assistant"
  | "assistant_delta"   // P4-S25 A1: streaming partial assistant content
  | "reasoning_delta"   // P4-S25 A1: streaming thinking-mode chain-of-thought
  | "tool_call"
  | "tool_result"
  | "plan"              // P4-S25 A2: plan card preceding execution
  | "skill_candidate"   // FP-5 WI-4.3c: 技能自创确认卡（后端 propose → 用户确认）
  | "ppt_outline"       // PPT Pro WI-4: 大纲确认卡（后端 propose → 用户确认/修改/复用）
  | "workflow_progress"
  | "workflow_stage"
  | "slash_result"      // FEAT-A2: /slash 命令结果（help/goal/prefs/skill/error）
  | "error";

export type WorkflowProgressStatus =
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled";

export type WorkflowV5ControlAction =
  | "none"
  | "generate_now"
  | "continue_research"
  | "retry_from_start"
  | "cancel_settle";

export type WorkflowV5ControlStatus =
  | "none"
  | "open"
  | "accepted"
  | "observed"
  | "settled"
  | "consumed"
  | "rejected"
  | "expired";

export interface WorkflowV5ProgressProjection {
  action: string;
  result: string;
  discarded: number | "none";
  remaining_gap: number | "none";
  next_step: string;
  dimension_counts: {
    total: number;
    core_total: number;
    covered: number;
    partially_covered: number;
    uncovered: number;
    not_applicable: number;
    core_covered: number;
    core_partially_covered: number;
    core_uncovered: number;
  };
  dimension_status_changes: {
    improved: number | "none";
    regressed: number | "none";
    unchanged: number | "none";
  };
  source_counts: { valid: number; first_party: number };
  active_gap: "none" | {
    status: "pending" | "running" | "completed" | "failed" | "cancelled";
    work_kind: "query" | "source_target" | "fetch";
    dimension_ordinal: number;
  };
  elapsed_seconds: number;
  soft_checkpoint: "none" | "before" | "reached";
  lease_reason: string;
  quality_score: number | "none";
  hard_failures: string[];
  predicted_delivery: "pending" | "completed" | "partial" | "insufficient_evidence";
  token_budget_ratio: number | "none";
  control_action: WorkflowV5ControlAction;
  control_status: WorkflowV5ControlStatus;
  parent_operation: string | "none";
  failed_dimensions: Array<{
    dimension_ordinal: number;
    status: "uncovered" | "partially_covered";
    reason_codes: string[];
  }>;
  rejection_reasons: Array<{ reason_code: string; count: number }>;
}

export type WorkflowV7ChildStatus =
  | "queued"
  | "running"
  | "retrying"
  | "valid"
  | "insufficient";

export interface WorkflowV7ChildProgress {
  child_id: string;
  question: string;
  status: WorkflowV7ChildStatus;
  attempt: number;
  max_attempts: number;
  n_sources: number;
  reason_code?: string;
}

export interface WorkflowEventEnvelope {
  event_id?: string;
  run_id?: string;
  seq?: number;
  event_type?: string;
  payload?: Record<string, unknown>;
  created_at?: number;
  delivery_aggregate?: unknown;
}

export interface PlanStep {
  title: string;
  detail: string;
}

export interface Message {
  id: string;
  role: MessageRole;
  text?: string;
  // Tool-call specific
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  // Tool-result specific
  tool_ok?: boolean;
  tool_result?: string;
  tool_error?: string;
  // P4-S25 A2: plan-card payload
  plan_rationale?: string;
  plan_steps?: PlanStep[];
  // superpowers 决策2 plan-confirm 硬门: 等用户点 [执行]/[取消]
  plan_awaiting_confirm?: boolean;
  plan_sid?: string;  // which session this plan belongs to (for plan_confirm WS)
  // FP-5 WI-4.3c: 技能自创确认卡 payload（镜像 plan-card 的 awaiting 模式）
  skill_candidate_id?: number;     // 后端 pending_skill_candidates 行 id（回传 key）
  skill_candidate_name?: string;
  skill_candidate_description?: string;
  skill_candidate_steps?: string[];
  // 等用户点 [保存技能]/[忽略]；resolve 后置 false → 按钮消失 / 显示结果
  skill_candidate_awaiting?: boolean;
  // resolve 后记录用户最终决定（true=已保存, false=已忽略），驱动结果文案
  skill_candidate_accepted?: boolean;
  skill_candidate_sid?: string;    // 该卡所属 session（回传 WS 时不需要但便于定位）
  // PPT Pro WI-4: 大纲确认卡 payload（后端 propose，前端 decision）。
  outline_id?: string;
  topic?: string;
  outline_md?: string;
  sources_count?: number;
  no_research?: boolean;
  history?: PPTOutlineHistoryItem[];
  ppt_outline_awaiting?: boolean;
  ppt_outline_decision_status?: string;
  // AC-23: one in-stream projection per durable workflow run.
  workflow_run_id?: string;
  workflow_name?: string;
  workflow_version?: string;
  workflow_status?: WorkflowProgressStatus;
  workflow_stage?: string;
  workflow_stage_id?: string;
  workflow_stage_instance_id?: string;
  workflow_transition?: string;
  workflow_ordinal?: number;
  workflow_display_ordinal?: number;
  workflow_total?: number;
  workflow_completed_count?: number;
  workflow_seq?: number;
  workflow_terminal?: boolean;
  workflow_event_id?: string;
  workflow_metrics?: Record<string, string | number | boolean>;
  workflow_duration_ms?: number;
  workflow_elapsed_ms?: number;
  workflow_started_at?: number;
  workflow_updated_at?: number;
  workflow_degraded?: boolean;
  workflow_warning_count?: number;
  workflow_next_stage?: string;
  workflow_action?: string;
  workflow_result?: string;
  workflow_result_code?: string;
  workflow_diagnostic_codes?: string[];
  workflow_published?: number;
  workflow_discarded?: number;
  workflow_repaired?: number;
  workflow_skipped_stage_ids?: string[];
  workflow_retry_action_id?: "retry_from_start";
  workflow_error?: string;
  workflow_recovery_action?: string;
  workflow_capability?: "deep_research_progress_v5";
  workflow_visibility?: "hidden" | "visible";
  workflow_v5?: WorkflowV5ProgressProjection;
  workflow_v7_children?: WorkflowV7ChildProgress[];
  workflow_v7_children_seq?: number;
  workflow_delivery?: WorkflowDeliveryAggregate;
  // Bookkeeping
  ts: number;
}

export interface Todo {
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}

export type SessionStatus =
  | "idle"
  | "thinking"
  | "running"
  | "permission"
  | "error";

/** P5-S3-Inbox — single supervisor alert payload, kept in two places:
 *   • `supervisor_alert` (latest, drives PetStateMachine)
 *   • `supervisor_inbox`  (queue of unhandled, drives toolbar badge + MessageStreamPanel) */
export interface SupervisorAlertEntry {
  alert_id: string;
  severity: "green" | "yellow" | "red";
  action: "nudge" | "ask_user";
  diagnosis: string;
  user_message: string;
  suggested_buttons: string[];
  received_at: number;
}

/** 2026-05-31 restore — single-snapshot of "how full is this session's
 *  context". All numbers are LLM-authoritative (from ``usage.prompt_tokens``
 *  of the most recent response) except ``context_window`` / thresholds
 *  which come from the resolved ``ModelContextInfo``. */
export interface ContextUsageSnapshot {
  session_id: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  context_window: number;
  /** Practical ceiling = window × effective_pct (typically 0.95). */
  effective_ceiling: number;
  /** Suggested compact threshold = window × compact_at_pct. Ring turns
   *  orange when prompt_tokens crosses this line. */
  compact_at: number;
  /** "Recall sweet-spot" upper-bound — past this point the model's needle
   *  recall degrades sharply. Shown as a yellow tick on the ring. */
  recall_sweet: number;
  updated_at: number;
  /** True when this snapshot is a model-only stub emitted before any LLM
   *  turn has happened (prompt_tokens=0). UI may render this slightly
   *  dimmer than a real measured snapshot. */
  stub?: boolean;
  /** Context OS ON only: body-free facts for actual provider attempts. */
  attempts?: ContextAttemptSnapshot[];
}

export interface SessionState {
  base_session_id: string;
  code_session_id: string | null;
  project_root: string | null;
  project_name: string;
  messages: Message[];
  todos: Todo[];
  token_usage: { prompt: number; completion: number };
  /** 2026-05-31 restore — Claude-Code-style context-usage snapshot pushed
   * by backend after every LLM turn (and once on connect via
   * `context_usage_request`). Drives ring gauge + breakdown modal.
   * `null` = never received → ring renders dimmed. */
  context_usage?: ContextUsageSnapshot | null;
  status: SessionStatus;
  last_activity: number;
  inflight: boolean;
  // P5-S3: supervisor signals — fed by `supervisor_alert` ws event +
  // local heuristics (recent tool-call repeat detection happens in
  // backend; here we only stash what the alert told us). Drives the
  // pet's visual state (PetStateMachine) + tile severity colour.
  current_iteration?: number;
  max_iterations?: number;
  /** Highest signature-window count seen in the most recent watchdog
   * snapshot, used in severity_score. Reset to 0 when no recent alert. */
  tool_signature_repeat?: number;
  /** Last supervisor severity colour (green | yellow | red). */
  supervisor_severity?: "green" | "yellow" | "red";
  /** Latest supervisor alert payload, or null if none active. */
  supervisor_alert?: SupervisorAlertEntry | null;
  /** Inbox of unhandled supervisor alerts (yellow + red). Newest first.
   * Lives in the session so jumping to a session shows its history.
   * Cleared on dismiss / handle. */
  supervisor_inbox?: SupervisorAlertEntry[];
  // P5-S2 Phase 5: 自愈尝试计数。0 = 未在自愈中；>0 表示
  // backend AutoResumeOrchestrator 正在第 N 次尝试。
  // ws.ts 收到 auto_resume_started 时设为 attempt 值；
  // succeeded / exhausted 时归 0。AutoResumeBanner 订阅此字段。
  auto_resume_attempts?: number;
  // multi-provider-management Phase 5: per-session provider binding.
  // `provider_id == null` 表示走全局 chain；非空时表示 pin 到某 provider。
  // `preferred_model` 可选 model 覆盖，独立于 provider 选择。
  // ws.ts 在 code_sessions_list_response / code_session_provider_set /
  // code_session_model_set 时写入这两个字段。
  provider_id?: string | null;
  preferred_model?: string | null;
  // code-session-model-params S2: Cursor 风格的 per-session 模型参数。
  // null/undefined = 走 provider 默认（未显式配置）。后端 echo 回的
  // `model_params` 原样写入这里，ChangeModelModal 据此预填。
  model_params?: CodeModelParams | null;
}

/** code-session-model-params — the structured picker params persisted
 * per code session. Mirrors the backend IPC contract exactly:
 *   { thinking, fast, context, effort }
 * Effort `extra_high`/`max` clamp to `high` server-side (OpenAI only
 * exposes low/medium/high) — the UI still shows all 5 rungs. */
export interface CodeModelParams {
  thinking?: boolean;
  fast?: boolean;
  context?: "300k" | "1m";
  effort?: "low" | "medium" | "high" | "extra_high" | "max";
}

interface SessionsStore {
  active_sid: string;
  sessions: Record<string, SessionState>;
  // Concurrency limiter inflight count (for status rendering)
  inflight_count: number;
  inflight_max: number;

  set_active(sid: string): void;
  ensure(sid: string, init?: Partial<SessionState>): void;
  upsert(sid: string, patch: Partial<SessionState>): void;
  push_message(sid: string, msg: Omit<Message, "id" | "ts"> & { id?: string; ts?: number }): void;
  /** Replace the entire message list for a session — used by F5
   * rehydration when the panel reloads and pulls history from
   * SessionDB via `session_messages_load`. */
  set_messages(sid: string, messages: Message[]): void;
  reduce_workflow_event(sid: string, event: WorkflowEventEnvelope): boolean;
  merge_history_messages(
    sid: string,
    messages: Message[],
    workflowEvents: WorkflowEventEnvelope[],
  ): void;
  /** superpowers 决策2: 用户点了 plan 卡片的 [执行]/[取消] 后，清掉该
   *  plan 消息的 awaiting_confirm（按钮消失）。msgId 可空 → 清该会话最近
   *  一条仍 awaiting 的 plan（超时取消路径用）。 */
  resolve_plan(sid: string, msgId?: string): void;
  /** FP-5 WI-4.3c: 用户点了技能卡的 [保存技能]/[忽略] 后，清掉该卡的
   *  awaiting（按钮消失），并记录 accepted 决定以驱动结果文案。镜像
   *  resolve_plan 的 by-id / latest-fallback 模式。candidateId 优先按 id 命中。 */
  resolve_skill_candidate(sid: string, candidateId: number, accepted: boolean): void;
  /** PPT Pro WI-4: 清掉指定 outline_id 的待确认卡。用于本地 decision
   *  以及后端 `ppt_outline_resolved` 广播清理 stale 副本。 */
  resolve_ppt_outline(sid: string, outlineId: string, decisionStatus?: string): void;
  upsert_todos(sid: string, todos: Todo[]): void;
  remove(sid: string): void;
  set_inflight(delta: number): void;
  // P5-S3: supervisor surface
  apply_supervisor_alert(sid: string, alert: SupervisorAlertEntry): void;
  clear_supervisor_alert(sid: string): void;
  /** Remove a single alert from the inbox (and clear `supervisor_alert`
   * if it matches). Used when user clicks a button or "已知道" in the
   * MessageStreamPanel. */
  dismiss_alert(sid: string, alert_id: string): void;
  /** Clear all alerts of one severity across all sessions. Used by the
   * toolbar's "全部已读" sweep button. */
  dismiss_all_alerts(severity: "yellow" | "red"): void;
}

const newId = (): string =>
  globalThis.crypto?.randomUUID?.() ?? `m-${Math.random().toString(36).slice(2, 10)}`;

function dedupe_ppt_outline_messages(messages: Message[]): Message[] {
  const lastByOutlineId = new Map<string, number>();
  messages.forEach((m, idx) => {
    if (m.role === "ppt_outline" && m.outline_id) {
      lastByOutlineId.set(m.outline_id, idx);
    }
  });
  if (lastByOutlineId.size === 0) return messages;
  return messages.filter((m, idx) => {
    if (m.role !== "ppt_outline" || !m.outline_id) return true;
    return lastByOutlineId.get(m.outline_id) === idx;
  });
}

const WORKFLOW_CARD_EVENTS = new Set([
  "workflow.accepted",
  "workflow.progress",
  "workflow.decision",
  "workflow.final",
]);

const DEEP_RESEARCH_STAGE_METRICS: Record<string, readonly string[]> = {
  normalize: ["mode"],
  plan: ["question_count", "active_branch_count"],
  expand: ["query_count"],
  search: [
    "providers", "providers_attempted", "providers_hit", "actual_requests", "hits",
    "empty", "timeouts", "cooldown_skips", "busy_skips", "queue_timeouts", "probes",
    "rescue_considered_count", "rescue_executed_count", "candidates", "kept",
  ],
  direct: ["direct_sources", "candidates"],
  fetch: ["attempted", "succeeded", "dropped"],
  score: ["passages", "kept"],
  gap: ["iteration", "followup_count", "new_evidence"],
  rerank: ["passages", "domains"],
  synth: ["sections", "claim_count"],
  cite: [
    "citations", "domains", "supported", "unsupported", "support_rate",
    "factual_claims_pre_repair", "supported_factual", "published", "discarded",
    "repaired", "body_bytes",
  ],
  persist: ["artifact_count", "report_bytes", "status"],
  finalize: [
    "citations", "status", "published", "discarded", "repaired", "actual_requests",
    "hits", "empty", "timeouts", "cooldown_skips", "busy_skips", "queue_timeouts",
    "probes", "rescue_considered_count", "rescue_executed_count", "candidates",
  ],
};

const DEEP_RESEARCH_SKIPPABLE_STAGES = new Set([
  "fetch", "score", "gap", "rerank", "synth", "cite", "persist",
]);

const WORKFLOW_RESULT_CODES = new Set([
  "started", "waiting", "failed", "cancelled", "stage_ok", "stage_degraded",
  "success", "partial", "degraded", "completed", "insufficient_evidence", "no_results",
]);

const WORKFLOW_DIAGNOSTIC_CODES = new Set([
  "cooldown", "half_open_busy", "timeout", "blocked", "captcha", "rate_limit",
  "http_error", "invalid_response", "budget_exhausted", "provider_degraded",
  "partial_results", "evidence_missing", "low_quality_evidence", "missing_exact_token",
  "insufficient_support", "claim_unsupported", "claim_pruned", "deterministic_repair",
  "insufficient_evidence", "artifact_missing", "no_results", "degraded",
  "deadline_exhausted", "search_port_unavailable", "provider_failure",
  "direct_failure", "fetch_failure", "blob_unavailable", "low_quality_source",
  "low_quality_content", "search_degraded", "support_rate_below_threshold",
  "published_factual_below_threshold", "citation_count_below_threshold",
  "domain_count_below_threshold", "body_bytes_below_threshold",
]);

const WORKFLOW_DELIVERY_STATUSES = new Set<WorkflowDeliveryAggregate["status"]>([
  "queued", "delivering", "delivered", "retrying", "fenced", "failed",
]);

function workflowEventTime(event: WorkflowEventEnvelope): number {
  const created = Number(event.created_at);
  return Number.isFinite(created) && created > 0 ? created * 1000 : Date.now();
}

function workflowErrorText(value: unknown): string | undefined {
  if (!value) return undefined;
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    for (const key of ["message", "message_ref", "reason", "code"]) {
      if (typeof item[key] === "string" && item[key]) return item[key] as string;
    }
  }
  return undefined;
}

function workflowNumber(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : undefined;
}

function parseWorkflowDeliveryAggregate(value: unknown): WorkflowDeliveryAggregate | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const raw = value as Record<string, unknown>;
  const status = String(raw.status || "") as WorkflowDeliveryAggregate["status"];
  const runId = String(raw.run_id || "").trim();
  const manifestRef = String(raw.manifest_ref || "").trim();
  const countKeys = [
    "required_total", "pending", "delivering", "delivered", "retrying", "fenced", "failed",
  ] as const;
  const counts = Object.fromEntries(countKeys.map((key) => [key, Number(raw[key])])) as Record<
    typeof countKeys[number], number
  >;
  const updatedAt = Number(raw.updated_at);
  if (
    raw.schema_version !== 1 || !runId || !manifestRef ||
    !WORKFLOW_DELIVERY_STATUSES.has(status) ||
    !countKeys.every((key) => Number.isInteger(counts[key]) && counts[key] >= 0) ||
    !Number.isFinite(updatedAt) || updatedAt < 0
  ) return undefined;
  if (
    counts.pending + counts.delivering + counts.delivered + counts.retrying +
      counts.fenced + counts.failed !== counts.required_total ||
    counts.required_total < 1
  ) return undefined;
  return {
    schema_version: 1,
    run_id: runId,
    manifest_ref: manifestRef,
    status,
    required_total: counts.required_total,
    pending: counts.pending,
    delivering: counts.delivering,
    delivered: counts.delivered,
    retrying: counts.retrying,
    fenced: counts.fenced,
    failed: counts.failed,
    updated_at: updatedAt,
  };
}

function workflowSafeText(value: unknown, maximum = 240): string | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.trim().replace(/\s+/g, " ").slice(0, maximum);
  if (!text || /https?:\/\//i.test(text)) return undefined;
  return text;
}

function workflowResultCode(value: unknown): string | undefined {
  const code = typeof value === "string" ? value.trim() : "";
  return WORKFLOW_RESULT_CODES.has(code) ? code : undefined;
}

function workflowDiagnosticCodes(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const codes = Array.from(new Set(
    value.map((item) => String(item)).filter((item) => WORKFLOW_DIAGNOSTIC_CODES.has(item)),
  )).sort();
  return codes.length > 0 ? codes : undefined;
}

function workflowSkippedStageIds(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const stages = Array.from(new Set(
    value.map((item) => String(item)).filter((item) => DEEP_RESEARCH_SKIPPABLE_STAGES.has(item)),
  ));
  return stages.length > 0 ? stages.slice(0, 7) : undefined;
}

function sanitizeWorkflowMetrics(
  stageId: string,
  value: unknown,
): Record<string, string | number | boolean> | undefined {
  if (!value || typeof value !== "object") return undefined;
  const allowed = DEEP_RESEARCH_STAGE_METRICS[stageId] ?? [];
  const source = value as Record<string, unknown>;
  const metrics: Record<string, string | number | boolean> = {};
  for (const key of allowed) {
    const item = source[key];
    if (typeof item === "string" || typeof item === "boolean") {
      metrics[key] = item;
    } else if (typeof item === "number" && Number.isFinite(item)) {
      metrics[key] = item;
    }
  }
  return Object.keys(metrics).length > 0 ? metrics : undefined;
}

const V5_ACTIONS = new Set([
  "normalize_request", "model_dimensions", "plan_queries", "expand_queries",
  "search_sources", "find_first_party_sources", "fetch_sources", "score_evidence",
  "evaluate_gaps", "research_gap", "commit_gap_result", "rerank_evidence",
  "synthesize_report", "audit_quality", "repair_report", "commit_repair",
  "persist_report", "finalize_delivery", "finalize_insufficient",
]);
const V5_RESULTS = new Set([
  "started", "waiting", "failed", "cancelled", "completed", "insufficient_evidence",
]);
const V5_CONTROL_ACTIONS = new Set<WorkflowV5ControlAction>([
  "none", "generate_now", "continue_research", "retry_from_start", "cancel_settle",
]);
const V5_CONTROL_STATUSES = new Set<WorkflowV5ControlStatus>([
  "none", "open", "accepted", "observed", "settled", "consumed", "rejected", "expired",
]);
const V5_HARD_FAILURES = new Set([
  "completed_core_uncovered", "unsupported_key_claim", "invalid_or_captcha_citation",
  "secondary_replaces_available_official", "citation_dimension_mismatch",
  "internal_diagnostics_leak",
]);
const V5_LEASE_REASONS = new Set([
  "none", "before_soft_or_lease_checkpoint", "lease_renewed_measurable_gain",
  "lease_not_renewed_no_gain", "automatic_cap", "running",
  "generate_now_settling", "cancelled", "plateau_settling",
  "lease_no_gain_settling", "automatic_cap_settling",
]);
const V5_GAP_REASONS = new Set([
  "insufficient_admitted_passages", "insufficient_strong_distinct_families",
  "winning_relevance_below_threshold", "duplicate_rate_above_threshold",
  "invalid_rate_above_threshold", "first_party_requirement_unsatisfied", "evidence_gap",
]);
const V5_REJECTION_REASONS = new Set([
  "invalid_page", "body_too_short", "body_span_missing",
  "dimension_relevance_below_threshold", "other_rejected",
]);

function nonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

function v5MaybeCount(value: unknown): number | "none" | undefined {
  return value === "none" ? "none" : nonNegativeInteger(value);
}

function parseV5Progress(payload: Record<string, unknown>): WorkflowV5ProgressProjection | undefined {
  if (
    payload.schema_version !== 5 ||
    payload.capability !== "deep_research_progress_v5" ||
    payload.workflow_name !== "deep_research" ||
    payload.workflow_version !== "v5"
  ) return undefined;
  const action = String(payload.action || "");
  const result = String(payload.result || "");
  const nextStep = String(payload.next_step || "");
  const controlAction = String(payload.control_action || "") as WorkflowV5ControlAction;
  const controlStatus = String(payload.control_status || "") as WorkflowV5ControlStatus;
  if (!V5_ACTIONS.has(action) || !V5_RESULTS.has(result) ||
    (nextStep !== "none" && !V5_ACTIONS.has(nextStep)) ||
    !V5_CONTROL_ACTIONS.has(controlAction) || !V5_CONTROL_STATUSES.has(controlStatus)) {
    return undefined;
  }
  const dimensions = payload.dimension_counts as Record<string, unknown> | undefined;
  const changes = payload.dimension_status_changes as Record<string, unknown> | undefined;
  const sources = payload.source_counts as Record<string, unknown> | undefined;
  if (!dimensions || !changes || !sources) return undefined;
  const dimensionCounts = {
    total: nonNegativeInteger(dimensions.total),
    core_total: nonNegativeInteger(dimensions.core_total),
    covered: nonNegativeInteger(dimensions.covered),
    partially_covered: nonNegativeInteger(dimensions.partially_covered),
    uncovered: nonNegativeInteger(dimensions.uncovered),
    not_applicable: nonNegativeInteger(dimensions.not_applicable),
    core_covered: nonNegativeInteger(dimensions.core_covered),
    core_partially_covered: nonNegativeInteger(dimensions.core_partially_covered),
    core_uncovered: nonNegativeInteger(dimensions.core_uncovered),
  };
  const dimensionChanges = {
    improved: v5MaybeCount(changes.improved),
    regressed: v5MaybeCount(changes.regressed),
    unchanged: v5MaybeCount(changes.unchanged),
  };
  const sourceCounts = {
    valid: nonNegativeInteger(sources.valid),
    first_party: nonNegativeInteger(sources.first_party),
  };
  if (Object.values(dimensionCounts).some((value) => value === undefined) ||
    Object.values(dimensionChanges).some((value) => value === undefined) ||
    Object.values(sourceCounts).some((value) => value === undefined)) return undefined;

  let activeGap: WorkflowV5ProgressProjection["active_gap"] = "none";
  if (payload.active_gap !== "none") {
    if (!payload.active_gap || typeof payload.active_gap !== "object") return undefined;
    const gap = payload.active_gap as Record<string, unknown>;
    const gapStatus = String(gap.status || "") as Exclude<WorkflowV5ProgressProjection["active_gap"], "none">["status"];
    const workKind = String(gap.work_kind || "") as Exclude<WorkflowV5ProgressProjection["active_gap"], "none">["work_kind"];
    const dimensionOrdinal = nonNegativeInteger(gap.dimension_ordinal);
    if (!["pending", "running", "completed", "failed", "cancelled"].includes(gapStatus) ||
      !["query", "source_target", "fetch"].includes(workKind) || dimensionOrdinal === undefined) {
      return undefined;
    }
    activeGap = { status: gapStatus, work_kind: workKind, dimension_ordinal: dimensionOrdinal };
  }
  if (!Array.isArray(payload.hard_failures) ||
    payload.hard_failures.some((item) => typeof item !== "string" || !V5_HARD_FAILURES.has(item))) {
    return undefined;
  }
  if (!Array.isArray(payload.failed_dimensions) || payload.failed_dimensions.length > 8 ||
    !Array.isArray(payload.rejection_reasons) || payload.rejection_reasons.length > 5) {
    return undefined;
  }
  const failedDimensions: WorkflowV5ProgressProjection["failed_dimensions"] = [];
  for (const value of payload.failed_dimensions) {
    if (!value || typeof value !== "object") return undefined;
    const item = value as Record<string, unknown>;
    const ordinal = nonNegativeInteger(item.dimension_ordinal);
    const failedStatus = String(item.status || "") as "uncovered" | "partially_covered";
    if (ordinal === undefined || !["uncovered", "partially_covered"].includes(failedStatus) ||
      !Array.isArray(item.reason_codes) || item.reason_codes.length < 1 || item.reason_codes.length > 6 ||
      item.reason_codes.some((reason) => typeof reason !== "string" || !V5_GAP_REASONS.has(reason))) {
      return undefined;
    }
    failedDimensions.push({
      dimension_ordinal: ordinal,
      status: failedStatus,
      reason_codes: [...item.reason_codes] as string[],
    });
  }
  const rejectionReasons: WorkflowV5ProgressProjection["rejection_reasons"] = [];
  for (const value of payload.rejection_reasons) {
    if (!value || typeof value !== "object") return undefined;
    const item = value as Record<string, unknown>;
    const reasonCode = String(item.reason_code || "");
    const count = nonNegativeInteger(item.count);
    if (!V5_REJECTION_REASONS.has(reasonCode) || count === undefined || count < 1) return undefined;
    rejectionReasons.push({ reason_code: reasonCode, count });
  }
  const discarded = v5MaybeCount(payload.discarded);
  const remainingGap = v5MaybeCount(payload.remaining_gap);
  const qualityScore = v5MaybeCount(payload.quality_score);
  const tokenBudgetRatio = v5MaybeCount(payload.token_budget_ratio);
  const elapsedSeconds = nonNegativeInteger(payload.elapsed_seconds);
  const predicted = String(payload.predicted_delivery || "") as WorkflowV5ProgressProjection["predicted_delivery"];
  const softCheckpoint = String(payload.soft_checkpoint || "") as WorkflowV5ProgressProjection["soft_checkpoint"];
  const leaseReason = String(payload.lease_reason || "");
  const parentOperation = String(payload.parent_operation || "");
  if ([discarded, remainingGap, qualityScore, tokenBudgetRatio, elapsedSeconds].some((value) => value === undefined) ||
    !["pending", "completed", "partial", "insufficient_evidence"].includes(predicted) ||
    !["none", "before", "reached"].includes(softCheckpoint) ||
    !V5_LEASE_REASONS.has(leaseReason) ||
    !(parentOperation === "none" || /^op_[0-9a-f]{20}$/.test(parentOperation))) return undefined;
  return {
    action,
    result,
    discarded: discarded!,
    remaining_gap: remainingGap!,
    next_step: nextStep,
    dimension_counts: dimensionCounts as WorkflowV5ProgressProjection["dimension_counts"],
    dimension_status_changes: dimensionChanges as WorkflowV5ProgressProjection["dimension_status_changes"],
    source_counts: sourceCounts as WorkflowV5ProgressProjection["source_counts"],
    active_gap: activeGap,
    elapsed_seconds: elapsedSeconds!,
    soft_checkpoint: softCheckpoint,
    lease_reason: leaseReason,
    quality_score: qualityScore!,
    hard_failures: [...payload.hard_failures] as string[],
    predicted_delivery: predicted,
    token_budget_ratio: tokenBudgetRatio!,
    control_action: controlAction,
    control_status: controlStatus,
    parent_operation: parentOperation,
    failed_dimensions: failedDimensions,
    rejection_reasons: rejectionReasons,
  };
}

function isDeepResearchCompletedStage(payload: Record<string, unknown>): boolean {
  const stageId = String(payload.stage_id || "");
  return Number(payload.schema_version) === 2 &&
    String(payload.kind || "") === "stage" &&
    String(payload.workflow_name || "") === "deep_research" &&
    stageId in DEEP_RESEARCH_STAGE_METRICS &&
    String(payload.status || payload.transition || "") === "completed";
}

const V7_CHILD_STATUSES = new Set<WorkflowV7ChildStatus>([
  "queued", "running", "retrying", "valid", "insufficient",
]);

function parseV7Children(
  payload: Record<string, unknown>,
): WorkflowV7ChildProgress[] | undefined {
  if (
    payload.schema_version !== 7 ||
    payload.kind !== "research_children" ||
    payload.workflow_version !== "v7" ||
    !Array.isArray(payload.children) ||
    payload.children.length < 2 ||
    payload.children.length > 6
  ) return undefined;
  const seen = new Set<string>();
  const result: WorkflowV7ChildProgress[] = [];
  for (const raw of payload.children) {
    if (!raw || typeof raw !== "object") return undefined;
    const item = raw as Record<string, unknown>;
    const childId = String(item.child_id || "").trim();
    const question = String(item.question || "").trim();
    const status = String(item.status || "") as WorkflowV7ChildStatus;
    const attempt = Number(item.attempt);
    const maxAttempts = Number(item.max_attempts);
    const nSources = Number(item.n_sources);
    if (
      !/^dr-\d+$/.test(childId) || seen.has(childId) ||
      !question || question.length > 600 || !V7_CHILD_STATUSES.has(status) ||
      !Number.isInteger(attempt) || !Number.isInteger(maxAttempts) ||
      attempt < 0 || maxAttempts < 1 || maxAttempts > 3 || attempt > maxAttempts ||
      !Number.isInteger(nSources) || nSources < 0 || nSources > 10_000
    ) return undefined;
    result.push({
      child_id: childId,
      question,
      status,
      attempt,
      max_attempts: maxAttempts,
      n_sources: nSources,
      reason_code: typeof item.reason_code === "string"
        ? item.reason_code.slice(0, 128)
        : undefined,
    });
    seen.add(childId);
  }
  return result;
}

function appendWorkflowStage(
  messages: Message[],
  event: WorkflowEventEnvelope,
  payload: Record<string, unknown>,
  runId: string,
  seq: number,
): Message[] {
  const v5 = parseV5Progress(payload);
  if (v5 && payload.kind === "stage" && payload.status === "completed") {
    const stageInstanceId = String(payload.stage_instance_id || "");
    const eventId = String(event.event_id || "").trim();
    const visibility = payload.visibility === "visible" ? "visible" :
      payload.visibility === "hidden" ? "hidden" : undefined;
    if (!eventId || !/^[0-9a-f]{24}$/.test(stageInstanceId) || !visibility) return messages;
    const duplicate = messages.some((message) =>
      message.role === "workflow_stage" && message.workflow_run_id === runId &&
      (message.workflow_stage_instance_id === stageInstanceId || message.workflow_event_id === eventId),
    );
    if (duplicate) return messages;
    const stageId = String(payload.public_stage_id || payload.stage_id || "");
    const child: Message = {
      id: `workflow-stage:${runId}:${stageInstanceId}`,
      role: "workflow_stage",
      text: String(payload.summary || "阶段已完成"),
      ts: workflowEventTime(event),
      workflow_run_id: runId,
      workflow_name: "深度调研",
      workflow_version: "v5",
      workflow_status: "completed",
      workflow_stage: String(payload.stage || "已完成阶段"),
      workflow_stage_id: stageId,
      workflow_stage_instance_id: stageInstanceId,
      workflow_transition: "completed",
      workflow_ordinal: workflowNumber(payload.ordinal),
      workflow_total: workflowNumber(payload.total) ?? 9,
      workflow_seq: seq,
      workflow_event_id: eventId,
      workflow_action: v5.action,
      workflow_result: v5.result,
      workflow_discarded: typeof v5.discarded === "number" ? v5.discarded : undefined,
      workflow_next_stage: v5.next_step === "none" ? undefined : v5.next_step,
      workflow_degraded: v5.hard_failures.length > 0 ||
        ["partial", "insufficient_evidence"].includes(v5.predicted_delivery),
      workflow_capability: "deep_research_progress_v5",
      workflow_visibility: visibility,
      workflow_v5: v5,
    };
    const insertAt = messages.findIndex((message) => message.ts > child.ts);
    const updated = [...messages];
    updated.splice(insertAt < 0 ? updated.length : insertAt, 0, child);
    return updated;
  }
  if (!isDeepResearchCompletedStage(payload)) return messages;
  const eventId = String(event.event_id || "").trim();
  if (!eventId) return messages;
  const duplicate = messages.some((message) =>
    message.role === "workflow_stage" &&
    message.workflow_run_id === runId &&
    (message.workflow_event_id === eventId || message.workflow_seq === seq),
  );
  if (duplicate) return messages;

  const stageId = String(payload.stage_id || "").trim();
  const summary = String(payload.summary || payload.text || "").trim();
  const child: Message = {
    id: `workflow-stage:${eventId}`,
    role: "workflow_stage",
    text: summary,
    ts: workflowEventTime(event),
    workflow_run_id: runId,
    workflow_name: String(payload.workflow_label || "深度调研"),
    workflow_version: String(payload.workflow_version || ""),
    workflow_status: "completed",
    workflow_stage: String(payload.stage || stageId || "已完成阶段"),
    workflow_stage_id: stageId,
    workflow_stage_instance_id: String(payload.stage_instance_id || ""),
    workflow_transition: "completed",
    workflow_ordinal: workflowNumber(payload.ordinal),
    workflow_total: workflowNumber(payload.total) ?? 13,
    workflow_completed_count: workflowNumber(payload.completed_count),
    workflow_seq: seq,
    workflow_event_id: eventId,
    workflow_metrics: sanitizeWorkflowMetrics(stageId, payload.metrics),
    workflow_duration_ms: workflowNumber(payload.duration_ms),
    workflow_degraded: Boolean(payload.degraded),
    workflow_action: workflowSafeText(payload.action),
    workflow_result: workflowSafeText(payload.result),
    workflow_result_code: workflowResultCode(payload.result_code),
    workflow_diagnostic_codes: workflowDiagnosticCodes(payload.diagnostic_codes),
    workflow_published: workflowNumber(payload.published),
    workflow_discarded: workflowNumber(payload.discarded),
    workflow_repaired: workflowNumber(payload.repaired),
    workflow_next_stage: typeof payload.next_stage === "string"
      ? payload.next_stage
      : undefined,
  };
  const insertAt = messages.findIndex((message) => message.ts > child.ts);
  const updated = [...messages];
  updated.splice(insertAt < 0 ? updated.length : insertAt, 0, child);
  return updated;
}

export function applyWorkflowEvent(
  messages: Message[],
  event: WorkflowEventEnvelope,
): { messages: Message[]; handled: boolean } {
  const eventType = String(event.event_type || "");
  if (!WORKFLOW_CARD_EVENTS.has(eventType)) {
    return { messages, handled: false };
  }
  if (eventType === "workflow.decision") {
    const payload = event.payload || {};
    const prompt = payload.prompt && typeof payload.prompt === "object"
      ? payload.prompt as Record<string, unknown>
      : {};
    if (String(payload.decision_kind || prompt.kind || "") !== "ppt_outline") {
      return { messages, handled: true };
    }
    const outlineId = String(prompt.outline_id || prompt.decision_id || "").trim();
    if (!outlineId) return { messages, handled: true };
    const decisionStatus = String(payload.status || "open");
    const next: Message = {
      id: `workflow-decision:${String(payload.decision_id || outlineId)}`,
      role: "ppt_outline",
      ts: workflowEventTime(event),
      outline_id: outlineId,
      topic: String(prompt.topic || ""),
      outline_md: String(prompt.outline_markdown || ""),
      sources_count: Number(prompt.sources_count || 0),
      no_research: Boolean(prompt.no_research),
      history: [],
      ppt_outline_awaiting: decisionStatus === "open",
      ppt_outline_decision_status: decisionStatus === "open" ? undefined : decisionStatus,
    };
    return {
      messages: dedupe_ppt_outline_messages([
        ...messages.filter((message) => message.outline_id !== outlineId),
        next,
      ]),
      handled: true,
    };
  }
  const runId = String(event.run_id || "").trim();
  const seq = Number(event.seq);
  if (!runId || !Number.isInteger(seq) || seq < 0) {
    return { messages, handled: false };
  }

  const payload = event.payload || {};
  const incomingDelivery = parseWorkflowDeliveryAggregate(event.delivery_aggregate);
  const v5Projection = parseV5Progress(payload);
  const cardId = `workflow-run:${runId}`;
  let messagesWithStage = appendWorkflowStage(messages, event, payload, runId, seq);
  const index = messagesWithStage.findIndex(
    (message) =>
      message.role === "workflow_progress" && message.workflow_run_id === runId,
  );
  let previous = index >= 0 ? messagesWithStage[index] : undefined;
  const incomingV7Children = parseV7Children(payload);
  if (
    previous && incomingV7Children &&
    seq > (previous.workflow_v7_children_seq ?? -1)
  ) {
    const updatedPrevious: Message = {
      ...previous,
      workflow_v7_children: incomingV7Children,
      workflow_v7_children_seq: seq,
    };
    messagesWithStage = [...messagesWithStage];
    messagesWithStage[index] = updatedPrevious;
    previous = updatedPrevious;
  }
  if (
    previous?.workflow_terminal ||
    (typeof previous?.workflow_seq === "number" && seq <= previous.workflow_seq)
  ) {
    if (
      previous && incomingDelivery && incomingDelivery.run_id === runId &&
      (!previous.workflow_delivery || (
        previous.workflow_delivery.manifest_ref === incomingDelivery.manifest_ref &&
        incomingDelivery.updated_at >= previous.workflow_delivery.updated_at
      ))
    ) {
      const updated = [...messagesWithStage];
      updated[index] = { ...previous, workflow_delivery: incomingDelivery };
      return { messages: updated, handled: true };
    }
    return { messages: messagesWithStage, handled: true };
  }

  const incomingOrdinal = Number(payload.ordinal);
  const ordinal = Number.isFinite(incomingOrdinal) && incomingOrdinal >= 0
    ? incomingOrdinal
    : previous?.workflow_ordinal ?? 0;
  const incomingTotal = Number(payload.total);
  const total = Number.isFinite(incomingTotal) && incomingTotal >= 0
    ? incomingTotal
    : previous?.workflow_total ?? 0;
  let status: WorkflowProgressStatus = previous?.workflow_status ?? "running";
  let terminal = false;
  if (eventType === "workflow.progress") {
    const transition = String(payload.status || "started");
    status = transition === "waiting"
      ? "waiting"
      : transition === "failed"
        ? "failed"
        : transition === "cancelled"
          ? "cancelled"
          : "running";
  } else if (eventType === "workflow.final") {
    const finalStatus = String(payload.status || "failed");
    status = finalStatus === "completed"
      ? "completed"
      : finalStatus === "cancelled"
        ? "cancelled"
        : "failed";
    terminal = true;
  } else {
    status = "running";
  }

  const incomingVersion = String(payload.workflow_version || previous?.workflow_version || "");
  const preserveActiveV5Control = Boolean(
    previous?.workflow_v5 &&
    previous.workflow_v5.control_action !== "none" &&
    ["open", "accepted", "observed"].includes(previous.workflow_v5.control_status) &&
    v5Projection?.control_action === "none" &&
    v5Projection.control_status === "none" &&
    eventType === "workflow.progress" &&
    payload.kind !== "stage",
  );
  let mergedV5 = v5Projection
    ? (payload.kind === "stage" && payload.status === "completed") || !previous?.workflow_v5
      ? v5Projection
      : {
          ...previous.workflow_v5,
          action: v5Projection.action,
          result: v5Projection.result,
          next_step: v5Projection.next_step,
          elapsed_seconds: Math.max(previous.workflow_v5.elapsed_seconds, v5Projection.elapsed_seconds),
          soft_checkpoint: v5Projection.soft_checkpoint,
          lease_reason: v5Projection.lease_reason,
          // A hidden next-stage `started` projection carries none/none because it
          // does not own control state. Keep the last server-projected action
          // actionable until an explicit control update or terminal event closes it.
          control_action: preserveActiveV5Control
            ? previous.workflow_v5.control_action
            : v5Projection.control_action,
          control_status: preserveActiveV5Control
            ? previous.workflow_v5.control_status
            : v5Projection.control_status,
          parent_operation: v5Projection.parent_operation,
        }
    : previous?.workflow_v5;
  if (eventType === "workflow.final" && incomingVersion === "v5" && mergedV5) {
    const matrix = Array.isArray(payload.action_matrix) ? payload.action_matrix : [];
    const projected = matrix.find((item) =>
      item && typeof item === "object" && (item as Record<string, unknown>).enabled === true &&
      V5_CONTROL_ACTIONS.has(String((item as Record<string, unknown>).action_id) as WorkflowV5ControlAction)
    ) as Record<string, unknown> | undefined;
    const recovery = payload.recovery_action === "retry_from_start" ? "retry_from_start" : undefined;
    const terminalAction = String(projected?.action_id || recovery || "none") as WorkflowV5ControlAction;
    const delivery = String(payload.delivery_status || "");
    mergedV5 = {
      ...mergedV5,
      control_action: terminalAction,
      control_status: terminalAction === "none" ? "none" : "open",
      predicted_delivery: ["completed", "partial", "insufficient_evidence"].includes(delivery)
        ? delivery as WorkflowV5ProgressProjection["predicted_delivery"]
        : mergedV5.predicted_delivery,
    };
  }
  const incomingElapsedMs = v5Projection && (!previous?.workflow_v5 ||
    (payload.kind === "stage" && payload.status === "completed"))
    ? v5Projection.elapsed_seconds * 1000
    : workflowNumber(payload.elapsed_ms);
  const monotonicElapsedMs = Math.max(
    previous?.workflow_elapsed_ms ?? 0,
    incomingElapsedMs ?? 0,
  );

  const next: Message = {
    id: cardId,
    role: "workflow_progress",
    ts: previous?.ts ?? workflowEventTime(event),
    workflow_run_id: runId,
    workflow_name: String(
      eventType === "workflow.progress"
        ? payload.workflow_label || payload.workflow_name || previous?.workflow_name || "任务"
        : previous?.workflow_name || payload.workflow_label || payload.workflow_name || "任务",
    ),
    workflow_version: String(payload.workflow_version || previous?.workflow_version || ""),
    workflow_status: status,
    workflow_stage: String(payload.stage || previous?.workflow_stage || "准备中"),
    workflow_stage_id:
      workflowSafeText(payload.stage_id, 64) ?? previous?.workflow_stage_id,
    workflow_ordinal: ordinal,
    // Graph revision loops may return to an earlier stage. Keep the stage
    // truthful while the visual percentage remains monotonic.
    workflow_display_ordinal: Math.max(
      previous?.workflow_display_ordinal ?? previous?.workflow_ordinal ?? 0,
      ordinal,
    ),
    workflow_total: total,
    workflow_completed_count:
      workflowNumber(payload.completed_count) ?? previous?.workflow_completed_count,
    workflow_seq: seq,
    workflow_terminal: terminal,
    workflow_event_id: String(event.event_id || ""),
    workflow_elapsed_ms: monotonicElapsedMs,
    workflow_started_at: previous?.workflow_started_at ?? Math.max(
      0,
      workflowEventTime(event) - monotonicElapsedMs,
    ),
    workflow_updated_at: workflowEventTime(event),
    workflow_warning_count:
      workflowNumber(payload.warning_count) ?? previous?.workflow_warning_count,
    workflow_metrics:
      sanitizeWorkflowMetrics(
        workflowSafeText(payload.stage_id, 64) ?? previous?.workflow_stage_id ?? "finalize",
        payload.metrics,
      ) ?? previous?.workflow_metrics,
    workflow_action: v5Projection?.action ??
      workflowSafeText(payload.action) ?? previous?.workflow_action,
    workflow_result: v5Projection?.result ??
      workflowSafeText(payload.result ?? payload.text) ?? previous?.workflow_result,
    workflow_result_code:
      workflowResultCode(payload.result_code ?? payload.status) ?? previous?.workflow_result_code,
    workflow_diagnostic_codes:
      workflowDiagnosticCodes(payload.diagnostic_codes) ?? previous?.workflow_diagnostic_codes,
    workflow_skipped_stage_ids:
      workflowSkippedStageIds(payload.skipped_stage_ids) ?? previous?.workflow_skipped_stage_ids,
    workflow_retry_action_id:
      String(payload.workflow_version || previous?.workflow_version || "") === "v4" &&
      payload.retry_action_id === "retry_from_start"
        ? "retry_from_start"
        : previous?.workflow_retry_action_id,
    workflow_error:
      eventType === "workflow.final" &&
      String(payload.workflow_version || previous?.workflow_version || "") !== "v5"
        ? workflowErrorText(payload.terminal_error ?? payload.error) ?? previous?.workflow_error
        : previous?.workflow_error,
    workflow_recovery_action:
      eventType === "workflow.final" && payload.recovery_action
        ? String(payload.recovery_action)
        : previous?.workflow_recovery_action,
    workflow_capability: v5Projection
      ? "deep_research_progress_v5"
      : previous?.workflow_capability,
    workflow_visibility: v5Projection &&
      (payload.visibility === "hidden" || payload.visibility === "visible")
      ? payload.visibility
      : previous?.workflow_visibility,
    workflow_v5: mergedV5,
    workflow_v7_children: incomingV7Children ?? previous?.workflow_v7_children,
    workflow_v7_children_seq: incomingV7Children
      ? seq
      : previous?.workflow_v7_children_seq,
    workflow_delivery: incomingDelivery ?? previous?.workflow_delivery,
  };

  if (index >= 0) {
    const updated = [...messagesWithStage];
    updated[index] = next;
    return { messages: updated, handled: true };
  }
  const insertAt = messagesWithStage.findIndex((message) => message.ts > next.ts);
  const updated = [...messagesWithStage];
  updated.splice(insertAt < 0 ? updated.length : insertAt, 0, next);
  return { messages: updated, handled: true };
}

const blank_session = (sid: string): SessionState => ({
  base_session_id: sid,
  code_session_id: null,
  project_root: null,
  project_name: "(untitled)",
  messages: [],
  todos: [],
  token_usage: { prompt: 0, completion: 0 },
  context_usage: null,
  status: "idle",
  last_activity: Date.now(),
  inflight: false,
  current_iteration: 0,
  max_iterations: 50,
  tool_signature_repeat: 0,
  supervisor_severity: "green",
  supervisor_alert: null,
  supervisor_inbox: [],
  auto_resume_attempts: 0,
  // multi-provider-management Phase 5: default = no binding ⇒ "Global Chain".
  provider_id: null,
  preferred_model: null,
  // code-session-model-params S2: no params ⇒ provider defaults.
  model_params: null,
});

export const useSessionsStore = create<SessionsStore>((set) => ({
  active_sid: "default",
  sessions: { default: blank_session("default") },
  inflight_count: 0,
  inflight_max: 2,

  set_active(sid) {
    // Auto-create slot if frontend asks to switch to an unknown sid
    // (e.g. dashboard tile before WS event landed).
    set((state) => {
      if (!state.sessions[sid]) {
        return {
          active_sid: sid,
          sessions: { ...state.sessions, [sid]: blank_session(sid) },
        };
      }
      return { active_sid: sid };
    });
  },

  ensure(sid, init) {
    set((state) => {
      if (state.sessions[sid]) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...blank_session(sid), ...init },
        },
      };
    });
  },

  upsert(sid, patch) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, ...patch, last_activity: Date.now() },
        },
      };
    });
  },

  push_message(sid, msg) {
    const id = msg.id ?? newId();
    const ts = msg.ts ?? Date.now();
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const nextMessages = dedupe_ppt_outline_messages([
        ...cur.messages,
        { ...msg, id, ts },
      ]);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: nextMessages,
            last_activity: ts,
          },
        },
      };
    });
  },

  set_messages(sid, messages) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      // 2026-06-06 真机 bug fix：skill_candidate / plan 确认卡是 ephemeral 前端-only
      // 消息（不持久化到 SessionDB）。打开「完整 chat」/ F5 触发 session_messages_load
      // → 这里整体替换 messages 会**丢掉仍 awaiting 的确认卡**（真机找技能卡时开
      // 完整 chat 反而弄丢卡的根因）。重载时把内存里仍 awaiting 的卡 merge 回尾部。
      const awaitingCards = cur.messages.filter(
        (m) =>
          (m.role === "skill_candidate" && m.skill_candidate_awaiting) ||
          (m.role === "ppt_outline" && m.ppt_outline_awaiting) ||
          (m.role === "plan" && m.plan_awaiting_confirm),
      );
      const reloadedIds = new Set(messages.map((m) => m.id));
      const preserved = awaitingCards.filter((m) => !reloadedIds.has(m.id));
      const nextMessages = dedupe_ppt_outline_messages([...messages, ...preserved]);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: nextMessages,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  resolve_plan(sid, msgId) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return {};
      let cleared = false;
      // clear by id, or (no id) the last still-awaiting plan
      const next = [...cur.messages];
      for (let i = next.length - 1; i >= 0; i--) {
        const m = next[i];
        if (m.role !== "plan" || !m.plan_awaiting_confirm) continue;
        if (msgId && m.id !== msgId) continue;
        next[i] = { ...m, plan_awaiting_confirm: false };
        cleared = true;
        if (!msgId) break; // no id → only the latest one
        break;
      }
      if (!cleared) return {};
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, messages: next },
        },
      };
    });
  },

  resolve_skill_candidate(sid, candidateId, accepted) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return {};
      let cleared = false;
      const next = [...cur.messages];
      for (let i = next.length - 1; i >= 0; i--) {
        const m = next[i];
        if (m.role !== "skill_candidate" || !m.skill_candidate_awaiting) continue;
        if (m.skill_candidate_id !== candidateId) continue;
        next[i] = {
          ...m,
          skill_candidate_awaiting: false,
          skill_candidate_accepted: accepted,
        };
        cleared = true;
        break;
      }
      if (!cleared) return {};
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, messages: next },
        },
      };
    });
  },

  reduce_workflow_event(sid, event) {
    let handled = false;
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const reduced = applyWorkflowEvent(cur.messages, event);
      handled = reduced.handled;
      if (!reduced.handled || reduced.messages === cur.messages) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: reduced.messages,
            last_activity: Date.now(),
          },
        },
      };
    });
    return handled;
  },

  merge_history_messages(sid, messages, workflowEvents) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const consumedEventIds = new Set(
        workflowEvents
          .filter((event) => WORKFLOW_CARD_EVENTS.has(String(event.event_type || "")))
          .map((event) => String(event.event_id || ""))
          .filter(Boolean),
      );
      const byId = new Map<string, Message>();
      for (const message of [...cur.messages, ...messages]) {
        if (consumedEventIds.has(message.id) && message.role !== "workflow_progress") {
          continue;
        }
        if (!byId.has(message.id)) byId.set(message.id, message);
      }
      let merged = [...byId.values()]
        .map((message, index) => ({ message, index }))
        .sort((a, b) => a.message.ts - b.message.ts || a.index - b.index)
        .map(({ message }) => message);
      const orderedEvents = workflowEvents
        .map((event, index) => ({ event, index }))
        .sort((a, b) => {
          const run = String(a.event.run_id || "").localeCompare(String(b.event.run_id || ""));
          return run || Number(a.event.seq || 0) - Number(b.event.seq || 0) || a.index - b.index;
        });
      for (const { event } of orderedEvents) {
        merged = applyWorkflowEvent(merged, event).messages;
      }
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages: dedupe_ppt_outline_messages(merged),
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  resolve_ppt_outline(sid, outlineId, decisionStatus) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return {};
      let cleared = false;
      const next = cur.messages.map((m) => {
        if (
          m.role !== "ppt_outline" ||
          m.outline_id !== outlineId ||
          (!m.ppt_outline_awaiting && !decisionStatus)
        ) {
          return m;
        }
        cleared = true;
        return {
          ...m,
          ppt_outline_awaiting: false,
          ppt_outline_decision_status:
            decisionStatus ?? m.ppt_outline_decision_status,
        };
      });
      if (!cleared) return {};
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, messages: next },
        },
      };
    });
  },

  upsert_todos(sid, todos) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, todos, last_activity: Date.now() },
        },
      };
    });
  },

  remove(sid) {
    set((state) => {
      if (!state.sessions[sid]) return state;
      const next = { ...state.sessions };
      delete next[sid];
      const active =
        state.active_sid === sid
          ? Object.keys(next)[0] ?? "default"
          : state.active_sid;
      return { sessions: next, active_sid: active };
    });
  },

  set_inflight(delta) {
    set((state) => ({
      inflight_count: Math.max(0, state.inflight_count + delta),
    }));
  },

  apply_supervisor_alert(sid, alert) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const prev_inbox = cur.supervisor_inbox ?? [];
      // Dedup by alert_id — if the same alert lands twice (ws reconnect
      // replays, double broadcast), don't grow the badge.
      const filtered = prev_inbox.filter((a) => a.alert_id !== alert.alert_id);
      const next_inbox =
        alert.severity === "yellow" || alert.severity === "red"
          ? [alert, ...filtered].slice(0, 50)
          : filtered;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            supervisor_severity: alert.severity,
            supervisor_alert: alert,
            supervisor_inbox: next_inbox,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  clear_supervisor_alert(sid) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return state;
      return {
        sessions: {
          ...state.sessions,
          [sid]: { ...cur, supervisor_alert: null, supervisor_severity: "green" },
        },
      };
    });
  },

  dismiss_alert(sid, alert_id) {
    set((state) => {
      const cur = state.sessions[sid];
      if (!cur) return state;
      const next_inbox = (cur.supervisor_inbox ?? []).filter(
        (a) => a.alert_id !== alert_id,
      );
      const next_alert =
        cur.supervisor_alert && cur.supervisor_alert.alert_id === alert_id
          ? null
          : cur.supervisor_alert ?? null;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            supervisor_inbox: next_inbox,
            supervisor_alert: next_alert,
            // Severity downgrades to green only when nothing pending.
            supervisor_severity: next_inbox.length === 0
              ? "green"
              : cur.supervisor_severity,
          },
        },
      };
    });
  },

  dismiss_all_alerts(severity) {
    set((state) => {
      const next: Record<string, SessionState> = {};
      for (const [sid, s] of Object.entries(state.sessions)) {
        const remaining = (s.supervisor_inbox ?? []).filter(
          (a) => a.severity !== severity,
        );
        const cur_alert_dismissed =
          s.supervisor_alert && s.supervisor_alert.severity === severity;
        next[sid] = {
          ...s,
          supervisor_inbox: remaining,
          supervisor_alert: cur_alert_dismissed ? null : s.supervisor_alert ?? null,
          supervisor_severity:
            remaining.length === 0 ? "green" : s.supervisor_severity,
        };
      }
      return { sessions: next };
    });
  },
}));

// ----------------------------------------------------------------------
// Inbox selectors. Pure helpers — components call these from useMemo.
// ----------------------------------------------------------------------

export function count_unhandled_by_severity(
  sessions: Record<string, SessionState>,
  severity: "yellow" | "red",
): number {
  let n = 0;
  for (const s of Object.values(sessions)) {
    for (const a of s.supervisor_inbox ?? []) {
      if (a.severity === severity) n += 1;
    }
  }
  return n;
}

export interface InboxItem extends SupervisorAlertEntry {
  session_id: string;
  project_name: string;
}

export function collect_inbox(
  sessions: Record<string, SessionState>,
  severity: "yellow" | "red",
): InboxItem[] {
  const out: InboxItem[] = [];
  for (const s of Object.values(sessions)) {
    for (const a of s.supervisor_inbox ?? []) {
      if (a.severity === severity) {
        out.push({
          ...a,
          session_id: s.base_session_id,
          project_name: s.project_name || s.base_session_id,
        });
      }
    }
  }
  // Newest first
  out.sort((a, b) => b.received_at - a.received_at);
  return out;
}

// ----------------------------------------------------------------------
// P5-S3 — severity score + pet focus selectors.
//
// Pure functions over SessionState so they're trivial to unit-test and
// to memoize at call sites. Backend's watchdog also computes a similar
// score for telemetry but the pet UI's "which session looks worst" is
// frontend-derived (no need to round-trip ws for every event).
// ----------------------------------------------------------------------

/** Decompose severity for debug overlay display. */
export interface SeverityBreakdown {
  base: number;
  age: number;
  repeat: number;
  supervisor: number;
  iteration: number;
  total: number;
}

const STATUS_BASE: Record<SessionStatus, number> = {
  idle: 0,
  thinking: 5,
  running: 10,
  permission: 25,
  error: 60,
};

const SUPERVISOR_BOOST: Record<"green" | "yellow" | "red", number> = {
  green: 0,
  yellow: 20,
  red: 50,
};

/** Compute severity score breakdown for one session.
 * See spec D7 for the formula.
 * `now` is injectable for unit tests; defaults to Date.now(). */
export function severity_score_breakdown(
  s: SessionState,
  now: number = Date.now(),
): SeverityBreakdown {
  const base = STATUS_BASE[s.status] ?? 0;
  const age_seconds = Math.max(0, (now - (s.last_activity || now)) / 1000);
  // log2 of minutes-since-activity, clamped at 30 points (roughly 32 min
  // saturates the dial).
  const age = Math.min(
    30,
    Math.log2(Math.max(1, age_seconds / 60)) * 6,
  );
  const repeat = Math.min(40, Math.max(0, s.tool_signature_repeat ?? 0) * 10);
  const supervisor = SUPERVISOR_BOOST[s.supervisor_severity ?? "green"] ?? 0;
  const cur = s.current_iteration ?? 0;
  const max = Math.max(1, s.max_iterations ?? 50);
  const iteration = (cur / max) * 10;
  const total = base + age + repeat + supervisor + iteration;
  return { base, age, repeat, supervisor, iteration, total };
}

/** Pure helper used by selectors + tests. */
export function severity_score(s: SessionState, now: number = Date.now()): number {
  return severity_score_breakdown(s, now).total;
}

/** Return the sid of the most-dangerous session (highest score), or null
 * if the store has no sessions to evaluate. Code-mode-only sessions are
 * eligible (companion sessions don't carry code_session_id) — except
 * a session with an active supervisor_alert is ALWAYS eligible, since
 * the supervisor's own decision says "this matters".
 *
 * The companion "default" sid is still excluded so the pet doesn't
 * focus itself when an alert hypothetically targets the chitchat
 * channel — supervisor only watches Code mode by design. */
export function pet_focus_sid(
  sessions: Record<string, SessionState>,
  now: number = Date.now(),
): string | null {
  let best_sid: string | null = null;
  let best_score = -1;
  for (const [sid, s] of Object.entries(sessions)) {
    // Companion sid is never eligible. Otherwise: Code-mode metadata
    // OR an active supervisor_alert qualifies the session for focus.
    if (sid === "default") continue;
    const has_code_meta = !!(s.code_session_id || s.project_root);
    const has_active_alert = !!s.supervisor_alert;
    if (!has_code_meta && !has_active_alert) continue;
    const score = severity_score(s, now);
    if (score > best_score) {
      best_score = score;
      best_sid = sid;
    }
  }
  return best_sid;
}

// ----------------------------------------------------------------------
// Concurrency limiter — wrap outbound chat sends so the relay doesn't
// see N parallel chat_v2 messages from N tiles all at once. Default
// max is 2 simultaneous in-flight LLM round-trips; the rest queue.
// ----------------------------------------------------------------------

export class ConcurrencyLimiter {
  private inflight = 0;
  private queue: (() => void)[] = [];
  private max: number;

  constructor(max: number) {
    this.max = max;
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.inflight >= this.max) {
      await new Promise<void>((resolve) => this.queue.push(resolve));
    }
    this.inflight++;
    useSessionsStore.getState().set_inflight(+1);
    try {
      return await fn();
    } finally {
      this.inflight--;
      useSessionsStore.getState().set_inflight(-1);
      const next = this.queue.shift();
      if (next) next();
    }
  }

  get_max() {
    return this.max;
  }
}

export const chatLimiter = new ConcurrencyLimiter(2);
