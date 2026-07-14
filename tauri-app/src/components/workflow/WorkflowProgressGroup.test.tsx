import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Message } from "../../stores/sessionsStore";
import { WorkflowProgressGroup } from "./WorkflowProgressGroup";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function summary(overrides: Partial<Message> = {}): Message {
  return {
    id: "workflow-run:run-group",
    role: "workflow_progress",
    ts: 1000,
    workflow_run_id: "run-group",
    workflow_name: "深度调研",
    workflow_version: "v2",
    workflow_status: "running",
    workflow_stage: "搜索资料",
    workflow_total: 13,
    workflow_completed_count: 4,
    workflow_elapsed_ms: 62000,
    workflow_seq: 9,
    ...overrides,
  };
}

function stage(
  seq: number,
  stageId: string,
  overrides: Partial<Message> = {},
): Message {
  return {
    id: `workflow-stage:event-${seq}`,
    role: "workflow_stage",
    text: `阶段 ${stageId} 已完成`,
    ts: seq * 1000,
    workflow_run_id: "run-group",
    workflow_name: "深度调研",
    workflow_version: "v2",
    workflow_status: "completed",
    workflow_stage: stageId,
    workflow_stage_id: stageId,
    workflow_stage_instance_id: `${stageId}-${seq}`,
    workflow_transition: "completed",
    workflow_seq: seq,
    workflow_event_id: `event-${seq}`,
    workflow_total: 13,
    workflow_duration_ms: 1500,
    ...overrides,
  };
}

describe("WorkflowProgressGroup", () => {
  it("keeps stage bubbles collapsed while summary, count, elapsed, and warnings stay visible", () => {
    render(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary()}
        stages={[
          stage(2, "search"),
          stage(3, "fetch", { workflow_degraded: true }),
        ]}
      />,
    );

    expect(screen.getByText("4/13 · 已用时 1 分 2 秒")).toBeTruthy();
    expect(screen.getByText("⚠ 1")).toBeTruthy();
    const toggle = screen.getByRole("button", { name: "查看阶段 (2)" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    const detailsId = toggle.getAttribute("aria-controls")!;
    expect(document.getElementById(detailsId)?.hidden).toBe(true);
  });

  it("expands with pointer and keyboard and renders only allowlisted metrics", () => {
    render(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary()}
        stages={[stage(2, "search", {
          workflow_metrics: {
            providers: 3,
            candidates: 18,
            prompt: "do-not-render",
          },
          workflow_next_stage: "direct",
        })]}
      />,
    );
    const toggle = screen.getByRole("button", { name: "查看阶段 (1)" });
    fireEvent.keyDown(toggle, { key: " " });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("来源: 3")).toBeTruthy();
    expect(screen.getByText("候选: 18")).toBeTruthy();
    expect(screen.queryByText(/do-not-render/)).toBeNull();
    expect(screen.getByText("下一步：direct")).toBeTruthy();
    fireEvent.keyDown(toggle, { key: "Enter" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("counts repeated gap instances once and caps overall completion at 13", () => {
    render(
      <WorkflowProgressGroup
        runId="run-group"
        stages={[
          stage(2, "gap"),
          stage(3, "gap"),
          ...Array.from({ length: 14 }, (_, index) => stage(
            index + 10,
            `stage-${index}`,
          )),
        ]}
      />,
    );
    expect(screen.getByText(/13\/13/)).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("99");
  });

  it("updates elapsed time while a workflow remains active", () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    render(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary({
          workflow_elapsed_ms: 10_000,
          workflow_updated_at: 10_000,
        })}
        stages={[]}
      />,
    );
    expect(screen.getByText(/10 绉?/)).toBeTruthy();
    act(() => vi.advanceTimersByTime(5_000));
    expect(screen.getByText(/15 绉?/)).toBeTruthy();
  });
});
