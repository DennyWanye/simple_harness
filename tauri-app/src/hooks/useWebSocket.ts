// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useEffectEvent, useMemo, useState } from "react";
import { ControlChannel, type ConnectionState } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";

/**
 * 控制通道。每条消息都在通道回调里交给 onMessage（读最新一次渲染的处理函数），
 * 不经"最后一条消息"状态中转，同一批到达的多条消息不会丢。
 */
export function useControlChannel(
  port: number = 8100,
  secret: string = "",
  onMessage?: (message: IncomingMessage) => void,
) {
  // 端口或密钥变了就换一个通道对象（构造不连网；连接与断开在 effect 里）。
  // getChannel 随通道变：按 getChannel 订阅的面板会跟着换到新通道上。
  const channel = useMemo(() => new ControlChannel(port, secret), [port, secret]);
  const [state, setState] = useState<ConnectionState>("disconnected");
  const handleMessage = useEffectEvent((message: IncomingMessage) => onMessage?.(message));

  useEffect(() => {
    const unsubState = channel.onStateChange(setState);
    const unsubMsg = channel.onMessage((message) => handleMessage(message));

    channel.connect();

    return () => {
      unsubState();
      unsubMsg();
      channel.disconnect();
    };
  }, [channel]);

  const sendChat = useCallback(
    (text: string) => {
      channel.sendChat(text);
    },
    [channel],
  );

  const sendChatV2 = useCallback(
    (text: string) => {
      channel.sendChatV2(text);
    },
    [channel],
  );

  const sendInterrupt = useCallback(() => {
    channel.sendInterrupt();
  }, [channel]);

  // Expose the underlying channel so feature panels (memory management, etc.)
  // can attach their own listeners / send custom messages without reaching
  // into the control-channel transport directly.
  const getChannel = useCallback(() => channel, [channel]);

  return { state, sendChat, sendChatV2, sendInterrupt, getChannel };
}
