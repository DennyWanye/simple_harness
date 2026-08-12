import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { PublicRunSnapshotV3 } from "../types/messages";
import {
  buildAgentActivityTrace,
  buildAgentPublicOutputs,
  buildWorkflowTaskTraces,
} from "./AgentActivityMessage";
import { DurableTaskSteps } from "./workflow/DurableTaskSteps";

afterEach(cleanup);

function snapshot(overrides: Partial<PublicRunSnapshotV3> = {}): PublicRunSnapshotV3 {
  return {
    schema_version: "3",
    projection_id: "projection-1",
    session_id: "session-1",
    root_run_id: "root-1",
    aggregate_outcome: {
      status: "completed_with_recovery",
      explanation_code: "child_recovered",
      evidence_refs: ["root:final"],
      child_warnings: [{ run_id: "child-1", status: "failed", evidence_refs: ["child:failed"] }],
    },
    semantic_phases: [{
      phase_id: "execute",
      taxonomy: "execute",
      title: "创建项目",
      status: "completed_with_recovery",
      mapping_reason: "tool_spec",
      evidence_refs: [],
      tool_refs: ["detail-1"],
      child_refs: ["child-1"],
      workflow_steps: [],
      items: [],
      order_key: [3, 0, 0, "execute"],
    }],
    tool_public_views: [{
      tool_ref: "detail-1",
      stable_id: "tool-1",
      phase_id: "execute",
      public_name: "文件工具",
      action_label: "写入文件",
      safe_target_label: "project.godot",
      status: "completed",
      public_input: { target: "project.godot" },
      public_result: { summary: "已写入" },
      details_available: true,
      result_available: true,
      truncated: false,
      created_at: 2,
    }],
    public_messages: [{
      message_id: "message-1",
      stable_id: "message-1",
      phase_id: "execute",
      text: "我会先创建项目骨架。",
      created_at: 1,
    }],
    totals: { workflow_facts: 5, content_facts: 1, provider_details: 0, tool_details: 1 },
    projection_complete: true,
    diagnostics: [],
    ...overrides,
  };
}

describe("public Agent activity projection", () => {
  it("uses only server-approved narration with stable IDs", () => {
    expect(buildAgentPublicOutputs(snapshot())).toEqual([{
      id: "message-1",
      text: "我会先创建项目骨架。",
      ts: 1000,
    }]);
    expect(buildAgentActivityTrace(snapshot())).toHaveLength(1);
  });

  it("binds tools and narration to the same semantic phase", () => {
    const [trace] = buildWorkflowTaskTraces(snapshot());
    expect(trace.runId).toBe("root-1");
    expect(trace.status).toBe("completed_with_recovery");
    expect(trace.startedAt).toBe(1000);
    expect(trace.endedAt).toBe(2000);
    expect(trace.steps).toHaveLength(1);
    expect(trace.steps[0]).toMatchObject({
      id: "execute",
      title: "创建项目",
      status: "completed_with_recovery",
      messages: ["我会先创建项目骨架。"],
    });
    expect(trace.steps[0].tools[0]).toMatchObject({
      id: "tool-1",
      action: "写入文件",
      target: "project.godot",
      resultAvailable: true,
    });
  });

  it("deduplicates a stable item across the left and right consumers by identity", () => {
    const value = snapshot({
      public_messages: [
        snapshot().public_messages[0],
        { ...snapshot().public_messages[0] },
      ],
    });
    expect(buildAgentPublicOutputs(value)).toHaveLength(1);
  });

  it("settles open steps and tools when the Root is already cancelled", () => {
    const value = snapshot({
      aggregate_outcome: {
        status: "cancelled",
        explanation_code: "root_cancelled",
        evidence_refs: ["root:final"],
        child_warnings: [],
        started_at: 10,
        ended_at: 22.5,
      },
      semantic_phases: [{
        ...snapshot().semantic_phases[0],
        status: "cancelled",
        workflow_steps: [{
          workflow_step_id: "tool_execution",
          label: "执行命令",
          status: "running",
          step_index: 0,
          step_total: 1,
        }],
      }],
      tool_public_views: [{
        ...snapshot().tool_public_views[0],
        status: "running",
      }],
    });

    const [trace] = buildWorkflowTaskTraces(value);

    expect(trace.startedAt).toBe(10_000);
    expect(trace.endedAt).toBe(22_500);
    expect(trace.steps[0].current).toBe(false);
    expect(trace.steps[0].substeps[0].current).toBe(false);
    expect(trace.steps[0].tools[0]).toMatchObject({
      status: "cancelled",
      ok: false,
    });
  });

  it("settles stale running phases and substeps when the Root is completed", () => {
    const value = snapshot({
      aggregate_outcome: {
        status: "completed",
        explanation_code: "root_completed",
        evidence_refs: ["root:final"],
        child_warnings: [],
      },
      semantic_phases: [{
        ...snapshot().semantic_phases[0],
        status: "running",
        workflow_steps: [{
          workflow_step_id: "tool_execution",
          label: "执行命令",
          status: "running",
          step_index: 0,
          step_total: 1,
        }],
      }],
      tool_public_views: [{
        ...snapshot().tool_public_views[0],
        status: "running",
      }],
    });

    const [trace] = buildWorkflowTaskTraces(value);

    expect(trace.steps[0]).toMatchObject({ status: "completed", current: false });
    expect(trace.steps[0].substeps[0]).toMatchObject({ status: "completed", current: false });
    expect(trace.steps[0].tools[0]).toMatchObject({ status: "completed", ok: true });
    render(<DurableTaskSteps trace={trace} />);
    expect(screen.getByText("1/1 步")).toBeTruthy();
    expect(screen.getByText("步骤记录")).toBeTruthy();
    expect(screen.queryByText(/当前：/)).toBeNull();
  });

  it("deduplicates repeated workflow steps across snapshot, trace, and rendered DOM", () => {
    const value = snapshot({
      aggregate_outcome: {
        status: "running",
        explanation_code: "root_running",
        evidence_refs: [],
        child_warnings: [],
      },
      semantic_phases: [{
        ...snapshot().semantic_phases[0],
        status: "running",
        workflow_steps: [{
          workflow_step_id: "llm_proposal",
          label: "准备方案",
          status: "running",
          step_index: 0,
          step_total: 2,
        }, {
          workflow_step_id: "llm_proposal",
          label: "准备方案",
          status: "completed",
          step_index: 0,
          step_total: 2,
        }, {
          workflow_step_id: "tool_execution",
          label: "执行工具",
          status: "running",
          step_index: 1,
          step_total: 2,
        }],
      }],
    });

    const [trace] = buildWorkflowTaskTraces(value);
    expect(trace.steps[0].substeps).toHaveLength(2);
    expect(trace.steps[0].substeps[0]).toMatchObject({
      id: "llm_proposal",
      status: "completed",
    });

    render(<DurableTaskSteps trace={trace} />);
    expect(screen.getAllByText("1. 准备方案")).toHaveLength(1);
    expect(screen.getAllByText("2. 执行工具")).toHaveLength(1);
  });

  it("returns no public narration or raw details for a legacy snapshot", () => {
    const legacy = {
      schema_version: 2,
      session_id: "session-legacy",
      run: { run_id: "root-legacy", status: "cancelled" },
      provider_invocations: [{ output: { token: "SECRET_SHOULD_NOT_RENDER" } }],
      tool_calls: [{ call_id: "call-1", tool_name: "shell_command", outcome_status: "failed" }],
    };
    expect(buildAgentPublicOutputs(legacy)).toEqual([]);
    const [trace] = buildWorkflowTaskTraces(legacy);
    expect(trace.status).toBe("cancelled");
    expect(JSON.stringify(trace)).not.toContain("SECRET_SHOULD_NOT_RENDER");
    expect(trace.steps[0].tools[0].detailsAvailable).toBe(false);
  });
});
