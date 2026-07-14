import { describe, expect, it } from "vitest";

import type { Message } from "../stores/sessionsStore";
import { shouldHideToolTrace } from "./messageVisibility";

const message = (overrides: Partial<Message>): Message => ({
  id: "m1",
  role: "tool_result",
  ...overrides,
});

describe("shouldHideToolTrace", () => {
  it("keeps ordinary tool traces visible when the preference is off", () => {
    expect(shouldHideToolTrace(message({ tool_result: '{"ok":true}' }), false)).toBe(false);
  });

  it("hides calls and ordinary results when tool traces are hidden", () => {
    expect(shouldHideToolTrace(message({ role: "tool_call" }), true)).toBe(true);
    expect(shouldHideToolTrace(message({ tool_result: '{"ok":true}' }), true)).toBe(true);
  });

  it("keeps successful artifact deliverables visible when traces are hidden", () => {
    const toolResult = JSON.stringify({
      ok: true,
      artifacts: [{ kind: "file", path: "C:\\reports\\research.md", title: "research.md" }],
    });
    expect(shouldHideToolTrace(message({ tool_name: "artifact_create", tool_result: toolResult }), true)).toBe(false);
  });

  it("does not surface artifacts from failed tool results", () => {
    const toolResult = JSON.stringify({
      ok: false,
      artifacts: [{ kind: "file", path: "C:\\reports\\partial.md" }],
    });
    expect(shouldHideToolTrace(message({ tool_ok: false, tool_result: toolResult }), true)).toBe(true);
  });
});
