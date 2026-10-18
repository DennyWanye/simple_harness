// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { PPTOutlineHistoryItem } from "../types/skillPlatform";
import type {
  CompanionEvent,
  CompanionOwnerFence,
  ContextUsageSnapshot,
  WorkflowDeliveryAggregate,
} from "../types/messages";
import type {
  ProjectCatalogPage,
  ProjectCatalogState,
  ProjectInspection,
  ProjectSessionPage,
  ProjectSessionPageState,
} from "../types/projectSessions";

export type MessageRole =
  | "user"
  | "assistant"
  | "assistant_delta" // streaming partial assistant content
  | "reasoning_delta" // Legacy live-only provider reasoning; never render as public CoT
  | "reasoning_summary" // Durable public execution summary, excluded from model context
  | "tool_call"
  | "tool_result"
  | "plan" // plan card preceding execution
  | "ppt_outline" // PPT Pro WI-4: 大纲确认卡（后端 propose → 用户确认/修改/复用）
  | "workflow_progress"
  | "workflow_stage"
  | "slash_result" // /slash 命令结果（help/goal/prefs/skill/error）
  | "error";

export type WorkflowProgressStatus = "running" | "waiting" | "completed" | "failed" | "cancelled";

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
  active_gap:
    | "none"
    | {
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

export type WorkflowV7ChildStatus = "queued" | "running" | "retrying" | "valid" | "insufficient";

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
  // plan-card payload
  plan_rationale?: string;
  plan_steps?: PlanStep[];
  plan_target_directory?: string;
  plan_action_categories?: string[];
  plan_auto_confirmed?: boolean;
  // superpowers 决策2 plan-confirm 硬门: 等用户点 [执行]/[取消]
  plan_awaiting_confirm?: boolean;
  plan_sid?: string; // which session this plan belongs to (for plan_confirm WS)
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

export type SessionStatus = "idle" | "thinking" | "running" | "permission" | "error";

/** single supervisor alert payload, kept in two places:
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
  // supervisor signals — fed by `supervisor_alert` ws event +
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

export const TERMINAL_TASK_RUN_STATUSES = new Set<TaskRunProjectionStatus>([
  "completed",
  "failed",
  "cancelled",
]);

export function isTerminalTaskRunStatus(status: TaskRunProjectionStatus | undefined): boolean {
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

export interface SessionsStore {
  active_sid: string;
  sessions: Record<string, SessionState>;
  /** Set only from a backend identity bind/status acknowledgement. */
  companion_owner: CompanionOwnerFence | null;
  /** Memory-only partial streams; never serialized into Message history. */
  companion_provisional_streams: Record<string, string>;
  // Concurrency limiter inflight count (for status rendering)
  inflight_count: number;
  inflight_max: number;
  /** Bounded backend-owned catalog projection; paths are display-only. */
  project_catalog: ProjectCatalogState;
  project_session_pages: Record<string, ProjectSessionPageState>;
  project_inspections: Record<string, ProjectInspection>;

  set_active(sid: string): void;
  begin_project_catalog(append?: boolean): void;
  apply_project_catalog_page(page: ProjectCatalogPage, append?: boolean): void;
  fail_project_catalog(error: string): void;
  begin_project_sessions(scopeKey: string): void;
  apply_project_session_page(page: ProjectSessionPage, append?: boolean): void;
  fail_project_sessions(scopeKey: string, error: string): void;
  apply_project_inspection(inspection: ProjectInspection): void;
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
  upsert_run_projection(sid: string, runId: string, patch: Partial<TaskRunProjectionState>): void;
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
  upsert_companion_provisional(
    runId: string,
    invocationId: string,
    streamEpoch: string,
    content: string,
  ): void;
  clear_companion_provisional(runId?: string, invocationId?: string, streamEpoch?: string): void;
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
  // supervisor surface
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
