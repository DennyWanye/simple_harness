// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import type { ConnectionState } from "./ControlChannel";
import { sendWhenConnected } from "./sendWhenConnected";

function channel(initial: ConnectionState) {
  const listeners = new Set<(state: ConnectionState) => void>();
  const sent: number[] = [];
  return {
    state: initial,
    sent,
    listeners,
    // 与 ControlChannel 一样：没连上时发不出去。
    send() {
      if (this.state !== "connected") return false;
      sent.push(sent.length + 1);
      return true;
    },
    onStateChange(fn: (state: ConnectionState) => void) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    move(state: ConnectionState) {
      this.state = state;
      listeners.forEach((fn) => fn(state));
    },
  };
}

describe("sendWhenConnected", () => {
  it("已连上立即发一次，不再等", () => {
    const ch = channel("connected");
    sendWhenConnected(ch, () => ch.send());
    ch.move("connected");
    expect(ch.sent).toHaveLength(1);
    expect(ch.listeners.size).toBe(0);
  });

  it("没连上就等连上再发一次；之后重连不重发", () => {
    const ch = channel("connecting");
    sendWhenConnected(ch, () => ch.send());
    expect(ch.sent).toHaveLength(0);
    ch.move("connected");
    ch.move("disconnected");
    ch.move("connected");
    expect(ch.sent).toHaveLength(1);
  });

  it("取消后连上也不发", () => {
    const ch = channel("disconnected");
    const cancel = sendWhenConnected(ch, () => ch.send());
    cancel();
    ch.move("connected");
    expect(ch.sent).toHaveLength(0);
  });

  it("通道不支持连接状态通知时只试一次", () => {
    let attempts = 0;
    sendWhenConnected({}, () => {
      attempts += 1;
      return false;
    });
    expect(attempts).toBe(1);
  });
});
