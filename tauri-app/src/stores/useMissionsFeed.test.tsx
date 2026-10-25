// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * useMissionsFeed 测试（代码评审第 1 轮 P2-5）：常驻订阅与视图是否打开无关；
 * 启动拉状态与列表，`session_event` 推送节流（≤1 次/秒）重拉列表，侧栏角标随之更新。
 */
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ConnectionState } from "../ws/ControlChannel";
import { useMissionsStore } from "./missionsStore";
import { useMissionsFeed } from "./useMissionsFeed";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  readonly listeners = new Set<(message: IncomingMessage) => void>();
  readonly states = new Set<(state: ConnectionState) => void>();
  onStateChange = (listener: (state: ConnectionState) => void) => {
    this.states.add(listener);
    return () => {
      this.states.delete(listener);
    };
  };
  state(state: ConnectionState) {
    act(() => {
      for (const listener of this.states) listener(state);
    });
  }

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
  it("task-page-only missions refresh without a Session delegation event", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    channel.reply("mission_list", { missions: [ROW] });
    act(() => vi.advanceTimersByTime(15000));
    expect(channel.count("mission_list")).toBe(2);
  });
  it("uses Session progress only to refresh the authoritative Host list", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    channel.reply("mission_list", { missions: [ROW] });
    channel.emit({
      type: "session_event",
      payload: {
        schema_version: 1,
        session_id: "desktop-main",
        seq: 2,
        kind: "result_ready",
        mission_id: "mission-1",
        payload: { status: "COMPLETED" },
      },
    });
    expect(useMissionsStore.getState().missions[0].status).toBe("ACTIVE");
    act(() => vi.advanceTimersByTime(1000));
    expect(channel.count("mission_list")).toBe(2);
    channel.reply("mission_list", { missions: [{ ...ROW, status: "COMPLETED" }] });
    expect(useMissionsStore.getState().missions[0].status).toBe("COMPLETED");
  });

  it("text previews never cause task-list reads", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    for (let seq = 1; seq <= 30; seq++)
      channel.emit({
        type: "session_event",
        payload: {
          schema_version: 1,
          session_id: "desktop-main",
          seq,
          kind: "text_delta",
          payload: { text: "x" },
        },
      });
    act(() => vi.advanceTimersByTime(5000));
    expect(channel.count("mission_list")).toBe(1);
  });
  it("reconnects the same channel, refreshes status/list and ignores prior list responses", () => {
    const channel = new FakeChannel();
    render(<Feed channel={channel} />);
    const old = channel.sent.find((m) => m.type === "mission_list")!;
    channel.state("disconnected");
    channel.state("connected");
    expect(channel.count("orchestration_status")).toBe(2);
    expect(channel.count("mission_list")).toBe(2);
    channel.reply("mission_list", { missions: [{ ...ROW, goal: "fresh" }] });
    channel.emit({
      type: "mission_list_response",
      payload: {
        request_id: old.request_id,
        ok: true,
        data: { missions: [{ ...ROW, goal: "old" }] },
      },
    });
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
    expect(state.missions[0]).toMatchObject({
      id: "mission-1",
      ui_state: "waiting_person",
      pending_approvals: 2,
    });
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
    channel.emit({
      type: "session_event",
      payload: { mission_id: "mission-1", status: "ACTIVE", last_seq: 3 },
    });
    unmount();
    expect(channel.listeners.size).toBe(0);
    act(() => vi.advanceTimersByTime(5000));
    expect(channel.count("mission_list")).toBe(1);
  });
});
