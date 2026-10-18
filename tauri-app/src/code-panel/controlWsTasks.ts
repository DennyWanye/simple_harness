// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ControlWireMessage, SlashCommandResult } from "./controlWsTypes";
import type { SessionsStore, WorkflowEventEnvelope } from "../stores/sessionTypes";
import {
  format_slash_result,
  publicToolErrorText,
  selectNewestInflightRunAfterTerminal,
} from "./controlWsShared";
import { useSessionsStore } from "../stores/sessionsStore";
import { useSubagentStore } from "./subagentStore";

/** 控制通道消息处理：任务投影、计划与大纲卡、工具调用与结果、授权与等待、自愈、监督。由 controlWs 的处理函数表按消息类型分发（9G 从 dispatch 的 switch 拆出，分支体原样）。 */
export function onTaskProjectionsResponse(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const projections = Array.isArray(msg.payload?.projections) ? msg.payload.projections : [];
  projections.forEach((projection) => {
    const runId = String(projection?.run_id || "").trim();
    if (!runId) return;
    const existing = useSessionsStore.getState().sessions[sid]?.run_projections?.[runId];
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
                : (existing?.status ?? "waiting");
    const inflight =
      durableStatus === "created" ||
      durableStatus === "queued" ||
      durableStatus === "running" ||
      durableStatus === "cancel_requested";
    store.upsert_run_projection(sid, runId, {
      projection_id: String(projection.projection_id || "") || undefined,
      task_scope_id: String(projection.task_scope_id || ""),
      version: Number(projection.version || 0),
      conversation_boundary_ref: String(projection.conversation_boundary_ref || "") || undefined,
      conversation_boundary_version:
        typeof projection.conversation_boundary_version === "number"
          ? projection.conversation_boundary_version
          : undefined,
      ui_state:
        projection.ui_state === "background" || projection.ui_state === "closed"
          ? projection.ui_state
          : "open",
      status,
      inflight: durableStatus ? inflight : (existing?.inflight ?? false),
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
}

export function onTaskProjectionUpdated(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const runId = String(p.run_id || "").trim();
  if (runId) {
    store.upsert_run_projection(sid, runId, {
      projection_id: String(p.projection_id || "") || undefined,
      task_scope_id: String(p.task_scope_id || ""),
      version: Number(p.version || 0),
      ui_state: p.ui_state === "background" || p.ui_state === "closed" ? p.ui_state : "open",
    });
  }
}

export function onSessionSwitched(
  msg: ControlWireMessage,
  store: SessionsStore,
  _sid: string,
): void {
  const p = msg.payload || {};
  const next_sid = typeof p.new_sid === "string" ? p.new_sid : "";
  if (next_sid) {
    store.ensure(next_sid);
    store.upsert(next_sid, {
      provider_id: p.provider_id === undefined ? null : p.provider_id,
      preferred_model: p.preferred_model === undefined ? null : p.preferred_model,
      model_params: p.model_params === undefined ? null : p.model_params,
    });
    store.set_active(next_sid);
  }
}

export function onWorkflowEvent(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
  const event = (msg.payload || {}) as WorkflowEventEnvelope;
  const eventId = String(event.event_id || "");
  const eventType = String(event.event_type || "");
  const body = event.payload || {};
  const nested = (body.payload || {}) as { text?: unknown };
  const text = String(nested.text || body.text || "").trim();
  const currentBefore = store.sessions[sid];
  const projectionRunId = String(event.root_run_id || event.task_root_run_id || "").trim();
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
    return;
  }
  const current = store.sessions[sid];
  if (eventId && current?.messages.some((message) => message.id === eventId)) return;
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
}

export function onChatV2Plan(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
  // Plan card from the planner phase. Render it as a
  // dedicated bubble in the chat stream so the user can see the
  // intended steps before / during execution.
  const p = msg.payload || {};
  store.push_message(sid, {
    role: "plan",
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
  });
}

export function onPptOutlineProposed(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const targetSid = typeof p.session_id === "string" && p.session_id ? p.session_id : sid;
  store.push_message(targetSid, {
    role: "ppt_outline",
    ppt_outline_awaiting: true,
    outline_id: p.outline_id,
    topic: p.topic,
    outline_md: p.outline_md,
    sources_count: typeof p.sources_count === "number" ? p.sources_count : 0,
    no_research: !!p.no_research,
    history: Array.isArray(p.history) ? p.history : [],
  });
}

export function onPptOutlineResolved(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const outlineId = typeof p.outline_id === "string" ? p.outline_id : "";
  if (outlineId) {
    const targetSession = typeof p.session_id === "string" && p.session_id ? p.session_id : sid;
    const decisionStatus = typeof p.decision_status === "string" ? p.decision_status : undefined;
    const targets = new Set<string>([targetSession]);
    for (const [sessionId, session] of Object.entries(store.sessions)) {
      if (session.messages.some((m) => m.role === "ppt_outline" && m.outline_id === outlineId)) {
        targets.add(sessionId);
      }
    }
    targets.forEach((targetSid) => store.resolve_ppt_outline(targetSid, outlineId, decisionStatus));
  }
}

export function onChatV2PlanCancelled(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
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
}

export function onSubagentProgress(
  msg: ControlWireMessage,
  _store: SessionsStore,
  _sid: string,
): void {
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
      peak_concurrent: typeof p.peak_concurrent === "number" ? p.peak_concurrent : undefined,
      total_queued: typeof p.total_queued === "number" ? p.total_queued : undefined,
      total_rejected: typeof p.total_rejected === "number" ? p.total_rejected : undefined,
    });
  }
}

export function onSlashCommandResult(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  // /help /goal /prefs /skill 结果：格式化成一条 slash_result bubble，不静默丢弃。
  const p = msg.payload || {};
  const text = format_slash_result(p.result as SlashCommandResult | undefined);
  store.push_message(sid, { role: "slash_result", text });
  // slash 是同步请求-响应：结果到达即结束本轮。不清 inflight 会让
  // InputBar 永卡"思考中"（发送钮变停止钮，后续输入无法提交）。
  store.upsert(sid, { status: "idle", inflight: false });
}

export function onToolCall(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
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
}

export function onToolResult(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
  const p = msg.payload || {};
  const workflowEventId = typeof p.workflow_event_id === "string" ? p.workflow_event_id : "";
  const messageId = workflowEventId ? `workflow-artifact:${workflowEventId}` : undefined;
  if (messageId && store.sessions[sid]?.messages.some((message) => message.id === messageId)) {
    return;
  }
  const baseResultRaw =
    Array.isArray(p.artifacts) && p.artifacts.length > 0
      ? JSON.stringify(p)
      : (p.result as string | undefined);
  const liveError =
    publicToolErrorText(p.error) ??
    (p.ok === false && typeof p.status === "string" ? p.status : undefined);
  const resultRaw =
    p.ok === false && liveError
      ? JSON.stringify({
          status: typeof p.status === "string" ? p.status : "failed",
          error: p.error ?? liveError,
          result: baseResultRaw,
        })
      : baseResultRaw;
  store.push_message(sid, {
    id: messageId,
    role: "tool_result",
    tool_name: p.tool,
    tool_ok: p.ok,
    tool_result: resultRaw,
    tool_error: liveError,
    workflow_event_id: workflowEventId || undefined,
    run_id: String(p.run_id || "") || undefined,
    task_scope_id: String(p.task_scope_id || "") || undefined,
    request_id: String(p.request_id || "") || undefined,
    invocation_id: String(p.invocation_id || "") || undefined,
    stream_epoch: String(p.stream_epoch || "") || undefined,
  });
}

export function onPermissionRequest(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const runId = String(msg.payload?.run_id || "").trim();
  let waitingForPermission = true;
  if (runId) {
    store.upsert_run_projection(sid, runId, {
      task_scope_id: String(msg.payload?.task_scope_id || ""),
      status: "waiting",
      inflight: false,
    });
    waitingForPermission =
      useSessionsStore.getState().sessions[sid]?.run_projections?.[runId]?.status === "waiting";
  }
  // Code panel doesn't host the popup (the pet window does), but
  // we mark the session so the tile shows a "🔒 waiting" pill.
  // A delayed permission event for an already terminal Run is stale and
  // must not put the whole session back into a waiting state.
  if (waitingForPermission) {
    store.upsert(sid, { status: "permission" });
  }
}

export function onExternalWaitRequest(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const runId = String(msg.payload?.run_id || "").trim();
  if (runId) {
    store.upsert_run_projection(sid, runId, {
      task_scope_id: String(msg.payload?.task_scope_id || ""),
      status: "waiting",
      inflight: false,
    });
  }
}

export function onSupervisorAlert(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  // supervisor flagged this session. The pet window owns
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
      ? p.suggested_buttons.map((b: unknown) => String(b)).slice(0, 2)
      : [],
    received_at: Date.now(),
  });
}

export function onSupervisorToggleAck(
  _msg: ControlWireMessage,
  _store: SessionsStore,
  _sid: string,
): void {}
