// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ControlWireMessage } from "./controlWsTypes";
import type { SessionsStore, Message } from "../stores/sessionTypes";
import type { ContextUsageSnapshot } from "../types/messages";
import { chatErrorMessage } from "../chatErrorMessage";
import { pendingRunStatusQueries, selectNewestInflightRunAfterTerminal } from "./controlWsShared";
import { useSessionsStore } from "../stores/sessionsStore";

/** 控制通道消息处理：对话流（增量、推理摘要、终态、出错、中断、运行状态回执）。由 controlWs 的处理函数表按消息类型分发（9G 从 dispatch 的 switch 拆出，分支体原样）。 */
export function onSessionTodoUpdate(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const items = Array.isArray(msg.payload?.items) ? msg.payload.items : [];
  store.upsert_todos(sid, items);
}

export function onChatV2ContinuationAccepted(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const runId = String(p.run_id || "").trim();
  const requestId = String(p.request_id || "").trim();
  if (requestId) {
    store.set_continuation_status(sid, requestId, p.queued === true ? "waiting" : "bound");
  }
  if (runId) {
    store.upsert_run_projection(sid, runId, {
      task_scope_id: String(p.task_scope_id || ""),
      conversation_boundary_ref: String(p.conversation_boundary_ref || "") || undefined,
      conversation_boundary_version:
        typeof p.conversation_boundary_version === "number"
          ? p.conversation_boundary_version
          : undefined,
    });
  }
}

export function onChatV2ContinuationStatus(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const requestId = String(p.request_id || "").trim();
  const continuationStatus = String(p.status || "").trim();
  if (requestId && (continuationStatus === "bound" || continuationStatus === "failed")) {
    store.set_continuation_status(
      sid,
      requestId,
      continuationStatus,
      String(p.error || "").trim() || undefined,
    );
  }
}

export function onChatResponse(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
  // Mid-loop assistant text (with tool calls). Render as assistant bubble.
  const p = msg.payload || {};
  const text = p.text;
  if (text) {
    store.push_message(sid, {
      role: "assistant",
      text,
      run_id: String(p.run_id || "").trim() || undefined,
      task_scope_id: String(p.task_scope_id || "").trim() || undefined,
    });
  }
}

export function onChatV2Delta(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
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
    return;
  }
  if (!chunk) return;
  if (p.provisional === true) {
    // Provisional provider output never enters ordinary Message history.
    // A malformed delta without the full triple is fail-closed.
    if (runId && invocationId && streamEpoch) {
      store.upsert_companion_provisional(runId, invocationId, streamEpoch, chunk);
    }
    return;
  }
  // Reasoning chunks are visually muted in the UI; stash them on a
  // separate role for now. (Could fold into one bubble in B2.)
  const role = kind === "reasoning" ? "reasoning_delta" : "assistant_delta";
  const taskScopeId = String(p.task_scope_id || "").trim();
  const cur = store.sessions[sid];
  const last = cur?.messages.findLast(
    (message) =>
      message.role === role &&
      (!runId || message.run_id === runId) &&
      (!invocationId || message.invocation_id === invocationId),
  );
  if (last && last.role === role) {
    // Append to existing partial bubble in-place.
    last.text = (last.text || "") + chunk;
    // Trigger zustand re-render via a no-op upsert.
    store.upsert(sid, { last_activity: Date.now() });
  } else {
    store.push_message(sid, {
      role,
      text: chunk,
      run_id: runId || undefined,
      task_scope_id: taskScopeId || undefined,
      invocation_id: invocationId || undefined,
      stream_epoch: streamEpoch || undefined,
    });
  }
}

export function onContextUsage(msg: ControlWireMessage, store: SessionsStore, _sid: string): void {
  // 2026-05-31 restore — Claude-Code-style ring gauge update.
  const p = msg.payload || {};
  // Authority frames without their own Session identity are unsafe:
  // active_sid may have changed while a reconnect response was in flight.
  store.upsert_context_usage(p as ContextUsageSnapshot);
}

export function onChatV2UserEcho(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
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
}

export function onChatV2ReasoningActivity(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
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
}

export function onChatV2ReasoningSummary(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const summaryId = String(p.summary_id || "").trim();
  const text = String(p.text || "").trim();
  if (!summaryId || !text) return;
  let current = store.sessions[sid];
  if (current) {
    const runId = String(p.run_id || "").trim();
    const runProjection = runId ? current.run_projections?.[runId] : undefined;
    const runIsKnownNonTerminal =
      runProjection !== undefined &&
      runProjection.status !== "completed" &&
      runProjection.status !== "failed" &&
      runProjection.status !== "cancelled";
    const lastUserIndex = current.messages.findLastIndex((message) => message.role === "user");
    const withoutLiveDuplicate = current.messages.filter(
      (message, index) =>
        !(
          index > lastUserIndex &&
          runIsKnownNonTerminal &&
          (message.role === "assistant_delta" || message.role === "assistant") &&
          String(message.text || "").trim() === text &&
          message.run_id === runId
        ),
    );
    if (withoutLiveDuplicate.length !== current.messages.length) {
      store.set_messages(sid, withoutLiveDuplicate);
      current = store.sessions[sid];
    }
  }
  const existingIndex =
    current?.messages.findIndex(
      (message) =>
        message.role === "reasoning_summary" && message.reasoning_summary_id === summaryId,
    ) ?? -1;
  const summary: Omit<Message, "id" | "ts"> = {
    role: "reasoning_summary" as const,
    text,
    reasoning_summary_id: summaryId,
    reasoning_phase: p.phase === "observation" || p.phase === "status" ? p.phase : "planning",
    reasoning_status: p.status === "failed" || p.status === "completed" ? p.status : "running",
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
}

export function onChatV2Final(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
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
          (m.role === "assistant_delta" || m.role === "reasoning_delta") &&
          (!runId || m.run_id === runId)
        ),
    );
    if (text) {
      const lastUserIdx = cleaned.findLastIndex(
        (m) => m.role === "user" && (!runId || m.run_id === runId),
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

    // 2026-08-17 Bug fix: 检查是否还有其他正在运行的任务
    // 如果没有，重置主会话状态为 idle
    const session = store.sessions[sid];
    if (session) {
      const hasOtherInflightRuns = Object.values(session.run_projections || {}).some(
        (proj) => proj.run_id !== runId && proj.inflight && proj.ui_state !== "closed",
      );
      if (!hasOtherInflightRuns) {
        store.upsert(sid, {
          status: "idle",
          inflight: false,
          active_run_id: null,
        });
      }
    }
  } else {
    store.upsert(sid, {
      status: "idle",
      inflight: false,
      active_run_id: null,
    });
  }
}

export function onChatV2Error(msg: ControlWireMessage, store: SessionsStore, sid: string): void {
  const p = msg.payload || {};
  // Empty-state creation failures have no durable Session to own an
  // error bubble. App.tsx renders the same frame in its global banner;
  // do not manufacture a store entry with the empty-string id.
  if (!sid) return;
  const runId = String(p.run_id || "").trim();
  const taskScopeId = String(p.task_scope_id || "").trim();
  const requestId = String(p.request_id || "").trim();
  store.clear_companion_provisional(runId);
  // 这一轮失败了：若它就是本会话正在等待的 root turn，必须解除等待。
  // pending_root_request_id 原先只在 chat_v2_run_reserved/started 里清，
  // 而 Run 预约之前就夭折的拒绝（companion_identity_not_ready、只读会话等）
  // 走不到那里 → 该字段永远挂着 → InputBar 的 shouldDefer 恒真 → 此后每条
  // 消息只 push 到本地流、根本不 send，永久停在「等待 Agent 读取…」。
  // 实测：r4 S18 删除当前会话后自动切到 default，default 正是被这样卡死的。
  // requestId 缺失时（老后端/未覆盖的拒绝分支）按会话级兜底清除——一轮 root
  // turn 失败后继续把后续消息挂起，没有任何可用语义。
  {
    const pending = store.sessions[sid]?.pending_root_request_id;
    if (pending && (!requestId || pending === requestId)) {
      store.upsert(sid, {
        pending_root_request_id: undefined,
        pending_root_turn_id: undefined,
      });
    }
  }
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
        (message) => !(message.provisional === true && (!runId || message.run_id === runId)),
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
  const txt = chatErrorMessage(p);
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
  // Run 预约前就夭折时没有 runId，上面那条按 run_id 收尾 deferred 消息的
  // 分支走不到（continuationError 分支同样走不到）。这些消息挂的是"等 root
  // run 出生"，而 root turn 已经失败，不落到 failed 就会永久停在
  // 「等待 Agent 读取…」。放在 if/else 之外，两条路径都覆盖。
  if (!runId) {
    const latest = useSessionsStore.getState().sessions[sid];
    const stuck = latest?.messages.some(
      (message) => message.deferred_send || message.continuation_status === "waiting",
    );
    if (latest && stuck) {
      store.set_messages(
        sid,
        latest.messages.map((message) =>
          message.deferred_send || message.continuation_status === "waiting"
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
  }
}

export function onChatV2Interrupted(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const runId = String(p.run_id || p.root_run_id || "").trim();
  const state = String(p.state || "").toLowerCase();
  const version = Number(p.run_version || 0);
  const terminal = ["completed", "failed", "cancelled"].includes(state);
  const current = runId ? store.sessions[sid]?.run_projections?.[runId] : undefined;
  if (runId && terminal && version >= (current?.version ?? 0)) {
    store.upsert_run_projection(sid, runId, {
      version,
      status: state as "completed" | "failed" | "cancelled",
      inflight: false,
    });
    selectNewestInflightRunAfterTerminal(sid, runId);
  } else if (
    runId &&
    version >= (current?.version ?? 0) &&
    (state === "running" || state === "waiting")
  ) {
    store.upsert_run_projection(sid, runId, {
      version,
      status: state,
      inflight: state === "running",
    });
    if (state === "waiting") {
      store.upsert(sid, { active_run_id: runId });
    }
  }
}

export function onSessionRunStatusAck(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  const p = msg.payload || {};
  const queryId = String(p.query_id || "");
  const pending = pendingRunStatusQueries.get(queryId);
  if (!pending) return;
  const ackSid = String(p.session_id || sid);
  const ackRunId = String(p.root_run_id || p.run_id || "").trim();
  if (ackSid !== pending.sid || ackRunId !== pending.runId) return;
  clearTimeout(pending.timer);
  pendingRunStatusQueries.delete(queryId);
  const runId = String(p.root_run_id || p.run_id || pending?.runId || "").trim();
  if (!runId) return;
  const current = store.sessions[sid]?.run_projections?.[runId];
  const version = Number(p.run_version || 0);
  if (version < (current?.version ?? 0)) return;
  const state = String(p.state || "").toLowerCase();
  if (["completed", "failed", "cancelled"].includes(state)) {
    store.upsert_run_projection(sid, runId, {
      version,
      status: state as "completed" | "failed" | "cancelled",
      inflight: false,
    });
    selectNewestInflightRunAfterTerminal(sid, runId);
  } else if (state === "waiting") {
    // WAITING is durable and actionable. Keep the active Run identity;
    // the UI may offer resume/cancel without pretending the Run is idle.
    store.upsert_run_projection(sid, runId, {
      version,
      status: "waiting",
      inflight: false,
    });
    store.upsert(sid, {
      status: "idle",
      inflight: false,
      active_run_id: runId,
    });
  } else if (["created", "queued", "running", "cancel_requested"].includes(state)) {
    store.upsert_run_projection(sid, runId, {
      version,
      status: "running",
      inflight: true,
    });
    store.upsert(sid, {
      status: "running",
      inflight: true,
      active_run_id: runId,
    });
  }
}
