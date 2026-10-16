// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useEffect, useEffectEvent, useRef, useState, useCallback } from "react";
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
  const channelRef = useRef<ControlChannel | null>(null);
  const [state, setState] = useState<ConnectionState>("disconnected");
  const handleMessage = useEffectEvent((message: IncomingMessage) => onMessage?.(message));

  useEffect(() => {
    const channel = new ControlChannel(port, secret);
    channelRef.current = channel;

    const unsubState = channel.onStateChange(setState);
    const unsubMsg = channel.onMessage((message) => handleMessage(message));

    channel.connect();

    return () => {
      unsubState();
      unsubMsg();
      channel.disconnect();
      channelRef.current = null;
    };
  }, [port, secret]);

  const sendChat = useCallback((text: string) => {
    channelRef.current?.sendChat(text);
  }, []);

  const sendChatV2 = useCallback((text: string) => {
    channelRef.current?.sendChatV2(text);
  }, []);

  const sendInterrupt = useCallback(() => {
    channelRef.current?.sendInterrupt();
  }, []);

  // Expose the underlying channel so feature panels (memory management, etc.)
  // can attach their own listeners / send custom messages without reaching
  // into the control-channel transport directly.
  const getChannel = useCallback(() => channelRef.current, []);

  return { state, sendChat, sendChatV2, sendInterrupt, getChannel };
}
