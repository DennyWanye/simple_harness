// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S23 — WebSocket dispatcher for the code panel window.
 *
 * Both windows talk to the same backend on `ws://127.0.0.1:8100`.
 * The pet window already owns its WS via `useControlChannel`; the
 * code panel opens its OWN connection so backend can broadcast the
 * same event to both windows independently (matters for multi-session
 * — a tile in the panel grid updates without round-tripping through
 * the pet's React state).
 *
 * The shared secret is fetched via Tauri's `get_shared_secret` IPC
 * (already implemented in process_manager.rs).
 */
import { invoke } from "@tauri-apps/api/core";
import { BACKEND_PORT } from "../backendPort";
import { useSessionsStore } from "../stores/sessionsStore";
import { withClientTurnIdentity } from "../ws/clientTurnIdentity";
import {
  getWindowControlCredential,
  type WindowControlCredential,
} from "../auth/windowControlCredential";
import type { CanonicalJson } from "../auth/controlCommandCanonical";
import { useSubagentStore } from "./subagentStore";
import { useProvidersStore } from "./providersStore";
import { useSessionModelsStore } from "./sessionModelsStore";
// P5-S2 Phase 4 — settings panel provider mutation events
import { dispatchProviderEvent } from "../components/SettingsProviders";
import type { CompanionEvent } from "../types/messages";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

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

function companionEventFrom(value: unknown): CompanionEvent | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const outer = value as Record<string, any>;
  const raw =
    outer.type === "companion_event" &&
    outer.payload &&
    typeof outer.payload === "object" &&
    !Array.isArray(outer.payload)
      ? outer.payload as Record<string, any>
      : outer;
  const notificationSource =
    raw.notification && typeof raw.notification === "object"
      ? raw.notification
      : raw;
  const event: CompanionEvent = {
    event_id: String(raw.event_id || raw.projection_event_id || ""),
    profile_id: String(raw.profile_id || ""),
    profile_generation: Number(raw.profile_generation || 0),
    session_id: String(raw.session_id || ""),
    seq: Number(raw.seq ?? 0),
    route_version:
      raw.route_version === undefined ? undefined : Number(raw.route_version),
    redaction_version:
      raw.redaction_version === undefined
        ? undefined
        : Number(raw.redaction_version),
    importance:
      raw.importance === "important" ? "important" : "normal",
    occurred_at:
      typeof raw.occurred_at === "string" ? raw.occurred_at : undefined,
    received_at:
      typeof raw.received_at === "number" ? raw.received_at : Date.now(),
    notification: {
      notification_id: String(notificationSource.notification_id || ""),
      kind: String(notificationSource.kind || "growth_notice"),
      summary: String(notificationSource.summary || ""),
      detail_ref: String(notificationSource.detail_ref || ""),
      detail_version: String(notificationSource.detail_version || ""),
      available_actions: Array.isArray(notificationSource.available_actions)
        ? notificationSource.available_actions.map(String)
        : [],
    },
    decision:
      raw.decision && typeof raw.decision === "object"
        ? raw.decision
        : undefined,
    tombstone: raw.tombstone === true,
  };
  if (
    !event.event_id ||
    !event.profile_id ||
    !Number.isInteger(event.profile_generation) ||
    event.profile_generation < 1 ||
    !event.session_id ||
    !Number.isInteger(event.seq) ||
    event.seq < 0 ||
    !event.notification.notification_id
  ) {
    return null;
  }
  return event;
}

function persistedWorkflowFileArtifact(workflowEvent: unknown): Record<string, unknown> | null {
  if (!workflowEvent || typeof workflowEvent !== "object" || Array.isArray(workflowEvent)) {
    return null;
  }
  const payload = (workflowEvent as Record<string, unknown>).payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const payloadRecord = payload as Record<string, unknown>;
  const nestedPayload = payloadRecord.payload;
  const nestedPayloadRecord =
    nestedPayload && typeof nestedPayload === "object" && !Array.isArray(nestedPayload)
      ? nestedPayload as Record<string, unknown>
      : null;

  // Older workflow rows wrapped the intent payload once more than newer rows.
  // Accept both persisted shapes, but never recursively scan arbitrary payloads.
  const candidates = [nestedPayloadRecord?.artifact, payloadRecord.artifact];
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) continue;
    const artifact = candidate as Record<string, unknown>;
    if (artifact.kind !== "file") continue;
    const path = typeof artifact.path === "string" ? artifact.path.trim() : "";
    if (!path) continue;
    return { ...artifact, kind: "file", path };
  }
  return null;
}

// P4-S23 fix: stash the live socket on globalThis so vite HMR (which
// may swap this module while the old WebSocket is still mid-handshake)
// can find and close the previous instance instead of stacking
// sockets and getting kicked by the backend's "session replaced"
// guard. Without this we saw a connect/disconnect storm in the
// backend log every dev session.
type GlobalWS = {
  __deskpet_panel_ws__?: WebSocket | null;
  __deskpet_panel_listeners__?: Set<(msg: any) => void>;
  __deskpet_companion_challenge__?: CompanionActionChallenge | null;
  __deskpet_companion_identity_status__?: any | null;
};
const G = globalThis as unknown as GlobalWS;

let ws: WebSocket | null = G.__deskpet_panel_ws__ ?? null;
let reconnect_timer: number | null = null;
let reconnect_attempt = 0;

type Listener = (msg: any) => void;
const listeners: Set<Listener> = G.__deskpet_panel_listeners__ ?? new Set<Listener>();
G.__deskpet_panel_listeners__ = listeners;

export interface ControlWS {
  send(msg: { type: string; payload?: Record<string, unknown> }): boolean;
  send_command(msg: WorkflowCommand): boolean;
  send_companion_action(
    commandKind: string,
    body: CanonicalJson,
  ): Promise<boolean>;
  on_message(fn: Listener): () => void;
  state(): "disconnected" | "connecting" | "connected";
}

export interface WorkflowCommand {
  type: string;
  request_id: string;
  payload: Record<string, unknown>;
}

interface CompanionActionChallenge {
  connectionId: string;
  controlEpoch: string;
  challenge: string;
  requestSeq: string;
  bindingEpoch: string;
}

let companionChallenge: CompanionActionChallenge | null =
  G.__deskpet_companion_challenge__ ?? null;
let latestCompanionIdentityStatus: any | null =
  G.__deskpet_companion_identity_status__ ?? null;
let companionActionInFlight = false;

let current_state: ControlWS["state"] extends () => infer R ? R : never = "disconnected";

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
    current_state = "connected";
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
  current_state = "connecting";
  let secret = "";
  try {
    secret = await invoke<string>("get_shared_secret");
  } catch (e) {
    console.warn("[session-control] get_shared_secret failed:", e);
    schedule_reconnect();
    return;
  }
  if (!secret) {
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
  ws.onopen = () => {
    reconnect_attempt = 0;
    current_state = "connected";
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
    // P4-S23 F5 fix: rehydrate chat history for every known session
    // from SessionDB. Without this, refreshing the panel wipes the
    // user's scrollback even though the messages are persisted.
    // We pull the panel's "default" session AND any code-* sessions
    // already in the store. Backend dedupes by session_id.
    const known_sids = new Set<string>([sid, "default"]);
    for (const k of Object.keys(store.sessions)) known_sids.add(k);
    for (const target of known_sids) {
      ws?.send(JSON.stringify({
        type: "session_messages_load",
        payload: { session_id: target, limit: 200 },
      }));
      ws?.send(JSON.stringify({
        type: "task_projections_list",
        payload: { session_id: target },
      }));
      // 2026-05-31 restore — pull cached context-usage so ring gauge
      // hydrates immediately on (re)connect instead of waiting for the
      // next LLM turn.
      ws?.send(JSON.stringify({
        type: "context_usage_request",
        payload: { session_id: target },
      }));
    }
  };
  ws.onmessage = (ev) => {
    let parsed: any;
    try {
      parsed = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (parsed?.type === "companion_control_challenge") {
      const payload = parsed.payload ?? {};
      const next: CompanionActionChallenge = {
        connectionId: String(payload.connection_id ?? payload.connectionId ?? ""),
        controlEpoch: String(payload.control_epoch ?? payload.controlEpoch ?? ""),
        challenge: String(payload.challenge ?? ""),
        requestSeq: String(payload.request_seq ?? payload.requestSeq ?? ""),
        bindingEpoch: String(payload.binding_epoch ?? payload.bindingEpoch ?? ""),
      };
      if (
        next.connectionId &&
        next.controlEpoch &&
        next.challenge &&
        next.requestSeq &&
        next.bindingEpoch
      ) {
        companionChallenge = next;
        G.__deskpet_companion_challenge__ = next;
      }
    } else if (
      parsed?.type === "companion_control_rechallenge" ||
      parsed?.type === "companion_control_error"
    ) {
      companionChallenge = null;
      companionActionInFlight = false;
      G.__deskpet_companion_challenge__ = null;
    } else if (
      parsed?.type === "companion_control_command_settled" &&
      companionChallenge
    ) {
      const payload = parsed.payload ?? {};
      companionChallenge = {
        ...companionChallenge,
        requestSeq: String(
          payload.next_request_seq ??
            payload.nextRequestSeq ??
            BigInt(companionChallenge.requestSeq) + 1n,
        ),
        bindingEpoch: String(
          payload.binding_epoch ??
            payload.bindingEpoch ??
            companionChallenge.bindingEpoch,
        ),
      };
      companionActionInFlight = false;
      G.__deskpet_companion_challenge__ = companionChallenge;
    }
    dispatch(parsed);
    listeners.forEach((fn) => {
      try { fn(parsed); } catch (e) { console.warn(e); }
    });
  };
  ws.onclose = () => {
    current_state = "disconnected";
    companionActionInFlight = false;
    useSessionsStore.getState().clear_companion_provisional();
    schedule_reconnect();
  };
  ws.onerror = () => {
    // onclose fires after; reconnect handled there.
  };
}

function schedule_reconnect() {
  if (reconnect_timer != null) return;
  const delay = Math.min(
    RECONNECT_MAX_MS,
    RECONNECT_BASE_MS * Math.pow(2, reconnect_attempt),
  );
  reconnect_attempt++;
  // P5-S2 Phase 5: globalThis.setTimeout works in both browser + node (vitest);
  // window.setTimeout would crash in the node test env when ws.ts is imported.
  reconnect_timer = (globalThis as any).setTimeout(() => {
    reconnect_timer = null;
    void open_socket();
  }, delay) as unknown as number;
}

/**
 * FEAT-A2 — 把 slash_command_result 的 result 对象格式化成一条可读文本。
 * 8 种 result.type 全覆盖（help / goal_set / goal_status / goal_cleared /
 * skill_result / prefs_list / prefs_cleared / error）+ default 兜底（raw JSON），
 * 禁止任何 type 落空被静默丢弃。
 */
export function format_slash_result(result: any): string {
  const r = result || {};
  switch (r.type) {
    case "help": {
      const builtins = (r.builtins || [])
        .map((b: any) => `  /${b.name} — ${b.description || ""}`)
        .join("\n");
      const skills = (r.skills || [])
        .map((s: any) => `  /${s.name} — ${s.description || ""}`)
        .join("\n");
      const parts = ["可用命令:"];
      if (builtins) parts.push(builtins);
      if (skills) parts.push("可用 skill:", skills);
      return parts.join("\n");
    }
    case "goal_set":
      return `已设置目标: ${r.text || ""}（上限 ${r.max_iterations ?? "?"} 轮）`;
    case "goal_status":
      return r.active
        ? `当前目标: ${r.text || ""}（已用 ${r.iterations_used ?? 0}/${r.max_iterations ?? "?"} 轮${r.done ? "，已完成" : ""}）`
        : "当前无活动目标";
    case "goal_cleared":
      return r.ok ? "已清除当前目标" : "无目标可清除";
    case "skill_result":
      return `skill /${r.skill || ""} 输出:\n${
        typeof r.output === "string" ? r.output : JSON.stringify(r.output, null, 2)
      }`;
    case "prefs_list": {
      const entries = r.entries || [];
      if (!entries.length) return "偏好记忆为空（暂无意图/计划记录）";
      const lines = entries.map(
        (e: any) => `  ${e.kind}/${e.label}: ${e.text}`,
      );
      return [`偏好记忆（共 ${r.count ?? entries.length} 条）:`, ...lines].join("\n");
    }
    case "prefs_cleared":
      return `已清除 ${r.removed ?? 0} 条偏好记忆${
        r.kind ? `（kind=${r.kind}）` : ""
      }`;
    case "error":
      return `命令出错: ${r.message || "unknown"}${r.hint ? `\n提示: ${r.hint}` : ""}`;
    default:
      // 兜底：未知 type 也要展示，禁止静默丢。
      return `[slash 结果] ${JSON.stringify(r)}`;
  }
}

/**
 * Route a backend WS message into the zustand store. Most events
 * carry `payload.session_id`; otherwise use the currently active session.
 */
function selectNewestInflightRunAfterTerminal(
  sid: string,
  terminalRunId: string,
): void {
  const state = useSessionsStore.getState();
  const session = state.sessions[sid];
  if (!session || session.selected_run_id !== terminalRunId) return;
  const next = Object.values(session.run_projections)
    .filter(
      (projection) =>
        projection.run_id !== terminalRunId &&
        projection.inflight &&
        projection.ui_state !== "closed",
    )
    .sort((left, right) => right.last_activity - left.last_activity)[0];
  if (next) state.select_run_projection(sid, next.run_id);
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
    if (
      !message.deferred_send ||
      message.deferred_parent_request_id !== parentRequestId
    ) {
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
      if (
        message.role !== "user" ||
        message.run_id !== runId ||
        message.deferred_send !== true
      ) {
        return message;
      }
      const didFail = Boolean(
        message.request_id && failed.has(message.request_id),
      );
      return {
        ...message,
        deferred_send: false,
        continuation_status: didFail ? "failed" : "waiting",
        continuation_error: didFail ? "控制通道未连接" : undefined,
      };
    }),
  );
}

function dispatch(msg: any) {
  if (
    msg?.type === "companion_identity_status" ||
    msg?.type === "companion_identity_unready"
  ) {
    latestCompanionIdentityStatus = msg;
    G.__deskpet_companion_identity_status__ = msg;
  }
  const store = useSessionsStore.getState();
  const sid: string =
    (msg?.payload && msg.payload.session_id) ||
    store.active_sid;

  switch (msg.type) {
    case "companion_profile_bound": {
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
        ws?.send(JSON.stringify({
          type: "session_messages_load",
          payload: {
            session_id: inboxSessionId || store.active_sid,
            limit: 200,
          },
        }));
      }
      break;
    }
    case "companion_identity_status": {
      const p = msg.payload || {};
      if (p.ready === false || p.status === "unready") {
        store.set_companion_owner(null);
        break;
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
        ws?.send(JSON.stringify({
          type: "session_messages_load",
          payload: {
            session_id: inboxSessionId || store.active_sid,
            limit: 200,
          },
        }));
      }
      break;
    }
    case "companion_identity_unready": {
      store.set_companion_owner(null);
      break;
    }
    case "companion_event": {
      const event = companionEventFrom(msg.payload);
      if (event) {
        store.reduce_companion_event(event);
        if (event.decision?.kind === "action_confirmation") {
          store.clear_companion_provisional(event.decision.run_id);
        }
      }
      break;
    }
    case "companion_projection_retracted": {
      const p = msg.payload || {};
      store.retract_companion_projection({
        event_id: String(p.event_id || p.projection_event_id || ""),
        notification_id: String(p.notification_id || ""),
        profile_id: String(p.profile_id || ""),
        profile_generation: Number(p.profile_generation || 0),
        session_id: String(p.session_id || store.active_sid),
        seq: Number(p.seq ?? 0),
        route_version:
          p.route_version === undefined ? undefined : Number(p.route_version),
        redaction_version: Number(p.redaction_version || 0),
        occurred_at:
          typeof p.occurred_at === "string" ? p.occurred_at : undefined,
        received_at: Date.now(),
      });
      break;
    }
    case "companion_provisional_delta": {
      const p = msg.payload || {};
      const runId = String(p.run_id || "");
      const invocationId = String(p.invocation_id || "");
      const streamEpoch = String(p.stream_epoch || "");
      if (p.retract === true || p.cancelled === true || p.unknown === true) {
        store.clear_companion_provisional(runId, invocationId, streamEpoch);
      } else {
        store.upsert_companion_provisional(
          runId,
          invocationId,
          streamEpoch,
          String(p.content || ""),
        );
      }
      break;
    }
    case "session_todo_update": {
      const items = Array.isArray(msg.payload?.items) ? msg.payload.items : [];
      store.upsert_todos(sid, items);
      break;
    }
    case "chat_v2_run_reserved":
    case "chat_v2_run_started": {
      const p = msg.payload || {};
      const runId = String(p.run_id || "").trim();
      if (runId) {
        const session = useSessionsStore.getState().sessions[sid];
        const requestId = String(p.request_id || "").trim();
        const taskScopeId = String(p.task_scope_id || "");
        bindDeferredMessagesToRun(
          sid,
          requestId,
          runId,
          taskScopeId,
        );
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
              ["completed", "failed", "cancelled"].includes(
                selectedProjection.status,
              ),
          );
        store.upsert_run_projection(sid, runId, {
          task_scope_id: taskScopeId,
          request_id: requestId || undefined,
          turn_id: String(p.turn_id || "") || undefined,
          conversation_boundary_ref:
            String(p.conversation_boundary_ref || "") || undefined,
          conversation_boundary_version:
            typeof p.conversation_boundary_version === "number"
              ? p.conversation_boundary_version
              : undefined,
          version:
            typeof p.projection_version === "number"
              ? p.projection_version
              : 0,
          status:
            msg.type === "chat_v2_run_reserved" ? "starting" : "running",
          inflight: true,
          ui_state: shouldSelect ? "open" : "background",
        });
        if (shouldSelect) {
          useSessionsStore.getState().select_run_projection(sid, runId);
        }
        if (
          requestId &&
          useSessionsStore.getState().sessions[sid]
            ?.pending_root_request_id === requestId
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
      break;
    }
    case "chat_v2_continuation_accepted": {
      const p = msg.payload || {};
      const runId = String(p.run_id || "").trim();
      const requestId = String(p.request_id || "").trim();
      if (requestId) {
        store.set_continuation_status(
          sid,
          requestId,
          p.queued === true ? "waiting" : "bound",
        );
      }
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          task_scope_id: String(p.task_scope_id || ""),
          conversation_boundary_ref:
            String(p.conversation_boundary_ref || "") || undefined,
          conversation_boundary_version:
            typeof p.conversation_boundary_version === "number"
              ? p.conversation_boundary_version
              : undefined,
        });
      }
      break;
    }
    case "chat_v2_continuation_status": {
      const p = msg.payload || {};
      const requestId = String(p.request_id || "").trim();
      const continuationStatus = String(p.status || "").trim();
      if (
        requestId &&
        (continuationStatus === "bound" ||
          continuationStatus === "failed")
      ) {
        store.set_continuation_status(
          sid,
          requestId,
          continuationStatus,
          String(p.error || "").trim() || undefined,
        );
      }
      break;
    }
    case "task_projections_response": {
      const projections = Array.isArray(msg.payload?.projections)
        ? msg.payload.projections
        : [];
      projections.forEach((projection: any) => {
        const runId = String(projection?.run_id || "").trim();
        if (!runId) return;
        const existing =
          useSessionsStore.getState().sessions[sid]?.run_projections?.[runId];
        const durableStatus = String(projection.status || "");
        const status =
          durableStatus === "waiting"
            ? "waiting"
            : durableStatus === "completed"
              ? "completed"
              : durableStatus === "failed"
                ? "failed"
                : durableStatus === "cancelled"
                  ? "cancelled"
                  : durableStatus
                    ? "running"
                    : existing?.status ?? "waiting";
        const inflight =
          durableStatus === "created" ||
          durableStatus === "queued" ||
          durableStatus === "running" ||
          durableStatus === "cancel_requested";
        store.upsert_run_projection(sid, runId, {
          projection_id:
            String(projection.projection_id || "") || undefined,
          task_scope_id: String(projection.task_scope_id || ""),
          version: Number(projection.version || 0),
          conversation_boundary_ref:
            String(projection.conversation_boundary_ref || "") || undefined,
          conversation_boundary_version:
            typeof projection.conversation_boundary_version === "number"
              ? projection.conversation_boundary_version
              : undefined,
          ui_state:
            projection.ui_state === "background" ||
            projection.ui_state === "closed"
              ? projection.ui_state
              : "open",
          status,
          inflight: durableStatus ? inflight : existing?.inflight ?? false,
          started_at:
            typeof projection.started_at === "number"
              ? projection.started_at * 1000
              : existing?.started_at,
          last_activity:
            typeof projection.updated_at === "number"
              ? projection.updated_at * 1000
              : existing?.last_activity,
        });
      });
      break;
    }
    case "task_projection_updated": {
      const p = msg.payload || {};
      const runId = String(p.run_id || "").trim();
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          projection_id: String(p.projection_id || "") || undefined,
          task_scope_id: String(p.task_scope_id || ""),
          version: Number(p.version || 0),
          ui_state:
            p.ui_state === "background" || p.ui_state === "closed"
              ? p.ui_state
              : "open",
        });
      }
      break;
    }
    case "session_switched":
    case "task_session_started": {
      const p = msg.payload || {};
      const next_sid = typeof p.new_sid === "string" ? p.new_sid : "";
      if (next_sid) {
        store.ensure(next_sid);
        store.upsert(next_sid, {
          provider_id:
            p.provider_id === undefined ? null : p.provider_id,
          preferred_model:
            p.preferred_model === undefined ? null : p.preferred_model,
          model_params:
            p.model_params === undefined ? null : p.model_params,
        });
        store.set_active(next_sid);
      }
      break;
    }
    case "chat_response": {
      // Mid-loop assistant text (with tool calls). Render as assistant bubble.
      const text = msg.payload?.text;
      if (text) store.push_message(sid, { role: "assistant", text });
      break;
    }
    case "chat_v2_delta": {
      // P4-S25 A1: token chunk during streaming. Append to the in-progress
      // assistant bubble. We track the last "assistant" message in the
      // session and append to it; if the previous message isn't an
      // assistant (e.g. first delta of the turn), create a new one.
      const p = msg.payload || {};
      const kind = p.kind || "content";
      const chunk: string = p.content || "";
      const invocationId = String(p.invocation_id || "").trim();
      const streamEpoch = String(p.stream_epoch || "").trim();
      const runId = String(p.run_id || "").trim();
      if (p.retract_provisional) {
        store.clear_companion_provisional(runId, invocationId, streamEpoch);
        const cur = store.sessions[sid];
        if (cur) {
          store.set_messages(
            sid,
            cur.messages.filter(
              (message) =>
                !(
                  message.provisional === true &&
                  (!runId || message.run_id === runId) &&
                  (!invocationId || message.invocation_id === invocationId)
                ),
            ),
          );
        }
        break;
      }
      if (!chunk) break;
      if (p.provisional === true) {
        // Provisional provider output never enters ordinary Message history.
        // A malformed delta without the full triple is fail-closed.
        if (runId && invocationId && streamEpoch) {
          store.upsert_companion_provisional(
            runId,
            invocationId,
            streamEpoch,
            chunk,
          );
        }
        break;
      }
      // Reasoning chunks are visually muted in the UI; stash them on a
      // separate role for now. (Could fold into one bubble in B2.)
      const role = kind === "reasoning" ? "reasoning_delta" : "assistant_delta";
      const taskScopeId = String(p.task_scope_id || "").trim();
      const cur = store.sessions[sid];
      const last = cur?.messages.findLast(
        (message) =>
          message.role === (role as any) &&
          (!runId || message.run_id === runId) &&
          (!invocationId || message.invocation_id === invocationId),
      );
      if (last && last.role === (role as any)) {
        // Append to existing partial bubble in-place.
        last.text = (last.text || "") + chunk;
        // Trigger zustand re-render via a no-op upsert.
        store.upsert(sid, { last_activity: Date.now() });
      } else {
        store.push_message(sid, {
          role: role as any,
          text: chunk,
          run_id: runId || undefined,
          task_scope_id: taskScopeId || undefined,
          invocation_id: invocationId || undefined,
          stream_epoch: streamEpoch || undefined,
        });
      }
      break;
    }
    case "context_usage": {
      // 2026-05-31 restore — Claude-Code-style ring gauge update.
      const p = msg.payload || {};
      const target_sid = p.session_id || sid;
      store.upsert_context_usage({ ...p, session_id: target_sid });
      break;
    }
    case "chat_v2_user_echo": {
      // 2026-05-31 restore — multi-window sync: a peer window typed a user
      // message. Backend skips originator, so receiving means peer-origin
      // → directly push to local store.
      const p = msg.payload || {};
      const text = p.text;
      if (text) {
        store.push_message(sid, {
          role: "user",
          text,
          run_id: String(p.run_id || "") || undefined,
          task_scope_id: String(p.task_scope_id || "") || undefined,
          request_id: String(p.request_id || "") || undefined,
        });
      }
      break;
    }
    case "chat_v2_reasoning_activity": {
      // Provider reasoning tokens are intentionally not exposed as raw
      // chain-of-thought. The durable public summary arrives separately.
      const p = msg.payload || {};
      const runId = String(p.run_id || "").trim();
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          task_scope_id: String(p.task_scope_id || ""),
          status: "running",
          inflight: true,
        });
      }
      break;
    }
    case "chat_v2_reasoning_summary": {
      const p = msg.payload || {};
      const summaryId = String(p.summary_id || "").trim();
      const text = String(p.text || "").trim();
      if (!summaryId || !text) break;
      let current = store.sessions[sid];
      if (current) {
        const runId = String(p.run_id || "").trim();
        const lastUserIndex = current.messages.findLastIndex(
          (message) => message.role === "user",
        );
        const withoutLiveDuplicate = current.messages.filter(
          (message, index) =>
            !(
              index > lastUserIndex &&
              (message.role === "assistant" ||
                message.role === "assistant_delta") &&
              String(message.text || "").trim() === text &&
              (!runId || !message.run_id || message.run_id === runId)
            ),
        );
        if (withoutLiveDuplicate.length !== current.messages.length) {
          store.set_messages(sid, withoutLiveDuplicate);
          current = store.sessions[sid];
        }
      }
      const existingIndex = current?.messages.findIndex(
        (message) =>
          message.role === "reasoning_summary" &&
          message.reasoning_summary_id === summaryId,
      ) ?? -1;
      const summary = {
        role: "reasoning_summary" as const,
        text,
        reasoning_summary_id: summaryId,
        reasoning_phase:
          p.phase === "observation" || p.phase === "status"
            ? p.phase
            : "planning",
        reasoning_status:
          p.status === "failed" || p.status === "completed"
            ? p.status
            : "running",
        run_id: String(p.run_id || "") || undefined,
        task_scope_id: String(p.task_scope_id || "") || undefined,
      };
      if (existingIndex >= 0 && current) {
        store.set_messages(
          sid,
          current.messages.map((message, index) =>
            index === existingIndex ? { ...message, ...summary } : message,
          ),
        );
      } else {
        store.push_message(sid, { id: summaryId, ...summary });
      }
      break;
    }
    case "chat_v2_final": {
      const p = msg.payload || {};
      const text = p.text;
      const runId = String(p.run_id || "").trim();
      const taskScopeId = String(p.task_scope_id || "").trim();
      store.clear_companion_provisional(
        runId,
        String(p.invocation_id || ""),
        String(p.stream_epoch || ""),
      );
      // P4-S25 A1: final text replaces any partial deltas. Drop trailing
      // assistant_delta + reasoning_delta bubbles (they were the streaming
      // preview); the canonical assistant bubble lands here.
      const cur = store.sessions[sid];
      if (cur) {
        let cleaned = cur.messages.filter(
          (m) =>
            !(
              (m.role === ("assistant_delta" as any) ||
                m.role === ("reasoning_delta" as any)) &&
              (!runId || m.run_id === runId)
            ),
        );
        if (text) {
          const lastUserIdx = cleaned.findLastIndex(
            (m) =>
              m.role === "user" && (!runId || m.run_id === runId),
          );
          const hasSameTurnPreview = cleaned.some(
            (m, idx) =>
              idx > lastUserIdx &&
              m.role === "assistant" &&
              (!runId || m.run_id === runId) &&
              m.text === text,
          );
          if (hasSameTurnPreview) {
            cleaned = cleaned.filter(
              (m, idx) =>
                !(
                  idx > lastUserIdx &&
                  m.role === "assistant" &&
                  (!runId || m.run_id === runId) &&
                  m.text === text
                ),
            );
          }
        }
        store.set_messages(sid, cleaned);
      }
      if (text) {
        store.push_message(sid, {
          role: "assistant",
          text,
          run_id: runId || undefined,
          task_scope_id: taskScopeId || undefined,
        });
      }
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          task_scope_id: taskScopeId,
          status: "completed",
          inflight: false,
        });
        selectNewestInflightRunAfterTerminal(sid, runId);
      } else {
        store.upsert(sid, {
          status: "idle",
          inflight: false,
          active_run_id: null,
        });
      }
      break;
    }
    case "workflow_event":
    case "workflow_final": {
      const event = msg.payload || {};
      const eventId = String(event.event_id || "");
      const eventType = String(event.event_type || "");
      const body = event.payload || {};
      const nested = body.payload || {};
      const text = String(nested.text || body.text || "").trim();
      const currentBefore = store.sessions[sid];
      const projectionRunId = String(
        event.root_run_id || event.task_root_run_id || "",
      ).trim();
      const finishesTaskProjection =
        eventType === "workflow.final" &&
        !!projectionRunId &&
        !!currentBefore?.run_projections?.[projectionRunId];
      if (store.reduce_workflow_event(sid, event)) {
        if (finishesTaskProjection) {
          store.upsert_run_projection(sid, projectionRunId, {
            status: "completed",
            inflight: false,
          });
          selectNewestInflightRunAfterTerminal(sid, projectionRunId);
        } else {
          store.upsert(sid, { last_activity: Date.now() });
        }
        break;
      }
      const current = store.sessions[sid];
      if (eventId && current?.messages.some((message) => message.id === eventId)) break;
      if (text) {
        store.push_message(sid, {
          id: eventId || undefined,
          role: "assistant",
          text,
          run_id: projectionRunId || undefined,
        });
      }
      if (finishesTaskProjection) {
        store.upsert_run_projection(sid, projectionRunId, {
          status: "completed",
          inflight: false,
        });
        selectNewestInflightRunAfterTerminal(sid, projectionRunId);
      } else {
        store.upsert(sid, { last_activity: Date.now() });
      }
      break;
    }
    case "chat_v2_error": {
      const p = msg.payload || {};
      const runId = String(p.run_id || "").trim();
      const taskScopeId = String(p.task_scope_id || "").trim();
      const requestId = String(p.request_id || "").trim();
      store.clear_companion_provisional(runId);
      const current = store.sessions[sid];
      const continuationError = Boolean(
        requestId &&
          current?.messages.some(
            (message) =>
              message.role === "user" &&
              message.request_id === requestId &&
              message.continuation_status,
          ),
      );
      if (current) {
        store.set_messages(
          sid,
          current.messages.filter(
            (message) =>
              !(
                message.provisional === true &&
                (!runId || message.run_id === runId)
              ),
          ),
        );
      }
      if (continuationError) {
        store.set_continuation_status(
          sid,
          requestId,
          "failed",
          String(p.error || p.detail || p.reason || "续接失败"),
        );
      }
      const parts = [p.error, p.detail, p.reason].filter(Boolean);
      const txt = parts.length ? parts.join(" — ") : "unknown";
      store.push_message(sid, {
        role: "error",
        text: txt,
        run_id: runId || undefined,
        task_scope_id: taskScopeId || undefined,
      });
      if (runId && !continuationError) {
        store.upsert_run_projection(sid, runId, {
          task_scope_id: taskScopeId,
          status: "failed",
          inflight: false,
        });
        const latest = useSessionsStore.getState().sessions[sid];
        if (latest) {
          store.set_messages(
            sid,
            latest.messages.map((message) =>
              message.run_id === runId && message.deferred_send
                ? {
                    ...message,
                    deferred_send: false,
                    continuation_status: "failed",
                    continuation_error: txt,
                  }
                : message,
            ),
          );
        }
        selectNewestInflightRunAfterTerminal(sid, runId);
      } else if (!continuationError) {
        store.upsert(sid, {
          status: "error",
          inflight: false,
          active_run_id: null,
        });
      }
      break;
    }
    case "chat_v2_plan": {
      // P4-S25 A2: Plan card from the planner phase. Render it as a
      // dedicated bubble in the chat stream so the user can see the
      // intended steps before / during execution.
      const p = msg.payload || {};
      store.push_message(sid, {
        role: "plan" as any,
        plan_rationale: p.rationale,
        plan_steps: p.steps,
        plan_target_directory: p.target_directory,
        plan_action_categories: Array.isArray(p.action_categories)
          ? p.action_categories.map(String)
          : [],
        plan_auto_confirmed: !!p.auto_confirmed,
        // superpowers 决策2: 硬门开 → 渲染 [执行]/[取消] 按钮等确认
        plan_awaiting_confirm: !!p.awaiting_confirm,
        plan_sid: sid,
        plan_run_id: p.run_id,
        plan_decision_id: p.decision_id,
        plan_nonce: p.nonce,
        plan_version: p.version,
        run_id: String(p.run_id || "") || undefined,
        task_scope_id: String(p.task_scope_id || "") || undefined,
      } as any);
      break;
    }
    case "ppt_outline_proposed": {
      const p = msg.payload || {};
      const targetSid = typeof p.session_id === "string" && p.session_id ? p.session_id : sid;
      store.push_message(targetSid, {
        role: "ppt_outline" as any,
        ppt_outline_awaiting: true,
        outline_id: p.outline_id,
        topic: p.topic,
        outline_md: p.outline_md,
        sources_count: typeof p.sources_count === "number" ? p.sources_count : 0,
        no_research: !!p.no_research,
        history: Array.isArray(p.history) ? p.history : [],
      } as any);
      break;
    }
    case "ppt_outline_resolved": {
      const p = msg.payload || {};
      const outlineId = typeof p.outline_id === "string" ? p.outline_id : "";
      if (outlineId) {
        const targetSession = typeof p.session_id === "string" && p.session_id ? p.session_id : sid;
        const decisionStatus = typeof p.decision_status === "string" ? p.decision_status : undefined;
        const targets = new Set<string>([targetSession]);
        for (const [sessionId, session] of Object.entries(store.sessions)) {
          if (
            session.messages.some(
              (m) => m.role === "ppt_outline" && m.outline_id === outlineId,
            )
          ) {
            targets.add(sessionId);
          }
        }
        targets.forEach((targetSid) => store.resolve_ppt_outline(targetSid, outlineId, decisionStatus));
      }
      break;
    }
    case "chat_v2_plan_cancelled": {
      // superpowers 决策2: 用户点[取消] 或 后端超时 → 清按钮 + 回 idle
      store.resolve_plan(sid);
      const runId = String(msg.payload?.run_id || "").trim();
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          status: "cancelled",
          inflight: false,
        });
        selectNewestInflightRunAfterTerminal(sid, runId);
      } else {
        store.upsert(sid, {
          status: "idle",
          inflight: false,
          active_run_id: null,
        });
      }
      break;
    }
    case "chat_v2_interrupted": {
      // P4-S25 B3: backend cancelled in-flight task. Clear status so
      // the button reverts to "发送" and user can type again.
      const runId = String(msg.payload?.run_id || "").trim();
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          status: "cancelled",
          inflight: false,
        });
        selectNewestInflightRunAfterTerminal(sid, runId);
      } else {
        store.upsert(sid, {
          status: "idle",
          inflight: false,
          active_run_id: null,
        });
      }
      break;
    }
    case "subagent_progress": {
      // subagent-concurrency-driver WI-3.4 — 子代理并发实时进度
      // （queued→running→completed/failed）。喂独立 subagentStore，
      // 由 SubagentProgressPanel 渲染（不碰消息流）。
      const p = msg.payload || {};
      if (p && p.run_id) {
        const sub = useSubagentStore.getState();
        sub.upsert({
          run_id: String(p.run_id),
          task_id: String(p.task_id || ""),
          kind: String(p.kind || ""),
          status: String(p.status || "queued"),
          // 排队中被取消时后端发 reason="cancelled" → 前端区分「已取消」与「失败」。
          reason: typeof p.reason === "string" ? p.reason : undefined,
          summary: typeof p.summary === "string" ? p.summary : undefined,
          ts: typeof p.ts === "number" ? p.ts : Date.now(),
        });
        // WI-OC-2：每条进度事件附带调度器累计快照（peak/total_queued/
        // total_rejected）。旧后端不推这些 key → setMetrics 忽略 → 缺省 0 降级。
        sub.setMetrics({
          peak_concurrent:
            typeof p.peak_concurrent === "number" ? p.peak_concurrent : undefined,
          total_queued:
            typeof p.total_queued === "number" ? p.total_queued : undefined,
          total_rejected:
            typeof p.total_rejected === "number" ? p.total_rejected : undefined,
        });
      }
      break;
    }
    case "slash_command_result": {
      // FEAT-A2: /help /goal /prefs /skill 结果。此前 ws.ts 无此 case →
      // 所有 slash 结果被静默丢弃。现在格式化成一条 slash_result bubble。
      const p = msg.payload || {};
      const text = format_slash_result(p.result);
      store.push_message(sid, { role: "slash_result" as any, text });
      // slash 是同步请求-响应：结果到达即结束本轮。不清 inflight 会让
      // InputBar 永卡"思考中"（发送钮变停止钮，后续输入无法提交）。
      store.upsert(sid, { status: "idle", inflight: false });
      break;
    }
    case "tool_call": {
      const p = msg.payload || {};
      store.push_message(sid, {
        role: "tool_call",
        tool_name: p.name,
        tool_args: p.arguments,
        run_id: String(p.run_id || "") || undefined,
        task_scope_id: String(p.task_scope_id || "") || undefined,
        request_id: String(p.request_id || "") || undefined,
        invocation_id: String(p.invocation_id || "") || undefined,
        stream_epoch: String(p.stream_epoch || "") || undefined,
      });
      store.upsert(sid, { status: "running" });
      break;
    }
    case "tool_result": {
      const p = msg.payload || {};
      const workflowEventId = typeof p.workflow_event_id === "string"
        ? p.workflow_event_id
        : "";
      const messageId = workflowEventId
        ? `workflow-artifact:${workflowEventId}`
        : undefined;
      if (
        messageId &&
        store.sessions[sid]?.messages.some((message) => message.id === messageId)
      ) {
        break;
      }
      const resultRaw =
        Array.isArray(p.artifacts) && p.artifacts.length > 0
          ? JSON.stringify(p)
          : p.result;
      store.push_message(sid, {
        id: messageId,
        role: "tool_result",
        tool_name: p.tool,
        tool_ok: p.ok,
        tool_result: resultRaw,
        workflow_event_id: workflowEventId || undefined,
        run_id: String(p.run_id || "") || undefined,
        task_scope_id: String(p.task_scope_id || "") || undefined,
        request_id: String(p.request_id || "") || undefined,
        invocation_id: String(p.invocation_id || "") || undefined,
        stream_epoch: String(p.stream_epoch || "") || undefined,
      });
      break;
    }
    case "session_messages_error":
    case "sessions_list_error": {
      // Fail closed: retain the last known local view. Replacing it with an
      // empty response would make a projection outage look like data loss.
      store.upsert(sid, { status: "idle", inflight: false });
      break;
    }
    case "session_messages_response": {
      // F5 rehydration response. Backend returned the message list
      // for a given session_id; replace whatever's in the store so
      // we don't end up with duplicates after reconnect.
      //
      // P6 bugfix 2026-05-14 (history persistence): backend rows may now
      // include role='assistant' with tool_calls JSON (means agent called
      // a tool — UI renders as tool_call bubble) or role='tool' with
      // tool_call_id (the tool's reply — render as tool_result bubble).
      // Pre-fix the frontend只 expected user/assistant, so even after the
      // backend persists everything UI would still drop them.
      const target = msg.payload?.session_id || sid;
      const items: any[] = msg.payload?.messages ?? [];
      const restored: any[] = [];
      const workflowEvents: any[] = [];
      const companionEvents: CompanionEvent[] = [];
      for (const rawEvent of msg.payload?.companion_events ?? []) {
        const event = companionEventFrom(rawEvent);
        if (event) companionEvents.push(event);
      }
      // P6 bugfix 2026-05-14: build tool_call_id → tool_name map first pass
      // so the subsequent tool reply row can show the right tool name
      // (instead of "(unknown)"). Backend SessionDB schema doesn't store
      // tool_name on the tool row; only the prior assistant row knows it
      // via tool_calls[].function.name.
      const tcid_to_name = new Map<string, string>();
      for (const m of items) {
        const tcs = Array.isArray(m.tool_calls) ? m.tool_calls : null;
        if (!tcs) continue;
        for (const tc of tcs) {
          const id = tc?.id;
          const name = tc?.function?.name || tc?.name;
          if (id && name) tcid_to_name.set(id, name);
        }
      }
      for (const [itemIndex, m] of items.entries()) {
        const ts = m.ts || Date.now();
        const base_id = m.id || `history:${target}:${itemIndex}:${ts}`;
        const role = m.role || "assistant";
        const tool_calls = Array.isArray(m.tool_calls) ? m.tool_calls : null;
        const workflowEvent = m.workflow_event;
        let companionSource: unknown = m.companion_event;
        if (!companionSource && m.projection_kind === "companion_event") {
          try {
            companionSource = JSON.parse(String(m.text || ""));
          } catch {
            companionSource = null;
          }
        }
        const companionEvent = companionEventFrom(companionSource);
        if (companionEvent) {
          companionEvents.push(companionEvent);
          continue;
        }
        if (workflowEvent && typeof workflowEvent === "object") {
          const eventType = String(workflowEvent.event_type || "");
          if (
            eventType === "workflow.accepted" ||
            eventType === "workflow.progress" ||
            eventType === "workflow.decision" ||
            eventType === "workflow.final"
          ) {
            workflowEvents.push(workflowEvent);
            continue;
          }
          // final_assistant and unknown workflow projections retain their
          // ordinary persisted message row for backward compatibility.
        }
        if (
          m.projection_kind === "workflow_progress" &&
          String(m.workflow_event_id || "").startsWith("reasoning-summary:")
        ) {
          restored.push({
            id: base_id,
            role: "reasoning_summary",
            text: m.text || "",
            reasoning_summary_id:
              m.workflow_event_id || m.projection_event_id || base_id,
            reasoning_phase: "status",
            reasoning_status: "completed",
            run_id: m.run_id || undefined,
            task_scope_id: m.task_scope_id || undefined,
            ts,
          });
          continue;
        }

        // Durable workflow artifacts are persisted as assistant rows carrying
        // the public artifact_create envelope. The live path receives the
        // same envelope as a tool_result, but history hydration previously
        // treated it as ordinary assistant text and exposed raw JSON after a
        // restart. Restore the live shape so ArtifactCard rendering and the
        // workflow-event idempotency key stay identical across reconnects.
        if (role === "assistant" && !tool_calls) {
          let artifactEnvelope: any = null;
          try {
            const parsed = JSON.parse(String(m.text || ""));
            if (
              parsed &&
              typeof parsed === "object" &&
              parsed.tool === "artifact_create" &&
              Array.isArray(parsed.artifacts) &&
              parsed.artifacts.length > 0
            ) {
              const legacyFileArtifact =
                parsed.artifacts.length === 1 && parsed.artifacts[0]?.kind === "text"
                  ? persistedWorkflowFileArtifact(workflowEvent)
                  : null;
              artifactEnvelope = legacyFileArtifact
                ? { ...parsed, artifacts: [legacyFileArtifact] }
                : parsed;
            }
          } catch {
            artifactEnvelope = null;
          }
          if (artifactEnvelope) {
            restored.push({
              id: `workflow-artifact:${base_id}`,
              role: "tool_result",
              tool_name: "artifact_create",
              tool_ok: artifactEnvelope.ok !== false,
              tool_result: JSON.stringify(artifactEnvelope),
              workflow_event_id: base_id,
              run_id: m.run_id || undefined,
              task_scope_id: m.task_scope_id || undefined,
              ts,
            });
            continue;
          }
        }

        if (role === "tool") {
          // Tool reply row → tool_result bubble. Reverse-map tool name
          // via the previously-built tcid → name dictionary.
          const tcid: string | undefined = m.tool_call_id;
          let embeddedToolName: string | undefined;
          try {
            const parsed = JSON.parse(String(m.text || ""));
            embeddedToolName = typeof parsed?.tool === "string" ? parsed.tool : undefined;
          } catch {
            embeddedToolName = undefined;
          }
          restored.push({
            id: base_id,
            role: "tool_result",
            tool_name: (tcid && tcid_to_name.get(tcid)) || embeddedToolName,
            tool_ok: true,
            tool_result: m.text || "",
            run_id: m.run_id || undefined,
            task_scope_id: m.task_scope_id || undefined,
            ts,
          });
          continue;
        }
        if (role === "assistant" && tool_calls && tool_calls.length > 0) {
          // assistant turn that issued one or more tool_calls. Expand each
          // tc into a tool_call bubble; if the row ALSO has text content
          // (rare — content + tool_calls in same turn), prepend that too.
          if (m.text && m.text.length > 0) {
            restored.push({
              id: `${base_id}-pre`,
              role: "assistant",
              text: m.text,
              run_id: m.run_id || undefined,
              task_scope_id: m.task_scope_id || undefined,
              ts,
            });
          }
          tool_calls.forEach((tc: any, idx: number) => {
            let args: any = tc?.function?.arguments;
            if (typeof args === "string") {
              try { args = JSON.parse(args); } catch { /* keep raw */ }
            }
            restored.push({
              id: `${base_id}-tc${idx}`,
              role: "tool_call",
              tool_name: tc?.function?.name || tc?.name || "unknown",
              tool_args: typeof args === "object" && args !== null ? args : undefined,
              run_id: m.run_id || undefined,
              task_scope_id: m.task_scope_id || undefined,
              ts,
            });
          });
          continue;
        }
        // Plain user / assistant (text) / etc.
        restored.push({
          id: base_id,
          role,
          text: m.text,
          run_id: m.run_id || undefined,
          task_scope_id: m.task_scope_id || undefined,
          ts,
        });
      }
      store.merge_history_messages(target, restored, workflowEvents);
      companionEvents
        .sort((a, b) => a.seq - b.seq || a.event_id.localeCompare(b.event_id))
        .forEach((event) => store.reduce_companion_event(event));
      // FEAT-A4: rehydration 重建 awaiting plan card。后端在 payload.plan
      // 单独带回（不混进被白名单过滤的 messages），awaiting 时重建 plan
      // bubble + [执行]/[取消] 栏（对齐 chat_v2_plan handler 的字段）。
      const _plan = msg.payload?.plan;
      if (_plan && _plan.awaiting) {
        store.push_message(target, {
          role: "plan" as any,
          plan_rationale: _plan.rationale,
          plan_steps: _plan.steps,
          plan_target_directory: _plan.target_directory,
          plan_action_categories: Array.isArray(
            _plan.action_categories,
          )
            ? _plan.action_categories.map(String)
            : [],
          plan_auto_confirmed: !!_plan.auto_confirmed,
          plan_awaiting_confirm: true,
          plan_sid: target,
        } as any);
      }
      break;
    }
    case "permission_request": {
      const runId = String(msg.payload?.run_id || "").trim();
      let waitingForPermission = true;
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          task_scope_id: String(msg.payload?.task_scope_id || ""),
          status: "waiting",
          inflight: false,
        });
        waitingForPermission =
          useSessionsStore.getState().sessions[sid]?.run_projections?.[runId]
            ?.status === "waiting";
      }
      // Code panel doesn't host the popup (the pet window does), but
      // we mark the session so the tile shows a "🔒 waiting" pill.
      // A delayed permission event for an already terminal Run is stale and
      // must not put the whole session back into a waiting state.
      if (waitingForPermission) {
        store.upsert(sid, { status: "permission" });
      }
      break;
    }
    case "external_wait_request":
    case "project_directory_request": {
      const runId = String(msg.payload?.run_id || "").trim();
      if (runId) {
        store.upsert_run_projection(sid, runId, {
          task_scope_id: String(msg.payload?.task_scope_id || ""),
          status: "waiting",
          inflight: false,
        });
      }
      break;
    }
    case "auto_resume_started": {
      // P5-S2 Phase 5: backend's AutoResumeOrchestrator started a self-healing
      // attempt for this session. Bump auto_resume_attempts so AutoResumeBanner
      // shows "🔄 agent 自愈中... (尝试 N/2)".
      const p = msg.payload || {};
      const target = p.session_id || sid;
      const attempt = typeof p.attempt === "number" ? p.attempt : 1;
      store.ensure(target);
      store.upsert(target, {
        auto_resume_attempts: attempt,
        inflight: true,
        status: "running",
      });
      break;
    }
    case "auto_resume_succeeded": {
      // P5-S2 Phase 5: orchestrator landed a final response. Reset counter so
      // banner dismisses; the regular chat_v2_final (which fires alongside)
      // will handle status/inflight cleanup.
      const p = msg.payload || {};
      const target = p.session_id || sid;
      store.upsert(target, { auto_resume_attempts: 0 });
      break;
    }
    case "auto_resume_exhausted": {
      // P5-S2 Phase 5: orchestrator gave up after max_attempts. Reset counter,
      // surface a red error message so the user sees what failed (mirrors
      // chat_v2_error semantics — supervisor_alert popup may also fire from a
      // separate ws message; here we just guarantee an error bubble lands).
      const p = msg.payload || {};
      const target = p.session_id || sid;
      const final_error = String(p.final_error ?? "auto-resume exhausted");
      const attempts_n = typeof p.attempts === "number" ? p.attempts : 0;
      store.upsert(target, {
        auto_resume_attempts: 0,
        status: "error",
        inflight: false,
      });
      store.push_message(target, {
        role: "error",
        text: `自愈失败（${attempts_n} 次尝试）: ${final_error}`,
      });
      break;
    }
    case "supervisor_alert": {
      // P5-S3: supervisor flagged this session. The pet window owns
      // the bubble UI; the panel uses this to colour the tile border.
      const p = msg.payload || {};
      const target_sid = p.session_id || sid;
      store.ensure(target_sid);
      store.apply_supervisor_alert(target_sid, {
        alert_id: String(p.alert_id || ""),
        severity: (p.severity as "green" | "yellow" | "red") || "yellow",
        action: (p.action as "nudge" | "ask_user") || "nudge",
        diagnosis: String(p.diagnosis || ""),
        user_message: String(p.user_message || ""),
        suggested_buttons: Array.isArray(p.suggested_buttons)
          ? p.suggested_buttons.map((b: any) => String(b)).slice(0, 2)
          : [],
        received_at: Date.now(),
      });
      break;
    }
    case "supervisor_toggle_ack": {
      // Settings panel may listen separately; nothing to do here.
      break;
    }
    // P5-S2 Phase 5 — code session binding events
    case "providers_changed": {
      // Settings-side mutation broadcast refreshes the registry snapshot.
      // Session bindings are durable tombstones and must not be silently
      // rewritten when a provider disappears.
      const incoming = Array.isArray(msg.payload?.providers)
        ? msg.payload.providers
        : [];
      useProvidersStore.getState().set_providers(incoming);
      // Mirror into Phase 4 settings store so SettingsProviders re-renders.
      dispatchProviderEvent(msg);
      break;
    }
    case "settings_providers_list_response": {
      // Initial provider list (frontend asks on panel mount). Same shape as
      // the broadcast — populate the store without binding reconciliation
      // (no UI state to reconcile yet on first load).
      const incoming = Array.isArray(msg.payload?.providers)
        ? msg.payload.providers
        : [];
      useProvidersStore.getState().set_providers(incoming);
      // Mirror into Phase 4 settings store.
      dispatchProviderEvent(msg);
      break;
    }
    // P5-S2 Phase 4 — individual provider mutation events route to
    // SettingsProviders' internal store only (no per-session reconciliation
    // needed — the upstream `providers_changed` broadcast handles that).
    case "settings_providers_reordered":
    case "settings_providers_added":
    case "settings_providers_updated":
    case "settings_providers_removed":
    case "settings_providers_error": {
      dispatchProviderEvent(msg);
      break;
    }
    case "session_provider_binding":
    case "session_provider_set": {
      // Ack from backend for a session_set_provider request. Mirror the
      // authoritative provider_id + preferred_model back into the store.
      // session_provider_binding is also the cold-start/history hydration
      // path, so a persisted Kimi selection cannot visually fall back to the
      // provider default after a restart.
      const p = msg.payload || {};
      const target = p.session_id || sid;
      store.ensure(target);
      store.upsert(target, {
        provider_id: p.provider_id === undefined ? null : p.provider_id,
        preferred_model:
          p.preferred_model === undefined ? null : p.preferred_model,
        // Backend's set_provider path preserves + echoes model_params.
        ...(p.model_params !== undefined
          ? { model_params: p.model_params }
          : {}),
        provider_incarnation_id: p.provider_incarnation_id ?? null,
        provider_config_revision: p.provider_config_revision ?? null,
        binding_epoch: Number(p.binding_epoch ?? 0),
      });
      break;
    }
    case "models_list_response": {
      // Live model catalog + per-model capabilities.
      const p = msg.payload || {};
      useSessionModelsStore
        .getState()
        .set_catalog(
          Array.isArray(p.models) ? p.models : [],
          typeof p.source === "string" ? p.source : "none",
          typeof p.default_model === "string" ? p.default_model : "",
        );
      break;
    }
    case "model_context_set_ack": {
      // 上下文覆盖保存 ack(p4_ipc 完整版)。catalog 刷新由 modal 保存时
      // 紧随的 models_list 请求完成,这里只记失败。
      const p = msg.payload || {};
      if (!p.ok) {
        console.warn("[ws] model_context_set rejected:", p.reason);
      }
      break;
    }
    case "session_model_set": {
      // Ack from backend for session_set_model. Same merge as above —
      // backend echoes provider_id + preferred_model + model_params so we
      // keep all three in sync even if the user only changed the model.
      const p = msg.payload || {};
      const target = p.session_id || sid;
      store.ensure(target);
      store.upsert(target, {
        provider_id: p.provider_id === undefined ? null : p.provider_id,
        preferred_model:
          p.preferred_model === undefined ? null : p.preferred_model,
        // code-session-model-params S2: dict ⇒ active params; null ⇒
        // binding cleared. undefined (legacy backend) ⇒ leave untouched.
        ...(p.model_params !== undefined
          ? { model_params: p.model_params }
          : {}),
        provider_incarnation_id: p.provider_incarnation_id ?? null,
        provider_config_revision: p.provider_config_revision ?? null,
        binding_epoch: Number(p.binding_epoch ?? 0),
      });
      break;
    }
    default:
      // Unknown event types are fine — the pet shell may handle them.
      break;
  }
}

// Bug#2 修复 (2026-06-11)：断连时的出站消息排队(原实现直接丢弃 — 用户
// 消息静默消失)。重连 onopen 时 flush。上限防泄漏:超出丢最旧并告警。
const _outbox: string[] = [];
const _OUTBOX_MAX = 50;

export const controlWS: ControlWS = {
  send(msg) {
    const identified = withClientTurnIdentity(msg);
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
  async send_companion_action(commandKind, body) {
    const challenge = companionChallenge;
    if (
      !challenge ||
      companionActionInFlight ||
      current_state !== "connected" ||
      !ws ||
      ws.readyState !== WebSocket.OPEN
    ) {
      schedule_reconnect();
      return false;
    }
    companionActionInFlight = true;
    let credential: WindowControlCredential;
    try {
      credential = await getWindowControlCredential({
        ...challenge,
        commandKind,
        body,
        requestedScope: "companion_action",
      });
    } catch (error) {
      companionActionInFlight = false;
      console.warn("[companion-action] credential request rejected", error);
      return false;
    }
    try {
      ws.send(JSON.stringify({
        type: commandKind,
        payload: { body, credential },
      }));
      return true;
    } catch (error) {
      companionActionInFlight = false;
      console.warn("[companion-action] signed command send failed", error);
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
  state() {
    return current_state;
  },
};

// P5-S2 Phase 5: vitest hook. Production never imports this; tests use it to
// drive `dispatch` without spinning up a real WebSocket. Exported under a
// `__test_` prefix to make the intent obvious at call sites.
export const __test_dispatch = dispatch;
export function __test_reset_companion_identity_status() {
  latestCompanionIdentityStatus = null;
  G.__deskpet_companion_identity_status__ = null;
}

// Auto-connect on import. Caller doesn't need to do anything.
void open_socket();

// Vite HMR: when this module is hot-replaced, close the existing
// socket cleanly so the backend frees the slot before the new module
// tries to open another. Without this we leak a socket per HMR cycle
// and the backend keeps closing them with code 4002 "session
// replaced", which onclose interprets as needing a reconnect — hence
// the reconnect storm.
if ((import.meta as any).hot) {
  (import.meta as any).hot.dispose(() => {
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
