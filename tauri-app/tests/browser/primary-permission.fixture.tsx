// Browser-only layout fixture. No backend, Provider, or real authorization material.
import React, { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { WorkbenchShell } from "../../src/components/WorkbenchShell";
import "../../src/index.css";

const listeners = new Set<(value: unknown) => void>();
const states = new Set<(value: string) => void>();
const bound = { type: "companion_profile_bound", payload: { profile_id: "layout-owner", profile_generation: 1 } };
const run = { run_ref: "layout-run", generation: 1, state: "RUNNING", execution_session_ref: "layout-exec", sdk_run_ref: "layout-sdk" };
let connected = true, decision = 1, reads = 0;
const replies: Array<{ decision_id: unknown; decision: unknown }> = [];
const emit = (value: unknown) => listeners.forEach((fn) => fn(value));
const channel = {
  get state() { return connected ? "connected" : "disconnected"; },
  getLatestMessage: () => connected ? bound : null,
  onMessage(fn: (value: unknown) => void) { listeners.add(fn); return () => listeners.delete(fn); },
  onStateChange(fn: (value: string) => void) { states.add(fn); return () => states.delete(fn); },
  send(message: { request_id: string; operation: string; request: Record<string, unknown> }) {
    if (!connected) return false;
    const state = { primary_ref: "layout-primary", revision: "layout-revision", current_run: run, queued_count: 0, queued_count_truncated: false };
    const target = { primary_ref: state.primary_ref, run_ref: run.run_ref, generation: run.generation, sdk_run_ref: run.sdk_run_ref };
    let result: unknown = state;
    if (message.operation === "primary.messages.page") {
      result = { primary_ref: state.primary_ref, revision: state.revision, next_cursor: null, items: Array.from({ length: 20 }, (_, i) => ({
        message_ref: `layout-message-${i}`, role: i % 2 ? "assistant" : "user", text: `历史消息 ${i}：布局回归固定文本。`, has_more: false, total_chars: 20,
      })) };
    } else if (message.operation === "primary.decisions.list") {
      reads++;
      result = { ...target, sdk_state: "waiting", truncated: false, pending: [{
        decision_id: `layout-decision-${decision}`, request_id: `layout-decision-${decision}`, nonce: "synthetic-layout-only", version: 0,
        session_id: run.execution_session_ref, run_id: run.run_ref, sdk_run_id: run.sdk_run_ref,
        category: "write_file", summary: `操作 ${decision}：${decision === 1 ? "context_route" : "task_scope_update"}`,
        params: { tool_name: decision === 1 ? "context_route" : "task_scope_update", arguments_preview: "参数预览 ".repeat(250) },
        default_action: "prompt", dangerous: false,
      }] };
    } else if (message.operation === "primary.decisions.respond") {
      replies.push({ decision_id: message.request.decision_id, decision: message.request.decision });
      result = { ...target, decision_id: message.request.decision_id, version: 0, outcome: message.request.decision === "allow" ? "allowed" : "denied" };
      decision++;
    }
    queueMicrotask(() => emit({ type: "human_memory_response", request_id: message.request_id, payload: { ok: true, operation: message.operation, result } }));
    return true;
  },
};

function Fixture() {
  const [view, setView] = useState("chat");
  const [mount, setMount] = useState(0);
  Object.assign(window, { layoutFixture: {
    hide: () => setView("hidden-fixture-view"), show: () => setView("chat"),
    remount: () => setMount((n) => n + 1),
    refresh: () => emit({ type: "human_memory_changed", payload: {} }),
    disconnect: () => { connected = false; states.forEach((fn) => fn("disconnected")); },
    reconnect: () => { connected = true; states.forEach((fn) => fn("connected")); emit(bound); },
    stats: () => ({ replies, reads }),
  } });
  // Same outer containing block as App; all inner layout is production JSX.
  return <div style={{ position: "relative", width: "100vw", height: "100vh", overflow: "hidden" }}>
    <WorkbenchShell key={mount} view={view as React.ComponentProps<typeof WorkbenchShell>["view"]} onViewChange={setView}
      primaryChannel={channel as unknown as React.ComponentProps<typeof WorkbenchShell>["primaryChannel"]}
      chatProps={{} as React.ComponentProps<typeof WorkbenchShell>["chatProps"]}
      settingsProps={{} as React.ComponentProps<typeof WorkbenchShell>["settingsProps"]} skillsProps={{} as React.ComponentProps<typeof WorkbenchShell>["skillsProps"]} />
  </div>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);
