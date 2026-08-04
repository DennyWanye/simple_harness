import { describe, expect, it } from "vitest";

import { isInternalDeepResearchAttempt } from "./subagentProgress";

describe("SubagentProgressPanel v7 filtering", () => {
  it("hides scheduler attempts owned by the v7 workflow business card", () => {
    expect(isInternalDeepResearchAttempt({
      run_id: "parent-run.dr-2.a2",
      task_id: "dr-2-a2",
      kind: "research",
      status: "completed",
      ts: 1,
    })).toBe(true);
  });

  it("keeps ordinary research subagents visible", () => {
    expect(isInternalDeepResearchAttempt({
      run_id: "standalone-research-run",
      task_id: "research-1",
      kind: "research",
      status: "running",
      ts: 1,
    })).toBe(false);
  });
});
