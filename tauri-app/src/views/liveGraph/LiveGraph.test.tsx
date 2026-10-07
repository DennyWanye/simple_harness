// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../../types/messages";
import type { ConnectionState } from "../../ws/ControlChannel";
import { useMissionsStore } from "../../stores/missionsStore";
import { LiveGraph } from "./LiveGraph";
import { M, snapshot } from "./fixture";
import { PROTOCOL_ERROR_TEXT, guardIncoming } from "../../ws/orchestrationContracts";

const SNAP = "taskgraph.execution_snapshot";
const DETAIL = "taskgraph.execution_detail";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  private readonly stateListeners = new Set<(state: ConnectionState) => void>();
  onStateChange = (listener: (state: ConnectionState) => void) => { this.stateListeners.add(listener); return () => { this.stateListeners.delete(listener); }; };
  setState(state: ConnectionState) { act(() => { for (const l of [...this.stateListeners]) l(state); }); }
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
  reply(type: string, data: unknown, ok = true, code = "") {
    const request = [...this.sent].reverse().find((m) => m.type === type);
    this.emit({ type: `${type}_response`, payload: { request_id: request?.request_id, ok, data,
      error_code: ok ? undefined : code, error: ok ? undefined : "读不到" } });
  }
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => { cleanup(); vi.useRealTimers(); useMissionsStore.getState().reset(); });

const MISSION_DETAIL = {
  mission: { id: M, goal: { text: "写说明并发布", source: "human" }, status: "ACTIVE" },
  tasks: [{ id: "task-a", goal: { text: "整个任务目标" } }],
};

function mount() {
  const channel = new FakeChannel();
  render(<LiveGraph missionId={M} channel={channel} detail={MISSION_DETAIL} />);
  return channel;
}

describe("LiveGraph（SDK 执行过程接口）", () => {
  it("打开就读一次 SDK 执行过程；步骤标题用方法步骤职责，根用任务目标；不再用直读表的旧动词", async () => {
    const channel = mount();
    expect(channel.all(SNAP)).toHaveLength(1);
    expect(channel.all(SNAP)[0].payload).toEqual({ mission_id: M });
    channel.reply(SNAP, snapshot());
    const list = screen.getByText("全部步骤（3）").closest("details")!;
    expect(within(list).getByText("写说明并发布")).toBeTruthy();
    expect(list.textContent).toContain("notes：产出 NOTES.md · 完成");
    expect(list.textContent).toContain("发布：产出 NOTES.md · 执行中");
    expect(channel.all("mission_live_graph")).toHaveLength(0);
    await waitFor(() => expect(screen.getByTestId("lg-exec-attempt:b1")).toBeTruthy());
    expect(screen.getByTestId("lg-exec-check:r-b1").textContent).toContain("不通过");
    expect(screen.getByTestId("lg-node-b").textContent).toContain("执行 2 次");
  });

  it("分页读完才换画面；翻页期间执行过程变了就从第一页重读一次", () => {
    const channel = mount();
    const full = snapshot();
    const nodes = full.execution_nodes as unknown[];
    channel.reply(SNAP, { ...full, execution_nodes: nodes.slice(0, 4), complete: false, next_cursor: "c1" });
    expect(channel.all(SNAP)).toHaveLength(2);
    expect(channel.all(SNAP)[1].payload).toEqual({ mission_id: M, cursor: "c1" });
    expect(screen.queryByText("全部步骤（3）")).toBeNull();
    channel.reply(SNAP, null, false, "SNAPSHOT_CHANGED");
    expect(channel.all(SNAP)).toHaveLength(3);
    expect(channel.all(SNAP)[2].payload).toEqual({ mission_id: M });
    channel.reply(SNAP, { ...full, execution_nodes: nodes.slice(0, 4), complete: false, next_cursor: "c2" });
    channel.reply(SNAP, { ...full, graph: null, occurrence_labels: null, execution_nodes: nodes.slice(4), next_cursor: null, complete: true });
    expect(screen.getByText("全部步骤（3）")).toBeTruthy();
  });

  it("时间线：执行过程按时间排开，点开读这一回合，显示模型原话、工具与提交", () => {
    const channel = mount();
    channel.reply(SNAP, snapshot());
    fireEvent.click(screen.getByRole("tab", { name: "时间线" }));
    const timeline = screen.getByRole("list", { name: "执行过程时间线" });
    const rows = within(timeline).getAllByRole("button");
    expect(rows[0].textContent).toContain("规划：拆分");
    expect(rows.map((r) => r.textContent).join("|")).toContain("第 1 次执行 · 未通过");
    fireEvent.click(within(timeline).getByText(/第 1 次执行 · 未通过/));
    expect(channel.all(DETAIL)).toHaveLength(1);
    expect(channel.all(DETAIL)[0].payload).toEqual({ mission_id: M, node_id: "attempt:b1" });
    channel.reply(DETAIL, { mission_id: M, node: {}, turn: { coverage: "COMPLETE" }, hidden_items: 0, items: [
      { t: "say", text: "我先读上一步的 NOTES.md" },
      { t: "tool", tool: "workspace_read_file", ok: true, path: "NOTES.md", chars: 1200 },
      { t: "tool", tool: "workspace_write_file", ok: false, error: "路径越界" },
      { t: "submit", outcome: "candidate", text: "写了发布候选" },
    ] });
    const detail = screen.getByTestId("lg-detail-attempt:b1");
    expect(detail.textContent).toContain("我先读上一步的 NOTES.md");
    expect(detail.textContent).toContain("workspace_read_file：NOTES.md · 1200 字");
    expect(detail.textContent).toContain("路径越界");
    expect(detail.textContent).toContain("提交（candidate）：写了发布候选");
  });

  it("没有执行图的旧任务：说清楚原因，不伪造图", () => {
    const channel = mount();
    channel.reply(SNAP, null, false, "NOT_ENABLED");
    expect(screen.getByRole("alert").textContent).toContain("创建于执行图启用之前");
    expect(screen.queryByTestId("lg-node-root")).toBeNull();
  });

  it("推送到了 800ms 防抖后重读；读不到时保留旧画面并标明可能过期", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply(SNAP, snapshot());
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 11 } });
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 12 } });
    expect(channel.all(SNAP)).toHaveLength(1);
    act(() => { vi.advanceTimersByTime(800); });
    expect(channel.all(SNAP)).toHaveLength(2);
    channel.reply(SNAP, null, false, "SOURCE_UNAVAILABLE");
    expect(screen.getByRole("alert").textContent).toContain("可能已过期");
    expect(screen.getByText("全部步骤（3）")).toBeTruthy();
  });

  it("点步骤看它的职责和全部执行过程", () => {
    const channel = mount();
    channel.reply(SNAP, snapshot());
    const list = screen.getByText("全部步骤（3）").closest("details")!;
    fireEvent.click(within(list).getByText("发布：产出 NOTES.md"));
    const panel = screen.getByTestId("lg-panel");
    expect(panel.textContent).toContain("把 NOTES.md 发布到授权目录");
    expect(panel.textContent).toContain("执行过程（5）");
    expect(panel.textContent).toContain("修补请求");
  });

  it("点开还没开工的步骤，读 SDK 的「为什么还不开工」并说人话", () => {
    const channel = mount();
    channel.reply(SNAP, snapshot({}, { phases: { b: "PENDING" } }));
    const list = screen.getByText("全部步骤（3）").closest("details")!;
    fireEvent.click(within(list).getByText("发布：产出 NOTES.md"));
    const [why] = channel.all("taskgraph.why_not_ready");
    expect(why.payload).toEqual({ mission_id: M, occurrence_id: "b" });
    channel.reply("taskgraph.why_not_ready", { reason_codes: ["WAITING_ORDER"], details: ["phase=PENDING", "上一步还没交付"] });
    const box = screen.getByTestId("lg-why-not-ready");
    expect(box.textContent).toContain("等上一步完成");
    expect(box.textContent).toContain("上一步还没交付");
    expect(box.textContent).not.toContain("phase=");
  });

  it("已完成的步骤详情不再显示「还没开始」的原因", () => {
    const channel = mount();
    channel.reply(SNAP, snapshot());
    const list = screen.getByText("全部步骤（3）").closest("details")!;
    fireEvent.click(within(list).getAllByRole("button")[1]);
    const panel = screen.getByTestId("lg-panel");
    expect(panel.textContent).toContain("完成");
    expect(panel.textContent).not.toContain("未选入执行");
  });

  it("断线时保留画面并提示；重连后自动重读", () => {
    const channel = new FakeChannel();
    render(<LiveGraph missionId={M} channel={channel} detail={MISSION_DETAIL} />);
    channel.reply(SNAP, snapshot());
    channel.setState("disconnected");
    expect(screen.getByText(/连接断开了/)).toBeTruthy();
    expect(screen.getByText("全部步骤（3）")).toBeTruthy();
    channel.setState("connected");
    expect(channel.all(SNAP)).toHaveLength(2);
  });

  it("U09：执行图与回合详情收到坏消息——保留上次画面并标已过期，回合详情显示协议错那句话", () => {
    vi.useFakeTimers();
    const channel = mount();
    channel.reply(SNAP, snapshot());
    expect(screen.getByText("全部步骤（3）")).toBeTruthy();
    channel.emit({ type: "mission_changed", payload: { mission_id: M, status: "ACTIVE", last_seq: 2 } });
    act(() => { vi.advanceTimersByTime(5000); });
    const again = channel.all(SNAP).at(-1)!;
    channel.emit(guardIncoming({ type: SNAP + "_response", payload: { request_id: again.request_id, ok: true,
      data: { ...snapshot(), debug_row: 1 } } }));
    expect(screen.getByRole("alert").textContent).toBe(PROTOCOL_ERROR_TEXT + "（下面是上次读到的画面，可能已过期）");
    expect(screen.getByText("全部步骤（3）")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "时间线" }));
    fireEvent.click(within(screen.getByRole("list", { name: "执行过程时间线" })).getByText(/第 1 次执行 · 未通过/));
    const ask = channel.all(DETAIL).at(-1)!;
    channel.emit(guardIncoming({ type: DETAIL + "_response", payload: { request_id: ask.request_id, ok: true,
      data: { mission_id: M, node: {}, items: [] } } }));
    expect(screen.getByTestId("lg-detail-attempt:b1").textContent).toContain(PROTOCOL_ERROR_TEXT);
  });
});
