// @vitest-environment jsdom
// 第 2 批 H11（2026-10-06）：设置页「任务全局预算」——所有任务合计的 token 上限与用量，只读说明，不改值。

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GlobalBudgetSection, formatTokens } from "./SettingsPanel";
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

const status = (pool: Record<string, unknown>) => ({ available: true, state: "available", reason: null, global_budget: pool });

describe("GlobalBudgetSection", () => {
  afterEach(cleanup);

  it("formats token counts in 万 / 亿", () => {
    expect(formatTokens(2_000_000_000)).toBe("20 亿");
    expect(formatTokens(20_000_000)).toBe("2000 万");
    expect(formatTokens(1_310_000)).toBe("131 万");
    expect(formatTokens(950)).toBe("950");
  });

  it("asks for the orchestration status once and explains the pool before any task opened it", async () => {
    const channel = new FakeControlChannel();
    render(<GlobalBudgetSection getChannel={() => channel as unknown as ControlChannel} />);
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    expect(channel.sent[0].type).toBe("orchestration_status");
    const id = String(channel.sent[0].request_id);
    act(() => { channel.emit({ type: "orchestration_status_response", request_id: id, payload: { ok: true, request_id: id,
      data: status({ max_tokens: 2_000_000_000, opened: false, reserved_tokens: 0, settled_tokens: 0, remaining_tokens: 2_000_000_000 }) } } as unknown as IncomingMessage); });
    const text = screen.getByTestId("global-budget-status").textContent ?? "";
    expect(text).toContain("上限 20 亿");
    expect(text).toContain("还没有任务用过");
    expect(screen.getByTestId("global-budget").textContent).toContain("config.toml");
    expect(screen.getByTestId("global-budget").textContent).toContain("global_max_tokens");
  });

  it("shows used and remaining once tasks have drawn on it", async () => {
    const channel = new FakeControlChannel();
    render(<GlobalBudgetSection getChannel={() => channel as unknown as ControlChannel} />);
    await waitFor(() => expect(channel.sent).toHaveLength(1));
    const id = String(channel.sent[0].request_id);
    act(() => { channel.emit({ type: "orchestration_status_response", request_id: id, payload: { ok: true, request_id: id,
      data: status({ max_tokens: 2_000_000_000, opened: true, reserved_tokens: 3_000_000, settled_tokens: 1_310_000, remaining_tokens: 1_995_690_000 }) } } as unknown as IncomingMessage); });
    const text = screen.getByTestId("global-budget-status").textContent ?? "";
    expect(text).toContain("已用 131 万");
    expect(text).toContain("预留中 300 万");
    expect(text).toContain("剩余 19.96 亿");
  });
});
