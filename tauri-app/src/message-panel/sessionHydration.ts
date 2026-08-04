// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

export type SessionHydrationCommand = {
  type:
    | "session_messages_load"
    | "task_projections_list"
    | "context_usage_request"
    | "session_provider_get";
  payload: {
    session_id: string;
    limit?: number;
  };
};

/**
 * A restored topic has three independent read models. Keep them together so
 * the message stream, Harness Run selector and context ring cannot disagree.
 */
export function sessionHydrationCommands(
  sessionId: string,
): SessionHydrationCommand[] {
  return [
    {
      type: "session_messages_load",
      payload: { session_id: sessionId, limit: 200 },
    },
    {
      type: "task_projections_list",
      payload: { session_id: sessionId },
    },
    {
      type: "context_usage_request",
      payload: { session_id: sessionId },
    },
    {
      type: "session_provider_get",
      payload: { session_id: sessionId },
    },
  ];
}
