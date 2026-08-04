import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunInspectorPanel } from "./RunInspectorPanel";

afterEach(cleanup);

const run = {
  run_id: "run-1234567890",
  workflow_name: "deep-research",
  workflow_version: "1",
  status: "running" as const,
  created_at: 1,
  updated_at: 2,
  active_nodes: ["search"],
};

describe("RunInspectorPanel", () => {
  it("renders graph, trace, checkpoints and evaluations", () => {
    const onFork = vi.fn();
    const { rerender } = render(
      <RunInspectorPanel
        runs={[run]}
        selectedRunId={run.run_id}
        nodes={[{ id: "search", label: "Search", status: "running", attempt: 2 }]}
        edges={[]}
        spans={[{ span_id: "root", name: "workflow", kind: "workflow", status: "ok", duration_ms: 12 }]}
        checkpoints={[{ checkpoint_id: "checkpoint-1", node_id: "search", created_at: 1, status: "safe" }]}
        evaluations={[{ evaluation_id: "eval-1", evaluator_name: "citation", evaluator_version: "1", verdict: "pass", score: 0.9 }]}
        onSelectRun={vi.fn()}
        onRefresh={vi.fn()}
        onFork={onFork}
      />,
    );

    expect(screen.getByTestId("workflow-graph").textContent).toContain("Search");
    rerender(
      <RunInspectorPanel view="trace" runs={[run]} selectedRunId={run.run_id} nodes={[]} edges={[]} spans={[{ span_id: "root", name: "workflow", kind: "workflow", status: "ok", duration_ms: 12 }]} checkpoints={[]} evaluations={[]} onSelectRun={vi.fn()} onRefresh={vi.fn()} />,
    );
    expect(screen.getByRole("tree").textContent).toContain("workflow");
    rerender(
      <RunInspectorPanel view="checkpoints" runs={[run]} selectedRunId={run.run_id} nodes={[]} edges={[]} spans={[]} checkpoints={[{ checkpoint_id: "checkpoint-1", node_id: "search", created_at: 1, status: "safe" }]} evaluations={[]} onSelectRun={vi.fn()} onRefresh={vi.fn()} onFork={onFork} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "从这里创建分支" }));
    expect(onFork).toHaveBeenCalledWith("checkpoint-1");
    rerender(
      <RunInspectorPanel view="evaluations" runs={[run]} selectedRunId={run.run_id} nodes={[]} edges={[]} spans={[]} checkpoints={[]} evaluations={[{ evaluation_id: "eval-1", evaluator_name: "citation", evaluator_version: "1", verdict: "pass", score: 0.9 }]} onSelectRun={vi.fn()} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("citation")).toBeTruthy();
  });

  it("shows stable empty and error states", () => {
    render(
      <RunInspectorPanel
        runs={[]}
        nodes={[]}
        edges={[]}
        spans={[]}
        checkpoints={[]}
        evaluations={[]}
        error="trace unavailable"
        onSelectRun={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText("暂无运行记录")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("trace unavailable");
  });

  it("shows a stable loading state", () => {
    render(
      <RunInspectorPanel runs={[]} nodes={[]} edges={[]} spans={[]} checkpoints={[]} evaluations={[]} loading onSelectRun={vi.fn()} onRefresh={vi.fn()} />,
    );
    expect(screen.getByRole("status").textContent).toContain("正在加载运行记录");
  });

  it("routes delivery retry and discard actions", () => {
    const onDeliveryAction = vi.fn();
    const delivery = { delivery_id: "delivery-1", status: "dead_letter", channel: "session", version: 4, last_error: "send failed" };
    render(
      <RunInspectorPanel runs={[run]} selectedRunId={run.run_id} nodes={[]} edges={[]} spans={[]} checkpoints={[]} evaluations={[]} deliveries={[delivery]} onSelectRun={vi.fn()} onRefresh={vi.fn()} onDeliveryAction={onDeliveryAction} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "重试交付" }));
    fireEvent.click(screen.getByRole("button", { name: "丢弃交付" }));
    expect(onDeliveryAction).toHaveBeenNthCalledWith(1, delivery, "retry");
    expect(onDeliveryAction).toHaveBeenNthCalledWith(2, delivery, "discard");
  });

  it("keeps an open decision above the workflow graph", () => {
    render(
      <RunInspectorPanel
        runs={[{ ...run, status: "waiting" as const }]}
        selectedRunId={run.run_id}
        nodes={[{ id: "review", label: "Review", status: "waiting" }]}
        edges={[]}
        spans={[]}
        checkpoints={[]}
        evaluations={[]}
        decisions={[{ decision_id: "decision-1", run_id: run.run_id, kind: "ppt_outline", status: "open", prompt: { title: "确认大纲" }, nonce: "nonce", version: 1, created_at: 1 }]}
        onSelectRun={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    const decision = screen.getByText("需要你的决定").closest("section")!;
    const graph = screen.getByTestId("workflow-graph");
    expect(decision.compareDocumentPosition(graph) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
