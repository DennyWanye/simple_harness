// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ControlMessage } from "../types/messages";

export interface ClientTurnIdentity {
  request_id: string;
  turn_id: string;
}

function randomId(prefix: "request" | "turn"): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `${prefix}-${uuid}`;
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function createClientTurnIdentity(): ClientTurnIdentity {
  return {
    request_id: randomId("request"),
    turn_id: randomId("turn"),
  };
}

/**
 * Freeze one client identity onto a chat message before it reaches a transport.
 * Reconnect queues retain the resulting serialized frame, so a transport retry
 * reuses the exact request/turn ids instead of creating a second Run.
 */
export function withClientTurnIdentity(message: ControlMessage): ControlMessage {
  if (message.type !== "chat_v2") return message;

  const payload = message.payload ?? {};
  const requestId = String(payload.request_id ?? message.request_id ?? "").trim();
  const turnId = String(payload.turn_id ?? "").trim();
  if (requestId && turnId) return message;

  const generated = createClientTurnIdentity();
  return {
    ...message,
    payload: {
      ...payload,
      request_id: requestId || generated.request_id,
      turn_id: turnId || generated.turn_id,
    },
  };
}
