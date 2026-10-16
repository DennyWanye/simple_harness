// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ConnectionState } from "./ControlChannel";

interface ReconnectingChannel {
  onStateChange?(fn: (state: ConnectionState) => void): () => void;
}

/**
 * 读取类请求在 effect 里用：先试着发；没发出去（通道没连上）就等下次连上再发一次。
 * 结果都走消息回调，effect 里不必同步写状态。返回取消等待的函数。
 */
export function sendWhenConnected(channel: ReconnectingChannel, send: () => boolean): () => void {
  if (send()) return () => undefined;
  let sent = false;
  const off = channel.onStateChange?.((state) => {
    if (sent || state !== "connected") return;
    sent = send();
  });
  return () => off?.();
}
