import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrimaryChatView } from "./PrimaryChatView";
import type { ControlChannel } from "../ws/ControlChannel";

vi.mock("../code-panel/controlWs", () => ({ controlWS: {
  state: () => "connected", on_message: () => () => {}, send_command: () => true,
} }));
afterEach(cleanup);

// Keep the real View, RunPanel, channel, permission hook and request correlator.
// Only the authenticated transport and its persisted server snapshots are simulated.
function fixture() {
  const listeners = new Set<(message: unknown) => void>();
  const states = new Set<(state: string) => void>();
  const sent: Array<Record<string, unknown>> = [];
  const run = { run_ref: "host-run", generation: 4, state: "RUNNING",
    execution_session_ref: "execution-session", sdk_run_ref: "sdk-run" };
  const item = { request_id: "d1", decision_id: "d1", nonce: "test-nonce", version: 0,
    session_id: run.execution_session_ref, run_id: run.run_ref, sdk_run_id: run.sdk_run_ref,
    category: "write_file", summary: "本次测试写入", params: {}, default_action: "prompt", dangerous: false };
  let pending: Array<typeof item> = [item];
  let sdkState = "waiting";
  let wrongRun = false;
  const bound = { type: "companion_profile_bound", payload: { profile_id: "owner", profile_generation: 1 } };
  const emit = (message: unknown) => listeners.forEach((fn) => fn(message));
  const channel = {
    state: "connected", getLatestMessage: () => bound,
    onMessage: (fn: (message: unknown) => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    onStateChange: (fn: (state: string) => void) => { states.add(fn); return () => { states.delete(fn); }; },
    send: (request: Record<string, unknown>) => {
      sent.push(request);
      const result = request.operation === "primary.decisions.list"
        ? { primary_ref: "primary", run_ref: wrongRun ? "other" : run.run_ref,
          generation: run.generation, sdk_run_ref: run.sdk_run_ref, sdk_state: sdkState, pending, truncated: false }
        : request.operation === "primary.messages.page"
          ? { primary_ref: "primary", revision: "r1", items: [], next_cursor: null }
          : { primary_ref: "primary", revision: "r1", current_run: run, queued_count: 0, queued_count_truncated: false };
      queueMicrotask(() => emit({ type: "human_memory_response", request_id: request.request_id,
        payload: { ok: true, operation: request.operation, result } }));
      return true;
    },
  };
  return { channel: channel as unknown as ControlChannel, sent, run, emit,
    settle: () => { pending = []; sdkState = "running"; },
    emptyWait: () => { pending = []; sdkState = "waiting"; },
    wrong: () => { wrongRun = true; },
    disconnect: () => { channel.state = "disconnected"; states.forEach((fn) => fn("disconnected")); },
  };
}

it("manual refresh recovers the same Run's settled decision without approving or replaying it", async () => {
  const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("等待授权");
  expect(screen.getByRole("dialog")).toBeTruthy();
  h.settle();
  await waitFor(() => expect(screen.getByRole("button", { name: "刷新状态" }).hasAttribute("disabled")).toBe(false));
  const reads = h.sent.filter((r) => r.operation === "primary.decisions.list").length;
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByText("等待授权")).toBeNull();
  expect(h.sent.filter((r) => r.operation === "primary.decisions.list")).toHaveLength(reads + 1);
  expect(h.sent.filter((r) => r.operation === "primary.decisions.list").at(-1)?.request).toEqual({
    primary_ref: "primary", expected_run_ref: h.run.run_ref, expected_generation: 4,
  });
  expect(h.sent.some((r) => r.operation === "primary.decisions.respond")).toBe(false);
});

it("exact tool progress rereads decisions; an empty waiting snapshot and disconnect cannot retain stale authorization text", async () => {
  const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("等待授权");
  h.emptyWait();
  await act(async () => h.emit({ type: "tool_result", payload: {
    session_id: h.run.execution_session_ref, run_id: h.run.sdk_run_ref, call_id: "c1", tool: "tool_search", ok: true,
  } }));
  await screen.findByText("运行等待中，暂无可操作的授权请求。");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("等待授权")).toBeNull();
  await act(async () => h.disconnect());
  expect(screen.queryByText("等待授权")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(h.sent.some((r) => r.operation === "primary.decisions.respond")).toBe(false);
});

it("manual refresh does not accept another Run's settled snapshot", async () => {
  const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("等待授权");
  h.settle(); h.wrong();
  await waitFor(() => expect(screen.getByRole("button", { name: "刷新状态" }).hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
  await screen.findByText("授权状态读取未确认，请刷新状态重试。");
  // refreshLatest retracts the parent's read projection while reloading.
  // A rejected replacement must expose uncertainty, never an allowed state.
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("已允许本次操作")).toBeNull();
  expect(h.sent.some((r) => r.operation === "primary.decisions.respond")).toBe(false);
});
