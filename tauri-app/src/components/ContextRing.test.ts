// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { ContextRing, ringColor, ringPercent } from "./ContextRing";
import type { ContextUsageSnapshot } from "../stores/sessionsStore";

function snap(overrides: Partial<ContextUsageSnapshot> = {}): ContextUsageSnapshot {
  return {
    schema_version: 2,
    session_id: "default",
    source: "measured",
    sample_id: "sample-1",
    snapshot_id: "snapshot-1",
    version: 1,
    availability: "available",
    has_measurement: true,
    model: "deepseek-v4-pro",
    prompt_tokens: 0,
    completion_tokens: 0,
    cached_tokens: 0,
    context_window: 1_000_000,
    effective_ceiling: 950_000,
    compact_at: 750_000,
    recall_sweet: 384_000,
    updated_at: 0,
    ...overrides,
  };
}

afterEach(cleanup);

describe("ringPercent", () => {
  it("returns 0 for null / undefined / zero ceiling", () => {
    expect(ringPercent(null)).toBe(0);
    expect(ringPercent(undefined)).toBe(0);
    expect(ringPercent(snap({ effective_ceiling: 0 }))).toBe(0);
    expect(ringPercent(snap({
      source: "binding_only",
      has_measurement: false,
      prompt_tokens: 99,
    }))).toBe(0);
  });

  it("computes percentage of effective_ceiling", () => {
    expect(ringPercent(snap({ prompt_tokens: 0 }))).toBe(0);
    expect(ringPercent(snap({ prompt_tokens: 95_000 }))).toBeCloseTo(10, 1);
    expect(ringPercent(snap({ prompt_tokens: 475_000 }))).toBeCloseTo(50, 1);
    expect(ringPercent(snap({ prompt_tokens: 950_000 }))).toBe(100);
  });

  it("clamps to [0, 100]", () => {
    expect(ringPercent(snap({ prompt_tokens: -100 }))).toBe(0);
    expect(ringPercent(snap({ prompt_tokens: 2_000_000 }))).toBe(100);
  });
});

describe("ringColor", () => {
  it("green below 50%", () => {
    expect(ringColor(0)).toBe("#10b981");
    expect(ringColor(49.9)).toBe("#10b981");
  });

  it("amber at 50% to 80%", () => {
    expect(ringColor(50)).toBe("#f59e0b");
    expect(ringColor(79.9)).toBe("#f59e0b");
  });

  it("orange at 80% to 95%", () => {
    expect(ringColor(80)).toBe("#ea580c");
    expect(ringColor(94.9)).toBe("#ea580c");
  });

  it("red at 95% and above", () => {
    expect(ringColor(95)).toBe("#dc2626");
    expect(ringColor(100)).toBe("#dc2626");
  });
});

describe("ContextRing authority states", () => {
  it("renders unavailable as unavailable rather than a numeric zero", () => {
    render(React.createElement(ContextRing, { showLabel: true, snapshot: snap({
      source: "binding_only",
      availability: "unavailable",
      has_measurement: false,
      prompt_tokens: 0,
      effective_ceiling: 0,
    }) }));
    expect(screen.getByTestId("context-ring").getAttribute("aria-label"))
      .toBe("Context usage unavailable");
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText("0%")).toBeNull();
  });
});
