import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.hoisted(() => vi.fn(async () => ""));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));

import type {
  Message,
  TaskRunProjectionState,
  WorkflowProgressStatus,
} from "../stores/sessionsStore";
import { useSessionsStore } from "../stores/sessionsStore";
import { __test_dispatch } from "../code-panel/controlWs";
import type { HarnessInspectorSnapshot, PublicRunSnapshotV3 } from "../types/messages";
import { MessageStreamPanel, type ChatStreamMessage } from "./MessageStreamPanel";

afterEach(cleanup);

function progress(
  status: WorkflowProgressStatus,
): Extract<ChatStreamMessage, { role: "workflow_progress" }> {
  const message: Message = {
    id: "workflow-run:run-ui",
    role: "workflow_progress",
    ts: 1000,
    workflow_run_id: "run-ui",
    workflow_name: "PPT",
    workflow_status: status,
    workflow_stage: status === "failed" ? "生成页面失败" : "生成完整页面",
    workflow_ordinal: 8,
    workflow_display_ordinal: 8,
    workflow_total: 12,
    workflow_seq: 3,
    workflow_terminal: ["completed", "failed", "cancelled"].includes(status),
  };
  return { role: "workflow_progress", message, ts: message.ts };
}

function stageMessage(
  runId: string,
  seq: number,
  stageId = "search",
  eventId = `${runId}-event-${seq}`,
): Extract<ChatStreamMessage, { role: "workflow_stage" }> {
  const message: Message = {
    id: `workflow-stage:${eventId}`,
    role: "workflow_stage",
    text: `${stageId} summary`,
    ts: seq * 1000,
    workflow_run_id: runId,
    workflow_name: "深度调研",
    workflow_version: "v2",
    workflow_status: "completed",
    workflow_stage: stageId,
    workflow_stage_id: stageId,
    workflow_stage_instance_id: `${stageId}-${seq}`,
    workflow_transition: "completed",
    workflow_total: 13,
    workflow_seq: seq,
    workflow_event_id: eventId,
  };
  return { role: "workflow_stage", message, ts: message.ts };
}

function panel(
  messages: ChatStreamMessage[],
  onWorkflowRetry?: (
    runId: string,
    actionId: "generate_now" | "continue_research" | "retry_from_start" | "cancel_settle",
    retryKey: string,
  ) => Promise<{ run_id: string; accepted?: boolean }>,
  agentSnapshot?: unknown,
  runProjections?: Record<string, TaskRunProjectionState>,
) {
  return (
    <MessageStreamPanel
      embedded
      filter="all"
      chatMessages={messages}
      warnings={[]}
      errors={[]}
      onSetFilter={() => undefined}
      onDismiss={() => undefined}
      onDismissAll={() => undefined}
      onJumpToSession={() => undefined}
      onChoice={() => undefined}
      onWorkflowRetry={onWorkflowRetry}
      agentSnapshot={agentSnapshot}
      runProjections={runProjections}
    />
  );
}

function cancelledHarnessSnapshot(workflowRunId: string): HarnessInspectorSnapshot {
  return {
    schema_version: 2,
    refreshed_at: 2000,
    session_id: "session-ui",
    run: {
      run_id: "root-ui",
      root_run_id: "root-ui",
      request_id: "request-ui",
      turn_id: "turn-ui",
      trace_id: "trace-ui",
      driver_kind: "react",
      profile_key: "agent.general",
      persistence_level: "durable",
      status: "cancelled",
      version: 3,
      durable_seq: 4,
      owner_kind: "kernel",
      owner_generation: 1,
      created_at: 1,
      updated_at: 2,
      ended_at: 2,
    },
    projection: null,
    prepared_context: {
      available: false,
      prepared_ref_count: 0,
      terminal_delivery_count: 0,
    },
    continuation: null,
    goal: null,
    lineage: [{
      run_id: workflowRunId,
      parent_run_id: "root-ui",
      driver_kind: "workflow",
      profile_key: "workflow.durable_task",
      status: "cancelled",
      version: 2,
      created_at: 1,
      updated_at: 2,
      ended_at: 2,
    }],
    provider_invocations: [],
    action_batches: [],
    tool_calls: [],
    effects: [],
    attempts: [],
    failures: [],
    events: [],
  };
}

function publicSnapshot(): PublicRunSnapshotV3 {
  return {
    schema_version: "3",
    projection_id: "public-run-ui",
    session_id: "session-ui",
    root_run_id: "run-ui",
    aggregate_outcome: {
      status: "running",
      explanation_code: "running",
      evidence_refs: [],
      child_warnings: [],
    },
    semantic_phases: [
      {
        phase_id: "prepare",
        taxonomy: "prepare",
        title: "创建项目配置",
        status: "completed",
        mapping_reason: "tool_spec",
        evidence_refs: [],
        tool_refs: ["detail-file-write"],
        child_refs: [],
        workflow_steps: [],
        items: [],
        order_key: [1, 0, 0, "prepare"],
      },
      {
        phase_id: "verify",
        taxonomy: "verify_repair",
        title: "验证项目结构",
        status: "running",
        mapping_reason: "tool_spec",
        evidence_refs: [],
        tool_refs: [],
        child_refs: [],
        workflow_steps: [{
          workflow_step_id: "verify-1",
          label: "检查项目文件",
          status: "running",
          step_index: 0,
          step_total: 2,
        }, {
          workflow_step_id: "verify-2",
          label: "启动 Godot",
          status: "unknown",
          step_index: 1,
          step_total: 2,
        }],
        items: [],
        order_key: [4, 0, 0, "verify"],
      },
    ],
    tool_public_views: [{
      tool_ref: "detail-file-write",
      stable_id: "stable-file-write",
      phase_id: "prepare",
      public_name: "file_write",
      action_label: "写入文件",
      safe_target_label: "project.godot",
      status: "completed",
      public_input: { path: "project.godot" },
      public_result: { bytes_written: 13 },
      details_available: true,
      result_available: true,
      truncated: false,
      created_at: 1.7,
    }],
    public_messages: [{
      message_id: "message-1",
      stable_id: "message-1",
      phase_id: "prepare",
      text: "现在创建 Godot 项目配置。",
      created_at: 1.5,
    }],
    totals: { workflow_facts: 3, content_facts: 1, provider_details: 0, tool_details: 1 },
    projection_complete: false,
    diagnostics: [],
  };
}

function view(message: ChatStreamMessage) {
  return render(panel([message]));
}

describe("WorkflowProgressRow (AC-23)", () => {
  it.each([
    ["running", "进行中"],
    ["waiting", "等待操作"],
    ["completed", "已完成"],
    ["failed", "失败"],
    ["cancelled", "已取消"],
  ] as const)("renders %s as a lightweight message with accessible progress", (status, label) => {
    view(progress(status));
    const message = screen.getByTestId("workflow-progress-run-ui");
    expect(message.getAttribute("data-status")).toBe(status);
    expect(message.style.height).toBe("");
    expect(message.style.minHeight).toBe("");
    expect(message.style.maxHeight).toBe("");
    expect(message.style.background).toBe("");
    expect(message.style.border).toBe("");
    expect(screen.getByText(label)).toBeTruthy();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuemin")).toBe("0");
    expect(bar.getAttribute("aria-valuemax")).toBe("100");
    expect(bar.getAttribute("aria-valuenow")).toBe(status === "completed" ? "100" : "67");
  });

  it("updates the same keyed card without adding a row", () => {
    const rendered = view(progress("running"));
    rendered.rerender(panel([progress("waiting")]));
    expect(screen.getAllByTestId("workflow-progress-run-ui")).toHaveLength(1);
    expect(screen.getByTestId("workflow-progress-run-ui").getAttribute("data-status")).toBe("waiting");
  });

  it("settles a stale running summary from the terminal Session Run projection", () => {
    render(panel(
      [progress("running")],
      undefined,
      undefined,
      {
        "run-ui": {
          run_id: "run-ui",
          task_scope_id: "scope-ui",
          version: 4,
          status: "cancelled",
          inflight: false,
          ui_state: "open",
          started_at: 1,
          last_activity: 1,
        },
      },
    ));

    const card = screen.getByTestId("workflow-progress-run-ui");
    expect(card.getAttribute("data-status")).toBe("cancelled");
    expect(card.textContent).toContain("已取消");
    expect(card.textContent).toContain("耗时未记录");
    expect(card.textContent).not.toContain("进行中");
  });

  it("settles a child workflow card from its terminal parent Run projection", () => {
    const child = progress("running");
    child.message.workflow_run_id = "child-ui";
    child.message.run_id = "root-ui";
    render(panel(
      [child],
      undefined,
      undefined,
      {
        "root-ui": {
          run_id: "root-ui",
          task_scope_id: "scope-ui",
          version: 5,
          status: "cancelled",
          inflight: false,
          ui_state: "open",
          started_at: 1,
          last_activity: 2,
        },
      },
    ));

    const card = screen.getByTestId("workflow-progress-child-ui");
    expect(card.getAttribute("data-status")).toBe("cancelled");
    expect(card.textContent).toContain("已取消");
    expect(card.textContent).not.toContain("进行中");
  });

  it("keeps a child's failed terminal summary when only its completed parent projection is loaded", () => {
    const child = progress("failed");
    child.message.workflow_run_id = "child-ui";
    child.message.run_id = "root-ui";
    render(panel(
      [child],
      undefined,
      undefined,
      {
        "root-ui": {
          run_id: "root-ui",
          task_scope_id: "scope-root-ui",
          version: 7,
          status: "completed",
          inflight: false,
          ui_state: "open",
          started_at: 1,
          last_activity: 3,
        },
      },
    ));

    const card = screen.getByTestId("workflow-progress-child-ui");
    expect(card.getAttribute("data-status")).toBe("failed");
    expect(card.textContent).toContain("失败");
    expect(card.textContent).not.toContain("已完成");
  });

  it("merges a child workflow card into the parent public task trace", () => {
    const child = progress("running");
    child.message.workflow_run_id = "child-ui";
    child.message.run_id = "run-ui";
    const snapshot = publicSnapshot();
    snapshot.aggregate_outcome = {
      ...snapshot.aggregate_outcome,
      status: "cancelled",
      explanation_code: "user_interrupt",
    };
    snapshot.projection_complete = true;

    render(panel([child], undefined, snapshot));

    expect(screen.getAllByTestId(/^workflow-progress-/)).toHaveLength(1);
    const card = screen.getByTestId("workflow-progress-child-ui");
    expect(card.getAttribute("data-status")).toBe("cancelled");
    expect(card.textContent).toContain("1/2 步");
    expect(card.textContent).not.toContain("进行中");
  });

  it("does not let a legacy raw child Run overwrite workflow card authority", () => {
    render(panel(
      [progress("running")],
      undefined,
      cancelledHarnessSnapshot("run-ui"),
    ));

    const card = screen.getByTestId("workflow-progress-run-ui");
    expect(card.getAttribute("data-status")).toBe("running");
  });

  it("projects summary and sorted children as one group row and removes duplicates", () => {
    render(panel([
      stageMessage("run-ui", 5, "fetch"),
      { role: "assistant", text: "ordinary", ts: 2500 },
      progress("running"),
      stageMessage("run-ui", 2, "search"),
      stageMessage("run-ui", 2, "search", "duplicate-event-id"),
      stageMessage("run-ui", 6, "fetch", "run-ui-event-5"),
    ]));

    expect(screen.getAllByTestId("workflow-progress-run-ui")).toHaveLength(1);
    const toggle = screen.getByRole("button", { name: "查看阶段 (2)" });
    fireEvent.click(toggle);
    const stages = screen.getAllByTestId(/^workflow-stage-/);
    expect(stages).toHaveLength(2);
    expect(stages[0].getAttribute("data-testid")).toContain("run-ui-event-2");
    expect(stages[1].getAttribute("data-testid")).toContain("run-ui-event-5");
    expect(screen.getByText("ordinary")).toBeTruthy();
  });

  it("renders stage-before-summary fallback and upgrades the stable group in place", () => {
    const child = stageMessage("fallback-run", 2, "normalize");
    const rendered = render(panel([child]));
    const toggle = screen.getByRole("button", { name: "查看阶段 (1)" });
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    const upgraded: Message = {
      ...progress("waiting").message,
      id: "workflow-run:fallback-run",
      workflow_run_id: "fallback-run",
      workflow_name: "正式深度调研",
      workflow_seq: 3,
    };
    rendered.rerender(panel([
      child,
      { role: "workflow_progress", message: upgraded, ts: upgraded.ts },
    ]));

    expect(screen.getAllByTestId("workflow-progress-fallback-run")).toHaveLength(1);
    expect(screen.getByText("正式深度调研")).toBeTruthy();
    expect(screen.getByRole("button", { name: "收起阶段" }).getAttribute("aria-expanded"))
      .toBe("true");
  });

  it("keeps concurrent runs in independent groups", () => {
    const other = {
      ...stageMessage("run-b", 1, "normalize"),
      ts: 500,
    };
    render(panel([stageMessage("run-a", 1, "normalize"), other]));
    expect(screen.getByTestId("workflow-progress-run-a")).toBeTruthy();
    expect(screen.getByTestId("workflow-progress-run-b")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "查看阶段 (1)" })).toHaveLength(2);
  });

  it("does not force-scroll when a user is reading above", () => {
    const rendered = render(panel([
      { role: "assistant", text: "older", ts: 1000 },
    ]));
    const list = screen.getByTestId("msgstream-list");
    Object.defineProperty(list, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(list, "clientHeight", { configurable: true, value: 200 });
    list.scrollTop = 100;
    fireEvent.scroll(list);

    rendered.rerender(panel([
      { role: "assistant", text: "older", ts: 1000 },
      stageMessage("late-run", 2, "search"),
    ]));
    expect(list.scrollTop).toBe(100);
  });

  it("forwards a v4 retry action from the grouped failure card", () => {
    const failed = progress("failed");
    failed.message.workflow_version = "v4";
    failed.message.workflow_retry_action_id = "retry_from_start";
    failed.message.workflow_error = "coverage too low";
    const onWorkflowRetry = vi.fn<(
      runId: string,
      actionId: "generate_now" | "continue_research" | "retry_from_start" | "cancel_settle",
      retryKey: string,
    ) => Promise<{ run_id: string; accepted?: boolean }>>(async () => ({ run_id: "new-run" }));
    render(panel([failed], onWorkflowRetry));

    const retryButton = screen.getByRole("alert").querySelector("button");
    expect(retryButton).toBeTruthy();
    fireEvent.click(retryButton!);
    expect(onWorkflowRetry).toHaveBeenCalledTimes(1);
    expect(onWorkflowRetry.mock.calls[0][0]).toBe("run-ui");
    expect(onWorkflowRetry.mock.calls[0][1]).toBe("retry_from_start");
  });

  it("forwards one generate-now control for rapid clicks on a running v6 deep-research group", () => {
    const running = progress("running");
    running.message.workflow_name = "deep_research";
    running.message.workflow_version = "v6";
    const pending = new Promise<{ run_id: string }>(() => undefined);
    const onWorkflowRetry = vi.fn(() => pending);
    const rendered = render(panel([running], onWorkflowRetry));
    const button = screen.getByRole("button", { name: "立即用现有证据生成" });

    act(() => {
      button.click();
      button.click();
    });

    expect(onWorkflowRetry).toHaveBeenCalledTimes(1);
    expect(onWorkflowRetry).toHaveBeenCalledWith("run-ui", "generate_now", expect.any(String));

    const completed = progress("completed");
    completed.message.workflow_name = "deep_research";
    completed.message.workflow_version = "v6";
    rendered.rerender(panel([completed], onWorkflowRetry));
    expect(screen.queryByRole("button", { name: "立即用现有证据生成" })).toBeNull();
  });
});

describe("ThinkingProcessGroup (AC-9)", () => {
  const thinkingMessages = (): ChatStreamMessage[] => [{
    role: "progress",
    runId: "run-thinking",
    text: "我会先检查项目结构，再选择需要修改的文件。",
    ts: 2_000,
    status: "running",
  }, {
    role: "tool",
    runId: "run-thinking",
    text: "调用 file_read",
    ts: 3_000,
    toolName: "file_read",
    toolArgs: { path: "src/App.tsx" },
  }];

  const projection = (
    status: TaskRunProjectionState["status"],
    startedAt = 1_000,
    lastActivity = 62_000,
  ): Record<string, TaskRunProjectionState> => ({
    "run-thinking": {
      run_id: "run-thinking",
      task_scope_id: "scope-thinking",
      version: 1,
      status,
      inflight: !["completed", "failed", "cancelled"].includes(status),
      ui_state: "open",
      started_at: startedAt,
      last_activity: lastActivity,
    },
  });

  it("groups public narration and tool trace by canonical Run and expands while running", () => {
    render(panel(thinkingMessages(), undefined, undefined, projection("running")));

    const group = screen.getByTestId("thinking-process-run-thinking");
    expect(group.getAttribute("data-status")).toBe("running");
    expect(screen.getByRole("button", { name: /思考中/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("我会先检查项目结构，再选择需要修改的文件。")).toBeTruthy();
    expect(screen.getByRole("button", { name: /file_read/ })).toBeTruthy();
  });

  it.each(["starting", "waiting", "running"] as const)(
    "defaults %s Run thinking to expanded",
    (status) => {
      render(panel(thinkingMessages(), undefined, undefined, projection(status)));
      expect(
        screen.getByRole("button", { name: /思考中/ }).getAttribute("aria-expanded"),
      ).toBe("true");
    },
  );

  it.each(["completed", "failed", "cancelled"] as const)(
    "defaults historical %s Run thinking to collapsed",
    (status) => {
      render(panel(thinkingMessages(), undefined, undefined, projection(status)));
      expect(
        screen.getByRole("button", { name: /耗时/ }).getAttribute("aria-expanded"),
      ).toBe("false");
    },
  );

  it("auto-collapses on terminal transition, reports elapsed time, and permits manual toggle", () => {
    const rendered = render(panel(
      thinkingMessages(),
      undefined,
      undefined,
      projection("running"),
    ));
    expect(screen.getByRole("button", { name: /思考中/ }).getAttribute("aria-expanded")).toBe("true");

    rendered.rerender(panel(
      thinkingMessages(),
      undefined,
      undefined,
      projection("completed"),
    ));
    const header = screen.getByRole("button", { name: /耗时 1 分 1 秒/ });
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("我会先检查项目结构，再选择需要修改的文件。")).toBeNull();

    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("我会先检查项目结构，再选择需要修改的文件。")).toBeTruthy();
    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps historical terminal Runs collapsed and degrades missing duration to 已完成", () => {
    render(panel(
      thinkingMessages(),
      undefined,
      undefined,
      projection("failed", 1_000, 1_000),
    ));

    const header = screen.getByRole("button", { name: "已完成" });
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("我会先检查项目结构，再选择需要修改的文件。")).toBeNull();
  });

  it("keeps a tool-only SDK Run elapsed time stable after history hydration without a projection", () => {
    render(panel([
      {
        role: "user",
        runId: "run-history-tool-only",
        text: "读取测试文件",
        ts: 1_000,
      },
      {
        role: "tool",
        runId: "run-history-tool-only",
        text: "调用 file_read",
        ts: 6_000,
        toolName: "file_read",
        toolArgs: { path: "README.md" },
      },
      {
        role: "tool",
        runId: "run-history-tool-only",
        text: "file_read 完成",
        ts: 11_000,
        toolName: "file_read",
        toolOk: true,
        toolResultRaw: "done",
      },
      {
        role: "assistant",
        runId: "run-history-tool-only",
        text: "读取完成。",
        ts: 20_000,
      },
    ]));

    const header = screen.getByRole("button", { name: "耗时 19 秒" });
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByText("读取完成。")).toBeTruthy();
  });

  it("never renders NaN or a negative duration when terminal timestamps are reversed", () => {
    render(panel(
      thinkingMessages(),
      undefined,
      undefined,
      projection("cancelled", 62_000, 1_000),
    ));

    const header = screen.getByRole("button", { name: "已完成" });
    expect(header.textContent).not.toMatch(/NaN|-/);
  });

  it("keeps a terminal group collapsed after a late running upsert for the same Run", () => {
    const sid = "thinking-terminal-lock";
    const store = useSessionsStore.getState();
    store.ensure(sid);
    store.upsert_run_projection(sid, "run-thinking", {
      task_scope_id: "scope-thinking",
      status: "completed",
      inflight: false,
      ui_state: "open",
      started_at: 1_000,
      last_activity: 62_000,
    });
    store.upsert_run_projection(sid, "run-thinking", {
      status: "running",
      inflight: true,
    });
    const durable = useSessionsStore.getState().sessions[sid].run_projections;
    render(panel(thinkingMessages(), undefined, undefined, durable));

    expect(screen.getByTestId("thinking-process-run-thinking").getAttribute("data-status"))
      .toBe("completed");
    expect(screen.getByRole("button", { name: /耗时 1 分 1 秒/ }).getAttribute("aria-expanded"))
      .toBe("false");
  });

  it("bounds an expanded long public narration with an internal scroll container", () => {
    const long = "公开工作叙述。".repeat(2_000);
    render(panel([{
      role: "progress",
      runId: "run-long",
      text: long,
      ts: Date.now(),
      status: "running",
    }], undefined, undefined, {
      "run-long": {
        ...projection("running")["run-thinking"],
        run_id: "run-long",
        started_at: Date.now(),
        last_activity: Date.now(),
      },
    }));

    const body = screen.getByTestId("thinking-process-body-run-long");
    expect(body.style.maxHeight).toBe("360px");
    expect(body.style.overflowY).toBe("auto");
  });

  it("deduplicates repeated Run events but leaves legacy messages without runId flat", () => {
    const messages = thinkingMessages();
    render(panel([
      messages[0],
      { ...messages[0] },
      messages[1],
      { role: "progress", text: "旧版公开进度", ts: 4_000, status: "completed" },
    ], undefined, undefined, projection("running")));

    expect(screen.getAllByTestId("thinking-process-run-thinking")).toHaveLength(1);
    expect(screen.getAllByText("我会先检查项目结构，再选择需要修改的文件。")).toHaveLength(1);
    expect(screen.getByText("旧版公开进度")).toBeTruthy();
  });

  it("groups a tool-only Run as observable thinking without inventing narration", () => {
    const rendered = render(panel([{
      role: "tool",
      runId: "run-tool-only",
      text: "调用 file_read",
      ts: 2_000,
      toolName: "file_read",
      toolArgs: { path: "README.md" },
    }, {
      role: "tool",
      runId: "run-tool-only",
      text: "file_read 完成",
      ts: 3_000,
      toolName: "file_read",
      toolOk: true,
      toolResultRaw: "done",
    }, {
      role: "assistant",
      runId: "run-tool-only",
      text: "最终回复仍然正常显示。",
      ts: 4_000,
    }], undefined, undefined, {
      "run-tool-only": {
        run_id: "run-tool-only",
        task_scope_id: "scope-tool-only",
        version: 2,
        status: "completed",
        inflight: false,
        ui_state: "open",
        started_at: 1_000,
        last_activity: 5_000,
      },
    }));

    const group = screen.getByTestId("thinking-process-run-tool-only");
    expect(group.getAttribute("data-status")).toBe("completed");
    const header = screen.getByRole("button", { name: /耗时 4 秒/ });
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(rendered.container.querySelectorAll('[data-role="tool"]')).toHaveLength(0);
    fireEvent.click(header);
    expect(rendered.container.querySelectorAll('[data-role="tool"]')).toHaveLength(2);
    expect(screen.queryByText(/公开工作叙述/)).toBeNull();
    expect(screen.getByText("最终回复仍然正常显示。")).toBeTruthy();
  });

  it("expands a running tool-only Run and omits a group only when no observable rows remain", () => {
    const runningTool: ChatStreamMessage = {
      role: "tool",
      runId: "run-tool-running",
      text: "调用 file_read",
      ts: 2_000,
      toolName: "file_read",
      toolArgs: { path: "README.md" },
    };
    const rendered = render(panel([runningTool], undefined, undefined, {
      "run-tool-running": {
        run_id: "run-tool-running",
        task_scope_id: "scope-tool-running",
        version: 1,
        status: "running",
        inflight: true,
        ui_state: "open",
        started_at: 1_000,
        last_activity: 2_000,
      },
    }));

    expect(screen.getByTestId("thinking-process-run-tool-running")).toBeTruthy();
    expect(screen.getByRole("button", { name: /思考中/ }).getAttribute("aria-expanded"))
      .toBe("true");
    expect(rendered.container.querySelectorAll('[data-role="tool"]')).toHaveLength(1);

    rendered.rerender(panel([], undefined, undefined, {
      "run-tool-running": {
        run_id: "run-tool-running",
        task_scope_id: "scope-tool-running",
        version: 1,
        status: "running",
        inflight: true,
        ui_state: "open",
        started_at: 1_000,
        last_activity: 2_000,
      },
    }));
    expect(screen.queryByTestId("thinking-process-run-tool-running")).toBeNull();
  });

  it("rehydrates a durable summary with its Run id and collapses it from terminal projection authority", () => {
    const sid = "thinking-hydration";
    useSessionsStore.getState().ensure(sid);
    __test_dispatch({
      type: "session_messages_response",
      payload: {
        session_id: sid,
        messages: [{
          id: "persisted-summary",
          role: "assistant",
          text: "恢复后的公开工作叙述。",
          projection_kind: "workflow_progress",
          workflow_event_id: "reasoning-summary:run-hydrated:1:status",
          run_id: "run-hydrated",
          task_scope_id: "scope-hydrated",
          ts: 2_000,
        }],
      },
    });
    __test_dispatch({
      type: "task_projections_response",
      payload: {
        session_id: sid,
        projections: [{
          projection_id: "projection-hydrated",
          run_id: "run-hydrated",
          task_scope_id: "scope-hydrated",
          ui_state: "open",
          version: 4,
          status: "completed",
          started_at: 1,
          updated_at: 3,
          ended_at: 3,
        }],
      },
    });

    const hydrated = useSessionsStore.getState().sessions[sid];
    const summary = hydrated.messages.find((message) =>
      message.role === "reasoning_summary");
    expect(summary).toMatchObject({ run_id: "run-hydrated" });
    render(panel([{
      role: "progress",
      runId: summary?.run_id,
      text: summary?.text ?? "",
      ts: summary?.ts ?? 0,
      phase: summary?.reasoning_phase,
      status: summary?.reasoning_status,
    }], undefined, undefined, hydrated.run_projections));

    expect(screen.getByTestId("thinking-process-run-hydrated").getAttribute("data-status"))
      .toBe("completed");
    expect(screen.getByRole("button", { name: /耗时 2 秒/ }).getAttribute("aria-expanded"))
      .toBe("false");
  });
});

describe("PublicProgressRow", () => {
  it("shows server-approved narration in the same semantic phase as its tool", () => {
    render(panel([], undefined, publicSnapshot()));

    const prepare = screen.getByRole("button", { name: /第 1 步，创建项目配置，已完成/ });
    expect(prepare.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(prepare);
    expect(screen.getByText("现在创建 Godot 项目配置。")).toBeTruthy();
    expect(screen.getByTestId("compact-tool-stable-file-write")).toBeTruthy();
  });

  it("keeps public tool input and result independently folded", () => {
    render(panel([], undefined, publicSnapshot()));
    fireEvent.click(screen.getByRole("button", { name: /第 1 步，创建项目配置，已完成/ }));
    const input = screen.getByRole("button", { name: /查看.*file_write.*输入/ });
    const result = screen.getByRole("button", { name: /查看.*file_write.*结果/ });
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(result.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/bytes_written/)).toBeNull();
    fireEvent.click(result);
    expect(screen.getByText(/bytes_written/)).toBeTruthy();
    expect(input.getAttribute("aria-expanded")).toBe("false");
  });

  it("opens only the current semantic phase by default", () => {
    render(panel([progress("running")], undefined, publicSnapshot()));

    expect(screen.getByText("当前：验证项目结构")).toBeTruthy();
    const completedStep = screen.getByRole("button", {
      name: /第 1 步，创建项目配置，已完成/,
    });
    const currentStep = screen.getByRole("button", {
      name: /第 2 步，验证项目结构，进行中/,
    });
    expect(completedStep.getAttribute("aria-expanded")).toBe("false");
    expect(currentStep.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("1. 检查项目文件")).toBeTruthy();
    expect(screen.getByText("2. 启动 Godot")).toBeTruthy();
    expect(screen.queryByText("现在创建 Godot 项目配置。")).toBeNull();
  });

  it("shows public work narration as progress, never as hidden reasoning", () => {
    view({
      role: "progress",
      text: "准备检查当前任务并调用工具。",
      ts: 1000,
      phase: "planning",
      status: "running",
    });

    expect(screen.getByText("执行进度")).toBeTruthy();
    expect(screen.getByText("准备检查当前任务并调用工具。")).toBeTruthy();
    expect(screen.queryByText("思考过程")).toBeNull();
    expect(document.querySelector('[data-role="progress"]')).toBeTruthy();
  });

  it("hides old host-generated tool summaries while keeping the tool UI", () => {
    render(panel([
      {
        role: "progress",
        text: "准备使用 memory_search 处理当前步骤。",
        ts: 1000,
        phase: "planning",
        status: "completed",
      },
      {
        role: "tool",
        text: '🔧 调用 memory_search {"query":"DeskPet"}',
        ts: 1001,
      },
    ]));

    expect(screen.queryByText("准备使用 memory_search 处理当前步骤。")).toBeNull();
    expect(screen.getByText(/调用 memory_search/)).toBeTruthy();
    expect(document.querySelector('[data-role="tool"]')).toBeTruthy();
  });

  it("shows the Agent-triggered project directory picker inside the message stream", async () => {
    invokeMock.mockResolvedValueOnce("D:\\Games");
    const onConfirm = vi.fn(() => true);
    render(
      <MessageStreamPanel
        embedded
        filter="all"
        chatMessages={[]}
        warnings={[]}
        errors={[]}
        onSetFilter={() => undefined}
        onDismiss={() => undefined}
        onDismissAll={() => undefined}
        onJumpToSession={() => undefined}
        onChoice={() => undefined}
        projectDirectoryRequest={{
          session_id: "default",
          run_id: "root-project",
          request_id: "decision-project",
          decision_id: "decision-project",
          nonce: "nonce-project",
          version: 0,
          title: "选择项目保存位置",
          required_action: "请选择项目保存到哪个文件夹下面",
          wait_kind: "user_content",
          wait_ref: "external-wait:project",
          project_name: "末日生存 Demo",
          folder_name: "apocalypse-demo",
          project_kind: "Godot 游戏",
          directory_mode: "use_existing",
          received_at: 1000,
        }}
        onProjectDirectoryConfirm={onConfirm}
      />,
    );

    expect(screen.getByText(/Godot 游戏「末日生存 Demo」/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "选择文件夹" }));
    await waitFor(() => {
      expect(screen.getByText(/将使用：D:\\Games/)).toBeTruthy();
    });
    expect(screen.queryByLabelText("项目文件夹名")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "使用此文件夹" }));
    expect(onConfirm).toHaveBeenCalledWith(
      "D:\\Games",
      "apocalypse-demo",
    );
    expect(screen.getByText("Agent 已收到位置，正在继续现有项目。")).toBeTruthy();
  });

  it("joins a new project name with the native macOS path separator", async () => {
    invokeMock.mockResolvedValueOnce("/Users/denny/projects");
    render(
      <MessageStreamPanel
        embedded
        filter="all"
        chatMessages={[]}
        warnings={[]}
        errors={[]}
        onSetFilter={() => undefined}
        onDismiss={() => undefined}
        onDismissAll={() => undefined}
        onJumpToSession={() => undefined}
        onChoice={() => undefined}
        projectDirectoryRequest={{
          session_id: "default",
          run_id: "root-project",
          request_id: "decision-project",
          decision_id: "decision-project",
          nonce: "nonce-project",
          version: 0,
          title: "选择项目保存位置",
          required_action: "请选择项目保存到哪个文件夹下面",
          wait_kind: "user_content",
          wait_ref: "external-wait:project",
          project_name: "Harness Demo",
          folder_name: "harness-demo",
          project_kind: "Web app",
          directory_mode: "create_new",
          received_at: 1000,
        }}
        onProjectDirectoryConfirm={() => true}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择文件夹" }));
    await waitFor(() => {
      expect(
        screen.getByText(/将创建到：\/Users\/denny\/projects\/harness-demo/),
      ).toBeTruthy();
    });
    expect(screen.queryByText(/projects\\harness-demo/)).toBeNull();
  });

  it("restores a confirmed parent directory after reconnect", () => {
    const onConfirm = vi.fn(() => true);
    render(
      <MessageStreamPanel
        embedded
        filter="all"
        chatMessages={[]}
        warnings={[]}
        errors={[]}
        onSetFilter={() => undefined}
        onDismiss={() => undefined}
        onDismissAll={() => undefined}
        onJumpToSession={() => undefined}
        onChoice={() => undefined}
        projectDirectoryRequest={{
          session_id: "default",
          run_id: "root-project",
          request_id: "decision-project",
          decision_id: "decision-project",
          nonce: "nonce-project",
          version: 0,
          title: "选择项目保存位置",
          required_action: "确认现有目录",
          wait_kind: "user_content",
          wait_ref: "external-wait:project",
          project_name: "Jurassic Park: Escape",
          folder_name: "jurassic-park-escape",
          project_kind: "Godot 4 game",
          directory_mode: "use_existing",
          parent_directory: "F:\\projects\\jurassic-park-escape",
          received_at: 1000,
        }}
        onProjectDirectoryConfirm={onConfirm}
      />,
    );

    expect(
      screen.getByText(/将使用：F:\\projects\\jurassic-park-escape/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "使用此文件夹" }));
    expect(onConfirm).toHaveBeenCalledWith(
      "F:\\projects\\jurassic-park-escape",
      "jurassic-park-escape",
    );
  });

  it("removes the project directory picker when its Run is cancelled", () => {
    render(
      <MessageStreamPanel
        embedded
        filter="all"
        chatMessages={[]}
        warnings={[]}
        errors={[]}
        onSetFilter={() => undefined}
        onDismiss={() => undefined}
        onDismissAll={() => undefined}
        onJumpToSession={() => undefined}
        onChoice={() => undefined}
        projectDirectoryRequest={{
          session_id: "default",
          run_id: "root-ui",
          request_id: "decision-project",
          decision_id: "decision-project",
          nonce: "nonce-project",
          version: 0,
          title: "选择项目保存位置",
          required_action: "请选择项目保存到哪个文件夹下面",
          wait_kind: "user_content",
          wait_ref: "external-wait:project",
          project_name: "末日生存 Demo",
          folder_name: "apocalypse-demo",
          project_kind: "Godot 游戏",
          received_at: 1000,
        }}
        agentSnapshot={cancelledHarnessSnapshot("workflow-project")}
        onProjectDirectoryConfirm={() => true}
      />,
    );

    expect(screen.queryByTestId("project-directory-card")).toBeNull();
  });
});
