// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../../types/messages";
import { useMissionsStore } from "../../stores/missionsStore";
import { LiveGraph } from "./LiveGraph";

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
afterEach(() => { cleanup(); vi.useRealTimers(); useMissionsStore.getState().reset(); });

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

describe("LiveGraph", () => {
  it("打开就读一次运行视图；节点标题用任务内容，根节点用任务目标", async () => {
    const channel = mount();
    expect(channel.all("mission_live_graph")).toHaveLength(1);
    expect(channel.all("mission_live_graph")[0].payload).toEqual({ mission_id: M });
    channel.reply("mission_live_graph", graph());
    const list = screen.getByText("全部步骤（3）").closest("details")!;
    expect(within(list).getByText("写一份调研报告")).toBeTruthy();
    expect(within(list).getByText("收集资料")).toBeTruthy();
    expect(list.textContent).toContain("写第一章 · 运行中");
    expect(list.textContent).toContain("收集资料 · 完成");
    await waitFor(() => expect(screen.getByTestId("lg-node-b")).toBeTruthy());
  });

  it("推送到了 800ms 防抖后重读；读取在途再来推送只合并成一次后续读取", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply("mission_live_graph", graph());
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 11 } });
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 12 } });
    act(() => { vi.advanceTimersByTime(799); });
    expect(channel.all("mission_live_graph")).toHaveLength(1);
    act(() => { vi.advanceTimersByTime(1); });
    expect(channel.all("mission_live_graph")).toHaveLength(2);
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 13 } });
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 14 } });
    act(() => { vi.advanceTimersByTime(800); });
    expect(channel.all("mission_live_graph")).toHaveLength(2); // one in flight: merged
    channel.reply("mission_live_graph", graph({ through_seq: 12 }));
    expect(channel.all("mission_live_graph")).toHaveLength(3);
  });

  it("别的任务的推送不触发重读", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply("mission_live_graph", graph());
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-2", status: "ACTIVE", last_seq: 99 } });
    act(() => { vi.advanceTimersByTime(2000); });
    expect(channel.all("mission_live_graph")).toHaveLength(1);
  });

  it("旧的读取结果（through_seq 更小）不覆盖新画面", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply("mission_live_graph", graph({ through_seq: 20 }));
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 21 } });
    act(() => { vi.advanceTimersByTime(800); });
    const older = graph({ through_seq: 15 });
    (older.nodes[2] as Record<string, unknown>).task_status = "FAILED";
    channel.reply("mission_live_graph", older);
    expect(screen.getByText("全部步骤（3）").closest("details")!.textContent).toContain("写第一章 · 运行中");
  });

  it("还没有计划时显示正在规划", () => {
    const channel = mount();
    channel.reply("mission_live_graph", { ...graph(), source: "planning", plan_revision: null, nodes: [], edges: [], revisions: [] });
    expect(screen.getByText("正在规划，计划生成后这里会自动出现执行图。")).toBeTruthy();
  });

  it("读取失败显示原因", () => {
    const channel = mount();
    channel.reply("mission_live_graph", null, false);
    expect(screen.getByRole("alert").textContent).toBe("读不到");
  });

  it("点步骤看详情：尝试、验证、事件；复合任务另读规划决定", () => {
    useMissionsStore.getState().appendEvents(M, [
      { seq: 3, type: "AttemptStarted", created_at: 3, task_id: "task-b" },
      { seq: 4, type: "TaskCompleted", created_at: 4, task_id: "task-a" },
    ]);
    const channel = mount();
    channel.reply("mission_live_graph", graph());
    const list = screen.getByText("全部步骤（3）").closest("details")!;
    fireEvent.click(within(list).getByText("写第一章"));
    const panel = screen.getByTestId("lg-panel");
    expect(panel.textContent).toContain("（模型生成）");
    expect(panel.textContent).toContain("第 1 次 · RUNNING · deepseek-flash");
    expect(panel.textContent).toContain("format_check：PASS · 格式正确");
    expect(panel.textContent).toContain("AttemptStarted");
    expect(panel.textContent).not.toContain("TaskCompleted");
    expect(channel.all("mission_planning_decisions")).toHaveLength(0);

    fireEvent.click(within(list).getByText("写一份调研报告"));
    expect(channel.all("mission_planning_decisions")).toHaveLength(1);
    channel.reply("mission_planning_decisions", { mission_id: M, decisions: [
      { decision_id: "d1", decision_type: "REFINE", status: "COMMITTED", rejection_codes: [], created_at: 0, base_plan_revision: 0 },
      { decision_id: "d2", decision_type: "REPAIR", status: "REJECTED", rejection_codes: ["ORDER_CYCLE"], created_at: 0, base_plan_revision: 1 },
    ] });
    const root = screen.getByTestId("lg-panel");
    expect(root.textContent).toContain("子任务进行中 · 等待前序完成");
    expect(root.textContent).toContain("拆分 · 已提交");
    expect(root.textContent).toContain("修补计划 · 被拒绝 · 原因：ORDER_CYCLE");
  });

  it("有多个版本时可以切到历史版本，只看结构", () => {
    const channel = mount();
    channel.reply("mission_live_graph", graph({ plan_revision: 2, revisions: [1, 2] }));
    fireEvent.change(screen.getByLabelText("计划版本"), { target: { value: "1" } });
    expect(channel.all("mission_live_graph").at(-1)!.payload).toEqual({ mission_id: M, revision: 1 });
    expect(screen.getByText("历史版本，仅看结构；状态以当前为准。")).toBeTruthy();
  });

  it("运行中超过 10 分钟没动静的步骤提示可能卡住", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2_000_000 * 1000));
    const channel = mount();
    const stuck = graph();
    (stuck.nodes[2] as Record<string, unknown>).last_event_at = 2_000_000 - 700;
    channel.reply("mission_live_graph", stuck);
    expect(screen.getByText("1 个步骤可能卡住")).toBeTruthy();
  });
});

describe("还没有执行图时说清楚原因", () => {
  it("任务刚创建、等确认完成要求：不等回复就说明要先确认", () => {
    const channel = new FakeChannel();
    render(<LiveGraph missionId={M} channel={channel} detail={{ ...DETAIL, mission: { ...DETAIL.mission, status: "CREATED" } }} />);
    expect(screen.getByText(/还没开始规划：请先按上面「下一步」的提示确认完成要求/)).toBeTruthy();
  });

  it("读取超过 8 秒没回复：提示并可重试", () => {
    vi.useFakeTimers();
    const channel = mount();
    act(() => { vi.advanceTimersByTime(8000); });
    fireEvent.click(screen.getByText("重试"));
    expect(channel.all("mission_live_graph")).toHaveLength(2);
    channel.reply("mission_live_graph", graph());
    expect(screen.queryByText("重试")).toBeNull();
  });
});
