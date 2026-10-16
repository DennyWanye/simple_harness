// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import {
  type MessageRole,
  type PlanStep,
  type SessionModelParams,
  type WorkflowEventEnvelope,
} from "../stores/sessionTypes";
import type { PPTOutlineHistoryItem } from "../types/skillPlatform";

// P4-S23 fix: stash the live socket on globalThis so vite HMR (which
// may swap this module while the old WebSocket is still mid-handshake)
// can find and close the previous instance instead of stacking
// sockets and getting kicked by the backend's "session replaced"
// guard. Without this we saw a connect/disconnect storm in the
// backend log every dev session.
/** 控制通道收到的一条消息：JSON 解析结果，未经校验；载荷字段由 type 决定。 */
export interface ControlWireMessage {
  type?: string;
  request_id?: string;
  payload?: ControlWirePayload;
}

/** 各类消息载荷里 dispatch 读取的字段（后端按消息类型各发其中一部分）。 */
export interface ControlWirePayload {
  action?: unknown;
  action_categories?: unknown;
  alert_id?: unknown;
  arguments?: Record<string, unknown>;
  artifacts?: unknown;
  attempt?: unknown;
  attempts?: unknown;
  auto_confirmed?: unknown;
  awaiting_confirm?: unknown;
  bindingEpoch?: unknown;
  binding_epoch?: unknown;
  cancelled?: unknown;
  challenge?: unknown;
  companion_events?: unknown[];
  connectionId?: unknown;
  connection_id?: unknown;
  content?: string;
  controlEpoch?: unknown;
  control_epoch?: unknown;
  conversation_boundary_ref?: unknown;
  conversation_boundary_version?: unknown;
  decision_id?: string;
  decision_status?: unknown;
  default_model?: unknown;
  detail?: unknown;
  diagnosis?: unknown;
  error?: unknown;
  event_id?: unknown;
  event_type?: unknown;
  final_error?: unknown;
  history?: PPTOutlineHistoryItem[];
  invocation_id?: unknown;
  items?: unknown;
  kind?: unknown;
  messages?: HistoryRow[];
  model_params?: SessionModelParams | null;
  models?: unknown;
  name?: string;
  new_sid?: unknown;
  nextRequestSeq?: unknown;
  next_request_seq?: unknown;
  no_research?: unknown;
  nonce?: string;
  notification_id?: unknown;
  occurred_at?: unknown;
  ok?: boolean;
  outline_id?: string;
  outline_md?: string;
  payload?: unknown;
  peak_concurrent?: unknown;
  phase?: unknown;
  plan?: HistoryPlan;
  preferred_model?: string | null;
  profile_generation?: unknown;
  profile_id?: unknown;
  projection_event_id?: unknown;
  projection_id?: unknown;
  projection_version?: unknown;
  projections?: Record<string, unknown>[];
  provider_config_revision?: number | null;
  provider_id?: string | null;
  provider_incarnation_id?: string | null;
  providers?: unknown;
  provisional?: unknown;
  query_id?: unknown;
  queued?: unknown;
  rationale?: string;
  ready?: unknown;
  reason?: unknown;
  redaction_version?: unknown;
  requestSeq?: unknown;
  request_id?: unknown;
  request_seq?: unknown;
  result?: unknown;
  retract?: unknown;
  retract_provisional?: unknown;
  root_run_id?: unknown;
  route_version?: unknown;
  run_id?: string;
  run_version?: unknown;
  seq?: unknown;
  session_id?: string;
  severity?: unknown;
  source?: unknown;
  sources_count?: unknown;
  state?: unknown;
  status?: unknown;
  steps?: PlanStep[];
  stream_epoch?: unknown;
  suggested_buttons?: unknown;
  summary?: unknown;
  summary_id?: unknown;
  target_directory?: string;
  task_id?: unknown;
  task_root_run_id?: unknown;
  task_scope_id?: unknown;
  text?: string;
  tool?: string;
  topic?: string;
  total_queued?: unknown;
  total_rejected?: unknown;
  ts?: unknown;
  turn_id?: unknown;
  ui_state?: unknown;
  unknown?: unknown;
  user_message?: unknown;
  version?: number;
  workflow_event_id?: unknown;
}

/** 会话历史里的一行（session_messages_response 的 messages 元素）。 */
export interface HistoryRow {
  id?: string;
  ts?: number;
  role?: MessageRole | "tool";
  text?: string;
  tool_calls?: HistoryToolCall[];
  tool_call_id?: string;
  workflow_event?: WorkflowEventEnvelope;
  companion_event?: unknown;
  projection_kind?: string;
  workflow_event_id?: string;
  projection_event_id?: string;
  run_id?: string;
  task_scope_id?: string;
}

/** 历史行里助手发起的一次工具调用（OpenAI tool_calls 形状，旧行可能只有 name）。 */
export interface HistoryToolCall {
  id?: string;
  name?: string;
  function?: { name?: string; arguments?: unknown };
}

/** session_messages_response 单独带回的待确认计划。 */
export interface HistoryPlan {
  awaiting?: boolean;
  rationale?: string;
  steps?: PlanStep[];
  target_directory?: string;
  action_categories?: unknown;
  auto_confirmed?: unknown;
}

/** slash_command_result 的 result 对象（后端契约，按 type 各带其中一部分字段）。 */
export interface SlashCommandResult {
  type?: string;
  builtins?: Array<{ name: string; description?: string }>;
  skills?: Array<{ name: string; description?: string }>;
  text?: string;
  max_iterations?: number;
  active?: boolean;
  iterations_used?: number;
  done?: boolean;
  ok?: boolean;
  skill?: string;
  output?: unknown;
  entries?: Array<{ kind: string; label: string; text: string; ts?: number }>;
  count?: number;
  removed?: number;
  kind?: string;
  message?: string;
  hint?: string;
}
