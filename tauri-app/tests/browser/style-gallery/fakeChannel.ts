// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 画廊用的假控制通道：send 返回 true，回包在微任务里发出；onMessage/onStateChange 返回退订函数；state 恒为 connected。
// responder 按收到的请求返回零到多条回包（固定夹具），返回 null/undefined 表示不回。
import type { ControlChannel } from "../../../src/ws/ControlChannel";

export type Wire = { type?: string; request_id?: string; operation?: string; payload?: unknown; request?: unknown; [k: string]: unknown };
export type Responder = (msg: Wire) => unknown[] | unknown | null | undefined;

export interface FakeChannel {
  channel: ControlChannel;
  emit: (msg: unknown) => void;
}

export function makeFakeChannel(responder: Responder = () => null, latest: Record<string, unknown> = {}): FakeChannel {
  const listeners = new Set<(value: unknown) => void>();
  const emit = (value: unknown) => listeners.forEach((fn) => fn(value));
  const fake = {
    state: "connected",
    getLatestMessage: (type: string) => latest[type] ?? null,
    onMessage(fn: (value: unknown) => void) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    onStateChange() {
      return () => undefined;
    },
    hasServerFeature: () => true,
    getServerHello: () => ({ version: "v2.0", features: [] }),
    connect() {},
    disconnect() {},
    sendChat: () => true,
    sendInterrupt: () => true,
    send(msg: Wire) {
      const out = responder(msg);
      const list = out == null ? [] : Array.isArray(out) ? out : [out];
      if (list.length) queueMicrotask(() => list.forEach(emit));
      return true;
    },
  };
  return { channel: fake as unknown as ControlChannel, emit };
}

/** 主对话控制面（human_memory_request 风格）的成功回包。 */
export function okReply(msg: Wire, result: unknown) {
  return { type: "human_memory_response", request_id: msg.request_id, payload: { ok: true, operation: msg.operation, result } };
}
