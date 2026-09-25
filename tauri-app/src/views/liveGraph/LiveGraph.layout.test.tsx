// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../../types/messages";
import { useMissionsStore } from "../../stores/missionsStore";
import { LiveGraph } from "./LiveGraph";

const layoutCalls: { resolve: (value: unknown) => void; graph: unknown }[] = [];
vi.mock("./layout", () => ({
  layoutGraph: (graph: unknown) => new Promise((resolve) => { layoutCalls.push({ resolve, graph }); }),
}));

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  private readonly listeners = new Set<(message: IncomingMessage) => void>();
  send = (message: ControlMessage) => { this.sent.push(message); return true; };
  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  emit(message: unknown) {
    act(() => { for (const listener of [...this.listeners]) listener(message as IncomingMessage); });
  }
  all(type: string): ControlMessage[] { return this.sent.filter((m) => m.type === type); }
  reply(type: string, data: unknown, ok = true) {
    const request = [...this.sent].reverse().find((m) => m.type === type);
    this.emit({ type: `${type}_response`, payload: { request_id: request?.request_id, ok, data, error: ok ? undefined : "读不到" } });
  }
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => { cleanup(); vi.useRealTimers(); useMissionsStore.getState().reset(); layoutCalls.length = 0; });

const M = "mission-1";
function graph(extra: Record<string, unknown> = {}) {
  return {
    schema_version: 1, mission_id: M, source: "htn", plan_revision: 1, through_seq: 10, revisions: [1],
    nodes: [
      { occurrence_id: "root", task_id: "task-root", form: "compound", parent: null, method: null, task_status: "ACTIVE",
        phase: "waiting_children", readiness_reason: "WAITING_ORDER", attempt_count: 0, last_event_at: null },
      { occurrence_id: "a", task_id: "task-a", form: "primitive", parent: "root", method: "synth@1", task_status: "COMPLETED",
        phase: null, readiness_reason: null, attempt_count: 1, last_event_at: 5 },
      { occurrence_id: "b", task_id: "task-b", form: "primitive", parent: "root", method: "synth@1", task_status: "ACTIVE",
        phase: null, readiness_reason: null, attempt_count: 2, last_event_at: 9 },
    ],
    edges: [{ kind: "order", source: "a", target: "b" }],
    ...extra,
  };
}
const DETAIL = {
  mission: { id: M, goal: { text: "写一份调研报告", source: "human" }, status: "ACTIVE" },
  tasks: [{ id: "task-a", goal: { text: "收集资料", source: "model" } }, { id: "task-b", goal: { text: "写第一章", source: "model" } }],
  attempts: [{ id: "att-1", task_id: "task-b", status: "RUNNING", model: "deepseek-flash", ordinal: 0 }],
  results: [{ task_id: "task-b", attempt_id: "att-1", verification_layers: [{ layer: "format_check", status: "PASS", summary: { text: "格式正确" } }] }],
};

function mount(detail: Record<string, unknown> = DETAIL) {
  const channel = new FakeChannel();
  render(<LiveGraph missionId={M} channel={channel} detail={detail} />);
  return channel;
}

describe("排版不被状态更新打断", () => {
  it("排版进行中再收到一次重读（同一版本），不重新排版，结果照样用上", async () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply("mission_live_graph", graph());
    expect(layoutCalls).toHaveLength(1);
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 11 } });
    act(() => { vi.advanceTimersByTime(800); });
    const newer = graph({ through_seq: 11 });
    (newer.nodes[2] as Record<string, unknown>).task_status = "VERIFYING";
    channel.reply("mission_live_graph", newer);
    expect(layoutCalls).toHaveLength(1);
    vi.useRealTimers(); // elk schedules its own work
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const output = await new ELK().layout(layoutCalls[0].graph as any);
    await act(async () => { layoutCalls[0].resolve(output); });
    expect(screen.getByTestId("lg-node-b").textContent).toContain("验证中");
  });

  it("新版本才重新排版", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply("mission_live_graph", graph());
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 11 } });
    act(() => { vi.advanceTimersByTime(800); });
    channel.reply("mission_live_graph", graph({ through_seq: 11, plan_revision: 2, revisions: [1, 2] }));
    expect(layoutCalls).toHaveLength(2);
  });
});
