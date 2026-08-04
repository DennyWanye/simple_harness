import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { harnessPublicSnapshotStore } from "../stores/harnessPublicSnapshotStore";
import type { TaskRunProjectionState } from "../stores/sessionsStore";
import type { PublicRunSnapshotV3 } from "../types/messages";
import { HarnessInspectorPanel } from "./HarnessInspectorPanel";

const runProjection: TaskRunProjectionState = {
  run_id: "root-1",
  task_scope_id: "scope-1",
  version: 1,
  status: "running",
  inflight: true,
  ui_state: "open",
  started_at: 1,
  last_activity: 1,
};

function snapshot(status: PublicRunSnapshotV3["aggregate_outcome"]["status"] = "running"): PublicRunSnapshotV3 {
  return {
    schema_version: "3",
    projection_id: `projection-${status}`,
    session_id: "session-1",
    root_run_id: "root-1",
    aggregate_outcome: {
      status,
      explanation_code: status,
      evidence_refs: [],
      child_warnings: status === "completed_with_recovery"
        ? [{ run_id: "child-1", status: "failed", evidence_refs: [] }]
        : [],
    },
    semantic_phases: [{
      phase_id: "execute",
      taxonomy: "execute",
      title: "创建项目",
      status: status === "completed_with_recovery" ? "completed_with_recovery" : status === "completed" ? "completed" : "running",
      mapping_reason: "tool_spec",
      evidence_refs: [],
      tool_refs: ["detail-1"],
      child_refs: [],
      workflow_steps: [],
      items: [],
      current_step: 1,
      total_steps: 4,
      order_key: [3, 0, 0, "execute"],
    }],
    tool_public_views: [{
      tool_ref: "detail-1",
      stable_id: "tool-1",
      detail_ref: "detail-1",
      phase_id: "execute",
      public_name: "文件工具",
      action_label: "写入文件",
      safe_target_label: "project.godot",
      status: "running",
      details_available: true,
      result_available: true,
      truncated: false,
      created_at: 1,
    }],
    public_messages: [],
    totals: { workflow_facts: 2, content_facts: 0, provider_details: 0, tool_details: 1 },
    projection_complete: status !== "running",
    diagnostics: [],
  };
}

function setup(projection: TaskRunProjectionState = runProjection) {
  const listeners = new Set<(message: unknown) => void>();
  const sendCommand = vi.fn<(message: {
    type: string;
    request_id: string;
    payload: Record<string, unknown>;
  }) => boolean>(() => true);
  const view = render(
    <HarnessInspectorPanel
      open
      sessionId="session-1"
      selectedRunId="root-1"
      runProjections={[projection]}
      onSelectRun={() => undefined}
      onClose={() => undefined}
      sendCommand={sendCommand}
      subscribe={(listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      }}
    />,
  );
  return {
    ...view,
    sendCommand,
    emit(message: unknown) {
      act(() => listeners.forEach((listener) => listener(message)));
    },
  };
}

afterEach(() => {
  cleanup();
  harnessPublicSnapshotStore.clearForTests();
  vi.useRealTimers();
});

describe("HarnessInspectorPanel public run view", () => {
  it("waits for a reserved Run before requesting the ledger", () => {
    vi.useFakeTimers();
    const view = setup({ ...runProjection, status: "starting" });
    act(() => vi.runOnlyPendingTimers());
    expect(screen.getByText("正在启动任务…")).toBeTruthy();
    expect(view.sendCommand).not.toHaveBeenCalled();
  });

  it("polls only after a response and renders backend semantic phases", () => {
    vi.useFakeTimers();
    const view = setup();
    act(() => vi.runOnlyPendingTimers());
    expect(view.sendCommand).toHaveBeenCalledTimes(1);
    expect(view.sendCommand.mock.calls[0][0]).toMatchObject({
      type: "harness_inspector_snapshot_request",
      payload: { session_id: "session-1", root_run_id: "root-1", schema_version: "3" },
    });
    const requestId = view.sendCommand.mock.calls[0][0].request_id;
    act(() => vi.advanceTimersByTime(5_000));
    expect(view.sendCommand).toHaveBeenCalledTimes(1);
    view.emit({ type: "harness_inspector_snapshot_response", request_id: requestId, ok: true, snapshot: snapshot() });
    expect(screen.getByTestId("harness-run-graph")).toBeTruthy();
    expect(screen.getAllByText("创建项目").length).toBeGreaterThan(0);
    expect(screen.getAllByText("当前 2/4 步").length).toBeGreaterThan(0);
    act(() => vi.advanceTimersByTime(1_199));
    expect(view.sendCommand).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(1));
    expect(view.sendCommand).toHaveBeenCalledTimes(2);
  });

  it("stops polling a complete terminal projection and keeps recovery as overall success", () => {
    vi.useFakeTimers();
    const view = setup();
    act(() => vi.runOnlyPendingTimers());
    const requestId = view.sendCommand.mock.calls[0][0].request_id;
    view.emit({
      type: "harness_inspector_snapshot_response",
      request_id: requestId,
      ok: true,
      snapshot: snapshot("completed_with_recovery"),
    });
    expect(screen.getByText("子任务遇到问题，主 Agent 已接管并完成")).toBeTruthy();
    act(() => vi.advanceTimersByTime(5_000));
    expect(view.sendCommand).toHaveBeenCalledTimes(1);
  });

  it("folds repeated workflow-step state updates before rendering the shared trace", () => {
    vi.useFakeTimers();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const view = setup();
    act(() => vi.runOnlyPendingTimers());
    const requestId = view.sendCommand.mock.calls[0][0].request_id;
    const repeated = snapshot();
    repeated.semantic_phases[0].workflow_steps = [
      {
        workflow_step_id: "llm_proposal",
        label: "模型规划",
        status: "running",
        step_index: 0,
        step_total: 2,
      },
      {
        workflow_step_id: "llm_proposal",
        label: null,
        status: "completed",
        step_index: 0,
        step_total: 2,
      },
      {
        workflow_step_id: "tool_execution",
        label: "执行工具",
        status: "running",
        step_index: 1,
        step_total: 2,
      },
      {
        workflow_step_id: "tool_execution",
        label: null,
        status: "completed",
        step_index: 1,
        step_total: 2,
      },
    ];

    view.emit({
      type: "harness_inspector_snapshot_response",
      request_id: requestId,
      ok: true,
      snapshot: repeated,
    });

    expect(screen.getAllByText(/模型规划/)).toHaveLength(1);
    expect(screen.getAllByText(/执行工具/)).toHaveLength(1);
    expect(consoleError).not.toHaveBeenCalledWith(
      expect.stringContaining("Encountered two children with the same key"),
      expect.anything(),
    );
    consoleError.mockRestore();
  });

  it("discards a late response whose request identity is no longer active", () => {
    vi.useFakeTimers();
    const view = setup();
    act(() => vi.runOnlyPendingTimers());
    view.emit({
      type: "harness_inspector_snapshot_response",
      request_id: "stale-request",
      ok: true,
      snapshot: snapshot("completed"),
    });
    expect(screen.queryByTestId("harness-run-graph")).toBeNull();
    expect(harnessPublicSnapshotStore.get("session-1", "root-1")).toBeNull();
  });

  it("loads tool details in an independent slot and keeps the result folded", () => {
    vi.useFakeTimers();
    const view = setup();
    act(() => vi.runOnlyPendingTimers());
    const requestId = view.sendCommand.mock.calls[0][0].request_id;
    view.emit({ type: "harness_inspector_snapshot_response", request_id: requestId, ok: true, snapshot: snapshot() });
    fireEvent.click(screen.getByRole("button", { name: /查看.*输入/ }));
    const detailsRequest = view.sendCommand.mock.calls.find(([message]) =>
      message.type === "harness_inspector_details_request",
    )?.[0];
    expect(detailsRequest).toBeTruthy();
    if (!detailsRequest) throw new Error("details request was not sent");
    view.emit({
      type: "harness_inspector_details_response",
      request_id: detailsRequest.request_id,
      projection_id: snapshot().projection_id,
      query_kind: "tool_details",
      items: [{
        source: "workflow",
        stable_id: "tool-1",
        root_run_id: "root-1",
        kind: "tool",
        created_at: 1,
        public_payload: {
          tool_name: "文件工具",
          activity_kind: "写入文件",
          action_code: "write_file",
          safe_target_label: "project.godot",
          status: "completed",
          safe_input: { target: "project.godot" },
          bounded_result: { summary: "已完成" },
          truncation_hashes: [],
          policy_hash: "policy",
          interpreter_version: 1,
          schema_version: 1,
        },
      }],
      next_cursor: null,
    });
    expect(screen.getByText(/project\.godot/)).toBeTruthy();
    expect(screen.queryByText(/已完成/)).toBeNull();
    expect(screen.getByRole("button", { name: /查看.*结果/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("never renders raw secrets from an old schema", () => {
    vi.useFakeTimers();
    const view = setup();
    act(() => vi.runOnlyPendingTimers());
    const requestId = view.sendCommand.mock.calls[0][0].request_id;
    view.emit({
      type: "harness_inspector_snapshot_response",
      request_id: requestId,
      ok: true,
      payload: {
        schema_version: 2,
        session_id: "session-1",
        run: { run_id: "root-1", status: "failed" },
        provider_invocations: [{ output: { api_key: "SECRET_NEVER_RENDER" } }],
        tool_calls: [{ call_id: "call-1", tool_name: "shell_command", outcome_status: "failed" }],
      },
    });
    expect(document.body.textContent).not.toContain("SECRET_NEVER_RENDER");
    expect(screen.getByText("旧版运行记录仅显示安全摘要，详细阶段不可用")).toBeTruthy();
  });
});
