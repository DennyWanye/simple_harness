import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PublicRunSnapshotV3 } from "../types/messages";
import {
  harnessPublicSnapshotStore,
  normalizePublicRunSnapshot,
} from "./harnessPublicSnapshotStore";

function snapshot(
  sessionId: string,
  rootRunId: string,
  version: number,
  status: PublicRunSnapshotV3["aggregate_outcome"]["status"] = "running",
): PublicRunSnapshotV3 {
  return {
    schema_version: "3",
    projection_id: `${sessionId}:${rootRunId}:${version}:${status}`,
    session_id: sessionId,
    root_run_id: rootRunId,
    aggregate_outcome: { status, explanation_code: status, evidence_refs: [], child_warnings: [] },
    semantic_phases: [],
    tool_public_views: [],
    public_messages: [],
    totals: { workflow_facts: 0, content_facts: 0, provider_details: 0, tool_details: 0 },
    projection_complete: status !== "running",
    diagnostics: [],
    read_cut: {
      workflow: { captured_at: version, data_version: version, complete: true },
      state: { captured_at: version, data_version: version, complete: true },
      unmatched_refs: [],
    },
  };
}

beforeEach(() => harnessPublicSnapshotStore.clearForTests());

describe("HarnessPublicSnapshotStore", () => {
  it("isolates two Sessions and notifies only the matching root", () => {
    const first = vi.fn();
    const second = vi.fn();
    harnessPublicSnapshotStore.subscribe("session-a", "root", first);
    harnessPublicSnapshotStore.subscribe("session-b", "root", second);
    harnessPublicSnapshotStore.publish(snapshot("session-a", "root", 1));
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();
  });

  it("rejects an older or non-terminal late response after cancellation", () => {
    harnessPublicSnapshotStore.publish(snapshot("session-a", "root", 5, "cancelled"));
    harnessPublicSnapshotStore.publish(snapshot("session-a", "root", 4, "running"));
    harnessPublicSnapshotStore.publish(snapshot("session-a", "root", 6, "running"));
    expect(harnessPublicSnapshotStore.get("session-a", "root")?.aggregate_outcome.status).toBe("cancelled");
  });

  it("accepts a newer terminal snapshot even when SQLite data_version restarts lower", () => {
    const running = snapshot("session-a", "root", 20, "running");
    const completed = snapshot("session-a", "root", 21, "completed");
    running.read_cut!.workflow!.data_version = 50;
    running.read_cut!.state!.data_version = 50;
    completed.read_cut!.workflow!.data_version = 1;
    completed.read_cut!.state!.data_version = 1;

    harnessPublicSnapshotStore.publish(running);
    harnessPublicSnapshotStore.publish(completed);

    expect(harnessPublicSnapshotStore.get("session-a", "root")?.aggregate_outcome.status).toBe("completed");
  });

  it("does not notify either surface twice for the same projection identity", () => {
    const listener = vi.fn();
    harnessPublicSnapshotStore.subscribe("session-a", "root", listener);
    const value = snapshot("session-a", "root", 2);
    harnessPublicSnapshotStore.publish(value);
    harnessPublicSnapshotStore.publish({ ...value });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("drops raw data while adapting an unknown schema", () => {
    const normalized = normalizePublicRunSnapshot({
      schema_version: 2,
      session_id: "session-a",
      run: { run_id: "root", status: "failed" },
      provider_invocations: [{ input: "RAW_INPUT_SECRET", output: "RAW_OUTPUT_SECRET" }],
      tool_calls: [{ call_id: "call", tool_name: "shell_command", details: "RAW_TOOL_SECRET" }],
    });
    expect(normalized?.legacy_fallback).toBe(true);
    expect(JSON.stringify(normalized)).not.toMatch(/RAW_(INPUT|OUTPUT|TOOL)_SECRET/);
  });

  it("folds repeated workflow-step updates into one latest public row", () => {
    const normalized = normalizePublicRunSnapshot({
      ...snapshot("session-a", "root", 1),
      semantic_phases: [{
        phase_id: "plan",
        taxonomy: "plan",
        title: "规划",
        status: "running",
        workflow_steps: [
          { workflow_step_id: "llm_proposal", label: "模型规划", status: "running", step_index: 1 },
          { workflow_step_id: "llm_proposal", status: "completed", step_index: 1 },
        ],
      }],
    });

    expect(normalized?.semantic_phases[0].workflow_steps).toEqual([{
      workflow_step_id: "llm_proposal",
      label: "模型规划",
      status: "completed",
      step_index: 1,
      step_total: null,
    }]);
  });

  it("unwraps only the allowlisted public payload from details envelopes", () => {
    harnessPublicSnapshotStore.publish({
      ...snapshot("session-a", "root", 1),
      tool_public_views: [{
        tool_ref: "tool-stable",
        stable_id: "tool-stable",
        detail_ref: "tool-stable",
        public_name: "工具",
        action_label: "执行",
        status: "running",
        result_available: true,
        details_available: true,
      }],
    });
    const current = harnessPublicSnapshotStore.get("session-a", "root")!;
    harnessPublicSnapshotStore.mergeToolDetails("session-a", "root", current.projection_id, [{
      source: "workflow",
      stable_id: "tool-stable",
      root_run_id: "root",
      kind: "tool",
      created_at: 2,
      raw_payload: "MUST_NOT_CROSS",
      public_payload: {
        tool_name: "文件工具",
        activity_kind: "写入文件",
        status: "completed",
        safe_input: { target: "project.godot" },
        bounded_result: { summary: "已完成" },
      },
    }]);
    const updated = harnessPublicSnapshotStore.get("session-a", "root")?.tool_public_views[0];
    expect(updated).toMatchObject({
      public_name: "文件工具",
      public_input: { target: "project.godot" },
      public_result: { summary: "已完成" },
    });
    expect(JSON.stringify(updated)).not.toContain("MUST_NOT_CROSS");
  });

  it("keeps production consumers free of raw durable field access", () => {
    const files = [
      "components/AgentActivityMessage.tsx",
      "components/MessageStreamPanel.tsx",
      "components/workflow/DurableTaskSteps.tsx",
      // T9（workbench-ui）：message-panel/ 退役 —— 巡检面板迁 chat/，
      // MessagePanelRoot 由 views/ChatView 承接。
      "chat/HarnessInspectorPanel.tsx",
      "chat/HarnessRunGraph.tsx",
      "views/ChatView.tsx",
    ];
    const forbidden = [
      /\.provider_invocations\b/,
      /\.workflow_effects\b/,
      /\.prepared\b/,
      /\.outcome\b/,
      /\.details\b/,
      /buildHarnessActivityFeed\(/,
      /buildHarnessLayers\(/,
    ];
    for (const file of files) {
      const content = readFileSync(resolve("src", file), "utf8");
      forbidden.forEach((pattern) => expect(content, `${file}: ${pattern}`).not.toMatch(pattern));
    }
  });
});
