// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 受保护核心文件的会话内申请（plans/2026-09-26-permission-open-by-default）。
 *
 * 运行从不等这张卡片：模型已经收到"已申请"的说明并继续做别的事。用户点允许后，
 * 模型再调用一次即可通过。所以这里只是一个不挡操作的队列，没有超时也不会卡住任何东西。
 */
import { useCallback, useEffect, useState } from "react";
import type { ControlMessage } from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";

export type ProtectedPathRequest = {
  request_id: string; session_id: string; path: string; op: "read" | "write"; tool: string; reason: string;
};
export type ProtectedPathDecision = "allow_once" | "allow_session" | "deny";

export function useProtectedPathRequests(channel: Pick<ControlChannel, "send" | "onMessage"> | null) {
  const [queue, setQueue] = useState<ProtectedPathRequest[]>([]);

  useEffect(() => {
    if (!channel) return undefined;
    return channel.onMessage((message) => {
      const raw = message as unknown as { type?: string; payload?: Partial<ProtectedPathRequest> };
      if (raw.type !== "protected_path_request") return;
      const p = raw.payload;
      if (!p?.request_id || !p.path) return;
      const request: ProtectedPathRequest = {
        request_id: p.request_id, session_id: p.session_id ?? "", path: p.path,
        op: p.op === "read" ? "read" : "write", tool: p.tool ?? "", reason: p.reason ?? "",
      };
      setQueue((current) => current.some((r) => r.request_id === request.request_id) ? current : [...current, request]);
    });
  }, [channel]);

  const decide = useCallback((requestId: string, decision: ProtectedPathDecision) => {
    channel?.send({ type: "protected_path_decision", payload: { request_id: requestId, decision } } as unknown as ControlMessage);
    setQueue((current) => current.filter((r) => r.request_id !== requestId));
  }, [channel]);

  return { queue, decide } as const;
}
