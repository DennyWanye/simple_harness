// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ControlWireMessage, HistoryRow } from "./controlWsTypes";
import type { SessionsStore, Message, WorkflowEventEnvelope } from "../stores/sessionTypes";
import type { CompanionEvent } from "../types/messages";
import {
  companionEventFrom,
  persistedToolOutcome,
  persistedWorkflowFileArtifact,
} from "./controlWsShared";

/** 控制通道消息处理：会话历史恢复。由 controlWs 的处理函数表按消息类型分发（9G 从 dispatch 的 switch 拆出，分支体原样）。 */
export function onSessionMessagesError(
  _msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  // Fail closed: retain the last known local view. Replacing it with an
  // empty response would make a projection outage look like data loss.
  store.upsert(sid, { status: "idle", inflight: false });
}

export function onSessionMessagesResponse(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
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
  const items: HistoryRow[] = msg.payload?.messages ?? [];
  const restored: Message[] = [];
  const workflowEvents: WorkflowEventEnvelope[] = [];
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
        reasoning_summary_id: m.workflow_event_id || m.projection_event_id || base_id,
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
      let artifactEnvelope: Record<string, unknown> | null = null;
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
      const durableOutcome = persistedToolOutcome(m.text);
      restored.push({
        id: base_id,
        role: "tool_result",
        tool_name: (tcid && tcid_to_name.get(tcid)) || durableOutcome.toolName,
        tool_ok: durableOutcome.ok,
        tool_result: m.text || "",
        tool_error: durableOutcome.error,
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
      tool_calls.forEach((tc, idx) => {
        let args: unknown = tc?.function?.arguments;
        if (typeof args === "string") {
          try {
            args = JSON.parse(args);
          } catch {
            /* keep raw */
          }
        }
        restored.push({
          id: `${base_id}-tc${idx}`,
          role: "tool_call",
          tool_name: tc?.function?.name || tc?.name || "unknown",
          tool_args:
            typeof args === "object" && args !== null
              ? (args as Record<string, unknown>)
              : undefined,
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
  // rehydration 重建 awaiting plan card。后端在 payload.plan
  // 单独带回（不混进被白名单过滤的 messages），awaiting 时重建 plan
  // bubble + [执行]/[取消] 栏（对齐 chat_v2_plan handler 的字段）。
  const _plan = msg.payload?.plan;
  if (_plan && _plan.awaiting) {
    store.push_message(target, {
      role: "plan",
      plan_rationale: _plan.rationale,
      plan_steps: _plan.steps,
      plan_target_directory: _plan.target_directory,
      plan_action_categories: Array.isArray(_plan.action_categories)
        ? _plan.action_categories.map(String)
        : [],
      plan_auto_confirmed: !!_plan.auto_confirmed,
      plan_awaiting_confirm: true,
      plan_sid: target,
    });
  }
}
