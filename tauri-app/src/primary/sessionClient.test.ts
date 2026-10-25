import { describe, expect, it } from "vitest";
import { SessionClient } from "./sessionClient";

function fixture() {
  const listeners = new Set<(m: unknown) => void>();
  const states = new Set<(s: string) => void>();
  const sent: Array<{ type: string; request_id: string; payload: Record<string, unknown> }> = [];
  const channel = {
    state: "connected",
    send: (message: unknown) => {
      sent.push(message as (typeof sent)[number]);
      return true;
    },
    onMessage: (fn: (m: unknown) => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    onStateChange: (fn: (s: string) => void) => {
      states.add(fn);
      return () => states.delete(fn);
    },
  };
  return {
    channel,
    sent,
    client: new SessionClient(channel as never),
    reply: (data: unknown) =>
      listeners.forEach((fn) =>
        fn({
          type: `${sent.at(-1)!.type}_response`,
          payload: { request_id: sent.at(-1)!.request_id, ok: true, data },
        }),
      ),
    disconnect: () => states.forEach((fn) => fn("disconnected")),
  };
}

describe("SDK Session transport", () => {
  it("keeps the human request and version separate from the transport id", async () => {
    const h = fixture();
    const pending = h.client.request("respond", {
      request_id: "human",
      version: "1152921504606846975",
      decision: "approve",
    });
    expect(h.sent[0].type).toBe("session_respond");
    expect(h.sent[0].request_id).not.toBe("human");
    expect(h.sent[0].payload.version).toBe("1152921504606846975");
    h.reply({ applied: true });
    await expect(pending).resolves.toEqual({ applied: true });
    h.client.close();
  });
  it("disconnect rejects an uncertain send without resending it", async () => {
    const h = fixture();
    const pending = h.client.request("send", { text: "hi", idempotency_key: "same" });
    h.disconnect();
    await expect(pending).rejects.toThrow();
    expect(h.sent).toHaveLength(1);
    h.client.close();
  });
});
