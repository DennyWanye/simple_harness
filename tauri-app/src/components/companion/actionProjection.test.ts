// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import { shouldRefreshCompanionProjection } from "./actionProjection";

describe("Companion action projection recovery", () => {
  it("refreshes the durable card only for an advanced detail fence", () => {
    expect(shouldRefreshCompanionProjection({
      type: "companion_control_rechallenge",
      payload: { code: "companion_detail_changed", retryable: true },
    })).toBe(true);
    expect(shouldRefreshCompanionProjection({
      type: "companion_control_rechallenge",
      payload: { code: "companion_window_scope_mismatch", retryable: true },
    })).toBe(false);
    expect(shouldRefreshCompanionProjection(null)).toBe(false);
  });
});
