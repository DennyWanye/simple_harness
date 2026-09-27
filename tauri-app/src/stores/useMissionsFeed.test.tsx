// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * useMissionsFeed 测试（代码评审第 1 轮 P2-5）：常驻订阅与视图是否打开无关；
 * 启动拉状态与列表，`mission_changed` 推送节流（≤1 次/秒）重拉列表，侧栏角标随之更新。
 */
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ConnectionState } from "../ws/ControlChannel";
import { Sidebar } from "../components/Sidebar";
import { useMissionsStore } from "./missionsStore";
import { useMissionsFeed } from "./useMissionsFeed";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  readonly listeners = new Set<(message: IncomingMessage) => void>();
  readonly states = new Set<(state: ConnectionState) => void>();
  onStateChange = (listener: (state: ConnectionState) => void) => { this.states.add(listener); return () => { this.states.delete(listener); }; };
  state(state: ConnectionState) { act(() => { for (const listener of this.states) listener(state); }); }

  send = (message: ControlMessage) => {
    this.sent.push(message);
    return true;
  };

  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  emit(message: unknown) {
    act(() => {
      for (const listener of [...this.listeners]) listener(message as IncomingMessage);
    });
  }

  count(type: string): number {
    return this.sent.filter((m) => m.type === type).length;
  }

  reply(type: string, data: unknown, ok = true) {
    const request = [...this.sent].reverse().find((m) => m.type === type);
    this.emit({ type: `${type}_response`, payload: { request_id: request?.request_id, ok, data } });
  }
}

const Feed: React.FC<{ channel: FakeChannel | null }> = ({ channel }) => {
  useMissionsFeed(channel);
  return null;
};

const ROW = {
  id: "mission-1",
  mission_id: "mission-1",
  goal: "写 NOTES.md",
  status: "ACTIVE",
  stop_reason: null,
  created_at: 1789000000,
  pending_approvals: 2,
  blocked: false,
  ui_state: "waiting_person",
};

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  useMissionsStore.getState().reset();
});

describe("useMissionsFeed（P2-5）", () => {
  it("reconnects the same channel, refreshes status/list and ignores prior list responses", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    const old = channel.sent.find((m) => m.type === "mission_list")!;
    channel.state("disconnected");
    channel.state("connected");
    expect(channel.count("orchestration_status")).toBe(2);
    expect(channel.count("mission_list")).toBe(2);
    channel.reply("mission_list", { missions: [{ ...ROW, goal: "fresh" }] });
    channel.emit({ type: "mission_list_response", payload: { request_id: old.request_id, ok: true, data: { missions: [{ ...ROW, goal: "old" }] } } });
    expect(useMissionsStore.getState().missions[0].goal).toBe("fresh");
  });
  it("挂载时发 orchestration_status 与 mission_list（带信封 request_id）", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    expect(channel.count("orchestration_status")).toBe(1);
    expect(channel.count("mission_list")).toBe(1);
    expect(typeof channel.sent[0].request_id).toBe("string");
  });

  it("处理状态与列表应答，写进 store", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    channel.reply("orchestration_status", { available: true, state: "available", reason: null });
    channel.reply("mission_list", { missions: [ROW] });
    const state = useMissionsStore.getState();
    expect(state.status?.available).toBe(true);
    expect(state.missions[0]).toMatchObject({ id: "mission-1", ui_state: "waiting_person", pending_approvals: 2 });
  });

  it("mission_changed：applyChange，并节流重拉列表（≤1 次/秒）", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    channel.reply("mission_list", { missions: [ROW] });
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "COMPLETED", last_seq: 9 } });
    expect(useMissionsStore.getState().missions[0].status).toBe("COMPLETED");
    expect(useMissionsStore.getState().lastSeq["mission-1"]).toBe(9);
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "COMPLETED", last_seq: 10 } });
    expect(channel.count("mission_list")).toBe(1); // still inside the first second
    act(() => vi.advanceTimersByTime(999));
    expect(channel.count("mission_list")).toBe(1);
    act(() => vi.advanceTimersByTime(1));
    expect(channel.count("mission_list")).toBe(2); // one trailing refresh for both pushes
    act(() => vi.advanceTimersByTime(5000));
    expect(channel.count("mission_list")).toBe(2); // nothing pending, nothing sent
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "COMPLETED", last_seq: 11 } });
    expect(channel.count("mission_list")).toBe(3); // quiet for > 1 s: sent at once
  });

  it("推送没改任何任务状态时，列表最多每 5 秒重拉一次；状态变了照旧 1 秒内", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    channel.reply("mission_list", { missions: [ROW] });
    act(() => vi.advanceTimersByTime(1500));
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "ACTIVE", last_seq: 5 } });
    expect(channel.count("mission_list")).toBe(1); // same status: waits for the quiet interval
    act(() => vi.advanceTimersByTime(3000));
    expect(channel.count("mission_list")).toBe(1);
    act(() => vi.advanceTimersByTime(600));
    expect(channel.count("mission_list")).toBe(2); // 5 s after the previous list
    act(() => vi.advanceTimersByTime(1500));
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "COMPLETED", last_seq: 6 } });
    expect(channel.count("mission_list")).toBe(3); // a status change is urgent
    act(() => vi.advanceTimersByTime(1500));
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-2", status: "CREATED", last_seq: 1 } });
    expect(channel.count("mission_list")).toBe(4); // a Mission the list does not have yet
  });

  it("慢定时器等待中来了状态变化：不等 5 秒，按 1 秒节奏立刻重拉", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    channel.reply("mission_list", { missions: [ROW] });
    act(() => vi.advanceTimersByTime(1500));
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "ACTIVE", last_seq: 5 } });
    expect(channel.count("mission_list")).toBe(1); // quiet push: slow timer armed
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "COMPLETED", last_seq: 6 } });
    expect(channel.count("mission_list")).toBe(2); // status moved: sent now, not at 5 s
    act(() => vi.advanceTimersByTime(5000));
    expect(channel.count("mission_list")).toBe(2); // the slow timer was replaced, nothing extra
  });

  it("channel 为 null 时什么都不发；连上后再发", () => {
    const { rerender } = render(<Feed channel={null} />);
    const channel = new FakeChannel();
    rerender(<Feed channel={channel} />);
    expect(channel.count("orchestration_status")).toBe(1);
    expect(channel.count("mission_list")).toBe(1);
  });

  it("卸载时退订，并取消待发的列表重拉", () => {
    const channel = new FakeChannel();
    const { unmount } = render(<Feed channel={channel} />);
    channel.reply("mission_list", { missions: [ROW] });
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "ACTIVE", last_seq: 3 } });
    unmount();
    expect(channel.listeners.size).toBe(0);
    act(() => vi.advanceTimersByTime(5000));
    expect(channel.count("mission_list")).toBe(1);
  });

  it("侧栏角标（待审批总数）随推送后的列表重拉实时更新，与编排视图是否打开无关", () => {
    const channel = new FakeChannel();
    render(
      <>
        <Feed channel={channel} />
        <Sidebar view="chat" onViewChange={() => undefined} />
      </>,
    );
    channel.reply("mission_list", { missions: [ROW] });
    expect(screen.getByTestId("nav-missions-badge").textContent).toBe("2");
    channel.emit({ type: "mission_changed", payload: { mission_id: "mission-1", status: "ACTIVE", last_seq: 4 } });
    act(() => vi.advanceTimersByTime(1000));
    expect(channel.count("mission_list")).toBe(2);
    channel.reply("mission_list", { missions: [{ ...ROW, pending_approvals: 0, ui_state: "running" }] });
    expect(screen.queryByTestId("nav-missions-badge")).toBeNull();
  });
});
