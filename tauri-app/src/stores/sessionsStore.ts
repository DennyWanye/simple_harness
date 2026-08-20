// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Multi-session zustand store.
 *
 * Single source of truth for task sessions rendered by the companion
 * message UI. Events stamped with `payload.session_id` get fanned out
 * to the matching slice here.
 *
 * Design note: we deliberately keep this minimal. Session state lives
 * in this in-memory store + on backend's SessionDB; we don't try to
 * persist scrollback to localStorage (browser quota) — backend's
 * memory hierarchy already handles long-term retrieval.
 */
import { create } from "zustand";

import type { PPTOutlineHistoryItem } from "../types/skillPlatform";
import type {
  CompanionEvent,
  CompanionOwnerFence,
  ContextUsageSnapshot,
  WorkflowDeliveryAggregate,
} from "../types/messages";
import { canonicalJson } from "../context/contextAuthority";

export type { ContextUsageSnapshot } from "../types/messages";

export type MessageRole =
  | "user"
  | "assistant"
  | "assistant_delta"   // P4-S25 A1: streaming partial assistant content
  | "reasoning_delta"   // Legacy live-only provider reasoning; never render as public CoT
  | "reasoning_summary" // Durable public execution summary, excluded from model context
  | "tool_call"
  | "tool_result"
  | "plan"              // P4-S25 A2: plan card preceding execution
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
  root_run_id?: string;
  task_root_run_id?: string;
  task_scope_id?: string;
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
  run_id?: string;
  task_scope_id?: string;
  request_id?: string;
  turn_id?: string;
  conversation_boundary_ref?: string;
  /** Live-only acknowledgement for user steering sent to an existing Run.
   * `waiting` means the durable FIFO owns it; `bound` means the Driver has
   * incorporated it into the Run conversation; `failed` is terminal. */
  continuation_status?: "waiting" | "bound" | "failed";
  continuation_error?: string;
  /** A steering message queued while its parent Run is being reserved. */
  deferred_send?: boolean;
  deferred_parent_request_id?: string;
  reasoning_summary_id?: string;
  reasoning_phase?: "planning" | "observation" | "status";
  reasoning_status?: "running" | "completed" | "failed";
  invocation_id?: string;
  stream_epoch?: string;
  provisional?: boolean;
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
  plan_target_directory?: string;
  plan_action_categories?: string[];
  plan_auto_confirmed?: boolean;
  // superpowers 决策2 plan-confirm 硬门: 等用户点 [执行]/[取消]
  plan_awaiting_confirm?: boolean;
  plan_sid?: string;  // which session this plan belongs to (for plan_confirm WS)
  plan_run_id?: string;
  plan_decision_id?: string;
  plan_nonce?: string;
  plan_version?: number;
  // PPT Pro WI-4: 大纲确认卡 payload（后端 propose，前端 decision）。
  outline_id?: string;
  topic?: string;
  outline_md?: string;
  sources_count?: number;
  no_research?: boolean;
  history?: PPTOutlineHistoryItem[];
  ppt_outline_awaiting?: boolean;
  ppt_outline_decision_status?: string;
  ppt_run_id?: string;
  ppt_decision_id?: string;
  ppt_nonce?: string;
  ppt_version?: number;
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
export interface SessionState {
  base_session_id: string;
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
  active_run_id?: string | null;
  selected_run_id?: string | null;
  run_projections: Record<string, TaskRunProjectionState>;
  /** Root ingress sent by this client but not yet bound to a backend Run. */
  pending_root_request_id?: string;
  pending_root_turn_id?: string;
  /** Durable, owner-fenced Companion cards projected into this message page. */
  companion_events?: CompanionEvent[];
  /** Ephemeral detail pages. Cleared on owner switch and projection retract. */
  companion_detail_cache?: Record<string, unknown>;
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
  // ws.ts 在 session_provider_set / session_model_set 时写入这些字段。
  provider_id?: string | null;
  preferred_model?: string | null;
  // Cursor 风格的 per-session 模型参数。
  // null/undefined = 走 provider 默认（未显式配置）。后端 echo 回的
  // `model_params` 原样写入这里，ChangeModelModal 据此预填。
  model_params?: SessionModelParams | null;
  provider_incarnation_id?: string | null;
  provider_config_revision?: number | null;
  binding_epoch?: number;
}

export type TaskRunProjectionStatus =
  | "starting"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled";

export interface TaskRunProjectionState {
  run_id: string;
  task_scope_id: string;
  projection_id?: string;
  version: number;
  conversation_boundary_ref?: string;
  conversation_boundary_version?: number;
  request_id?: string;
  turn_id?: string;
  status: TaskRunProjectionStatus;
  inflight: boolean;
  ui_state: "open" | "background" | "closed";
  started_at: number;
  last_activity: number;
}

const TERMINAL_TASK_RUN_STATUSES = new Set<TaskRunProjectionStatus>([
  "completed",
  "failed",
  "cancelled",
]);

function isTerminalTaskRunStatus(
  status: TaskRunProjectionStatus | undefined,
): boolean {
  return status !== undefined && TERMINAL_TASK_RUN_STATUSES.has(status);
}

/** Structured model picker params persisted per session.
 * Mirrors the backend IPC contract exactly:
 *   { thinking, fast, context, effort }
 * Effort `extra_high`/`max` clamp to `high` server-side (OpenAI only
 * exposes low/medium/high) — the UI still shows all 5 rungs. */
export interface SessionModelParams {
  thinking?: boolean;
  fast?: boolean;
  context?: "300k" | "1m";
  effort?: "low" | "medium" | "high" | "extra_high" | "max";
}

interface SessionsStore {
  active_sid: string;
  sessions: Record<string, SessionState>;
  /** Set only from a backend identity bind/status acknowledgement. */
  companion_owner: CompanionOwnerFence | null;
  /** Memory-only partial streams; never serialized into Message history. */
  companion_provisional_streams: Record<string, string>;
  // Concurrency limiter inflight count (for status rendering)
  inflight_count: number;
  inflight_max: number;

  set_active(sid: string): void;
  ensure(sid: string, init?: Partial<SessionState>): void;
  upsert(sid: string, patch: Partial<SessionState>): void;
  upsert_context_usage(snapshot: ContextUsageSnapshot): boolean;
  push_message(sid: string, msg: Omit<Message, "id" | "ts"> & { id?: string; ts?: number }): void;
  set_continuation_status(
    sid: string,
    requestId: string,
    status: NonNullable<Message["continuation_status"]>,
    error?: string,
  ): void;
  upsert_run_projection(
    sid: string,
    runId: string,
    patch: Partial<TaskRunProjectionState>,
  ): void;
  select_run_projection(sid: string, runId: string | null): void;
  close_run_projection(sid: string, runId: string): void;
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
  set_companion_owner(owner: CompanionOwnerFence | null): void;
  reduce_companion_event(event: CompanionEvent): boolean;
  retract_companion_projection(
    event: Omit<CompanionEvent, "notification"> & {
      notification_id: string;
      redaction_version: number;
      notification?: CompanionEvent["notification"];
    },
  ): boolean;
  set_companion_detail_cache(notificationId: string, value: unknown): void;
  upsert_companion_provisional(
    runId: string,
    invocationId: string,
    streamEpoch: string,
    content: string,
  ): void;
  clear_companion_provisional(
    runId?: string,
    invocationId?: string,
    streamEpoch?: string,
  ): void;
  /** superpowers 决策2: 用户点了 plan 卡片的 [执行]/[取消] 后，清掉该
   *  plan 消息的 awaiting_confirm（按钮消失）。msgId 可空 → 清该会话最近
   *  一条仍 awaiting 的 plan（超时取消路径用）。 */
  resolve_plan(sid: string, msgId?: string): void;
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

function normalized_history_value(value: unknown): string {
  if (typeof value !== "string") return stable_json(value);
  try {
    return stable_json(JSON.parse(value));
  } catch {
    return value;
  }
}

/**
 * Live websocket frames are rendered before SessionDB assigns its durable row
 * id.  A later history hydration therefore cannot use `id` alone to replace
 * the temporary copy.  This key is deliberately limited to ordinary durable
 * chat/tool rows; cards and partial streams keep their explicit identities.
 * Matching is consumed one-for-one, so two genuinely repeated messages remain
 * two messages instead of being collapsed by a global text set.
 */
function history_reconciliation_key(message: Message): string | null {
  switch (message.role) {
    case "user":
    case "assistant":
    case "error":
      return `${message.role}\u001f${message.text ?? ""}`;
    case "reasoning_summary":
      return `${message.role}\u001f${message.reasoning_summary_id ?? ""}\u001f${message.text ?? ""}`;
    case "tool_call":
      return `${message.role}\u001f${message.tool_name ?? ""}\u001f${stable_json(message.tool_args ?? {})}`;
    case "tool_result":
      return `${message.role}\u001f${message.tool_name ?? ""}\u001f${normalized_history_value(message.tool_result ?? "")}`;
    default:
      return null;
  }
}

function compatible_history_scope(left: Message, right: Message): boolean {
  // 两边都知道自己属于哪个 Run 时，Run 就是权威判据 —— 不要再拿
  // task_scope_id 做相等比较：它们分属不同命名空间。后端把 user 行持久化成
  // **回合**作用域（turn-*），而本地乐观副本带的是 Run 投影的任务作用域
  // （task-*），两者永远不等 → 同一条消息被判成"不同作用域"而拒绝对账 →
  // 切走再切回后用户气泡渲染两次（r5 S05 真机实测，DB 只有一行）。
  if (left.run_id && right.run_id) return left.run_id === right.run_id;
  if (
    left.task_scope_id &&
    right.task_scope_id &&
    left.task_scope_id !== right.task_scope_id
  ) {
    return false;
  }
  return true;
}

function reconcile_live_messages_with_history(
  liveMessages: Message[],
  historyMessages: Message[],
): Message[] {
  const remainingLive = [...liveMessages];
  for (const historyMessage of historyMessages) {
    let liveIndex = remainingLive.findIndex(
      (message) => message.id === historyMessage.id,
    );
    if (liveIndex < 0) {
      const historyKey = history_reconciliation_key(historyMessage);
      if (historyKey) {
        liveIndex = remainingLive.findIndex(
          (message) =>
            history_reconciliation_key(message) === historyKey &&
            compatible_history_scope(message, historyMessage),
        );
      }
    }
    if (liveIndex >= 0) remainingLive.splice(liveIndex, 1);
  }
  return [...remainingLive, ...historyMessages];
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
      if (typeof item[key] === "string" && item[key]) {
        const text = item[key] as string;
        const friendly: Record<string, string> = {
          "provider:insufficient_balance": "模型服务余额不足，请充值或切换模型后重试。",
          // 2026-08-09：托管登录移除后没有"重新登录"这回事，改为指向设置页改 key。
          "provider:relay_key_invalid": "模型服务密钥无效，请在「设置 → LLM Provider」更新 apiKey 后重试。",
          "provider:empty_api_key": "没有可用的模型服务凭据，请登录或配置 API Key。",
        };
        return friendly[text] ?? text;
      }
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
  const rootRunId = String(
    event.root_run_id || event.task_root_run_id || "",
  ).trim();
  const taskScopeId = String(event.task_scope_id || "").trim();
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
      run_id: rootRunId || undefined,
      task_scope_id: taskScopeId || undefined,
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
    run_id: rootRunId || undefined,
    task_scope_id: taskScopeId || undefined,
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
      ppt_run_id: String(event.run_id || payload.run_id || "") || undefined,
      ppt_decision_id: String(payload.decision_id || "") || undefined,
      ppt_nonce: String(payload.nonce || "") || undefined,
      ppt_version: workflowNumber(payload.version),
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
  const rootRunId = String(
    event.root_run_id || event.task_root_run_id || "",
  ).trim();
  const taskScopeId = String(event.task_scope_id || "").trim();
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
    run_id: rootRunId || previous?.run_id,
    task_scope_id: taskScopeId || previous?.task_scope_id,
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

function stable_json(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable_json).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) =>
      `${JSON.stringify(key)}:${stable_json(record[key])}`,
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}

function same_companion_owner(
  owner: CompanionOwnerFence | null,
  event: Pick<CompanionEvent, "profile_id" | "profile_generation">,
): boolean {
  return !!owner &&
    owner.profile_id === event.profile_id &&
    owner.profile_generation === event.profile_generation;
}

function valid_companion_event(event: CompanionEvent): boolean {
  return !!event.event_id &&
    !!event.profile_id &&
    Number.isInteger(event.profile_generation) &&
    event.profile_generation > 0 &&
    !!event.session_id &&
    Number.isInteger(event.seq) &&
    event.seq >= 0 &&
    !!event.notification?.notification_id &&
    Array.isArray(event.notification.available_actions);
}

export function reduce_companion_events(
  current: CompanionEvent[],
  event: CompanionEvent,
  owner: CompanionOwnerFence | null,
): { events: CompanionEvent[]; handled: boolean } {
  if (!valid_companion_event(event) || !same_companion_owner(owner, event)) {
    return { events: current, handled: false };
  }
  const index = current.findIndex((item) => item.event_id === event.event_id);
  if (index < 0) {
    return {
      events: [...current, event].sort((a, b) =>
        a.seq - b.seq || a.event_id.localeCompare(b.event_id),
      ),
      handled: true,
    };
  }
  const previous = current[index];
  const previousRoute = previous.route_version ?? 0;
  const nextRoute = event.route_version ?? previousRoute;
  if (nextRoute < previousRoute || event.seq < previous.seq) {
    return { events: current, handled: false };
  }
  if (event.seq === previous.seq) {
    const { received_at: _nextReceived, ...nextDurable } = event;
    const { received_at: _previousReceived, ...previousDurable } = previous;
    if (stable_json(nextDurable) === stable_json(previousDurable)) {
      return { events: current, handled: true };
    }
    const nextWithoutDetailFence = {
      ...nextDurable,
      notification: {
        ...nextDurable.notification,
        detail_version: "",
      },
    };
    const previousWithoutDetailFence = {
      ...previousDurable,
      notification: {
        ...previousDurable.notification,
        detail_version: "",
      },
    };
    if (
      stable_json(nextWithoutDetailFence) !==
      stable_json(previousWithoutDetailFence)
    ) {
      return { events: current, handled: false };
    }
    const events = [...current];
    events[index] = event;
    return { events, handled: true };
  }
  const events = [...current];
  events[index] = event;
  events.sort((a, b) => a.seq - b.seq || a.event_id.localeCompare(b.event_id));
  return { events, handled: true };
}

const COMPANION_TOMBSTONE_SUMMARY = "这条内容已被遗忘或撤回。";

export function retract_companion_events(
  current: CompanionEvent[],
  event: Omit<CompanionEvent, "notification"> & {
    notification_id: string;
    redaction_version: number;
    notification?: CompanionEvent["notification"];
  },
  owner: CompanionOwnerFence | null,
): { events: CompanionEvent[]; handled: boolean } {
  if (
    !same_companion_owner(owner, event) ||
    !event.event_id ||
    !event.notification_id ||
    !Number.isInteger(event.redaction_version) ||
    event.redaction_version < 1
  ) {
    return { events: current, handled: false };
  }
  const index = current.findIndex((item) => item.event_id === event.event_id);
  const previous = index >= 0 ? current[index] : null;
  if (previous && event.redaction_version <= (previous.redaction_version ?? 0)) {
    return {
      events: current,
      handled: event.redaction_version === previous.redaction_version &&
        previous.tombstone === true,
    };
  }
  if (
    previous &&
    previous.notification.notification_id !== event.notification_id
  ) {
    return { events: current, handled: false };
  }
  const tombstone: CompanionEvent = {
    event_id: event.event_id,
    profile_id: event.profile_id,
    profile_generation: event.profile_generation,
    session_id: event.session_id || previous?.session_id || "",
    seq: Math.max(event.seq ?? 0, previous?.seq ?? 0),
    route_version: event.route_version ?? previous?.route_version,
    redaction_version: event.redaction_version,
    importance: event.importance ?? previous?.importance,
    occurred_at: event.occurred_at ?? previous?.occurred_at,
    received_at: event.received_at ?? Date.now(),
    notification: {
      notification_id: event.notification_id,
      kind: "tombstone",
      summary: COMPANION_TOMBSTONE_SUMMARY,
      detail_ref: "",
      detail_version: event.notification?.detail_version ?? "",
      available_actions: [],
    },
    tombstone: true,
  };
  if (!valid_companion_event(tombstone)) {
    return { events: current, handled: false };
  }
  const events = [...current];
  if (index >= 0) events[index] = tombstone;
  else events.push(tombstone);
  events.sort((a, b) => a.seq - b.seq || a.event_id.localeCompare(b.event_id));
  return { events, handled: true };
}

function companion_stream_key(
  runId: string,
  invocationId: string,
  streamEpoch: string,
): string {
  return `${runId}\u001f${invocationId}\u001f${streamEpoch}`;
}

const blank_session = (sid: string): SessionState => ({
  base_session_id: sid,
  project_root: null,
  project_name: "(untitled)",
  messages: [],
  todos: [],
  token_usage: { prompt: 0, completion: 0 },
  context_usage: null,
  status: "idle",
  last_activity: Date.now(),
  inflight: false,
  active_run_id: null,
  selected_run_id: null,
  run_projections: {},
  companion_events: [],
  companion_detail_cache: {},
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
  provider_incarnation_id: null,
  provider_config_revision: null,
  binding_epoch: 0,
});

export const useSessionsStore = create<SessionsStore>((set) => ({
  // 2026-08-09：不再预置保留会话 `default`。启动时零会话、无选中会话；
  // 首个会话由用户新建，或由输入框空态直发（chat_v2 + new_session）产生。
  active_sid: "",
  sessions: {},
  companion_owner: null,
  companion_provisional_streams: {},
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

  upsert_context_usage(snapshot) {
    let accepted = false;
    set((state) => {
      const sid = typeof snapshot.session_id === "string"
        ? snapshot.session_id.trim()
        : "";
      const incomingVersion = snapshot.version;
      if (!sid || !Number.isInteger(incomingVersion) || (incomingVersion as number) < 0) {
        return state;
      }
      const cur = state.sessions[sid] ?? blank_session(sid);
      const previous = cur.context_usage;
      const previousVersion = previous?.version;
      if (previous && Number.isInteger(previousVersion)) {
        if ((incomingVersion as number) < (previousVersion as number)) return state;
        if ((incomingVersion as number) === (previousVersion as number)) {
          if (canonicalJson(previous) !== canonicalJson(snapshot)) return state;
          accepted = true;
          return state;
        }
      }
      accepted = true;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            context_usage: snapshot,
            last_activity: Date.now(),
          },
        },
      };
    });
    return accepted;
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

  set_continuation_status(sid, requestId, status, error) {
    if (!requestId) return;
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      let changed = false;
      const messages = cur.messages.map((message) => {
        if (
          message.role !== "user" ||
          message.request_id !== requestId
        ) {
          return message;
        }
        changed = true;
        return {
          ...message,
          continuation_status: status,
          continuation_error: error || undefined,
        };
      });
      if (!changed) return {};
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  upsert_run_projection(sid, runId, patch) {
    if (!runId) return;
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const now = Date.now();
      const currentProjections = cur.run_projections ?? {};
      const existing = currentProjections[runId];
      const staleNonTerminalLifecycle =
        isTerminalTaskRunStatus(existing?.status) &&
        patch.status !== undefined &&
        !isTerminalTaskRunStatus(patch.status);
      if (staleNonTerminalLifecycle) {
        // Ignore the whole stale lifecycle frame, not just its status. Its
        // timestamps/version/scope belong to an older observation and must
        // not mutate the authoritative terminal projection or its duration.
        return state;
      }
      // A durable Run is immutable after reaching a terminal state. WebSocket
      // delivery can legitimately reorder a terminal lifecycle snapshot and an
      // older permission/progress event, so never let that older event reopen
      // an already completed, failed, or cancelled Run in the UI.
      const lifecycleStatus = isTerminalTaskRunStatus(existing?.status)
        ? existing!.status
        : patch.status ?? existing?.status ?? "starting";
      const nextProjection: TaskRunProjectionState = {
        ...existing,
        ...patch,
        run_id: runId,
        task_scope_id: patch.task_scope_id ?? existing?.task_scope_id ?? "",
        projection_id: patch.projection_id ?? existing?.projection_id,
        version: patch.version ?? existing?.version ?? 0,
        conversation_boundary_ref:
          patch.conversation_boundary_ref ??
          existing?.conversation_boundary_ref,
        conversation_boundary_version:
          patch.conversation_boundary_version ??
          existing?.conversation_boundary_version,
        request_id: patch.request_id ?? existing?.request_id,
        turn_id: patch.turn_id ?? existing?.turn_id,
        status: lifecycleStatus,
        inflight: isTerminalTaskRunStatus(lifecycleStatus)
          ? false
          : patch.inflight ?? existing?.inflight ?? true,
        ui_state: patch.ui_state ?? existing?.ui_state ?? "open",
        started_at: patch.started_at ?? existing?.started_at ?? now,
        last_activity: patch.last_activity ?? now,
      };
      const projections = {
        ...currentProjections,
        [runId]: nextProjection,
      };
      const requestId = nextProjection.request_id;
      const messages = requestId
        ? cur.messages.map((message) =>
            message.request_id === requestId && !message.run_id
              ? {
                  ...message,
                  run_id: runId,
                  task_scope_id:
                    nextProjection.task_scope_id || message.task_scope_id,
                  conversation_boundary_ref:
                    nextProjection.conversation_boundary_ref ??
                    message.conversation_boundary_ref,
                }
              : message,
          )
        : cur.messages;
      const selectedRunId = cur.selected_run_id ?? runId;
      const running = Object.values(projections).filter(
        (item) => item.inflight,
      );
      const waiting = Object.values(projections).filter(
        (item) => item.status === "waiting" && item.ui_state !== "closed",
      );
      const activeRunId = projections[selectedRunId]?.inflight
        ? selectedRunId
        : running[0]?.run_id ?? null;
      const selectedProjection = projections[selectedRunId];
      const sessionStatus: SessionStatus = running.length > 0
        ? "running"
        : waiting.length > 0
          ? cur.status === "permission" ? "permission" : "running"
          : selectedProjection?.status === "failed"
            ? "error"
            : "idle";
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            messages,
            run_projections: projections,
            selected_run_id: selectedRunId,
            active_run_id: activeRunId,
            inflight: running.length > 0,
            status: sessionStatus,
            last_activity: now,
          },
        },
      };
    });
  },

  select_run_projection(sid, runId) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      const currentProjections = cur.run_projections ?? {};
      if (runId !== null && !currentProjections[runId]) return state;
      const projections = Object.fromEntries(
        Object.entries(currentProjections).map(([id, item]) => [
          id,
          {
            ...item,
            ui_state:
              id === runId
                ? "open"
                : item.ui_state === "open"
                  ? "background"
                  : item.ui_state,
          },
        ]),
      ) as Record<string, TaskRunProjectionState>;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            run_projections: projections,
            selected_run_id: runId,
            active_run_id:
              runId && projections[runId]?.inflight
                ? runId
                : Object.values(projections).find((item) => item.inflight)
                    ?.run_id ?? null,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  close_run_projection(sid, runId) {
    set((state) => {
      const cur = state.sessions[sid];
      const currentProjections = cur?.run_projections ?? {};
      const target = currentProjections[runId];
      if (!cur || !target) return state;
      const projections = {
        ...currentProjections,
        [runId]: {
          ...target,
          ui_state: "closed" as const,
          last_activity: Date.now(),
        },
      };
      const nextSelected =
        cur.selected_run_id === runId
          ? Object.values(projections).find(
              (item) => item.ui_state !== "closed",
            )?.run_id ?? null
          : cur.selected_run_id ?? null;
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...cur,
            run_projections: projections,
            selected_run_id: nextSelected,
            active_run_id:
              nextSelected && projections[nextSelected]?.inflight
                ? nextSelected
                : Object.values(projections).find((item) => item.inflight)
                    ?.run_id ?? null,
            last_activity: Date.now(),
          },
        },
      };
    });
  },

  set_messages(sid, messages) {
    set((state) => {
      const cur = state.sessions[sid] ?? blank_session(sid);
      // Plan / PPT cards are ephemeral frontend projections. Preserve only
      // their durable decision envelopes while history is reloaded.
      const awaitingCards = cur.messages.filter(
        (m) =>
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
      const reconciled = reconcile_live_messages_with_history(
        cur.messages,
        messages,
      );
      for (const message of reconciled) {
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

  set_companion_owner(owner) {
    set((state) => {
      const previous = state.companion_owner;
      if (
        previous?.profile_id === owner?.profile_id &&
        previous?.profile_generation === owner?.profile_generation
      ) {
        return state;
      }
      const sessions = Object.fromEntries(
        Object.entries(state.sessions).map(([sid, session]) => [
          sid,
          {
            ...session,
            companion_events: [],
            companion_detail_cache: {},
          },
        ]),
      );
      return {
        companion_owner: owner,
        companion_provisional_streams: {},
        sessions,
      };
    });
  },

  reduce_companion_event(event) {
    let handled = false;
    set((state) => {
      const sid = event.session_id || state.active_sid;
      const session = state.sessions[sid] ?? blank_session(sid);
      const occurrences = Object.entries(state.sessions).flatMap(
        ([candidateSid, candidate]) =>
          (candidate.companion_events ?? [])
            .filter((item) => item.event_id === event.event_id)
            .map((item) => ({ sid: candidateSid, event: item })),
      );
      const previous = occurrences
        .map((item) => item.event)
        .sort((left, right) =>
          (right.route_version ?? 0) - (left.route_version ?? 0) ||
          right.seq - left.seq,
        )[0];
      const targetEvents = session.companion_events ?? [];
      const reducerInput =
        previous && !targetEvents.some((item) => item.event_id === event.event_id)
          ? [...targetEvents, previous]
          : targetEvents;
      const reduced = reduce_companion_events(
        reducerInput,
        event,
        state.companion_owner,
      );
      handled = reduced.handled;
      const needsMove =
        occurrences.length > 1 ||
        occurrences.some((item) => item.sid !== sid);
      const detailVersionChanged =
        previous?.notification.detail_version !==
        event.notification.detail_version;
      if (
        !reduced.handled ||
        (reduced.events === targetEvents && !needsMove)
      ) {
        return state;
      }
      const streamPrefix = event.decision?.kind === "action_confirmation"
        ? `${event.decision.run_id}\u001f`
        : "";
      const companion_provisional_streams = streamPrefix
        ? Object.fromEntries(Object.entries(state.companion_provisional_streams)
            .filter(([key]) => !key.startsWith(streamPrefix)))
        : state.companion_provisional_streams;
      const sessions = Object.fromEntries(
        Object.entries(state.sessions).map(([candidateSid, candidate]) => {
          const filtered = (candidate.companion_events ?? [])
            .filter((item) => item.event_id !== event.event_id);
          const detailCache = { ...(candidate.companion_detail_cache ?? {}) };
          if (needsMove || detailVersionChanged) {
            delete detailCache[event.notification.notification_id];
          }
          return [
            candidateSid,
            {
              ...candidate,
              companion_events: candidateSid === sid
                ? reduced.events
                : filtered,
              companion_detail_cache: detailCache,
              ...(candidateSid === sid ? { last_activity: Date.now() } : {}),
            },
          ];
        }),
      );
      if (!sessions[sid]) {
        sessions[sid] = {
          ...session,
          companion_events: reduced.events,
          companion_detail_cache: session.companion_detail_cache ?? {},
          last_activity: Date.now(),
        };
      }
      return {
        companion_provisional_streams,
        sessions,
      };
    });
    return handled;
  },

  retract_companion_projection(event) {
    let handled = false;
    set((state) => {
      const sid = event.session_id || state.active_sid;
      const session = state.sessions[sid] ?? blank_session(sid);
      const occurrences = Object.entries(state.sessions).flatMap(
        ([candidateSid, candidate]) =>
          (candidate.companion_events ?? [])
            .filter((item) => item.event_id === event.event_id)
            .map((item) => ({ sid: candidateSid, event: item })),
      );
      const previous = occurrences
        .map((item) => item.event)
        .sort((left, right) =>
          (right.redaction_version ?? 0) - (left.redaction_version ?? 0) ||
          (right.route_version ?? 0) - (left.route_version ?? 0) ||
          right.seq - left.seq,
        )[0];
      const targetEvents = session.companion_events ?? [];
      const reducerInput =
        previous && !targetEvents.some((item) => item.event_id === event.event_id)
          ? [...targetEvents, previous]
          : targetEvents;
      const reduced = retract_companion_events(
        reducerInput,
        event,
        state.companion_owner,
      );
      handled = reduced.handled;
      const needsMove =
        occurrences.length > 1 ||
        occurrences.some((item) => item.sid !== sid);
      if (
        !reduced.handled ||
        (reduced.events === targetEvents && !needsMove)
      ) {
        return state;
      }
      const sessions = Object.fromEntries(
        Object.entries(state.sessions).map(([candidateSid, candidate]) => {
          const detailCache = { ...(candidate.companion_detail_cache ?? {}) };
          delete detailCache[event.notification_id];
          return [
            candidateSid,
            {
              ...candidate,
              companion_events: candidateSid === sid
                ? reduced.events
                : (candidate.companion_events ?? [])
                    .filter((item) => item.event_id !== event.event_id),
              companion_detail_cache: detailCache,
              ...(candidateSid === sid ? { last_activity: Date.now() } : {}),
            },
          ];
        }),
      );
      if (!sessions[sid]) {
        sessions[sid] = {
          ...session,
          companion_events: reduced.events,
          companion_detail_cache: {},
          last_activity: Date.now(),
        };
      }
      return { sessions };
    });
    return handled;
  },

  set_companion_detail_cache(notificationId, value) {
    set((state) => {
      const sid = state.active_sid;
      const session = state.sessions[sid] ?? blank_session(sid);
      return {
        sessions: {
          ...state.sessions,
          [sid]: {
            ...session,
            companion_detail_cache: {
              ...(session.companion_detail_cache ?? {}),
              [notificationId]: value,
            },
          },
        },
      };
    });
  },

  upsert_companion_provisional(runId, invocationId, streamEpoch, content) {
    if (!runId || !invocationId || !streamEpoch) return;
    const key = companion_stream_key(runId, invocationId, streamEpoch);
    set((state) => ({
      companion_provisional_streams: {
        ...state.companion_provisional_streams,
        [key]: (state.companion_provisional_streams[key] || "") + content,
      },
    }));
  },

  clear_companion_provisional(runId, invocationId, streamEpoch) {
    set((state) => {
      if (!runId && !invocationId && !streamEpoch) {
        return { companion_provisional_streams: {} };
      }
      const exact = runId && invocationId && streamEpoch
        ? companion_stream_key(runId, invocationId, streamEpoch)
        : null;
      return {
        companion_provisional_streams: Object.fromEntries(
          Object.entries(state.companion_provisional_streams).filter(([key]) => {
            if (exact) return key !== exact;
            const [run, invocation, epoch] = key.split("\u001f");
            return !(
              (!runId || run === runId) &&
              (!invocationId || invocation === invocationId) &&
              (!streamEpoch || epoch === streamEpoch)
            );
          }),
        ),
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
      // 删掉当前会话就落到剩下的任意一条；一条不剩即空态（""），
      // 不再回落到保留会话。
      const active =
        state.active_sid === sid
          ? Object.keys(next)[0] ?? ""
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

/** Return the sid of the most-dangerous task session (highest score), or
 * null if the store has no sessions to evaluate. A session is eligible
 * when it has task metadata, an active run, or a supervisor alert.
 *
 * 2026-08-09：原先这里排除保留会话 `"default"`（免得桌宠为闲聊频道自我聚焦）。
 * 保留会话已移除，所有会话同构参与评分。 */
export function pet_focus_sid(
  sessions: Record<string, SessionState>,
  now: number = Date.now(),
): string | null {
  let best_sid: string | null = null;
  let best_score = -1;
  for (const [sid, s] of Object.entries(sessions)) {
    // Any durable task metadata, active run, or supervisor alert qualifies.
    const has_task_meta = !!(s.project_root || s.active_run_id);
    const has_active_alert = !!s.supervisor_alert;
    if (!has_task_meta && !has_active_alert) continue;
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
