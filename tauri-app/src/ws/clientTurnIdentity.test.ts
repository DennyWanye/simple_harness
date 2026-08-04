// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import { withClientTurnIdentity } from "./clientTurnIdentity";

describe("withClientTurnIdentity", () => {
  it("adds non-empty request and turn ids to chat_v2", () => {
    const identified = withClientTurnIdentity({
      type: "chat_v2",
      payload: { text: "hello", session_id: "default" },
    });

    expect(identified.payload?.request_id).toMatch(/^request-/);
    expect(identified.payload?.turn_id).toMatch(/^turn-/);
  });

  it("preserves ids when the same message is prepared for retry", () => {
    const first = withClientTurnIdentity({
      type: "chat_v2",
      payload: { text: "retry", session_id: "default" },
    });
    const retried = withClientTurnIdentity(first);

    expect(retried).toBe(first);
    expect(retried.payload?.request_id).toBe(first.payload?.request_id);
    expect(retried.payload?.turn_id).toBe(first.payload?.turn_id);
  });

  it("does not alter non-chat control messages", () => {
    const message = { type: "ping" };
    expect(withClientTurnIdentity(message)).toBe(message);
  });
});
