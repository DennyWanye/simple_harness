import { describe, expect, it } from "vitest";

import {
  isWorkflowLifecycleOnlyMessage,
  workflowFinalAssistant,
} from "./workflowFinalAssistant";

describe("workflowFinalAssistant", () => {
  it("extracts the durable final report from a workflow websocket event", () => {
    expect(workflowFinalAssistant({
      type: "workflow_event",
      payload: {
        event_id: "event-1",
        event_type: "workflow.final_assistant",
        payload: { payload: { text: "# Report\n\nResult" } },
      },
    })).toEqual({ eventId: "event-1", text: "# Report\n\nResult" });
  });

  it("ignores progress and empty assistant projections", () => {
    expect(workflowFinalAssistant({
      type: "workflow_event",
      payload: { event_type: "workflow.progress", payload: {} },
    })).toBeNull();
    expect(workflowFinalAssistant({
      type: "workflow_event",
      payload: {
        event_type: "workflow.final_assistant",
        payload: { payload: { text: " " } },
      },
    })).toBeNull();
  });

  it("hides lifecycle-only history rows but keeps final assistant rows", () => {
    expect(isWorkflowLifecycleOnlyMessage({
      workflow_event: { event_type: "workflow.final" },
    })).toBe(true);
    expect(isWorkflowLifecycleOnlyMessage({
      workflow_event: { event_type: "workflow.final_assistant" },
    })).toBe(false);
  });
});
