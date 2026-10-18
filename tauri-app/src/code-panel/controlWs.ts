// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 对话面板一侧的控制通道分发器。
 *
 * 与主界面连同一个后台（`ws://127.0.0.1:8100`）。主界面（App）经
 * `useControlChannel` 持有自己的连接；这里另开一条连接，后台把同一事件
 * 分别推给两条连接，会话卡片不必绕经主界面的状态就能更新。
 *
 * 共享密钥经 Tauri 的 `get_shared_secret` 取得（process_manager.rs 实现）。
 */
import { invoke } from "@tauri-apps/api/core";
import { BACKEND_PORT } from "../backendPort";
import { useSessionsStore } from "../stores/sessionsStore";
import type { SessionsStore } from "../stores/sessionTypes";
import { withClientTurnIdentity } from "../ws/clientTurnIdentity";
import { useProvidersStore, type ProviderEntry } from "./providersStore";
// settings panel provider mutation events
import { dispatchProviderEvent, type ProviderEventMessage } from "../components/providersStore";
import type { ControlHandler, ControlWireMessage } from "./controlWsTypes";
import { pendingRunStatusQueries } from "./controlWsShared";
import {
  onCompanionIdentityUnready,
  onCompanionEvent,
  onCompanionProjectionRetracted,
  onCompanionProvisionalDelta,
} from "./controlWsCompanion";
import {
  onSessionTodoUpdate,
  onChatV2ContinuationAccepted,
  onChatV2ContinuationStatus,
  onChatResponse,
  onChatV2Delta,
  onContextUsage,
  onChatV2UserEcho,
  onChatV2ReasoningActivity,
  onChatV2ReasoningSummary,
  onChatV2Final,
  onChatV2Error,
  onChatV2Interrupted,
  onSessionRunStatusAck,
} from "./controlWsStream";
import {
  onTaskProjectionsResponse,
  onTaskProjectionUpdated,
  onSessionSwitched,
  onWorkflowEvent,
  onChatV2Plan,
  onPptOutlineProposed,
  onPptOutlineResolved,
  onChatV2PlanCancelled,
  onSubagentProgress,
  onSlashCommandResult,
  onToolCall,
  onToolResult,
  onPermissionRequest,
  onExternalWaitRequest,
  onSupervisorAlert,
  onSupervisorToggleAck,
} from "./controlWsTasks";
import { onSessionMessagesError, onSessionMessagesResponse } from "./controlWsHistory";
import {
  onSettingsProvidersListResponse,
  onSettingsProvidersReordered,
  onSessionProviderBinding,
  onModelsListResponse,
  onModelContextSetAck,
  onSessionModelSet,
} from "./controlWsSettings";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;
export const CONNECT_TIMEOUT_MS = 5000;
const RUN_STATUS_ACK_TIMEOUT_MS = 5_000;
// ─── 控制连接身份（2026-08-04 Workbench UI 改版，B5）───────────────
// 聊天通道（本模块单例 WS）的显式身份声明。改版前由 route hash 推断
// （#/message-panel → message-panel 窗身份）；message-panel 窗口删除后
// 本模块只在主窗加载，身份固定为 main + companion_action。
/** 聊天通道声明的 Tauri 窗口标签（与 Rust/Python 白名单五处同步）。 */
const REQUESTED_LABEL = "main";
/** 聊天通道声明的特权作用域（companion challenge 走此作用域）。 */
const REQUESTED_SCOPE = "companion_action";
/**
 * 控制会话 sid。**历史命名保留**：字符串 "message-panel-main" 源自独立
 * 消息面板窗时代，后端 main.py（:6459/:7295 一带的会话过滤/投影链路）与
 * session/task_scope.py 硬编码映射此 sid → "default" 会话组。改名会抖动
 * 整条后端投影链路（D1 决策：sid 不改，仅注释说明）。
 */
export const CONTROL_SESSION_ID = "message-panel-main";
type GlobalWS = {
  __deskpet_panel_ws__?: WebSocket | null;
  __deskpet_panel_listeners__?: Set<(msg: unknown) => void>;
  __deskpet_companion_identity_status__?: ControlWireMessage | null;
};
const G = globalThis as unknown as GlobalWS;
let ws: WebSocket | null = G.__deskpet_panel_ws__ ?? null;
let reconnect_timer: number | null = null;
let reconnect_attempt = 0;
let connect_timeout: number | null = null;
function clear_connect_timeout() {
  if (connect_timeout == null) return;
  globalThis.clearTimeout(connect_timeout);
  connect_timeout = null;
}
type Listener = (msg: unknown) => void;
const listeners: Set<Listener> = G.__deskpet_panel_listeners__ ?? new Set<Listener>();
G.__deskpet_panel_listeners__ = listeners;
export interface ControlWS {
  send(msg: { type: string; payload?: Record<string, unknown> }): boolean;
  send_command(msg: WorkflowCommand): boolean;
  on_message(fn: Listener): () => void;
  on_state_change(fn: (state: "disconnected" | "connecting" | "connected") => void): () => void;
  state(): "disconnected" | "connecting" | "connected";
}
export interface WorkflowCommand {
  type: string;
  request_id: string;
  payload: Record<string, unknown>;
}
let latestCompanionIdentityStatus: ControlWireMessage | null =
  G.__deskpet_companion_identity_status__ ?? null;
let current_state: ControlWS["state"] extends () => infer R ? R : never = "disconnected";
const state_listeners = new Set<(state: typeof current_state) => void>();
function set_control_state(next: typeof current_state) {
  if (current_state === next) return;
  current_state = next;
  state_listeners.forEach((listener) => {
    try {
      listener(next);
    } catch (error) {
      console.warn(error);
    }
  });
}
async function open_socket() {
  // Idempotent guard with global lookup. Any pre-existing socket on
  // globalThis (from a previous module instance / HMR cycle) wins —
  // we just reuse it. Without this we saw a connect/disconnect
  // storm in the backend log every dev iteration because each
  // module reload was calling open_socket() while the old socket
  // was still mid-handshake.
  const existing = G.__deskpet_panel_ws__;
  if (existing && existing.readyState === WebSocket.OPEN) {
    ws = existing;
    set_control_state("connected");
    while (_outbox.length > 0) {
      const queued = _outbox.shift();
      if (queued) ws.send(queued);
    }
    return;
  }
  if (existing && existing.readyState === WebSocket.CONNECTING) {
    ws = existing;
    return;
  }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  set_control_state("connecting");
  let secret = "";
  try {
    // Dev browser preview (`?secret=`, same hatch as App.tsx): no Tauri IPC bridge.
    const devSecret = import.meta.env.DEV
      ? new URLSearchParams(window.location.search).get("secret")
      : null;
    secret = devSecret || (await invoke<string>("get_shared_secret"));
  } catch (e) {
    console.warn("[session-control] get_shared_secret failed:", e);
    set_control_state("disconnected");
    schedule_reconnect();
    return;
  }
  if (!secret) {
    // A missing secret means the backend is not ready. Do not leave the
    // visible ChatView/sidebar stuck in the transient "connecting" state
    // for the entire exponential-backoff interval.
    set_control_state("disconnected");
    schedule_reconnect();
    return;
  }
  // 2026-08-04 Workbench UI 改版（B5）：message-panel 独立窗口已并入主窗，
  // 原按 route hash 推断连接身份的逻辑删除，改为显式常量。本模块现在只在
  // 主窗（label=main）加载，聊天通道固定声明 companion_action 作用域——
  // 与 Rust webview_permissions 白名单、Python ALLOWED_WINDOW_SCOPES、
  // control_ingress expected_window_label、SQL CHECK（迁移 007）五处同步。
  const url = `ws://127.0.0.1:${BACKEND_PORT}/ws/control?secret=${encodeURIComponent(
    secret,
  )}&session_id=${CONTROL_SESSION_ID}&requested_window_label=${REQUESTED_LABEL}&requested_scope=${REQUESTED_SCOPE}`;
  try {
    ws = new WebSocket(url);
    G.__deskpet_panel_ws__ = ws;
  } catch (e) {
    console.warn("[session-control] ws ctor failed:", e);
    schedule_reconnect();
    return;
  }
  const connectingSocket = ws;
  clear_connect_timeout();
  connect_timeout = globalThis.setTimeout(() => {
    connect_timeout = null;
    if (ws !== connectingSocket || connectingSocket.readyState !== WebSocket.CONNECTING) {
      return;
    }
    console.warn("[session-control] websocket handshake timed out");
    set_control_state("disconnected");
    connectingSocket.close();
  }, CONNECT_TIMEOUT_MS) as unknown as number;
  ws.onopen = () => {
    clear_connect_timeout();
    reconnect_attempt = 0;
    set_control_state("connected");
    // A provisional stream is meaningful only on the socket that delivered
    // it. Drop every partial before durable history is requested.
    useSessionsStore.getState().clear_companion_provisional();
    // Bug#2 修复 (2026-06-11)：flush 断连期间排队的用户消息(原 send() 在
    // socket 非 OPEN 时直接丢弃 — 用户消息静默消失的传输层真因)。先 flush
    // 再拉列表,保持用户消息的先后顺序。
    while (_outbox.length > 0) {
      const queued = _outbox.shift();
      if (queued) ws?.send(queued);
    }
    // Pull the live model catalog so the
    // picker's dropdown is data-driven (the relay /models), not hardcoded.
    ws?.send(JSON.stringify({ type: "models_list" }));
    // Pull the provider list so the per-session provider dropdown shows
    // the REAL provider names from Settings → LLM Providers (e.g.
    // "relay"), not just the generic "Global Chain" placeholder.
    ws?.send(JSON.stringify({ type: "settings_providers_list_request" }));
    const store = useSessionsStore.getState();
    const sid = store.active_sid;
    // rehydrate chat history for every known session
    // from SessionDB. Without this, refreshing the panel wipes the
    // user's scrollback even though the messages are persisted.
    // 2026-08-09：原先这里额外硬拉一次保留会话 "default"。保留会话已移除，
    // 只回灌 store 里真实存在的会话（空态时一条都不发）。
    const known_sids = new Set<string>(sid ? [sid] : []);
    for (const k of Object.keys(store.sessions)) known_sids.add(k);
    for (const target of known_sids) {
      ws?.send(
        JSON.stringify({
          type: "session_messages_load",
          payload: { session_id: target, limit: 200 },
        }),
      );
      ws?.send(
        JSON.stringify({
          type: "task_projections_list",
          payload: { session_id: target },
        }),
      );
      // 2026-05-31 restore — pull cached context-usage so ring gauge
      // hydrates immediately on (re)connect instead of waiting for the
      // next LLM turn.
      ws?.send(
        JSON.stringify({
          type: "context_usage_request",
          payload: { session_id: target },
        }),
      );
    }
  };
  ws.onmessage = (ev) => {
    let parsed: ControlWireMessage;
    try {
      parsed = JSON.parse(ev.data);
    } catch {
      return;
    }
    dispatch(parsed);
    listeners.forEach((fn) => {
      try {
        fn(parsed);
      } catch (e) {
        console.warn(e);
      }
    });
  };
  ws.onclose = () => {
    clear_connect_timeout();
    set_control_state("disconnected");
    useSessionsStore.getState().clear_companion_provisional();
    schedule_reconnect();
  };
  ws.onerror = () => {
    // onclose fires after; reconnect handled there.
  };
}
function schedule_reconnect() {
  if (reconnect_timer != null) return;
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * Math.pow(2, reconnect_attempt));
  reconnect_attempt++;
  // globalThis.setTimeout works in both browser + node (vitest);
  // window.setTimeout would crash in the node test env when ws.ts is imported.
  reconnect_timer = globalThis.setTimeout(() => {
    reconnect_timer = null;
    void open_socket();
  }, delay) as unknown as number;
}
function bindDeferredMessagesToRun(
  sid: string,
  parentRequestId: string,
  runId: string,
  taskScopeId: string,
): void {
  if (!parentRequestId || !runId) return;
  const state = useSessionsStore.getState();
  const session = state.sessions[sid];
  if (!session) return;
  let changed = false;
  const messages = session.messages.map((message) => {
    if (!message.deferred_send || message.deferred_parent_request_id !== parentRequestId) {
      return message;
    }
    changed = true;
    return {
      ...message,
      run_id: runId,
      task_scope_id: taskScopeId || message.task_scope_id,
      deferred_parent_request_id: undefined,
    };
  });
  if (changed) state.set_messages(sid, messages);
}
function flushDeferredMessages(
  sid: string,
  runId: string,
  taskScopeId: string,
  conversationBoundaryVersion: number | undefined,
): void {
  if (typeof conversationBoundaryVersion !== "number") return;
  const state = useSessionsStore.getState();
  const session = state.sessions[sid];
  if (!session) return;
  const queued = session.messages.filter(
    (message) =>
      message.role === "user" &&
      message.run_id === runId &&
      message.deferred_send === true &&
      Boolean(message.request_id && message.turn_id && message.text),
  );
  if (queued.length === 0) return;

  const failed = new Set<string>();
  for (const message of queued) {
    const sent = controlWS.send({
      type: "chat_v2",
      payload: {
        text: message.text,
        session_id: sid,
        request_id: message.request_id,
        turn_id: message.turn_id,
        target_root_run_id: runId,
        task_scope_id: taskScopeId,
        conversation_boundary_version: conversationBoundaryVersion,
      },
    });
    if (!sent && message.request_id) failed.add(message.request_id);
  }
  const latest = useSessionsStore.getState().sessions[sid];
  if (!latest) return;
  useSessionsStore.getState().set_messages(
    sid,
    latest.messages.map((message) => {
      if (message.role !== "user" || message.run_id !== runId || message.deferred_send !== true) {
        return message;
      }
      const didFail = Boolean(message.request_id && failed.has(message.request_id));
      return {
        ...message,
        deferred_send: false,
        continuation_status: didFail ? "failed" : "waiting",
        continuation_error: didFail ? "控制通道未连接" : undefined,
      };
    }),
  );
}
function onCompanionProfileBound(
  msg: ControlWireMessage,
  store: SessionsStore,
  _sid: string,
): void {
  const p = msg.payload || {};
  const profileId = String(p.profile_id || "");
  const profileGeneration = Number(p.profile_generation || 0);
  if (profileId && Number.isInteger(profileGeneration) && profileGeneration > 0) {
    const inboxSessionId = String(p.session_id || "").trim();
    store.set_companion_owner({
      profile_id: profileId,
      profile_generation: profileGeneration,
    });
    if (inboxSessionId) {
      store.ensure(inboxSessionId);
      store.set_active(inboxSessionId);
    }
    ws?.send(
      JSON.stringify({
        type: "session_messages_load",
        payload: {
          session_id: inboxSessionId || store.active_sid,
          limit: 200,
        },
      }),
    );
  }
}

function onCompanionIdentityStatus(
  msg: ControlWireMessage,
  store: SessionsStore,
  _sid: string,
): void {
  const p = msg.payload || {};
  if (p.ready === false || p.status === "unready") {
    store.set_companion_owner(null);
    return;
  }
  const profileId = String(p.profile_id || "");
  const profileGeneration = Number(p.profile_generation || 0);
  if (profileId && Number.isInteger(profileGeneration) && profileGeneration > 0) {
    const inboxSessionId = String(p.session_id || "").trim();
    store.set_companion_owner({
      profile_id: profileId,
      profile_generation: profileGeneration,
    });
    if (inboxSessionId) {
      store.ensure(inboxSessionId);
      store.set_active(inboxSessionId);
    }
    ws?.send(
      JSON.stringify({
        type: "session_messages_load",
        payload: {
          session_id: inboxSessionId || store.active_sid,
          limit: 200,
        },
      }),
    );
  }
}

function onChatV2RunReserved(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
  const p = msg.payload || {};
  const runId = String(p.run_id || "").trim();
  if (runId) {
    const session = useSessionsStore.getState().sessions[sid];
    const requestId = String(p.request_id || "").trim();
    const taskScopeId = String(p.task_scope_id || "");
    bindDeferredMessagesToRun(sid, requestId, runId, taskScopeId);
    const refreshedSession = useSessionsStore.getState().sessions[sid];
    const selectedRunId = session?.selected_run_id;
    const selectedProjection = selectedRunId
      ? refreshedSession?.run_projections[selectedRunId]
      : undefined;
    const locallyOriginated = Boolean(
      requestId &&
        refreshedSession?.messages.some(
          (message) =>
            message.role === "user" &&
            message.request_id === requestId &&
            (!message.run_id || message.run_id === runId),
        ),
    );
    const shouldSelect =
      locallyOriginated ||
      !selectedRunId ||
      selectedRunId === runId ||
      Boolean(
        selectedProjection &&
          ["completed", "failed", "cancelled"].includes(selectedProjection.status),
      );
    store.upsert_run_projection(sid, runId, {
      task_scope_id: taskScopeId,
      request_id: requestId || undefined,
      turn_id: String(p.turn_id || "") || undefined,
      conversation_boundary_ref: String(p.conversation_boundary_ref || "") || undefined,
      conversation_boundary_version:
        typeof p.conversation_boundary_version === "number"
          ? p.conversation_boundary_version
          : undefined,
      version: typeof p.projection_version === "number" ? p.projection_version : 0,
      status: msg.type === "chat_v2_run_reserved" ? "starting" : "running",
      inflight: true,
      ui_state: shouldSelect ? "open" : "background",
    });
    if (shouldSelect) {
      useSessionsStore.getState().select_run_projection(sid, runId);
    }
    if (
      requestId &&
      useSessionsStore.getState().sessions[sid]?.pending_root_request_id === requestId
    ) {
      store.upsert(sid, {
        pending_root_request_id: undefined,
        pending_root_turn_id: undefined,
      });
    }
    if (msg.type === "chat_v2_run_started") {
      flushDeferredMessages(
        sid,
        runId,
        taskScopeId,
        typeof p.conversation_boundary_version === "number"
          ? p.conversation_boundary_version
          : undefined,
      );
    }
  }
}

// code session binding events
function onProvidersChanged(msg: ControlWireMessage, _store: SessionsStore, _sid: string): void {
  // Settings-side mutation broadcast refreshes the registry snapshot.
  // Session bindings are durable tombstones and must not be silently
  // rewritten when a provider disappears.
  const incoming = Array.isArray(msg.payload?.providers) ? msg.payload.providers : [];
  useProvidersStore.getState().set_providers(incoming as ProviderEntry[]);
  // Mirror into Phase 4 settings store so SettingsProviders re-renders.
  dispatchProviderEvent(msg as ProviderEventMessage);
  // The model catalog is a derived view of the active Provider chain.
  // Refresh it from the backend authority whenever that chain changes;
  // otherwise a catalog fetched before Provider setup remains empty for
  // the lifetime of the socket and the model picker only shows "default".
  ws?.send(JSON.stringify({ type: "models_list" }));
}

/** 控制通道消息类型 → 处理函数（9G：原 dispatch 的 switch 分支，同一分支的多个类型共用一个函数）。 */
const CONTROL_HANDLERS = new Map<string, ControlHandler>([
  ["companion_profile_bound", onCompanionProfileBound],
  ["companion_identity_status", onCompanionIdentityStatus],
  ["companion_identity_unready", onCompanionIdentityUnready],
  ["companion_event", onCompanionEvent],
  ["companion_projection_retracted", onCompanionProjectionRetracted],
  ["companion_provisional_delta", onCompanionProvisionalDelta],
  ["session_todo_update", onSessionTodoUpdate],
  ["chat_v2_run_reserved", onChatV2RunReserved],
  ["chat_v2_run_started", onChatV2RunReserved],
  ["chat_v2_continuation_accepted", onChatV2ContinuationAccepted],
  ["chat_v2_continuation_status", onChatV2ContinuationStatus],
  ["task_projections_response", onTaskProjectionsResponse],
  ["task_projection_updated", onTaskProjectionUpdated],
  ["session_switched", onSessionSwitched],
  ["task_session_started", onSessionSwitched],
  ["chat_response", onChatResponse],
  ["chat_v2_delta", onChatV2Delta],
  ["context_usage", onContextUsage],
  ["chat_v2_user_echo", onChatV2UserEcho],
  ["chat_v2_reasoning_activity", onChatV2ReasoningActivity],
  ["chat_v2_reasoning_summary", onChatV2ReasoningSummary],
  ["chat_v2_final", onChatV2Final],
  ["workflow_event", onWorkflowEvent],
  ["workflow_final", onWorkflowEvent],
  ["chat_v2_error", onChatV2Error],
  ["chat_v2_plan", onChatV2Plan],
  ["ppt_outline_proposed", onPptOutlineProposed],
  ["ppt_outline_resolved", onPptOutlineResolved],
  ["chat_v2_plan_cancelled", onChatV2PlanCancelled],
  ["chat_v2_interrupted", onChatV2Interrupted],
  ["session_run_status_ack", onSessionRunStatusAck],
  ["subagent_progress", onSubagentProgress],
  ["slash_command_result", onSlashCommandResult],
  ["tool_call", onToolCall],
  ["tool_result", onToolResult],
  ["session_messages_error", onSessionMessagesError],
  ["sessions_list_error", onSessionMessagesError],
  ["session_messages_response", onSessionMessagesResponse],
  ["permission_request", onPermissionRequest],
  ["external_wait_request", onExternalWaitRequest],
  ["project_directory_request", onExternalWaitRequest],
  ["supervisor_alert", onSupervisorAlert],
  ["supervisor_toggle_ack", onSupervisorToggleAck],
  ["providers_changed", onProvidersChanged],
  ["settings_providers_list_response", onSettingsProvidersListResponse],
  ["settings_providers_reordered", onSettingsProvidersReordered],
  ["settings_providers_added", onSettingsProvidersReordered],
  ["settings_providers_updated", onSettingsProvidersReordered],
  ["settings_providers_removed", onSettingsProvidersReordered],
  ["settings_providers_error", onSettingsProvidersReordered],
  ["session_provider_binding", onSessionProviderBinding],
  ["session_provider_set", onSessionProviderBinding],
  ["models_list_response", onModelsListResponse],
  ["model_context_set_ack", onModelContextSetAck],
  ["session_model_set", onSessionModelSet],
]);

function dispatch(msg: ControlWireMessage) {
  if (msg?.type === "companion_identity_status" || msg?.type === "companion_identity_unready") {
    latestCompanionIdentityStatus = msg;
    G.__deskpet_companion_identity_status__ = msg;
  }
  const store = useSessionsStore.getState();
  const sid: string = (msg?.payload && msg.payload.session_id) || store.active_sid;

  // 按消息类型查处理函数；表里没有的类型不处理（the pet shell may handle them）。
  const handler = typeof msg.type === "string" ? CONTROL_HANDLERS.get(msg.type) : undefined;
  if (handler) handler(msg, store, sid);
}
// Bug#2 修复 (2026-06-11)：断连时的出站消息排队(原实现直接丢弃 — 用户
// 消息静默消失)。重连 onopen 时 flush。上限防泄漏:超出丢最旧并告警。
const _outbox: string[] = [];
const _OUTBOX_MAX = 50;
export const controlWS: ControlWS = {
  send(msg) {
    let outbound = msg;
    if (msg.type === "chat_v2_interrupt") {
      const payload = { ...(msg.payload ?? {}) };
      const sid = String(payload.session_id || useSessionsStore.getState().active_sid);
      const session = useSessionsStore.getState().sessions[sid];
      const runId = String(payload.root_run_id || payload.run_id || session?.active_run_id || "");
      const version = session?.run_projections?.[runId]?.version ?? 0;
      outbound = {
        ...msg,
        payload: {
          ...payload,
          root_run_id: runId,
          expected_run_version: payload.expected_run_version ?? version,
          cancel_command_id: payload.cancel_command_id ?? `cancel:${runId}:v${version}`,
          reason: payload.reason ?? "user_interrupt",
        },
      };
    }
    const identified = withClientTurnIdentity(outbound);
    const serialized = JSON.stringify(identified);
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(serialized);
        return true;
      } catch (e) {
        console.warn("[session-control] socket send failed; queueing message", e);
        _outbox.push(serialized);
        if (_outbox.length > _OUTBOX_MAX) _outbox.shift();
        schedule_reconnect();
        return false;
      }
    } else {
      if (msg.type === "chat_v2" || msg.type === "slash_command") {
        console.warn("[session-control] socket not open; refusing interactive message");
        schedule_reconnect();
        return false;
      }
      console.warn("[session-control] socket not open; queueing message for flush on reconnect");
      _outbox.push(serialized);
      if (_outbox.length > _OUTBOX_MAX) _outbox.shift();
      schedule_reconnect();
      return true;
    }
  },
  send_command(msg) {
    if (current_state !== "connected" || !ws || ws.readyState !== WebSocket.OPEN) {
      schedule_reconnect();
      return false;
    }
    try {
      ws.send(JSON.stringify(msg));
      return true;
    } catch (e) {
      console.warn("[session-control] workflow command send failed", e);
      schedule_reconnect();
      return false;
    }
  },
  on_message(fn) {
    listeners.add(fn);
    const identityStatus = latestCompanionIdentityStatus;
    if (identityStatus) {
      queueMicrotask(() => {
        if (!listeners.has(fn)) return;
        try {
          fn(identityStatus);
        } catch (error) {
          console.warn(error);
        }
      });
    }
    return () => {
      listeners.delete(fn);
    };
  },
  on_state_change(fn) {
    state_listeners.add(fn);
    return () => {
      state_listeners.delete(fn);
    };
  },
  state() {
    return current_state;
  },
};
// vitest hook. Production never imports this; tests use it to
// drive `dispatch` without spinning up a real WebSocket. Exported under a
// `__test_` prefix to make the intent obvious at call sites.
// 测试钩子：与真实通道一样，传入的消息不经校验。
export const __test_dispatch = dispatch as (msg: unknown) => void;
export function __test_register_pending_run_status_query(
  queryId: string,
  sid: string,
  runId: string,
) {
  const timer = setTimeout(() => undefined, 60_000);
  pendingRunStatusQueries.set(queryId, { sid, runId, timer });
}
export function __test_reset_companion_identity_status() {
  latestCompanionIdentityStatus = null;
  G.__deskpet_companion_identity_status__ = null;
  for (const pending of pendingRunStatusQueries.values()) {
    clearTimeout(pending.timer);
  }
  pendingRunStatusQueries.clear();
}
// ─── Session Health Check (Bug #2 防御) ─────────────────────────────
// 2026-08-17: 防止会话状态永久卡在 "thinking" 或 "running"。
// 每 30 秒检查一次，如果发现会话卡住超过 2 分钟，强制重置为 idle。

const SESSION_HEALTH_CHECK_INTERVAL = 15_000;
// 15 秒（更频繁检查）
const SESSION_STUCK_TIMEOUT = 60_000;
// 60 秒（从 120 秒降低，更快恢复）

let healthCheckTimer: ReturnType<typeof setInterval> | null = null;
function startSessionHealthCheck() {
  if (healthCheckTimer) return; // 已经在运行

  healthCheckTimer = setInterval(() => {
    const store = useSessionsStore.getState();
    const sessions = store.sessions;
    const now = Date.now();

    Object.values(sessions).forEach((session) => {
      const stuckDuration = now - session.last_activity;
      const isStuck =
        (session.status === "thinking" || session.status === "running") &&
        session.inflight &&
        stuckDuration > SESSION_STUCK_TIMEOUT;

      if (isStuck) {
        console.warn(
          `[HealthCheck] Session ${session.base_session_id} requests authoritative Run status after ${stuckDuration}ms`,
        );

        const runId = String(session.active_run_id || "").trim();
        if (current_state === "connected" && runId) {
          if (
            [...pendingRunStatusQueries.values()].some(
              (pending) => pending.sid === session.base_session_id && pending.runId === runId,
            )
          )
            return;
          const projection = session.run_projections?.[runId];
          const queryId = `run-status:${runId}:${Date.now()}`;
          controlWS.send({
            type: "session_run_status_query",
            payload: {
              query_id: queryId,
              session_id: session.base_session_id,
              root_run_id: runId,
              sdk_run_id: "",
              observed_run_version: projection?.version ?? 0,
              observed_ui_status: session.status,
              observed_inflight: session.inflight,
              stuck_duration_ms: stuckDuration,
            },
          });
          const timer = setTimeout(() => {
            pendingRunStatusQueries.delete(queryId);
            const latest = useSessionsStore.getState().sessions[session.base_session_id];
            if (latest?.active_run_id === runId) {
              // Connection-recovery presentation only. Do not mutate the
              // authoritative Run projection or release inflight ownership.
              useSessionsStore.getState().upsert(session.base_session_id, {
                status: "error",
                inflight: latest.inflight,
                active_run_id: runId,
              });
            }
          }, RUN_STATUS_ACK_TIMEOUT_MS);
          pendingRunStatusQueries.set(queryId, {
            sid: session.base_session_id,
            runId,
            timer,
          });
        }
      }
    });
  }, SESSION_HEALTH_CHECK_INTERVAL);

  console.log("[HealthCheck] Session health monitor started");
}
function stopSessionHealthCheck() {
  if (healthCheckTimer) {
    clearInterval(healthCheckTimer);
    healthCheckTimer = null;
    console.log("[HealthCheck] Session health monitor stopped");
  }
}
// 启动健康检查
startSessionHealthCheck();
// Auto-connect on import. Caller doesn't need to do anything.
void open_socket();
// Vite HMR: when this module is hot-replaced, close the existing
// socket cleanly so the backend frees the slot before the new module
// tries to open another. Without this we leak a socket per HMR cycle
// and the backend keeps closing them with code 4002 "session
// replaced", which onclose interprets as needing a reconnect — hence
// the reconnect storm.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    stopSessionHealthCheck(); // 清理健康检查定时器
    if (reconnect_timer != null) {
      clearTimeout(reconnect_timer);
      reconnect_timer = null;
    }
    try {
      ws?.close(1000, "hmr dispose");
    } catch {
      /* noop */
    }
    ws = null;
    listeners.clear();
  });
}
