// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * T8（WB-4，挑战轮 P1）— controlWS（chat_v2 实际通道）连接状态的响应式
 * 订阅。controlWS 是模块单例、state() 为拉取式快照，无状态变更事件，
 * 因此用「轮询 + 消息活动即时纠偏」两路合一：任何入站消息都意味着已
 * 连接，断线只能靠轮询兜底（1s 粒度对状态条/徽章足够）。
 *
 * ChatView 的连接/错误显示与 Sidebar 底部徽章聚合（T13）共用此 hook，
 * 避免「徽章已连接、发送却拒发」的分叉（连接状态源=controlWS.state()）。
 */
import { useEffect, useState } from "react";

import { controlWS } from "../code-panel/controlWs";

export type ControlWsState = "disconnected" | "connecting" | "connected";

export function useControlWsState(pollMs = 1000): ControlWsState {
  const [state, setState] = useState<ControlWsState>(() => controlWS.state());
  useEffect(() => {
    const sync = () => setState(controlWS.state());
    sync();
    const timer = window.setInterval(sync, pollMs);
    const off = controlWS.on_message(sync);
    return () => {
      window.clearInterval(timer);
      off();
    };
  }, [pollMs]);
  return state;
}

/**
 * 双源聚合取最差态（T8/T13 口径）：ControlChannel（identity_bind）与
 * controlWS（companion_action）任一未连接，整体就不算已连接。
 */
export function worstConnectionState(
  a: ControlWsState,
  b: ControlWsState,
): ControlWsState {
  const rank: Record<ControlWsState, number> = {
    disconnected: 0,
    connecting: 1,
    connected: 2,
  };
  return rank[a] <= rank[b] ? a : b;
}
