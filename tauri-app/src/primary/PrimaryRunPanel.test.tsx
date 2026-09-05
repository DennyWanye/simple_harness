import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryRunPanel } from "./PrimaryRunPanel";
const mock = vi.hoisted(() => ({ listeners: new Set<(value: unknown) => void>(), send: vi.fn<(message: unknown) => boolean>(() => true) }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: {
  state: () => "connected", on_state_change: () => () => {}, send_command: mock.send,
  on_message: (fn: (value: unknown) => void) => { mock.listeners.add(fn); return () => { mock.listeners.delete(fn); }; },
} }));
afterEach(() => { cleanup(); mock.listeners.clear(); mock.send.mockClear(); });
describe("primary project permission UI", () => {
  it("shows hidden execution permission and returns its exact nonce/version without fake primary sid", () => {
    const stop = vi.fn();
    render(<PrimaryRunPanel run={{ run_ref: "foreground", generation: 1, state: "CLAIMED", execution_session_ref: "exec-hidden", sdk_run_ref: "sdk-hidden" }} onStop={stop} />);
    const payload = { request_id: "request", decision_id: "decision", nonce: "nonce", version: 7,
      session_id: "exec-hidden", run_id: "sdk-hidden", category: "write_file", summary: "写入任务 README", params: {}, default_action: "prompt", dangerous: false };
    act(() => mock.listeners.forEach((fn) => fn({ type: "permission_request", payload })));
    expect(screen.getByText("写入任务 README")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "允许一次" }));
    expect(mock.send).toHaveBeenLastCalledWith(expect.objectContaining({ type: "permission_response", payload: {
      request_id: "request", decision_id: "decision", nonce: "nonce", version: 7, session_id: "exec-hidden", run_id: "sdk-hidden", decision: "allow",
    } }));
    expect(screen.getByRole("button", { name: "提交中…" })).toBeTruthy();
    act(() => mock.listeners.forEach((fn) => fn({ type: "permission_response_applied", payload: { decision_id: "decision", ok: true } })));
    expect(screen.queryByText("写入任务 README")).toBeNull();
    expect(mock.send.mock.calls.some(([m]) => String((m as { type?: string })?.type).startsWith("chat_v2"))).toBe(false);
  });
});
