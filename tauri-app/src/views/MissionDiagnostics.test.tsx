// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ConnectionState } from "../ws/ControlChannel";
import { MissionDiagnostics } from "./MissionDiagnostics";

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
  reply(request: ControlMessage, data: unknown, ok = true) {
    this.emit(`${request.type}_response`, { request_id: request.request_id, ok, data, error: "late failure" });
  }
  reconnect() { act(() => this.states.forEach((fn) => fn("disconnected"))); }
}
const report = (mission_id = "m1") => ({
  mission_id, replay: { events: 12, comparison: { consistent: true, not_covered: ["execution"], mismatches: [] }, failure_timeline: [] },
  attribution: { mission_status: "FAILED", attempts: [{ attempt_id: "a1", task_id: "t1", role: "worker", model: "deepseek-flash", status: "FAILED", on_success_path: false, work: { tokens: 50 }, verification: { tokens: 30 } }], cost: { total: { tokens: 80, cost_micros: null } } },
});
const read = () => fireEvent.click(screen.getByRole("button", { name: "查看回放与贡献" }));
const exportReport = () => fireEvent.click(screen.getByRole("button", { name: "生成脱敏支持报告" }));
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("P36 selected Mission diagnostics", () => {
  it("reads only on demand, retains failed/unpriced/coverage facts, and exports no caller destination", () => {
    const channel = new Channel(); render(<MissionDiagnostics missionId="m1" channel={channel} />);
    expect(channel.sent).toEqual([]); read();
    expect(channel.last().payload).toEqual({ mission_id: "m1" });
    channel.reply(channel.last(), report());
    expect(screen.getByText(/任务状态：FAILED/)).toBeTruthy();
    expect(screen.getByText(/未覆盖字段 1 项/)).toBeTruthy();
    expect(screen.getByText(/金额：未计价/)).toBeTruthy();
    expect(screen.getByText(/记录不完整/)).toBeTruthy();
    expect(screen.getByText(/未知用量记录（已入账）：未知/)).toBeTruthy();
    expect(screen.getByText(/探索或未采用/)).toBeTruthy();
    expect(screen.getByText(/执行 50 tokens · 验证 30 tokens/)).toBeTruthy();
    exportReport(); expect(channel.last().type).toBe("mission_support_export");
    expect(channel.last().payload).toEqual({ mission_id: "m1" });
    channel.reply(channel.last(), { mission_id: "m1", path: "/local/support/report.json", sha256: "receipt-hash", size_bytes: 10 });
    expect(screen.getByText(/已保存：\/local\/support\/report.json/)).toBeTruthy();
    expect(channel.sent.map((m) => m.type)).toEqual(["mission_diagnostics", "mission_support_export"]);
  });
  it("ignores foreign responses, duplicates and late errors", () => {
    const channel = new Channel(); render(<MissionDiagnostics missionId="m1" channel={channel} />); read();
    const request = channel.last(); channel.reply(request, report("m2"));
    expect(screen.queryByText(/任务状态：FAILED/)).toBeNull();
    channel.reply(request, report()); channel.reply(request, null, false);
    expect(screen.getByText(/任务状态：FAILED/)).toBeTruthy(); expect(screen.queryByRole("alert")).toBeNull();
  });
  it("invalidates pending reads and receipts on Mission changes", () => {
    const channel = new Channel(); const view = render(<MissionDiagnostics missionId="m1" channel={channel} />); read();
    const old = channel.last(); view.rerender(<MissionDiagnostics missionId="m2" channel={channel} />);
    channel.reply(old, report()); channel.reply(old, null, false);
    expect(screen.queryByText(/任务状态/)).toBeNull(); expect(screen.queryByRole("alert")).toBeNull();
    exportReport(); channel.reply(channel.last(), { mission_id: "m2", path: "/m2.json", sha256: "m2", size_bytes: 20 });
    view.rerender(<MissionDiagnostics missionId="m1" channel={channel} />);
    expect(screen.queryByText(/已保存/)).toBeNull();
  });
  it("invalidates a prior socket read after reconnect or channel replacement", () => {
    const channel = new Channel(); const view = render(<MissionDiagnostics missionId="m1" channel={channel} />); read();
    const old = channel.last(); channel.reconnect(); channel.reply(old, report());
    expect(screen.queryByText(/任务状态/)).toBeNull(); read();
    const second = channel.last(); const replacement = new Channel(); view.rerender(<MissionDiagnostics missionId="m1" channel={replacement} />);
    channel.reply(second, report()); expect(screen.queryByText(/任务状态/)).toBeNull();
    read(); replacement.reply(replacement.last(), report()); expect(screen.getByText(/任务状态：FAILED/)).toBeTruthy();
  });
  it("permits bounded retry after send failure and timeout without retrying the Mission", () => {
    vi.useFakeTimers(); const channel = new Channel(); channel.available = false;
    render(<MissionDiagnostics missionId="m1" channel={channel} />); read(); expect(screen.getByRole("alert").textContent).toContain("未发送");
    channel.available = true; read(); const expired = channel.last();
    act(() => vi.advanceTimersByTime(30000)); expect(screen.getByRole("alert").textContent).toContain("读取超时");
    channel.reply(expired, report()); expect(screen.queryByText(/任务状态/)).toBeNull();
    read(); channel.reply(channel.last(), report()); expect(screen.queryByRole("alert")).toBeNull();
    expect(channel.sent.every((request) => request.type === "mission_diagnostics")).toBe(true);
  });
  it("marks a selected Mission snapshot stale without making an automatic read", () => {
    const channel = new Channel(); render(<MissionDiagnostics missionId="m1" channel={channel} />); read(); channel.reply(channel.last(), report());
    channel.emit("mission_changed", { mission_id: "m2" }); expect(screen.queryByText(/任务已有新事件/)).toBeNull();
    channel.emit("mission_changed", { mission_id: "m1" }); expect(screen.getByText(/任务已有新事件/)).toBeTruthy();
    expect(channel.sent).toHaveLength(1);
  });
  it("shows known zero reservations separately from settled charges and an unknown usage row", () => {
    const channel = new Channel(); render(<MissionDiagnostics missionId="m1" channel={channel} />); read();
    const data = report();
    channel.reply(channel.last(), { ...data, costs: { usage: { reserved_tokens: 0 } },
      attribution: { ...data.attribution, cost: { ...data.attribution.cost, reconciled: true, unknown_usage_rows: 1, ledger: { unsettled_usage_tokens: 12 } } } });
    expect(screen.getByText(/预留：0 tokens · 未知用量记录（已入账）：1 · 账本核对：一致/)).toBeTruthy();
    expect(screen.getByText(/待结算用量：12 tokens。记录不完整/)).toBeTruthy();
  });
  it("does not claim freshness when an event arrives while the snapshot response is pending", () => {
    const channel = new Channel(); render(<MissionDiagnostics missionId="m1" channel={channel} />); read();
    channel.emit("mission_changed", { mission_id: "m1", last_seq: 13 });
    channel.reply(channel.last(), report()); expect(screen.getByText(/任务已有新事件/)).toBeTruthy();
    read(); channel.reply(channel.last(), report()); expect(screen.queryByText(/任务已有新事件/)).toBeNull();
  });
  it.each([[4000, 0, true], [0, 12, true], [0, 0, false]])(
    "retains incomplete accounting with reserved=%s and unsettled=%s",
    (reserved, unsettled, incomplete) => {
      const channel = new Channel(); render(<MissionDiagnostics missionId="m1" channel={channel} />); read();
      const data = report();
      channel.reply(channel.last(), { ...data, costs: { usage: { reserved_tokens: reserved } },
        attribution: { ...data.attribution, cost: { ...data.attribution.cost, reconciled: true, unknown_usage_rows: 0, ledger: { unsettled_usage_tokens: unsettled } } } });
      expect(Boolean(screen.queryByText(/记录不完整/))).toBe(incomplete);
      expect(screen.getByText(/不包含尚未入账的在途调用/)).toBeTruthy();
    },
  );
});
