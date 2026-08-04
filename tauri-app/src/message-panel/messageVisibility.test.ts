import { describe, expect, it } from "vitest";

import type { Message } from "../stores/sessionsStore";
import {
  DEFAULT_HIDE_TOOL_TRACE,
  isMessageVisibleForSelectedRun,
  shouldHideToolTrace,
} from "./messageVisibility";

const message = (overrides: Partial<Message>): Message => ({
  id: "m1",
  role: "tool_result",
  ts: 0,
  ...overrides,
});

describe("shouldHideToolTrace", () => {
  it("defaults the message page to showing tool traces", () => {
    expect(DEFAULT_HIDE_TOOL_TRACE).toBe(false);
  });

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

describe("isMessageVisibleForSelectedRun", () => {
  it("keeps the complete conversation timeline visible across task selections", () => {
    expect(isMessageVisibleForSelectedRun(
      message({ role: "user", run_id: "root-a" }),
      "root-b",
    )).toBe(true);
    expect(isMessageVisibleForSelectedRun(
      message({ role: "assistant", run_id: "root-a" }),
      "root-b",
    )).toBe(true);
    expect(isMessageVisibleForSelectedRun(
      message({ role: "tool_result", run_id: "root-a" }),
      "root-b",
    )).toBe(true);
  });

  it("shows only workflow projections owned by the selected root Run", () => {
    expect(isMessageVisibleForSelectedRun(
      message({ role: "workflow_progress", run_id: "root-a" }),
      "root-b",
    )).toBe(false);
    expect(isMessageVisibleForSelectedRun(
      message({ role: "workflow_progress", run_id: "root-b" }),
      "root-b",
    )).toBe(true);
    expect(isMessageVisibleForSelectedRun(
      message({ role: "workflow_stage", run_id: "root-a" }),
      "root-b",
    )).toBe(false);
  });

  it("fails closed for legacy workflow progress without a Run owner", () => {
    expect(isMessageVisibleForSelectedRun(
      message({ role: "workflow_progress", run_id: undefined }),
      "root-b",
    )).toBe(false);
  });

  it("shows all workflow projections when no Run is selected", () => {
    expect(isMessageVisibleForSelectedRun(
      message({ role: "workflow_progress", run_id: "root-a" }),
      null,
    )).toBe(true);
  });
});
