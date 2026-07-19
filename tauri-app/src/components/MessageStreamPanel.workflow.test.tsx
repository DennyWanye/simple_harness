import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async () => ""),
}));

import type { Message, WorkflowProgressStatus } from "../stores/sessionsStore";
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
    />
  );
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
  ] as const)("renders %s with fixed height and accessible progress", (status, label) => {
    view(progress(status));
    const card = screen.getByTestId("workflow-progress-run-ui");
    expect(card.getAttribute("data-status")).toBe(status);
    expect(card.style.height).toBe("104px");
    expect(card.style.minHeight).toBe("104px");
    expect(card.style.maxHeight).toBe("104px");
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
