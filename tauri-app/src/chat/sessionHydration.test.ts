// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import { sessionHydrationCommands } from "./sessionHydration";

describe("sessionHydrationCommands", () => {
  it("restores messages, durable Run projections and context together", () => {
    expect(sessionHydrationCommands("session-history")).toEqual([
      {
        type: "session_messages_load",
        payload: { session_id: "session-history", limit: 200 },
      },
      {
        type: "task_projections_list",
        payload: { session_id: "session-history" },
      },
      {
        type: "context_usage_request",
        payload: { session_id: "session-history" },
      },
      {
        type: "session_provider_get",
        payload: { session_id: "session-history" },
      },
    ]);
  });
});
