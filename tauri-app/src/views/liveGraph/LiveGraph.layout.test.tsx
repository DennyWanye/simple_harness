// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../../types/messages";
import { useMissionsStore } from "../../stores/missionsStore";
import { LiveGraph } from "./LiveGraph";
import { M, snapshot } from "./fixture";

const layoutCalls: { resolve: (value: unknown) => void; graph: unknown }[] = [];
vi.mock("./layout", () => ({
  layoutGraph: (graph: unknown) => new Promise((resolve) => { layoutCalls.push({ resolve, graph }); }),
}));

const SNAP = "taskgraph.execution_snapshot";

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
  reply(data: unknown) {
    const request = [...this.sent].reverse().find((m) => m.type === SNAP);
    this.emit({ type: `${SNAP}_response`, payload: { request_id: request?.request_id, ok: true, data } });
  }
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => { cleanup(); vi.useRealTimers(); useMissionsStore.getState().reset(); layoutCalls.length = 0; });

function mount() {
  const channel = new FakeChannel();
  render(<LiveGraph missionId={M} channel={channel} detail={{ mission: { id: M, goal: "g", status: "ACTIVE" } }} />);
  return channel;
}

function reread(channel: FakeChannel, data: unknown) {
  channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 11 } });
  act(() => { vi.advanceTimersByTime(800); });
  channel.reply(data);
}

describe("排版只跟结构走", () => {
  it("只有状态变了（同样的节点）：不重新排版，文字照样更新", async () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply(snapshot());
    expect(layoutCalls).toHaveLength(1);
    reread(channel, snapshot({ execution_cut: { execution_hash: "h1", coverage: "COMPLETE" } }, { phases: { b: "VERIFYING" } }));
    expect(layoutCalls).toHaveLength(1);
    vi.useRealTimers(); // elk schedules its own work
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const output = await new ELK().layout(layoutCalls[0].graph as any);
    await act(async () => { layoutCalls[0].resolve(output); });
    expect(screen.getByTestId("lg-node-b").textContent).toContain("验收中");
  });

  it("执行过程多了一个节点才重新排版", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply(snapshot());
    const base = snapshot();
    const nodes = [...(base.execution_nodes as Record<string, unknown>[]),
      { node_id: "check:r-b2", kind: "check", at_ms: 5000, result_id: "r-b2", attempt_id: "b2", verdict: null, layers: [], turn: null, summary: null }];
    const edges = [...(base.execution_edges as Record<string, unknown>[]),
      { kind: "review_of", source: "check:r-b2", target: "attempt:b2", target_layer: "execution" }];
    reread(channel, snapshot({ execution_cut: { execution_hash: "h2", coverage: "COMPLETE" } }, { nodes, edges }));
    expect(layoutCalls).toHaveLength(2);
  });

  it("执行过程与步骤状态都没变：连画面都不换", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply(snapshot());
    const before = screen.getByText("全部步骤（3）");
    reread(channel, snapshot({ read_token: { plan_revision: 1, through_seq: 99, validity_epochs: [], snapshot_hash: "x", manifest_hash: "m" } }));
    expect(screen.getByText("全部步骤（3）")).toBe(before);
    expect(layoutCalls).toHaveLength(1);
  });
});
