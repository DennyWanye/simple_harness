import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryRunPanel } from "./PrimaryRunPanel";
import type { PrimaryPort } from "./controller";
const mock = vi.hoisted(() => ({ listeners: new Set<(value: unknown) => void>() }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: {
  state: () => "connected", on_state_change: () => () => {}, send_command: () => true,
  on_message: (fn: (value: unknown) => void) => { mock.listeners.add(fn); return () => { mock.listeners.delete(fn); }; },
} }));
afterEach(() => { cleanup(); mock.listeners.clear(); });
function fixture() {
  const listeners = new Set<(value: unknown) => void>();
  const states = new Set<(value: "connected" | "disconnected") => void>();
  const send = vi.fn<(message: unknown) => boolean>(() => true);
  const port: PrimaryPort = { state: () => "connected", send_command: send,
    on_state_change: (fn) => { states.add(fn); return () => { states.delete(fn); }; },
    on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; } };
  const run = { run_ref: "foreground", generation: 1, state: "RUNNING", execution_session_ref: "exec-hidden", sdk_run_ref: "sdk-hidden" };
  const item = { request_id: "decision", decision_id: "decision", nonce: "test-wire", version: 0,
    session_id: "exec-hidden", run_id: "foreground", sdk_run_id: "sdk-hidden", category: "write_file", summary: "写入任务 README", params: {}, default_action: "prompt", dangerous: false };
  const respond = async (result: object) => {
    const sent = send.mock.calls.at(-1)![0] as { request_id: string; operation: string };
    await act(async () => { listeners.forEach((fn) => fn({ type: "human_memory_response", request_id: sent.request_id,
      payload: { ok: true, operation: sent.operation, result: { primary_ref: "primary", run_ref: run.run_ref,
        generation: run.generation, sdk_run_ref: run.sdk_run_ref, ...result } } })); });
  };
  return { port, run, item, send, respond, states };
}
describe("Primary production decision wire UI", () => {
  it("cold mount reads an earlier decision; only click sends exact approval on the bound socket", async () => {
    const h = fixture();
    render(<PrimaryRunPanel primaryRef="primary" port={h.port} run={h.run} onStop={vi.fn()} />);
    act(() => mock.listeners.forEach((f) => f({ type: "permission_request", payload: h.item })));
    expect(screen.queryByText("写入任务 README")).toBeNull();
    await h.respond({ pending: [h.item], truncated: false, sdk_state: "waiting" });
    expect(screen.getByText("写入任务 README")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "本会话始终允许" })).toBeNull();
    expect(h.send).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "允许一次" }));
    expect(h.send).toHaveBeenLastCalledWith(expect.objectContaining({ operation: "primary.decisions.respond",
      request: { primary_ref: "primary", expected_run_ref: "foreground", expected_generation: 1,
        decision_id: "decision", nonce: h.item.nonce, version: 0, decision: "allow" } }));
    await h.respond({ decision_id: "decision", version: 0, outcome: "allowed" });
    expect(screen.queryByText("写入任务 README")).toBeNull();
    await h.respond({ pending: [{ ...h.item, request_id: "next", decision_id: "next", summary: "第二次操作" }], truncated: false, sdk_state: "waiting" });
    expect(screen.getByText("第二次操作")).toBeTruthy();
  });
  it("same-Run remount and reconnect recover cards from fresh exact reads", async () => {
    const h = fixture();
    const props = { primaryRef: "primary", port: h.port, run: h.run, onStop: vi.fn() };
    const first = render(<PrimaryRunPanel {...props} />);
    await h.respond({ pending: [h.item], truncated: false }); first.unmount();
    render(<PrimaryRunPanel {...props} />);
    await h.respond({ pending: [h.item], truncated: false });
    expect(screen.getByText("写入任务 README")).toBeTruthy();
    act(() => h.states.forEach((f) => f("disconnected")));
    expect(screen.queryByText("写入任务 README")).toBeNull();
    act(() => h.states.forEach((f) => f("connected")));
    await h.respond({ pending: [h.item], truncated: false });
    expect(screen.getByText("写入任务 README")).toBeTruthy();
  });
  it("StrictMode effect remount preserves the actual subscription and query", async () => {
    const h = fixture();
    render(<StrictMode><PrimaryRunPanel primaryRef="primary" port={h.port} run={h.run} onStop={vi.fn()} /></StrictMode>);
    await h.respond({ pending: [h.item], truncated: false });
    expect(screen.getByText("写入任务 README")).toBeTruthy();
  });
});
