// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import {
  buildSetModelMessage,
  buildSetProviderMessage,
} from "./sessionModelMessages";

describe("session model messages", () => {
  it("sets or clears a provider for an ordinary session", () => {
    expect(buildSetProviderMessage("task-1", "relay")).toEqual({
      type: "session_set_provider",
      payload: { session_id: "task-1", provider_id: "relay" },
    });
    expect(buildSetProviderMessage("task-1", null)).toEqual({
      type: "session_set_provider",
      payload: { session_id: "task-1", provider_id: null },
    });
  });

  it("normalizes an empty model and preserves supported params", () => {
    expect(
      buildSetModelMessage("task-2", "  ", {
        thinking: true,
        context: "1m",
        effort: "high",
      }),
    ).toEqual({
      type: "session_set_model",
      payload: {
        session_id: "task-2",
        model: null,
        params: { thinking: true, context: "1m", effort: "high" },
      },
    });
  });
});
