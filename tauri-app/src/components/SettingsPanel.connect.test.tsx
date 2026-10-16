// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 设置页读取类区块：挂载时没连上就显示读取中，连上后自动发一次；没有通道直接说明。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GlobalBudgetSection, StorageUsageSection } from "./SettingsPanel";
import type { ControlMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

afterEach(cleanup);

class WaitingChannel {
  state: ConnectionState = "connecting";
  sent: ControlMessage[] = [];
  private states = new Set<(state: ConnectionState) => void>();
  send(message: ControlMessage) {
    if (this.state !== "connected") return false;
    this.sent.push(message);
    return true;
  }
  onMessage() {
    return () => undefined;
  }
  onStateChange(fn: (state: ConnectionState) => void) {
    this.states.add(fn);
    return () => this.states.delete(fn);
  }
  connect() {
    act(() => {
      this.state = "connected";
      this.states.forEach((fn) => fn("connected"));
    });
  }
}

describe("设置页读取类区块的连接时机", () => {
  it("数据占用：没连上时显示读取中，连上后发一次测量请求", () => {
    const channel = new WaitingChannel();
    render(<StorageUsageSection getChannel={() => channel as unknown as ControlChannel} />);
    expect(screen.getByTestId("storage-usage").textContent).toContain("读取中");
    expect(screen.queryByText(/连接不可用/)).toBeNull();
    expect(channel.sent).toHaveLength(0);
    channel.connect();
    expect(channel.sent.map((m) => m.type)).toEqual(["orchestration_storage_get"]);
  });

  it("全局预算：没连上时显示读取中，连上后发一次状态请求；没有通道直接说明", () => {
    const channel = new WaitingChannel();
    render(<GlobalBudgetSection getChannel={() => channel as unknown as ControlChannel} />);
    expect(screen.getByTestId("global-budget-status").textContent).toBe("读取中…");
    channel.connect();
    expect(channel.sent.map((m) => m.type)).toEqual(["orchestration_status"]);
    cleanup();

    render(<GlobalBudgetSection getChannel={() => null} />);
    expect(screen.getByTestId("global-budget-status").textContent).toBe("连接不可用");
  });
});
