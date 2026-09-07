import { afterEach, describe, expect, it, vi } from "vitest";
import { CognitiveRequests } from "./cognitiveRequests";
import type { PrimaryPort } from "./controller";
import type { PrimaryWireRequest } from "./requests";

export const item = { memory_id: "canonical-memory", revision: 1, label: "偏好简洁", status: "active", can_forget: true, content_hash: "a".repeat(64) };
function fixture() {
  const listeners = new Set<(value: unknown) => void>();
  const states = new Set<(value: "connected" | "disconnected") => void>();
  const sent: PrimaryWireRequest[] = [];
  const port: PrimaryPort = { state: () => "connected", send_command: (r) => { sent.push(r); return true; },
    on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    on_state_change: (fn) => { states.add(fn); return () => { states.delete(fn); }; } };
  const client = new CognitiveRequests();
  const emit = (m: unknown) => [...listeners].forEach((fn) => fn(m));
  const reply = (i: number, result: object) => emit({ type: "human_memory_response", request_id: sent[i].request_id, payload: { ok: true, operation: sent[i].operation, result } });
  const page = (i: number, items = [item]) => reply(i, { primary_ref: "p", items, next_cursor: null });
  const ack = (i: number, extra: object = {}) => reply(i, { ...sent[i].request, status: "applied", directive_ref: "real-directive", decision_hash: "b".repeat(64), evidence_ref: "real-action", ...extra });
  const reject = (i: number, code: string) => emit({ type: "human_memory_response", request_id: sent[i].request_id, payload: { ok: false, error: { code } } });
  return { client, port, sent, emit, states, page, reply, ack, reject };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
afterEach(() => vi.useRealTimers());

describe("cognitive requests on the actual bound port", () => {
  it("accepts only correlated canonical pages; privacy retracts late data", async () => {
    const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush(); expect(h.client.getSnapshot().items).toEqual([item]);
    void h.client.refresh();
    h.emit({ type: "human_memory_privacy_changed", payload: {} });
    expect(h.client.getSnapshot().items).toEqual([]);
    h.page(1); await flush(); expect(h.client.getSnapshot().items).toEqual([]);
    h.page(2, []); await flush(); expect(h.client.getSnapshot().error).toBe("");
    expect(h.sent.every((r) => r.operation === "primary.memory.list")).toBe(true);
    stop();
  });
  it("unknown then same-owner rechallenge retry retains exact action without automatic write", async () => {
    vi.useFakeTimers(); const h = fixture();
    let stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush();
    const first = h.client.forget(item);
    await vi.advanceTimersByTimeAsync(15000); await first;
    const original = h.sent[1].request;
    h.emit({ type: "companion_control_rechallenge" });
    expect(h.client.getSnapshot().ready).toBe(false);
    stop(); stop = h.client.connect(h.port, "p", "owner", true);
    h.page(2, []); await flush();
    expect(h.sent.filter((r) => r.operation === "primary.memory.forget")).toHaveLength(1);
    const retry = h.client.retry(String(original.action_id));
    expect(h.sent[3].request).toEqual(original);
    h.ack(3); await flush(); h.page(4, []); await retry;
    expect(h.client.getSnapshot().pending).toEqual([]);
    expect(h.client.getSnapshot().notice).toContain("已忘记");
    stop();
  });
  it("privacy refresh does not discard a valid forget ACK and cannot resurrect old content", async () => {
    const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush(); const writing = h.client.forget(item);
    expect(h.client.getSnapshot().items).toEqual([]);
    h.emit({ type: "human_memory_changed" }); // separate reader; write remains pending
    h.ack(1); await flush();
    h.page(2); await flush(); expect(h.client.getSnapshot().items).toEqual([]);
    h.page(3, []); await writing;
    expect(h.client.getSnapshot().notice).toContain("已忘记"); stop();
  });
  it("wrong-target success remains unconfirmed and verified new owner clears old intent", async () => {
    const h = fixture(); let stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush(); const writing = h.client.forget(item);
    h.ack(1, { memory_id: "other" }); await writing;
    expect(h.client.getSnapshot().notice).toContain("尚未确认");
    expect(h.client.getSnapshot().pending).toHaveLength(1);
    stop(); stop = h.client.connect(h.port, "p2", "owner2", true);
    expect(h.client.getSnapshot().pending).toEqual([]);
    expect(h.client.getSnapshot().items).toEqual([]); stop();
  });
  it("unbound global ready never enables access", () => {
    const h = fixture(), stop = h.client.connect(h.port, "p", null, false);
    h.emit({ type: "companion_identity_status", payload: { ready: true } });
    expect(h.sent).toEqual([]); expect(h.client.getSnapshot().ready).toBe(false); stop();
  });
  it.each(["primary_memory_target_stale", "primary_memory_request_invalid", "primary_memory_action_invalid"])("first %s clears intent, but cannot erase an earlier unknown", async (code) => {
    vi.useFakeTimers(); const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush(); const first = h.client.forget(item);
    h.reject(1, code); await first;
    expect(h.client.getSnapshot().pending).toEqual([]);
    const read = h.client.refresh(); h.page(2); await read;
    const unknown = h.client.forget(item);
    await vi.advanceTimersByTimeAsync(15000); await unknown;
    const actionId = String(h.sent[3].request.action_id);
    const retry = h.client.retry(actionId); h.reject(4, code); await retry;
    expect(h.client.getSnapshot().pending[0].action_id).toBe(actionId);
    expect(h.sent[4].request).toEqual(h.sent[3].request);
    expect(h.sent[1].request.action_id).not.toBe(actionId); stop();
  });
  it("an identity or primary binding rejection preserves the original action", async () => {
    const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush();
    const writing = h.client.forget(item); h.reject(1, "human_memory_identity_unavailable"); await writing;
    const actionId = String(h.sent[1].request.action_id);
    expect(h.client.getSnapshot().pending[0].action_id).toBe(actionId);
    const retry = h.client.retry(actionId); h.reject(2, "primary_ref_mismatch"); await retry;
    expect(h.sent[2].request).toEqual(h.sent[1].request);
    expect(h.client.getSnapshot().pending[0].action_id).toBe(actionId);
    stop();
  });
  it("notifies the parent before fresh reads, while a failed parent refresh cannot undo ACK", async () => {
    const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush();
    const notified = vi.fn(() => {
      expect(h.sent).toHaveLength(2); // no post-ACK list has been sent yet
      expect(h.client.getSnapshot().items).toEqual([]);
      expect(h.client.getSnapshot().pending).toEqual([]);
      throw new Error("display-only refresh failed");
    });
    const off = h.client.onForgotten(notified);
    const writing = h.client.forget(item); h.ack(1); await flush();
    expect(notified).toHaveBeenCalledTimes(1);
    h.page(2, []); await writing;
    expect(h.client.getSnapshot().notice).toContain("已忘记");
    expect(h.client.getSnapshot().pending).toEqual([]);
    expect(h.client.getSnapshot().error).toContain("主对话刷新失败");
    off(); stop();
  });
  it("queued ACK followed by a rechallenge cannot notify or display success under revoked auth", async () => {
    const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    h.page(0); await flush();
    const notified = vi.fn(), off = h.client.onForgotten(notified);
    const writing = h.client.forget(item); h.ack(1);
    h.emit({ type: "companion_control_rechallenge" }); await writing;
    expect(notified).not.toHaveBeenCalled();
    expect(h.client.getSnapshot().ready).toBe(false);
    expect(h.client.getSnapshot().pending).toHaveLength(1);
    expect(h.client.getSnapshot().notice).not.toContain("已忘记");
    off(); stop();
  });
});
