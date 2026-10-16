// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IncomingMessage } from "../types/messages";

const channels: FakeChannel[] = [];

class FakeChannel {
  listeners = new Set<(message: IncomingMessage) => void>();
  constructor() {
    channels.push(this);
  }
  onMessage(fn: (message: IncomingMessage) => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  onStateChange() {
    return () => undefined;
  }
  connected = 0;
  disconnected = 0;
  connect() {
    this.connected += 1;
  }
  disconnect() {
    this.disconnected += 1;
  }
  emit(message: IncomingMessage) {
    this.listeners.forEach((fn) => fn(message));
  }
}

vi.mock("../ws/ControlChannel", () => ({ ControlChannel: FakeChannel }));

const { useControlChannel } = await import("./useWebSocket");

afterEach(() => {
  channels.length = 0;
});

const message = (type: string) => ({ type, payload: {} }) as unknown as IncomingMessage;

describe("useControlChannel", () => {
  it("同一批到达的多条消息逐条交给处理函数，不丢、按顺序", () => {
    const seen: string[] = [];
    renderHook(() => useControlChannel(8100, "s", (m) => seen.push(m.type)));
    act(() => {
      channels[0].emit(message("chat_v2_run_started"));
      channels[0].emit(message("chat_v2_final"));
    });
    expect(seen).toEqual(["chat_v2_run_started", "chat_v2_final"]);
  });

  it("重渲染换了处理函数后，后来的消息交给新的处理函数，不重建通道", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ handler }) => useControlChannel(8100, "s", handler), {
      initialProps: { handler: first },
    });
    rerender({ handler: second });
    act(() => channels[0].emit(message("chat_response")));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
    expect(channels).toHaveLength(1);
  });

  it("密钥变了换新通道：getChannel 跟着变，旧通道断开，新通道连接", () => {
    const { result, rerender } = renderHook(({ secret }) => useControlChannel(8100, secret), {
      initialProps: { secret: "" },
    });
    const firstGetter = result.current.getChannel;
    const first = firstGetter();
    rerender({ secret: "s3cret" });
    // 订阅方按 getChannel 的身份重订阅，所以它必须跟着变。
    expect(result.current.getChannel).not.toBe(firstGetter);
    const second = result.current.getChannel();
    expect(second).not.toBe(first);
    expect(channels.indexOf(first as unknown as FakeChannel)).toBeGreaterThanOrEqual(0);
    expect((first as unknown as FakeChannel).disconnected).toBe(1);
    expect((second as unknown as FakeChannel).connected).toBe(1);
  });
});
