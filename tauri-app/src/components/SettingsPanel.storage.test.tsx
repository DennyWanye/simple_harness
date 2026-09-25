// @vitest-environment jsdom
// 2026-09-25 主流程优化条目 7：设置页「任务与会话数据」只显示占用，超阈值提醒，没有删除入口。

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StorageUsageSection, buildStorageUsageMessage } from "./SettingsPanel";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

class FakeControlChannel {
  state: ConnectionState = "connected";
  sent: ControlMessage[] = [];
  private listeners = new Set<(message: IncomingMessage) => void>();
  send(message: ControlMessage) { this.sent.push(message); return true; }
  onMessage(listener: (message: IncomingMessage) => void) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  onStateChange() { return () => undefined; }
  emit(message: IncomingMessage) { for (const listener of this.listeners) listener(message); }
}

const usage = (bytes: number, over: boolean) => ({
  bytes, measured_at_ms: 1, warn_bytes: 5 * 1024 ** 3, over_warn: over, measured: true,
  breakdown: [
    { key: "agent_sessions", label: "Agent 会话与检索索引", bytes: bytes - 1024 },
    { key: "assurance", label: "质量检查记录", bytes: 512 },
    { key: "orchestration", label: "任务编排记录", bytes: 512 },
  ],
});

describe("StorageUsageSection", () => {
  afterEach(cleanup);
  it("asks once on mount, shows the number and no warning under the threshold", async () => {
    const channel = new FakeControlChannel();
    render(<StorageUsageSection getChannel={() => channel as unknown as ControlChannel} />);
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    const id = String(channel.sent[0].request_id);
    expect(channel.sent[0]).toEqual(buildStorageUsageMessage(id));
    act(() => { channel.emit({ type: "orchestration_storage_get_response", request_id: id, payload: { ok: true, request_id: id, data: usage(3.2 * 1024 ** 3, false) } } as unknown as IncomingMessage); });
    expect(screen.getByTestId("storage-usage").textContent).toContain("任务与会话数据：3.2 GB");
    expect(screen.queryByTestId("storage-usage-warning")).toBeNull();
    expect(screen.getByTestId("storage-usage").textContent).not.toContain("清理");
  });

  it("warns past the threshold and re-measures on request", async () => {
    const channel = new FakeControlChannel();
    render(<StorageUsageSection getChannel={() => channel as unknown as ControlChannel} />);
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    const id = String(channel.sent[0].request_id);
    act(() => { channel.emit({ type: "orchestration_storage_get_response", request_id: id, payload: { ok: true, request_id: id, data: usage(6 * 1024 ** 3, true) } } as unknown as IncomingMessage); });
    expect(screen.getByTestId("storage-usage-warning").textContent).toContain("已超过 5.0 GB");
    fireEvent.click(screen.getByTestId("storage-usage-refresh"));
    await waitFor(() => expect(channel.sent).toHaveLength(2));
    expect(channel.sent[1]).toEqual(buildStorageUsageMessage(String(channel.sent[1].request_id), true));
  });
});
