import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async () => ""),
}));

import type { Message, WorkflowProgressStatus } from "../stores/sessionsStore";
import { MessageStreamPanel, type ChatStreamMessage } from "./MessageStreamPanel";

afterEach(cleanup);

function progress(status: WorkflowProgressStatus): ChatStreamMessage {
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

function view(message: ChatStreamMessage) {
  return render(
    <MessageStreamPanel
      embedded
      filter="all"
      chatMessages={[message]}
      warnings={[]}
      errors={[]}
      onSetFilter={() => undefined}
      onDismiss={() => undefined}
      onDismissAll={() => undefined}
      onJumpToSession={() => undefined}
      onChoice={() => undefined}
    />,
  );
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
    rendered.rerender(
      <MessageStreamPanel
        embedded
        filter="all"
        chatMessages={[progress("waiting")]}
        warnings={[]}
        errors={[]}
        onSetFilter={() => undefined}
        onDismiss={() => undefined}
        onDismissAll={() => undefined}
        onJumpToSession={() => undefined}
        onChoice={() => undefined}
      />,
    );
    expect(screen.getAllByTestId("workflow-progress-run-ui")).toHaveLength(1);
    expect(screen.getByTestId("workflow-progress-run-ui").getAttribute("data-status")).toBe("waiting");
  });
});
