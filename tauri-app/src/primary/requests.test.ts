import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryRequests, type PrimaryWireRequest } from "./requests";
function harness() {
  let receive: (message: unknown) => void = () => {};
  const sent: PrimaryWireRequest[] = [];
  const port = { send_command: vi.fn((message: PrimaryWireRequest) => { sent.push(message); return true; }), on_message: (fn: typeof receive) => { receive = fn; return () => { receive = () => {}; }; } };
  const client = new PrimaryRequests(port);
  const reply = (index: number, payload: object, id = sent[index].request_id) => receive({ type: "human_memory_response", request_id: id, payload });
  return { client, sent, port, reply };
}
afterEach(() => vi.useRealTimers());
describe("primary correlated requests", () => {
  it("requires matching request and operation; writes top-level wire fields", async () => {
    const h = harness();
    const promise = h.client.request("primary.state", {});
    expect(h.sent[0]).toEqual({ type: "human_memory_request", request_id: expect.any(String), operation: "primary.state", request: {} });
    h.reply(0, { ok: true, operation: "primary.state", result: { wrong: true } }, "unrelated");
    h.reply(0, { ok: true, operation: "queue.enqueue", result: {} });
    await expect(promise).rejects.toThrow("响应操作不匹配");
    h.client.dispose();
  });
  it("returns only success result and preserves stable public errors", async () => {
    const h = harness();
    const good = h.client.request("primary.state", {});
    h.reply(0, { ok: true, operation: "primary.state", result: { primary_ref: "p" } });
    await expect(good).resolves.toEqual({ primary_ref: "p" });
    const bad = h.client.request("queue.enqueue", { text: "x", delivery_key: "stable" });
    h.reply(1, { ok: false, error: { code: "foreground_blocked" } });
    await expect(bad).rejects.toThrow("foreground_blocked");
    h.client.dispose();
  });
  it("rejects disconnected send without retry or buffering", async () => {
    const h = harness(); h.port.send_command.mockReturnValue(false);
    await expect(h.client.request("queue.enqueue", {})).rejects.toMatchObject({ uncertain: false });
    expect(h.port.send_command).toHaveBeenCalledTimes(1);
    h.client.dispose();
  });
  it("bounds timeout and ignores late responses after invalidation without replay", async () => {
    vi.useFakeTimers(); const h = harness();
    const promise = h.client.request("queue.enqueue", {});
    const result = expect(promise).rejects.toMatchObject({ uncertain: true });
    await vi.advanceTimersByTimeAsync(15_000); await result;
    const read = h.client.request("primary.state", {});
    const rejected = expect(read).rejects.toThrow("读取已失效");
    h.client.invalidate(); await rejected;
    h.reply(1, { ok: true, operation: "primary.state", result: { secret: "stale" } });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.sent).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(0);
    h.client.dispose();
  });
});
