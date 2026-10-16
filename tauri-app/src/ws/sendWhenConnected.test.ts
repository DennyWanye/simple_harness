// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it, vi } from "vitest";

import type { ConnectionState } from "./ControlChannel";
import { sendWhenConnected } from "./sendWhenConnected";

function channel(initial: ConnectionState) {
  const listeners = new Set<(state: ConnectionState) => void>();
  return {
    state: initial,
    onStateChange(fn: (state: ConnectionState) => void) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    move(state: ConnectionState) {
      this.state = state;
      listeners.forEach((fn) => fn(state));
    },
    listeners,
  };
}

describe("sendWhenConnected", () => {
  it("已连上立即发一次，不再等", () => {
    const ch = channel("connected");
    const send = vi.fn(() => true);
    sendWhenConnected(ch, send);
    ch.move("connected");
    expect(send).toHaveBeenCalledTimes(1);
    expect(ch.listeners.size).toBe(0);
  });

  it("没连上就等连上再发一次；之后重连不重发", () => {
    const ch = channel("connecting");
    const send = vi.fn(() => true);
    sendWhenConnected(ch, send);
    expect(send).not.toHaveBeenCalled();
    ch.move("connected");
    ch.move("disconnected");
    ch.move("connected");
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("连上时这次没发出去，下次连上再试", () => {
    const ch = channel("connected");
    const send = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
    sendWhenConnected(ch, send);
    ch.move("connected");
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("取消后连上也不发", () => {
    const ch = channel("disconnected");
    const send = vi.fn(() => true);
    const cancel = sendWhenConnected(ch, send);
    cancel();
    ch.move("connected");
    expect(send).not.toHaveBeenCalled();
  });
});
