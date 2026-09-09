import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryController, type PrimaryPort } from "./controller";
import type { PrimaryWireRequest } from "./requests";
import { PRIMARY_TURN_TEXT_MAX_BYTES } from "./turnText";
const bound = { type: "companion_profile_bound", payload: { profile_id: "owner", profile_generation: 1 } };
const state = { primary_ref: "primary-real", revision: "r1", current_run: null, queued_count: 0, queued_count_truncated: false };
const item = { message_ref: "message-1", turn_ref: "turn-1", run_ref: "run-1", delivery_key: "d1", role: "assistant", text: "真实终态文本", has_more: false, total_chars: 6 };
const tick = async () => { for (let i = 0; i < 10; ++i) await Promise.resolve(); };
function setup(running = false) {
  const readState = running ? { ...state, current_run: { run_ref: "run", generation: 1, state: "CLAIMED", sdk_run_ref: null, execution_session_ref: null } } : state;
  const listeners = new Set<(message: unknown) => void>();
  let onState: (state: "connected" | "disconnected") => void = () => {};
  let connected = true;
  const wire: PrimaryWireRequest[] = [];
  const emit = (message: unknown) => listeners.forEach((listener) => listener(message));
  let autoRead = true;
  let refuseEnqueue = false;
  const port: PrimaryPort = {
    state: () => connected ? "connected" : "disconnected",
    on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    on_state_change: (fn) => { onState = fn; return () => {}; },
    send_command: (request) => {
      wire.push(request);
      if (refuseEnqueue && request.operation === "queue.enqueue") return false;
      if (autoRead && ["primary.open", "primary.state", "primary.messages.page"].includes(request.operation)) {
        const result = request.operation === "primary.messages.page" ? { primary_ref: state.primary_ref, revision: state.revision, items: [item], next_cursor: null } : readState;
        queueMicrotask(() => emit({ type: "human_memory_response", request_id: request.request_id, payload: { ok: true, operation: request.operation, result } }));
      }
      return connected;
    },
  };
  const controller = new PrimaryController(port);
  const stop = controller.start();
  return { controller, stop, emit, wire, reads: (value: boolean) => { autoRead = value; },
    refuseEnqueue: (value: boolean) => { refuseEnqueue = value; },
    connect: (value: boolean) => { connected = value; onState(value ? "connected" : "disconnected"); },
    reply: (request: PrimaryWireRequest, result: object) => emit({ type: "human_memory_response", request_id: request.request_id, payload: { ok: true, operation: request.operation, result } }),
  };
}
afterEach(() => vi.useRealTimers());
describe("primary durable controller", () => {
  it.each(["local_send_failure", "server_rejection"])("keeps an earlier unknown delivery after retry %s", async (failure) => {
    vi.useFakeTimers(); const h = setup(); h.emit(bound); await vi.advanceTimersByTimeAsync(0);
    const original = h.controller.submit("original draft", []);
    const first = h.wire.at(-1)!;
    const timeout = expect(original).rejects.toMatchObject({ uncertain: true });
    await vi.advanceTimersByTimeAsync(15_000); await timeout;
    h.emit({ type: "companion_control_rechallenge" });
    h.emit(bound); await vi.advanceTimersByTimeAsync(0);

    h.refuseEnqueue(failure === "local_send_failure");
    const retry = h.controller.submit("original draft", []);
    const second = h.wire.at(-1)!;
    expect(second.request).toEqual(first.request);
    if (failure === "server_rejection") h.emit({ type: "human_memory_response", request_id: second.request_id,
      payload: { ok: false, operation: "queue.enqueue", error: { code: "human_memory_identity_unavailable" } } });
    await expect(retry).rejects.toMatchObject({ uncertain: false });

    h.refuseEnqueue(false);
    const finalRetry = h.controller.submit("original draft", []);
    const third = h.wire.at(-1)!;
    // Simulate replay of the original durable receipt, whose first ACK was lost.
    h.reply(third, { delivery_key: first.request.delivery_key, turn_ref: "original-turn", receipt_ref: "original-receipt",
      enqueue_sequence: 1, scope_ref: null, content_sha256: "a".repeat(64) });
    const finalResult = await finalRetry.then(() => "acknowledged", () => "rejected");
    h.stop(); await tick();
    expect(third.request).toEqual(first.request);
    expect(finalResult).toBe("acknowledged");
  });
  it("allows a different draft after a first attempt was definitely not sent", async () => {
    const h = setup(); h.emit(bound); await tick();
    h.refuseEnqueue(true);
    await expect(h.controller.submit("unsent draft", [])).rejects.toMatchObject({ uncertain: false });
    const first = h.wire.at(-1)!;
    h.refuseEnqueue(false);
    const next = h.controller.submit("edited draft", []);
    const sent = h.wire.at(-1)!;
    h.reply(sent, { delivery_key: sent.request.delivery_key, turn_ref: "new-turn", receipt_ref: "new-receipt",
      enqueue_sequence: 1, scope_ref: null, content_sha256: "a".repeat(64) });
    await next; h.stop(); await tick();
    expect(sent.request.text).toBe("edited draft");
    expect(sent.request.delivery_key).not.toBe(first.request.delivery_key);
    expect(Object.keys(sent.request).sort()).toEqual(["delivery_key", "text"]);
  });
  it("ignores global ready; opens/reads only after this socket's bound ACK", async () => {
    const h = setup();
    h.emit({ ...bound, type: "companion_identity_status" }); await tick();
    expect(h.wire).toHaveLength(0);
    h.emit(bound); await tick();
    expect(h.wire.map((r) => r.operation)).toEqual(["primary.open", "primary.state", "primary.messages.page"]);
    expect(h.wire[2].request).toEqual({ primary_ref: "primary-real", limit: 20 });
    expect(h.controller.getSnapshot().messages[0].text).toBe("真实终态文本");
    h.stop();
  });
  it("drops old content on disconnect and does not reuse global ready to rehydrate", async () => {
    const h = setup(); h.emit(bound); await tick();
    expect(h.controller.getSnapshot().verifiedOwnerKey).toBe("owner:1");
    h.connect(false); expect(h.controller.getSnapshot().messages).toEqual([]);
    expect(h.controller.getSnapshot().verifiedOwnerKey).toBeNull();
    h.connect(true); h.emit({ ...bound, type: "companion_identity_status" }); await tick();
    expect(h.wire).toHaveLength(3);
    h.emit(bound); await tick(); expect(h.wire).toHaveLength(6);
    h.stop();
  });
  it("clears privacy payloads immediately and discards pre-invalidation late pages", async () => {
    const h = setup(); h.emit(bound); await tick(); h.reads(false);
    void h.controller.refresh(); await tick();
    const oldRead = h.wire.at(-1)!;
    h.emit({ type: "human_memory_invalidated" });
    expect(h.controller.getSnapshot().messages).toEqual([]);
    h.reply(oldRead, state); await tick();
    expect(h.controller.getSnapshot().messages).toEqual([]);
    h.stop(); await tick();
  });
  it("does not resend unknown enqueue on reconnect and preserves delivery key for explicit retry", async () => {
    vi.useFakeTimers(); const h = setup(); h.emit(bound); await vi.advanceTimersByTimeAsync(0);
    const send = h.controller.submit("hello", []);
    const rejected = expect(send).rejects.toMatchObject({ uncertain: true });
    const first = h.wire.at(-1)!;
    h.connect(false); await rejected; h.connect(true); h.emit(bound); await vi.advanceTimersByTimeAsync(0);
    expect(h.wire.filter((r) => r.operation === "queue.enqueue")).toHaveLength(1);
    await expect(h.controller.submit("changed", [])).rejects.toThrow("上一条发送结果未知");
    const retry = h.controller.submit("hello", []);
    const second = h.wire.at(-1)!;
    expect(second.request).toEqual(first.request);
    expect(second.request_id).not.toBe(first.request_id);
    h.reply(second, { delivery_key: second.request.delivery_key, turn_ref: "turn", receipt_ref: "receipt", enqueue_sequence: 1, scope_ref: null, content_sha256: "a".repeat(64) });
    await retry; h.stop(); await tick();
  });
  // Incident G: an 18 393-byte Chinese turn never reached the Host.
  it("enqueues a long turn inside the bound and names the bound above it", async () => {
    const h = setup(); h.emit(bound); await tick();
    const long = "目标条款：核对主清单 A 的这一组条目，并保留原始出处引用。；".repeat(215);
    expect(new TextEncoder().encode(long).length).toBeGreaterThan(18_393);
    const submit = h.controller.submit(long, []);
    const sent = h.wire.at(-1)!;
    expect(sent.operation).toBe("queue.enqueue");
    expect(sent.request.text).toBe(long);
    h.reply(sent, { delivery_key: sent.request.delivery_key, turn_ref: "turn", receipt_ref: "receipt",
      enqueue_sequence: 1, scope_ref: null, content_sha256: "a".repeat(64) });
    await submit;

    const enqueues = h.wire.filter((r) => r.operation === "queue.enqueue").length;
    const tooLong = "很".repeat(PRIMARY_TURN_TEXT_MAX_BYTES);
    await expect(h.controller.submit(tooLong, [])).rejects.toThrow(String(PRIMARY_TURN_TEXT_MAX_BYTES));
    // Nothing was put on the wire, and no pending delivery was left behind.
    expect(h.wire.filter((r) => r.operation === "queue.enqueue")).toHaveLength(enqueues);
    const after = h.controller.submit("短消息", []);
    h.reply(h.wire.at(-1)!, { delivery_key: h.wire.at(-1)!.request.delivery_key, turn_ref: "t2", receipt_ref: "r2",
      enqueue_sequence: 2, scope_ref: null, content_sha256: "b".repeat(64) });
    await after;
    h.stop();
  });
  it("translates the Host oversize code into a readable rejection", async () => {
    const h = setup(); h.emit(bound); await tick();
    const submit = h.controller.submit("hello", []);
    h.emit({ type: "human_memory_response", request_id: h.wire.at(-1)!.request_id,
      payload: { ok: false, operation: "queue.enqueue", error: { code: "human_memory_turn_text_too_large" } } });
    await expect(submit).rejects.toThrow(/消息过长/);
    h.stop();
  });
  it("does not clear draft on incomplete receipt or silently strip attachments", async () => {
    const h = setup(); h.emit(bound); await tick();
    await expect(h.controller.submit("hello", [{ name: "source.txt" }])).rejects.toThrow("附件接线尚未就绪");
    const submit = h.controller.submit("hello", []);
    h.reply(h.wire.at(-1)!, { delivery_key: "wrong", turn_ref: "t", receipt_ref: "r" });
    await expect(submit).rejects.toMatchObject({ uncertain: true });
    expect(h.wire.filter((r) => r.operation === "queue.enqueue")).toHaveLength(1);
    h.stop();
  });
  it("targets the exact observed generation and does not mark a run stopped from control ACK", async () => {
    const h = setup(); h.reads(false); h.emit(bound);
    h.reply(h.wire[0], { primary_ref: "primary-real" }); await tick();
    const run = { run_ref: "A", generation: 7, state: "CLAIMED", sdk_run_ref: "sdk-A", execution_session_ref: "exec-A" };
    h.reply(h.wire[1], { ...state, current_run: run }); await tick();
    h.reply(h.wire[2], { primary_ref: state.primary_ref, revision: state.revision, items: [], next_cursor: null }); await tick();
    const control = h.controller.control("stop");
    const request = h.wire.at(-1)!;
    expect(request.request).toEqual({ control: "stop", expected_run_ref: "A", expected_generation: 7 });
    h.reply(request, { run_ref: "A", generation: 7, outcome: "signalled", receipt_ref: "control", state: "STOP_REQUESTED" }); await control;
    expect(h.controller.getSnapshot().state?.current_run?.state).toBe("CLAIMED");
    h.stop(); await tick();
  });
  it("bounds polling, but actual change events and user refresh can restart reads", async () => {
    vi.useFakeTimers(); const h = setup(); h.emit(bound); await vi.advanceTimersByTimeAsync(0);
    // No running/queued state: no polling at all.
    await vi.advanceTimersByTimeAsync(60_000); expect(h.wire).toHaveLength(3);
    h.emit({ type: "human_memory_changed" }); await vi.advanceTimersByTimeAsync(100);
    expect(h.wire).toHaveLength(6);
    h.stop(); await vi.advanceTimersByTimeAsync(60_000); expect(h.wire).toHaveLength(6);
  });
  it("ends active-run fallback after twelve reads without failing the run, and wakes on a real event", async () => {
    vi.useFakeTimers(); const h = setup(true); h.emit(bound);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(h.wire).toHaveLength(27);
    expect(h.controller.getSnapshot().state?.current_run?.state).toBe("CLAIMED");
    expect(h.controller.getSnapshot().error).toBe("");
    expect(h.controller.getSnapshot().notice).toContain("自动补读已暂停");
    expect(vi.getTimerCount()).toBe(0);
    h.emit({ type: "human_memory_changed" });
    await vi.advanceTimersByTimeAsync(100);
    expect(h.wire).toHaveLength(30);
    expect(h.controller.getSnapshot().state?.current_run?.state).toBe("CLAIMED");
    h.stop();
  });
  it("retains an enqueue ACK correlation when invalidation arrives before ACK", async () => {
    vi.useFakeTimers(); const h = setup(); h.emit(bound); await vi.advanceTimersByTimeAsync(0);
    const promise = h.controller.submit("hello", []); const sent = h.wire.at(-1)!;
    h.emit({ type: "human_memory_changed" });
    expect(h.controller.getSnapshot().messages).toEqual([]);
    h.reply(sent, { delivery_key: sent.request.delivery_key, turn_ref: "t", receipt_ref: "r", scope_ref: null, enqueue_sequence: 1, content_sha256: "a".repeat(64) });
    await expect(promise).resolves.toBeUndefined();
    h.stop(); await tick();
  });
  it("preserves an unknown delivery across timeout, rechallenge and verified same-owner bind", async () => {
    vi.useFakeTimers(); const h = setup(); h.emit(bound); await vi.advanceTimersByTimeAsync(0);
    const pending = h.controller.submit("hello", []); const first = h.wire.at(-1)!;
    const timeout = expect(pending).rejects.toMatchObject({ uncertain: true });
    await vi.advanceTimersByTimeAsync(15_000); await timeout;
    const draftEpoch = h.controller.getSnapshot().draftEpoch;
    h.emit({ type: "companion_control_rechallenge" });
    expect(h.controller.getSnapshot().ready).toBe(false);
    expect(h.controller.getSnapshot().draftEpoch).toBe(draftEpoch);
    h.emit({ ...bound, type: "companion_identity_status" });
    expect(h.controller.getSnapshot().ready).toBe(false);
    h.emit(bound); await vi.advanceTimersByTimeAsync(0);
    const retry = h.controller.submit("hello", []); const second = h.wire.at(-1)!;
    expect(second.request.delivery_key).toBe(first.request.delivery_key);
    h.reply(second, { delivery_key: second.request.delivery_key, turn_ref: "t", receipt_ref: "r", enqueue_sequence: 1, scope_ref: null, content_sha256: "a".repeat(64) });
    await retry; h.stop(); await tick();
  });
  it.each(["wrong_run", "wrong_generation", "stale", "rejected", "superseded", "already_terminal", "unknown"])("handles control receipt %s without claiming acceptance", async (kind) => {
    const h = setup(true); h.emit(bound); await tick(); h.reads(false);
    const action = h.controller.control("stop"); const request = h.wire.at(-1)!;
    h.reply(request, { run_ref: kind === "wrong_run" ? "other" : "run", generation: kind === "wrong_generation" ? 2 : 1,
      receipt_ref: "control", state: "CLAIMED", outcome: kind === "wrong_run" || kind === "wrong_generation" ? "signalled" : kind });
    await action;
    const snap = h.controller.getSnapshot();
    expect(snap.notice).not.toContain("已受理");
    if (kind === "already_terminal") expect(snap.notice).toContain("目标任务已结束");
    else expect(snap.error).not.toBe("");
    h.stop(); await tick();
  });
  it("accepts an empty filtered page with next_cursor; details preserve codepoint offset", async () => {
    const h = setup(); h.emit(bound); await tick(); h.reads(false);
    const read = h.controller.refresh("older-key");
    h.reply(h.wire.at(-1)!, state); await tick();
    const request = h.wire.at(-1)!;
    expect(request.request.cursor).toBe("older-key");
    h.reply(request, { primary_ref: state.primary_ref, revision: state.revision, items: [], next_cursor: "even-older" }); await read;
    expect(h.controller.getSnapshot().nextCursor).toBe("even-older");
    const detail = h.controller.detail("m", 2);
    const req = h.wire.at(-1)!;
    expect(req.request).toEqual({ primary_ref: "primary-real", message_ref: "m", offset: 2, limit: 4096 });
    h.reply(req, { message_ref: "m", text: "😀汉", offset: 2, next_offset: 4, total_chars: 8 });
    await expect(detail).resolves.toMatchObject({ text: "😀汉", next_offset: 4 }); h.stop();
  });
});

// 事件 AK（同 userdata 重启）：后端在闭合历史补读超时时仍会下发就绪帧并带稳定码；
// 签名 bind 被拒时前端此前完全无声，UI 永远停在「等待主对话就绪」。
describe("事件 AK 重启就绪", () => {
  it("降级的就绪帧照样进入 ready，并把稳定码说出来", async () => {
    const h = setup();
    h.emit({ type: "companion_profile_bound", payload: {
      profile_id: "owner", profile_generation: 1,
      projection_degraded_code: "companion_projection_bind_drain_timeout" } });
    await tick();
    const snap = h.controller.getSnapshot();
    expect(snap.ready).toBe(true);
    expect(snap.state).not.toBeNull();
    expect(snap.notice).toContain("companion_projection_bind_drain_timeout");
    h.stop();
  });
  it("绑定被拒时显示稳定码而不是静默等待", async () => {
    const h = setup();
    h.emit({ type: "companion_control_error", payload: { code: "auth_snapshot_mismatch" } });
    await tick();
    const snap = h.controller.getSnapshot();
    expect(snap.ready).toBe(false);
    expect(snap.error).toContain("auth_snapshot_mismatch");
    h.stop();
  });
  it("已就绪后的控制错误不覆盖主对话读态", async () => {
    const h = setup();
    h.emit(bound); await tick();
    expect(h.controller.getSnapshot().ready).toBe(true);
    h.emit({ type: "companion_control_error", payload: { code: "auth_snapshot_mismatch" } });
    await tick();
    expect(h.controller.getSnapshot().error).toBe("");
    h.stop();
  });
});
