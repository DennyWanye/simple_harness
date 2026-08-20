import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { PublicRunSnapshotV3 } from "../../types/messages";
import { buildActivityTimeline } from "../AgentActivityMessage";
import { ActivityTimeline } from "./ActivityTimeline";

afterEach(cleanup);

function snapshot(overrides: Partial<PublicRunSnapshotV3> = {}): PublicRunSnapshotV3 {
  return {
    schema_version: "3",
    projection_id: "projection-activity",
    session_id: "session-1",
    root_run_id: "root-1",
    aggregate_outcome: {
      status: "completed",
      explanation_code: "root_completed",
      evidence_refs: [],
      child_warnings: [],
      ended_at: 5,
    },
    semantic_phases: [],
    tool_public_views: [],
    public_messages: [],
    activity_items: [],
    totals: { workflow_facts: 0, content_facts: 0, provider_details: 0, tool_details: 0 },
    projection_complete: true,
    diagnostics: [],
    ...overrides,
  };
}

describe("Agent activity timeline", () => {
  it("deduplicates, orders, bounds text, and settles late open items", () => {
    const value = snapshot({
      aggregate_outcome: {
        status: "completed",
        explanation_code: "root_completed",
        evidence_refs: [],
        child_warnings: [],
        ended_at: 5,
      },
      activity_items: [
        {
          stable_id: "tool-2",
          kind: "tool",
          title: "读取文件",
          status: "running",
          created_at: 3,
          public_result: "x".repeat(3000),
          context_visibility: "exclude",
        },
        {
          stable_id: "tool-1",
          kind: "tool",
          title: "检查文件",
          status: "completed",
          created_at: 2,
          context_visibility: "exclude",
        },
        {
          stable_id: "tool-1",
          kind: "tool",
          title: "重复记录",
          status: "failed",
          created_at: 1,
          context_visibility: "exclude",
        },
      ],
    });
    const items = buildActivityTimeline(value);
    expect(items.map((item) => item.id)).toEqual(["tool-1", "tool-2", "run-terminal:root-1"]);
    expect(items[0].status).toBe("completed");
    expect(items[1].status).toBe("completed");
    expect(items[1].truncated).toBe(true);
    expect(items.every((item) => item.contextVisibility === "exclude")).toBe(true);
  });

  it("renders collapsed rows and keeps sensitive transport fields out of the DOM", () => {
    render(<ActivityTimeline snapshot={snapshot({
      activity_items: [{
        stable_id: "tool-1",
        kind: "tool",
        title: "执行检查",
        status: "completed",
        detail_ref: "tool-1",
        public_input: { path: "public.txt" },
        public_result: { summary: "通过" },
        context_visibility: "exclude",
      }],
    })} />);
    expect(screen.getByTestId("agent-activity-timeline")).toBeTruthy();
    expect(screen.getByText("执行检查")).toBeTruthy();
    expect(screen.queryByText("reasoning_content")).toBeNull();
    fireEvent.click(screen.getByText("执行检查"));
    expect(screen.getByText("输入详情")).toBeTruthy();
    fireEvent.click(screen.getByText("输入详情"));
    expect(screen.getByText(/public.txt/)).toBeTruthy();
  });
});
