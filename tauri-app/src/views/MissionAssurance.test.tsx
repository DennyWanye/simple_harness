// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ConnectionState } from "../ws/ControlChannel";
import { MissionAssurance } from "./MissionAssurance";
import { parseError, parseReview, parseSnapshot, parseUseCheck } from "../stores/assuranceStore";

class Channel {
  sent: ControlMessage[] = [];
  available = true;
  listeners = new Set<(message: IncomingMessage) => void>();
  states = new Set<(state: ConnectionState) => void>();
  send = (message: ControlMessage) => { this.sent.push(message); return this.available; };
  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener); return () => { this.listeners.delete(listener); };
  };
  onStateChange = (listener: (state: ConnectionState) => void) => {
    this.states.add(listener); return () => { this.states.delete(listener); };
  };
  last() { return this.sent.at(-1)!; }
  emit(type: string, payload: unknown) {
    act(() => { this.listeners.forEach((fn) => fn({ type, payload } as IncomingMessage)); });
  }
  reply(request: ControlMessage, data: unknown) {
    this.emit(`${request.type}_response`, { request_id: request.request_id, ok: true, data });
  }
  refuse(request: ControlMessage, wire: Record<string, unknown> | undefined, error_code = "CONTRACT_INVALID") {
    this.emit(`${request.type}_response`, { request_id: request.request_id, ok: false, error_code, error: "transport text",
      ...(wire === undefined ? {} : { assurance_error: wire }) });
  }
  reconnect() { act(() => this.states.forEach((fn) => fn("disconnected"))); }
}

const HASH = "a".repeat(64);
const envelope = (request: ControlMessage, extra: Record<string, unknown> = {}) => ({
  schema_version: 1, request_id: request.request_id, mission_id: "m1", view: "CURRENT", snapshot_seq: 7,
  root_incarnation_id: "root-1", sdk_fingerprint: HASH, host_fingerprint: "b".repeat(64), ...extra,
});
const item = (kind: string, id: string, extra: Record<string, unknown> = {}) => ({
  kind, id, history_state: "UNREVIEWED", current_use: "NOT_APPLICABLE", reason_codes: [], evidence_count: 0, artifact_ref: null, ...extra,
});
const snapshot = (request: ControlMessage, items: unknown[], extra: Record<string, unknown> = {}) =>
  envelope(request, { items, next_cursor: null, truncated: false, ...extra });
const wire = (request: ControlMessage, code: string) =>
  ({ schema_version: 1, request_id: request.request_id, code, message: "why", retryable: code === "SNAPSHOT_CHANGED" });
const read = () => fireEvent.click(screen.getByRole("button", { name: "读取保证状态" }));
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("C07 Assurance view", () => {
  it("reads only on demand with the contract body and shows history state next to current use", () => {
    const channel = new Channel(); render(<MissionAssurance missionId="m1" channel={channel} />);
    expect(channel.sent).toEqual([]); read();
    const request = channel.last();
    expect(request.type).toBe("mission_assurance_snapshot");
    expect(request.payload).toEqual({ schema_version: 1, request_id: request.request_id, mission_id: "m1",
      view: "CURRENT", at_event_seq: null, cursor: null, limit: 100 });
    channel.reply(request, snapshot(request, [
      item("CRITERION", "c-user-1"),
      item("CLOSEOUT", "m1", { history_state: "NOT_EVALUATED", current_use: "UNAVAILABLE", reason_codes: ["NO_CLOSEOUT_ROW"] }),
      item("REVIEW", "rv-1", { history_state: "OFFICIAL", current_use: "STALE", reason_codes: ["SOURCE_CHANGED"], evidence_count: 2,
        artifact_ref: { kind: "artifact", pin: { id: "art-1", revision: 3, content_hash: HASH } } }),
    ]));
    expect(screen.getByText(/当前视图 · 事件 7 · 根 root-1/)).toBeTruthy();
    expect(screen.getByText("NOT_EVALUATED")).toBeTruthy();
    expect(screen.getByText("无可用证书")).toBeTruthy();
    expect(screen.getByText("已过期或来源已变")).toBeTruthy();
    expect(screen.getByText(/产物 art-1 @ 3/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    // Review detail is fetched from the item; the review itself is pinned to its key.
    fireEvent.click(screen.getByRole("button", { name: "rv-1" }));
    const detail = channel.last();
    expect(detail.type).toBe("mission_assurance_review");
    expect(detail.payload).toEqual({ schema_version: 1, request_id: detail.request_id, mission_id: "m1", review_key: "rv-1", cursor: null, limit: 100 });
    channel.reply(detail, envelope(detail, {
      review_key: "rv-1", purpose: "MISSION_FINAL", package_ref: { kind: "review_package", pin: { id: "pkg", revision: 1, content_hash: HASH } },
      record_ref: null, official_status: "OFFICIAL", verdict: "ACCEPT", current_use: "STALE", next_cursor: null, truncated: false,
      assessments: [{ criterion_id: "c-user-1", model_grade: "PASS", effective_grade: "FAIL", check_gate: "FAIL",
        reason_codes: ["CHECK_GATE_FAILED"], evidence_labels: ["ev-" + HASH] }],
    }));
    expect(screen.getByText(/审阅 rv-1 · MISSION_FINAL · OFFICIAL · 判定 ACCEPT · 已过期或来源已变/)).toBeTruthy();
    expect(screen.getByText("CHECK_GATE_FAILED")).toBeTruthy();
  });

  it("keeps the last screen on invalid, foreign or drifting responses", () => {
    const channel = new Channel(); render(<MissionAssurance missionId="m1" channel={channel} />); read();
    const first = channel.last(); channel.reply(first, snapshot(first, [item("CRITERION", "c-user-1")]));
    expect(screen.getByText("c-user-1")).toBeTruthy();
    read(); const second = channel.last();
    channel.reply(second, { ...snapshot(second, [item("CRITERION", "c-user-2")]), tenant_id: "other" });
    expect(screen.getByRole("alert").textContent).toMatch(/格式不符/);
    expect(screen.queryByText("c-user-2")).toBeNull();
    read(); const third = channel.last();
    channel.reply(third, snapshot({ ...third, request_id: "someone-else" } as ControlMessage, [item("CRITERION", "c-user-3")]));
    expect(screen.getByRole("alert").textContent).toMatch(/其他请求/);
    read(); const fourth = channel.last();
    channel.reply(fourth, { ...snapshot(fourth, [item("CRITERION", "c-user-4")]), mission_id: "m2" });
    expect(screen.getByRole("alert").textContent).toMatch(/其他任务/);
    expect(screen.queryByText("c-user-4")).toBeNull();
    read(); const fifth = channel.last();
    channel.reply(fifth, snapshot(fifth, [item("CRITERION", "c-user-5", { current_use: "GRANTED" })]));
    expect(screen.queryByText("c-user-5")).toBeNull();
    // The previously rendered page survives every refusal above.
    expect(screen.getByText("c-user-1")).toBeTruthy();
  });

  it("restarts paging on SNAPSHOT_CHANGED and renders contract error codes", () => {
    const channel = new Channel(); render(<MissionAssurance missionId="m1" channel={channel} />); read();
    const first = channel.last();
    channel.reply(first, snapshot(first, [item("CRITERION", "c-user-1")], { next_cursor: "cur-1", truncated: true }));
    expect(screen.getByText(/尚有后续页/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    const next = channel.last();
    expect((next.payload as Record<string, unknown>).cursor).toBe("cur-1");
    channel.refuse(next, wire(next, "SNAPSHOT_CHANGED"), "SNAPSHOT_CHANGED");
    expect(screen.getByRole("alert").textContent).toMatch(/状态已变化，请从第一页重新读取：why（可重试）/);
    expect(screen.queryByText("c-user-1")).toBeNull();
    expect(screen.queryByRole("button", { name: "下一页" })).toBeNull();
    read(); const again = channel.last();
    channel.reply(again, snapshot(again, [item("CRITERION", "c-user-1")], { next_cursor: "cur-2", truncated: true }));
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    const page2 = channel.last();
    channel.reply(page2, snapshot(page2, [item("CRITERION", "c-user-2")]));
    expect(screen.getByText("c-user-1")).toBeTruthy(); expect(screen.getByText("c-user-2")).toBeTruthy();
    read(); const unbound = channel.last();
    channel.refuse(unbound, wire(unbound, "PROFILE_UNBOUND"), "PROFILE_UNBOUND");
    expect(screen.getByRole("alert").textContent).toMatch(/不在 Assurance 通道上/);
    read(); const malformed = channel.last();
    channel.refuse(malformed, { code: "NOT_FOUND" });
    expect(screen.getByRole("alert").textContent).toMatch(/错误回执格式无效/);
    read(); const plain = channel.last();
    channel.refuse(plain, undefined, "not_found");
    expect(screen.getByRole("alert").textContent).toBe("transport text");
  });

  it("reads history pinned to a seq and never accepts a mismatched view", () => {
    const channel = new Channel(); render(<MissionAssurance missionId="m1" channel={channel} />);
    fireEvent.change(screen.getByLabelText("Assurance 历史事件序号"), { target: { value: "5" } }); read();
    const request = channel.last();
    expect(request.payload).toMatchObject({ view: "HISTORY", at_event_seq: 5 });
    channel.reply(request, snapshot(request, [item("CRITERION", "c-user-1")], { view: "HISTORY", snapshot_seq: 6 }));
    expect(screen.getByRole("alert").textContent).toMatch(/视图与请求不符/);
    read(); const pinned = channel.last();
    channel.reply(pinned, snapshot(pinned, [item("CRITERION", "c-user-1")], { view: "HISTORY", snapshot_seq: 5 }));
    expect(screen.getByText(/历史视图（钉在事件 5）· 当前可用性按现行授权判断/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Assurance 历史事件序号"), { target: { value: "x" } }); read();
    expect(screen.getByRole("alert").textContent).toMatch(/有效的事件序号/);
    expect(channel.sent.length).toBe(2);
  });

  it("marks the screen stale on mission changes and clears it on reconnect or Mission switch", () => {
    const channel = new Channel(); const view = render(<MissionAssurance missionId="m1" channel={channel} />); read();
    const request = channel.last();
    channel.emit("mission_changed", { mission_id: "m2", status: "RUNNING" });
    expect(screen.queryByRole("status")).toBeNull();
    channel.emit("mission_changed", { mission_id: "m1", status: "RUNNING" });
    channel.reply(request, snapshot(request, [item("CRITERION", "c-user-1")]));
    expect(screen.getByRole("status").textContent).toMatch(/任务已变化/);
    read(); channel.reply(channel.last(), snapshot(channel.last(), [item("CRITERION", "c-user-1")]));
    expect(screen.queryByRole("status")).toBeNull();
    channel.reconnect();
    expect(screen.queryByText("c-user-1")).toBeNull();
    read(); const old = channel.last();
    view.rerender(<MissionAssurance missionId="m2" channel={channel} />);
    channel.reply(old, snapshot(old, [item("CRITERION", "c-user-1")]));
    expect(screen.queryByText("c-user-1")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("use check is diagnostic only and validates the subject before sending", () => {
    const channel = new Channel(); render(<MissionAssurance missionId="m1" channel={channel} />);
    fireEvent.click(screen.getByRole("button", { name: "核查" }));
    expect(screen.getByRole("alert").textContent).toMatch(/64 位十六进制/);
    expect(channel.sent).toEqual([]);
    fireEvent.change(screen.getByLabelText("结果编号"), { target: { value: "res-1" } });
    fireEvent.change(screen.getByLabelText("结果版本"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("结果内容摘要"), { target: { value: HASH } });
    fireEvent.click(screen.getByRole("button", { name: "核查" }));
    const request = channel.last();
    expect(request.type).toBe("mission_assurance_use_check");
    expect(request.payload).toEqual({ schema_version: 1, request_id: request.request_id, mission_id: "m1", view: "CURRENT", at_event_seq: null,
      subject_ref: { kind: "result", pin: { id: "res-1", revision: 2, content_hash: HASH } } });
    const body = envelope(request, { subject_ref: { kind: "result", pin: { id: "res-1", revision: 2, content_hash: HASH } },
      purpose: "ACCEPT", decision: "RECHECK_REQUIRED", diagnostic_only: true, coverage: "COMPLETE", reason_codes: ["CERTIFICATE_EXPIRED"],
      checked_at_ms: 1_700_000_000_000, expires_at_ms: null, certificate_ref: null });
    channel.reply(request, body);
    expect(screen.getByText(/需要重新核查 · 用途 ACCEPT · 覆盖 COMPLETE · CERTIFICATE_EXPIRED · 仅诊断，不构成使用许可/)).toBeTruthy();
    // A body that claims to carry a certificate is refused: the read verb never grants use.
    fireEvent.click(screen.getByRole("button", { name: "核查" }));
    const second = channel.last();
    channel.reply(second, { ...body, request_id: second.request_id, decision: "USABLE",
      certificate_ref: { kind: "certificate", pin: { id: "cert", revision: 1, content_hash: HASH } } });
    expect(screen.getByRole("alert").textContent).toMatch(/格式不符/);
    expect(screen.getByText(/需要重新核查 · 用途 ACCEPT/)).toBeTruthy();
  });

  it("times out a read and refuses to send without a channel", () => {
    vi.useFakeTimers();
    const channel = new Channel(); render(<MissionAssurance missionId="m1" channel={channel} />); read();
    act(() => { vi.advanceTimersByTime(30_001); });
    expect(screen.getByRole("alert").textContent).toMatch(/读取超时/);
    channel.available = false; read();
    expect(screen.getByRole("alert").textContent).toMatch(/连接不可用/);
    cleanup(); render(<MissionAssurance missionId="m1" channel={null} />);
    expect((screen.getByRole("button", { name: "读取保证状态" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("assurance parsers reject drift", () => {
  const request = { request_id: "r", type: "x", payload: {} } as unknown as ControlMessage;
  it("snapshot", () => {
    const good = snapshot(request, [item("CRITERION", "c")]);
    expect(parseSnapshot(good, "m1").items.length).toBe(1);
    for (const bad of [
      { ...good, schema_version: 2 }, { ...good, extra: 1 }, { ...good, items: [item("CRITERION", "c"), item("CRITERION", "c")] },
      { ...good, items: [item("CRITERION", "c", { artifact_ref: { kind: "result", pin: { id: "a", revision: 1, content_hash: HASH } } })] },
      { ...good, sdk_fingerprint: "short" }, { ...good, items: [item("OTHER", "c")] }, { ...good, snapshot_seq: -1 },
    ]) expect(() => parseSnapshot(bad, "m1")).toThrow();
  });
  it("review, use check and error", () => {
    const review = envelope(request, { review_key: "k", purpose: "TASK_CONTENT", package_ref: { kind: "review_package", pin: { id: "p", revision: 1, content_hash: HASH } },
      record_ref: null, official_status: "PENDING", verdict: null, assessments: [], next_cursor: null, truncated: false, current_use: "UNAVAILABLE" });
    expect(parseReview(review, "m1").verdict).toBeNull();
    expect(() => parseReview({ ...review, package_ref: { kind: "artifact", pin: { id: "p", revision: 1, content_hash: HASH } } }, "m1")).toThrow();
    expect(() => parseReview({ ...review, current_use: "NOT_APPLICABLE" }, "m1")).toThrow();
    const use = envelope(request, { subject_ref: { kind: "result", pin: { id: "r", revision: 0, content_hash: HASH } }, purpose: "PLAN",
      decision: "UNAVAILABLE", diagnostic_only: true, coverage: "INCOMPLETE", reason_codes: [], checked_at_ms: 1, expires_at_ms: null, certificate_ref: null });
    expect(parseUseCheck(use, "m1").decision).toBe("UNAVAILABLE");
    expect(() => parseUseCheck({ ...use, diagnostic_only: false }, "m1")).toThrow();
    expect(() => parseUseCheck({ ...use, decision: "GRANTED" }, "m1")).toThrow();
    expect(parseError({ schema_version: 1, request_id: "r", code: "LIMIT_REACHED", message: "", retryable: false }).code).toBe("LIMIT_REACHED");
    expect(() => parseError({ schema_version: 1, request_id: "r", code: "OTHER", message: "", retryable: false })).toThrow();
  });
});
