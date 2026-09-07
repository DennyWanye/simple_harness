import { afterEach, describe, expect, it, vi } from "vitest";
import { AuditRequests } from "./auditRequests";
import type { PrimaryPort } from "./controller";
import type { PrimaryWireRequest } from "./requests";

function fixture() {
  const listeners = new Set<(v: unknown) => void>();
  const states = new Set<(v: "connected" | "disconnected") => void>();
  const sent: PrimaryWireRequest[] = [];
  const port: PrimaryPort = {
    state: () => "connected", send_command: r => { sent.push(r); return true; },
    on_message: fn => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    on_state_change: fn => { states.add(fn); return () => { states.delete(fn); }; },
  };
  const client = new AuditRequests();
  const expires = Date.now() / 1000 + 300;
  const emit = (v: unknown) => [...listeners].forEach(fn => fn(v));
  const reply = (index: number, result: object) => emit({ type: "human_memory_response", request_id: sent[index].request_id,
    payload: { ok: true, operation: sent[index].operation, result } });
  const opened = (index: number, extra: object = {}) => reply(index, {
    primary_ref: "p", audit_ref: "audit", open_action_id: sent[index].request.open_action_id,
    expires_at: expires, max_reads: 32, page_limit: 100, purpose: "operation_metadata", ...extra,
  });
  const page = (index: number, extra: object = {}) => reply(index, {
    primary_ref: "p", audit_ref: "audit", page_action_id: sent[index].request.page_action_id,
    snapshot_hash: "a".repeat(64), page_hash: "b".repeat(64), access_event_hash: "c".repeat(64),
    expires_at: expires, max_reads: 32, reads_used: 1, all_operations_recorded: false,
    next_cursor_ref: "next", enumeration_complete: false, coverage: [],
    items: [{ family: "suppression", event_kind: "directive", outcome: "committed", occurred_at: 0,
      cognitive_effect: "not_applicable", operation_ref_hash: "d".repeat(64), item_hash: "e".repeat(64) }], ...extra,
  });
  return { client, port, sent, states, emit, reply, opened, page };
}
afterEach(() => vi.useRealTimers());

describe("explicit HUMAN audit metadata requests", () => {
  it("does not grant on mount/global ready, and only explicitly reads after matched grant ACK", async () => {
    const h = fixture(); let stop = h.client.connect(h.port, "p", null, false);
    h.emit({ type: "companion_identity_status", payload: { ready: true } });
    await h.client.open(); expect(h.sent).toEqual([]);
    stop(); stop = h.client.connect(h.port, "p", "owner", true);
    expect(h.sent).toEqual([]);
    const opening = h.client.open(); h.opened(0); await opening;
    expect(h.sent).toHaveLength(1); // no implicit charged read
    const reading = h.client.next(); h.page(1); await reading;
    expect(h.client.getSnapshot().page?.items[0].occurred_at).toBe(0);
    h.emit({ type: "human_memory_changed" });
    expect(h.sent).toHaveLength(2); // sealed snapshot, not automatic refresh
    stop();
  });
  it("unknown ACK retries the same logical action with a fresh transport id", async () => {
    vi.useFakeTimers(); const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    const opening = h.client.open(); h.opened(0); await opening;
    const first = h.client.next(); await vi.advanceTimersByTimeAsync(15_000); await first;
    expect(h.client.getSnapshot().pending).toBe(true);
    const retry = h.client.retry();
    expect(h.sent[2].request).toEqual(h.sent[1].request);
    expect(h.sent[2].request_id).not.toBe(h.sent[1].request_id);
    h.page(2); await retry;
    expect(h.client.getSnapshot().pending).toBe(false);
    expect(h.client.getSnapshot().page?.reads_used).toBe(1);
    stop();
  });
  it.each(["companion_control_rechallenge", "companion_profile_bound"])("%s retracts page and ignores old in-flight ACK", async event => {
    const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    const opening = h.client.open(); h.opened(0); await opening;
    const reading = h.client.next();
    h.emit({ type: event }); h.page(1); await reading;
    expect(h.client.getSnapshot()).toMatchObject({ ready: false, page: null, grant: null });
    expect(h.sent).toHaveLength(2); stop();
  });
  it("disconnect and verified new owner cannot inherit or replay the old grant", async () => {
    const h = fixture(); let stop = h.client.connect(h.port, "p", "owner", true);
    const opening = h.client.open(); h.opened(0); await opening;
    h.states.forEach(fn => fn("disconnected"));
    stop(); stop = h.client.connect(h.port, "other-p", "other-owner", true);
    expect(h.client.getSnapshot()).toMatchObject({ grant: null, page: null, pending: false });
    await h.client.retry(); expect(h.sent).toHaveLength(1); stop();
  });
  it("page snapshot mismatch remains unknown; close retracts before its ACK", async () => {
    const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    const opening = h.client.open(); h.opened(0); await opening;
    const first = h.client.next(); h.page(1); await first;
    const second = h.client.next(); h.page(2, { snapshot_hash: "f".repeat(64), reads_used: 2 }); await second;
    expect(h.client.getSnapshot()).toMatchObject({ page: null, pending: true });
    const closing = h.client.close();
    expect(h.client.getSnapshot().page).toBeNull();
    h.reply(3, { primary_ref: "p", audit_ref: "audit", status: "closed" }); await closing;
    expect(h.client.getSnapshot()).toMatchObject({ grant: null, pending: false }); stop();
  });
  it("unknown open survives hide/show under the same owner, without automatic replay", async () => {
    vi.useFakeTimers(); const h = fixture(); let stop = h.client.connect(h.port, "p", "owner", true);
    const opening = h.client.open(); await vi.advanceTimersByTimeAsync(15_000); await opening;
    stop(); stop = h.client.connect(h.port, "p", "owner", true);
    expect(h.sent).toHaveLength(1);
    const retry = h.client.retry(); expect(h.sent[1].request).toEqual(h.sent[0].request);
    h.opened(1); await retry;
    expect(h.client.getSnapshot().grant?.audit_ref).toBe("audit"); stop();
  });
  it("expiry clears all metadata and never silently opens a new grant", async () => {
    vi.useFakeTimers(); const h = fixture(), stop = h.client.connect(h.port, "p", "owner", true);
    const opening = h.client.open(); h.opened(0); await opening;
    const reading = h.client.next(); h.page(1); await reading;
    await vi.advanceTimersByTimeAsync(300_000);
    expect(h.client.getSnapshot()).toMatchObject({ page: null, grant: null, pending: false });
    expect(h.sent).toHaveLength(2); stop();
  });
});
