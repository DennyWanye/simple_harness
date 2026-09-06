import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { PrimaryWorkspaceBindings } from "./PrimaryWorkspaceBindings";
import type { PrimaryPort } from "./controller";
import type { PrimaryWireRequest } from "./requests";

afterEach(() => { cleanup(); vi.useRealTimers(); });
const item = { primary_ref: "p", run_ref: "host-run", sdk_run_ref: "sdk-run", generation: 1,
  effect_ref: "effect", challenge_ref: "challenge", challenge_hash: "a".repeat(64), scope_ref: "new-scope",
  proposal_hash: "b".repeat(64), root_path: "/actual/original-root", root_identity_hash: "c".repeat(64),
  state: "pending", can_decide: true, expires_at_millis: 1900000000000, binding_receipt_ref: null };
function fixture() {
  const messages = new Set<(message: unknown) => void>();
  const states = new Set<(state: "connected" | "disconnected" | "connecting") => void>();
  let state: "connected" | "disconnected" = "connected";
  const send = vi.fn((_message: PrimaryWireRequest) => state === "connected");
  const port: PrimaryPort = { state: () => state, send_command: send,
    on_message: f => { messages.add(f); return () => messages.delete(f); },
    on_state_change: f => { states.add(f); return () => states.delete(f); } };
  const response = (index: number, result: Record<string, unknown>) => {
    const wire = send.mock.calls[index][0] as { request_id: string; operation: string };
    messages.forEach(f => f({ type: "human_memory_response", request_id: wire.request_id,
      payload: { ok: true, operation: wire.operation, result } }));
  };
  return { port, send, response, disconnect() { state = "disconnected"; states.forEach(f => f(state)); },
    reconnect() { state = "connected"; states.forEach(f => f(state)); } };
}

describe("Manual binding UI uses durable Host source", () => {
  it("shows the actual root, submits one exact decision, and requires a bound ACK", async () => {
    const h = fixture();
    render(<PrimaryWorkspaceBindings port={h.port} primaryRef="p" ownerKey="owner" ready />);
    await waitFor(() => expect(h.send).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "允许本次绑定" })).toBeNull();
    await act(async () => h.response(0, { primary_ref: "p", items: [item], truncated: false, next_cursor: null }));
    expect(screen.getByText("目录：/actual/original-root")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "允许本次绑定" }));
    expect(h.send).toHaveBeenCalledTimes(2);
    const request = h.send.mock.calls[1][0] as { operation: string; request: Record<string, unknown> };
    expect(request.operation).toBe("primary.bindings.decide");
    expect(request.request).toEqual({ primary_ref: "p", run_ref: "host-run", sdk_run_ref: "sdk-run", generation: 1,
      effect_ref: "effect", challenge_ref: "challenge", challenge_hash: "a".repeat(64), scope_ref: "new-scope",
      proposal_hash: "b".repeat(64), decision: "allow" });
    await act(async () => h.response(1, { ...request.request, state: "bound", binding_receipt_ref: "actual-ack" }));
    expect(screen.getByText("新任务已绑定此目录")).toBeTruthy();
    expect(h.send.mock.calls.filter(([v]) => v.operation === "primary.bindings.decide")).toHaveLength(1);
  });

  it("reconnect reads pending again; allow_recorded requires an explicit same-decision retry", async () => {
    const h = fixture();
    render(<PrimaryWorkspaceBindings port={h.port} primaryRef="p" ownerKey="owner" ready />);
    await waitFor(() => expect(h.send).toHaveBeenCalledTimes(1));
    await act(async () => h.disconnect());
    await act(async () => h.response(0, { primary_ref: "p", items: [item], truncated: false, next_cursor: null }));
    expect(screen.queryByText("目录：/actual/original-root")).toBeNull();
    await act(async () => h.reconnect());
    await waitFor(() => expect(h.send).toHaveBeenCalledTimes(2));
    await act(async () => h.response(1, { primary_ref: "p", items: [{ ...item, state: "allow_recorded" }], truncated: false, next_cursor: null }));
    expect(screen.getByText("允许已记录，目录绑定尚未完成")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "拒绝" })).toBeNull();
    expect(h.send.mock.calls.filter(([v]) => v.operation === "primary.bindings.decide")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "重试完成已允许的绑定" }));
    expect(h.send.mock.calls.filter(([v]) => v.operation === "primary.bindings.decide")).toHaveLength(1);
  });
});

it("pages by the original cursor rather than treating 32 items as a lifetime limit", async () => {
  const h = fixture();
  render(<PrimaryWorkspaceBindings port={h.port} primaryRef="p" ownerKey="owner" ready />);
  await waitFor(() => expect(h.send).toHaveBeenCalledTimes(1));
  await act(async () => h.response(0, { primary_ref: "p", items: [item], truncated: false, next_cursor: "original-invocation" }));
  fireEvent.click(screen.getByRole("button", { name: "读取下一页目录授权" }));
  expect(h.send.mock.calls[1][0].request).toEqual({ primary_ref: "p", cursor: "original-invocation" });
  await act(async () => h.response(1, { primary_ref: "p", items: [{ ...item, challenge_ref: "older-challenge", scope_ref: "older-scope" }], truncated: false, next_cursor: null }));
  expect(screen.getByText("新任务：older-scope")).toBeTruthy();
  expect(screen.queryByText("新任务：new-scope")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "返回最新目录授权" }));
  expect(h.send.mock.calls[2][0].request).toEqual({ primary_ref: "p" });
});

it("ignores a decision reply after rebind and requires exact status even when pending is empty", async () => {
  const h = fixture();
  render(<PrimaryWorkspaceBindings port={h.port} primaryRef="p" ownerKey="owner" ready />);
  await waitFor(() => expect(h.send).toHaveBeenCalledTimes(1));
  await act(async () => h.response(0, { primary_ref: "p", items: [item], truncated: false, next_cursor: null }));
  fireEvent.click(screen.getByRole("button", { name: "允许本次绑定" }));
  await act(async () => h.disconnect());
  await act(async () => h.response(1, { ...item, state: "bound", binding_receipt_ref: "late-ack" }));
  expect(screen.queryByText("新任务已绑定此目录")).toBeNull();
  await act(async () => h.reconnect());
  await waitFor(() => expect(h.send).toHaveBeenCalledTimes(3));
  await act(async () => h.response(2, { primary_ref: "p", items: [], truncated: false, next_cursor: null }));
  expect(screen.queryByText("新任务已绑定此目录")).toBeNull();
  expect(h.send.mock.calls[3][0].operation).toBe("primary.bindings.status");
  expect(h.send.mock.calls[3][0].request).toEqual({ primary_ref: "p", challenge_ref: "challenge" });
  await act(async () => h.response(3, { primary_ref: "p", items: [{ ...item, state: "bound", can_decide: false, binding_receipt_ref: "durable-ack" }], truncated: false, next_cursor: null }));
  expect(screen.getByText("新任务已绑定此目录")).toBeTruthy();
  expect(h.send.mock.calls.filter(([v]) => v.operation === "primary.bindings.decide")).toHaveLength(1);
});
