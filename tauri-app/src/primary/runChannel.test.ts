import { afterEach, describe, expect, it, vi } from "vitest";
import { primaryRunChannel } from "./runChannel";
import type { PrimaryPort } from "./controller";
const run = { primary_ref: "primary", generation: 1, run_ref: "foreground-A", execution_session_ref: "hidden-exec-A", sdk_run_ref: "sdk-A" };
const item = { request_id: "d", decision_id: "d", nonce: "test-wire", version: 0, sdk_run_id: "sdk-A", session_id: "hidden-exec-A", run_id: "foreground-A" };
const cleanups: Array<() => void> = [];
afterEach(() => { cleanups.splice(0).forEach((f) => f()); vi.useRealTimers(); });
const tick = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
function fixture() {
  const listeners = new Set<(m: unknown) => void>();
  const states = new Set<(s: "connected" | "disconnected") => void>();
  const send = vi.fn<(message: unknown) => boolean>(() => true);
  const port: PrimaryPort = { state: () => "connected", send_command: send,
    on_state_change: (fn) => { states.add(fn); return () => { states.delete(fn); }; },
    on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; } };
  const channel = primaryRunChannel(port, run); channel.start();
  cleanups.push(() => channel.dispose());
  const received = vi.fn(); channel.on_message(received);
  const emit = (m: unknown) => listeners.forEach((fn) => fn(m));
  const response = (index: number, result: object) => {
    const request = send.mock.calls[index][0] as unknown as { request_id: string; operation: string };
    emit({ type: "human_memory_response", request_id: request.request_id,
      payload: { ok: true, operation: request.operation, result: { primary_ref: run.primary_ref, run_ref: run.run_ref,
        sdk_run_ref: run.sdk_run_ref, generation: run.generation, ...result } } });
  };
  return { channel, send, emit, response, received, states };
}
describe("authenticated Primary decisions", () => {
  it("ignores live and legacy approval frames, reads a pre-existing decision with exact Host binding", async () => {
    const h = fixture();
    h.emit({ type: "permission_request", payload: item });
    h.emit({ type: "permission_response_applied", payload: { ok: true, decision_id: "d" } });
    expect(h.received).not.toHaveBeenCalled();
    h.channel.send({ type: "permissions_pending_list" });
    expect(h.send).toHaveBeenLastCalledWith(expect.objectContaining({ type: "human_memory_request", operation: "primary.decisions.list",
      request: { primary_ref: "primary", expected_run_ref: "foreground-A", expected_generation: 1 } }));
    h.response(0, { pending: [item], truncated: false, sdk_state: "waiting" }); await tick();
    expect(h.received).toHaveBeenCalledWith({ type: "permissions_pending_list_response", payload: { pending: [item] } });
  });
  it("rejects cross-run snapshot and mismatched ACK without pretending approval", async () => {
    const h = fixture();
    h.channel.send({ type: "permissions_pending_list" });
    h.response(0, { pending: [{ ...item, run_id: "other" }], truncated: false }); await tick();
    expect(h.received.mock.calls.some(([m]) => m.type === "permissions_pending_list_response")).toBe(false);
    h.channel.send({ type: "permission_response", payload: { ...item, decision: "allow" } });
    h.response(1, { decision_id: "d", version: 0, outcome: "allowed", generation: 99 }); await tick();
    expect(h.received).toHaveBeenLastCalledWith(expect.objectContaining({ type: "permission_response_applied", payload: expect.objectContaining({ ok: false }) }));
  });
  it("preserves exact in-memory response, distinguishes expired and refuses allow_session", async () => {
    const h = fixture();
    expect(h.channel.send({ type: "permission_response", payload: { ...item, decision: "allow_session" } })).toBe(false);
    h.channel.send({ type: "permission_response", payload: { ...item, decision: "allow" } });
    expect(h.send).toHaveBeenLastCalledWith(expect.objectContaining({ operation: "primary.decisions.respond",
      request: expect.objectContaining({ decision_id: "d", nonce: item.nonce, version: 0, decision: "allow" }) }));
    h.response(0, { decision_id: "d", version: 0, outcome: "expired" }); await tick();
    expect(h.received).toHaveBeenCalledWith({ type: "primary_decision_status", payload: { outcome: "expired", error: "" } });
    expect(h.send.mock.calls).toHaveLength(2); // one bounded state refresh
  });
  it("coalesces notifications, clears disconnect cards and can remount without listener leaks", async () => {
    const h = fixture();
    h.channel.send({ type: "permissions_pending_list" });
    for (let i = 0; i < 20; i++) h.emit({ type: "human_memory_changed", payload: {} });
    expect(h.send).toHaveBeenCalledTimes(1);
    h.response(0, { pending: [item], truncated: false }); await tick();
    expect(h.send).toHaveBeenCalledTimes(2);
    h.states.forEach((f) => f("disconnected")); await tick();
    expect(h.received).toHaveBeenCalledWith({ type: "permissions_pending_list_response", payload: { pending: [] } });
    h.channel.dispose(); h.channel.start(); h.channel.on_message(h.received);
    h.channel.send({ type: "permissions_pending_list" });
    h.response(2, { pending: [item], truncated: false }); await tick();
    expect(h.received).toHaveBeenCalledWith({ type: "permissions_pending_list_response", payload: { pending: [item] } });
  });
  it("timeout never automatically resends an approval", async () => {
    vi.useFakeTimers(); const h = fixture();
    h.channel.send({ type: "permission_response", payload: { ...item, decision: "allow" } });
    await vi.advanceTimersByTimeAsync(15001);
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(h.received).toHaveBeenCalledWith(expect.objectContaining({ type: "permission_response_applied", payload: expect.objectContaining({ ok: false }) }));
  });
});
