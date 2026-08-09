// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Rename-topic pure helpers — trim/clamp + display-label fallback. The inline
 * edit UI in MessagePanelRoot is driven by these; testing them directly (no
 * DOM) matches the project's "export pure helpers for vitest" convention and
 * covers the rename edge cases.
 */
import { describe, expect, it } from "vitest";

import { normalizeTopicTitle, topicDisplayLabel } from "./topicTitle";

describe("normalizeTopicTitle", () => {
  it("trims surrounding whitespace", () => {
    expect(normalizeTopicTitle("  晚安  ")).toBe("晚安");
  });

  it("whitespace-only collapses to empty (clears the custom title)", () => {
    expect(normalizeTopicTitle("   ")).toBe("");
    expect(normalizeTopicTitle("")).toBe("");
  });

  it("clamps to 80 chars", () => {
    expect(normalizeTopicTitle("a".repeat(200))).toBe("a".repeat(80));
  });

  it("trims first, then clamps", () => {
    expect(normalizeTopicTitle("  " + "b".repeat(200) + "  ")).toBe("b".repeat(80));
  });

  it("tolerates non-string-ish input without throwing", () => {
    expect(normalizeTopicTitle(undefined as unknown as string)).toBe("");
  });
});

describe("topicDisplayLabel", () => {
  it("custom title wins over preview", () => {
    expect(
      topicDisplayLabel({
        title: "我的话题",
        preview: "晚安",
        session_id: "task-1",
      }),
    ).toBe("我的话题");
  });

  it("falls back to preview when no custom title", () => {
    expect(
      topicDisplayLabel({ preview: "晚安", session_id: "task-1" }),
    ).toBe("晚安");
  });

  it("whitespace-only title is ignored (falls back)", () => {
    expect(
      topicDisplayLabel({
        title: "   ",
        preview: "晚安",
        session_id: "task-1",
      }),
    ).toBe("晚安");
  });

  it("falls back to session_id when neither title nor preview", () => {
    expect(
      topicDisplayLabel({ preview: "", session_id: "task-9" }),
    ).toBe("task-9");
  });

  it("曾经的保留 id 不再有特殊标签（无 title/preview 时退回 sid）", () => {
    expect(topicDisplayLabel({ session_id: "default" })).toBe("default");
  });
});
