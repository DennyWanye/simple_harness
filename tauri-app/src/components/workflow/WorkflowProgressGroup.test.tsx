import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Message, WorkflowV5ProgressProjection } from "../../stores/sessionsStore";
import type { CapabilityOperation } from "../../types/capabilities";
import type { WorkflowTaskTrace } from "../AgentActivityMessage";
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
    workflow_action: `执行 ${stageId}`,
    workflow_result: `完成 ${stageId}，保留有效结果`,
    ...overrides,
  };
}

function v5Projection(
  overrides: Partial<WorkflowV5ProgressProjection> = {},
): WorkflowV5ProgressProjection {
  return {
    action: "evaluate_gaps", result: "completed", discarded: 2,
    remaining_gap: 1, next_step: "research_gap",
    dimension_counts: {
      total: 5, core_total: 3, covered: 3, partially_covered: 1,
      uncovered: 1, not_applicable: 0, core_covered: 2,
      core_partially_covered: 1, core_uncovered: 0,
    },
    dimension_status_changes: { improved: 1, regressed: 0, unchanged: 4 },
    source_counts: { valid: 8, first_party: 3 },
    active_gap: { status: "running", work_kind: "query", dimension_ordinal: 2 },
    elapsed_seconds: 92, soft_checkpoint: "reached", lease_reason: "lease_renewed_measurable_gain",
    quality_score: 87, hard_failures: [], predicted_delivery: "partial",
    token_budget_ratio: 42, control_action: "generate_now", control_status: "open",
    parent_operation: "none", failed_dimensions: [], rejection_reasons: [], ...overrides,
  };
}

function capabilityOperation(): CapabilityOperation {
  return {
    operation_id: "operation-workflow",
    capability_id: "godot",
    capability_name: "Godot",
    kind: "build",
    phase: "verified",
    status: "running",
    authorization_mode: "auto",
    current_validation: "验证 Godot 能力包",
    latest_result: "清单哈希有效",
    available_actions: ["cancel"],
    artifacts: [],
    verification_receipts: [],
  };
}

describe("WorkflowProgressGroup", () => {
  it("uses the shared terminal Run projection over a stale running summary", () => {
    const taskTrace: WorkflowTaskTrace = {
      runId: "run-recovered",
      status: "completed_with_recovery",
      startedAt: 1_000,
      endedAt: 61_000,
      projectionComplete: true,
      steps: [{
        id: "validation",
        index: 0,
        title: "验证与修复",
        status: "completed_with_recovery",
        current: false,
        messages: [],
        tools: [],
        substeps: [],
      }],
    };
    render(
      <WorkflowProgressGroup
        runId="run-recovered"
        summary={summary({
          workflow_run_id: "run-recovered",
          workflow_status: "running",
          workflow_started_at: 1_000,
          workflow_elapsed_ms: 0,
        })}
        stages={[]}
        taskTrace={taskTrace}
      />,
    );

    const progress = screen.getByTestId("workflow-progress-run-recovered");
    expect(progress.getAttribute("data-status")).toBe("completed");
    expect(progress.textContent).toContain("已完成");
    expect(progress.textContent).toContain("1/1 步");
    expect(progress.textContent).toContain("已用时 1 分");
    expect(progress.textContent).not.toContain("进行中");
  });

  it("renders a capability operation projected by the existing workflow stream", () => {
    const onAction = vi.fn();
    render(
      <WorkflowProgressGroup
        runId="run-capability"
        summary={summary({ workflow_run_id: "run-capability" })}
        stages={[]}
        capabilityOperation={capabilityOperation()}
        onCapabilityOperationAction={onAction}
      />,
    );

    expect(
      screen.getByTestId("capability-operation-operation-workflow"),
    ).toBeTruthy();
    expect(screen.getByText("Auto 已授权（审计记录）")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onAction).toHaveBeenCalledWith(
      "cancel",
      expect.objectContaining({ operation_id: "operation-workflow" }),
    );
  });

  it("renders v7 business directions instead of scheduler attempt rows", () => {
    render(
      <WorkflowProgressGroup
        runId="run-v7"
        summary={summary({
          workflow_run_id: "run-v7",
          workflow_version: "v7",
          workflow_stage: "子代理调研与补救",
          workflow_total: 6,
          workflow_v7_children: [
            { child_id: "dr-0", question: "运行时架构是什么？", status: "valid", attempt: 1, max_attempts: 2, n_sources: 2 },
            { child_id: "dr-1", question: "核心组件有哪些？", status: "valid", attempt: 1, max_attempts: 2, n_sources: 1 },
            { child_id: "dr-2", question: "适用场景是什么？", status: "insufficient", attempt: 2, max_attempts: 2, n_sources: 0 },
            { child_id: "dr-3", question: "常见陷阱有哪些？", status: "running", attempt: 1, max_attempts: 2, n_sources: 0 },
          ],
        })}
        stages={[]}
      />,
    );

    const panel = screen.getByTestId("workflow-v7-children");
    expect(panel.textContent).toContain("主 Agent 拆出的 4 个子方向");
    expect(screen.getAllByTestId(/workflow-v7-child-dr-/)).toHaveLength(4);
    expect(screen.getByTestId("workflow-v7-child-dr-2").textContent).toContain("尝试 2/2");
    expect(screen.getByTestId("workflow-v7-child-dr-2").textContent).toContain("证据不足");
  });

  it("renders the receipt-derived delivery status independently from engine completion", () => {
    render(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary({
          workflow_version: "v6",
          workflow_status: "completed",
          workflow_delivery: {
            schema_version: 1,
            run_id: "run-group",
            manifest_ref: "manifest-v6",
            status: "retrying",
            required_total: 1,
            pending: 0,
            delivering: 0,
            delivered: 0,
            retrying: 1,
            fenced: 0,
            failed: 0,
            updated_at: 10,
          },
        })}
        stages={[]}
      />,
    );
    expect(screen.getByTestId("workflow-delivery-status").textContent).toBe("交付重试中");
    expect(screen.getByText("已完成")).toBeTruthy();
  });

  it("shows the safe v5 overview and only exposes server-projected controls", () => {
    const onAction = vi.fn(async () => ({ run_id: "run-v5", accepted: true }));
    render(
      <WorkflowProgressGroup
        runId="run-v5"
        summary={summary({
          workflow_run_id: "run-v5", workflow_version: "v5",
          workflow_v5: v5Projection(), workflow_capability: "deep_research_progress_v5",
        })}
        stages={[]}
        onWorkflowRetry={onAction}
      />,
    );
    const overview = screen.getByTestId("workflow-v5-overview");
    expect(overview.textContent).toContain("核心覆盖 2/3");
    expect(overview.textContent).toContain("有效/第一方来源 8/3");
    expect(overview.textContent).toContain("质量 87");
    fireEvent.click(screen.getByRole("button", { name: "立即用现有证据生成" }));
    expect(onAction).toHaveBeenCalledWith("run-v5", "generate_now", expect.any(String));
  });

  it("offers generate-now for a running v6 deep-research run and synchronously deduplicates rapid clicks", () => {
    const pending = new Promise<{ run_id: string }>(() => undefined);
    const onAction = vi.fn(() => pending);
    const rendered = render(
      <WorkflowProgressGroup
        runId="run-v6"
        summary={summary({
          workflow_run_id: "run-v6",
          workflow_name: "deep_research",
          workflow_version: "v6",
          workflow_status: "running",
          workflow_diagnostic_codes: ["insufficient_evidence"],
        })}
        stages={[]}
        onWorkflowRetry={onAction}
      />,
    );
    const button = screen.getByRole("button", { name: "立即用现有证据生成" });

    act(() => {
      button.click();
      button.click();
    });

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith("run-v6", "generate_now", expect.any(String));

    rendered.rerender(
      <WorkflowProgressGroup
        runId="run-v6"
        summary={summary({
          workflow_run_id: "run-v6",
          workflow_name: "deep_research",
          workflow_version: "v6",
          workflow_status: "completed",
        })}
        stages={[]}
        onWorkflowRetry={onAction}
      />,
    );
    expect(screen.queryByRole("button", { name: "立即用现有证据生成" })).toBeNull();
  });

  it("explains insufficient evidence with only safe projected reasons", () => {
    render(
      <WorkflowProgressGroup
        runId="run-v5-insufficient"
        summary={summary({
          workflow_run_id: "run-v5-insufficient",
          workflow_version: "v5",
          workflow_v5: v5Projection({
            predicted_delivery: "insufficient_evidence",
            failed_dimensions: [{
              dimension_ordinal: 1,
              status: "uncovered",
              reason_codes: ["first_party_requirement_unsatisfied", "evidence_gap"],
            }],
            rejection_reasons: [
              { reason_code: "dimension_relevance_below_threshold", count: 6 },
              { reason_code: "body_too_short", count: 2 },
            ],
          }),
        })}
        stages={[]}
      />,
    );
    const overview = screen.getByTestId("workflow-v5-overview");
    expect(overview.textContent).toContain("维度 2（未覆盖）：缺少所需第一方来源、证据覆盖不足");
    expect(overview.textContent).toContain("与调研维度相关度不足 6");
    expect(overview.textContent).toContain("正文过短 2");
    expect(overview.textContent).not.toContain("https://");
    expect(overview.textContent).not.toContain("dimension_id");
  });

  it("renders the fixed five-field v5 bubble without raw diagnostics", () => {
    render(
      <WorkflowProgressGroup
        runId="run-v5-stage"
        summary={summary({ workflow_run_id: "run-v5-stage", workflow_version: "v5", workflow_v5: v5Projection({ control_action: "none" }) })}
        stages={[stage(3, "gap_evaluate", {
          workflow_version: "v5", workflow_v5: v5Projection({ control_action: "none" }),
          workflow_error: undefined, workflow_diagnostic_codes: undefined,
        })]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "gap_evaluate，完成，查看详情" }));
    for (const label of ["做了什么", "得到什么", "舍弃什么", "仍缺什么", "下一步"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText(/raw provider error/i)).toBeNull();
  });

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
    expect(screen.getByTestId("workflow-progress-run-group").style.flexShrink).toBe("0");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("workflow-compact-timeline-run-group")).toBeTruthy();
    expect(screen.getAllByText("执行 search").length).toBeGreaterThan(0);
    expect(screen.getAllByText("完成 fetch，保留有效结果").length).toBeGreaterThan(0);
    const row = screen.getByRole("button", { name: "search，完成，查看详情" });
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById(row.getAttribute("aria-controls")!)?.hidden).toBe(true);
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
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const row = screen.getByRole("button", { name: "search，完成，查看详情" });
    expect(row.tagName).toBe("BUTTON");
    const details = document.getElementById(row.getAttribute("aria-controls")!);
    expect(details?.hidden).toBe(false);
    expect(screen.getByText("来源: 3")).toBeTruthy();
    expect(screen.getByText("候选: 18")).toBeTruthy();
    expect(screen.queryByText(/do-not-render/)).toBeNull();
    expect(screen.getByText("下一步：direct")).toBeTruthy();
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    expect(details?.hidden).toBe(true);
  });

  it("shows the current summary as a live row and replaces it with the matching durable child", () => {
    const { rerender } = render(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary({
          workflow_stage_id: "fetch",
          workflow_action: "抓取并提取来源正文",
          workflow_result: "正在抓取正文",
          workflow_seq: 4,
        })}
        stages={[stage(2, "search")]}
      />,
    );
    expect(screen.getAllByText("正在抓取正文").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "搜索资料，进行中，查看详情" })).toBeTruthy();

    rerender(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary({ workflow_stage_id: "fetch", workflow_seq: 4 })}
        stages={[stage(2, "search"), stage(4, "fetch")]}
      />,
    );
    expect(screen.queryByRole("button", { name: "搜索资料，进行中，查看详情" })).toBeNull();
    expect(screen.getByRole("button", { name: "fetch，完成，查看详情" })).toBeTruthy();
  });

  it("surfaces degraded reasons without requiring detail expansion", () => {
    render(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary({ workflow_stage_id: "cite", workflow_seq: 8 })}
        stages={[stage(8, "cite", {
          workflow_degraded: true,
          workflow_action: "核验论断与引用",
          workflow_result: "发布 9 条，丢弃 3 条，支持率 75%",
          workflow_diagnostic_codes: ["claim_pruned", "provider_degraded"],
        })]}
      />,
    );
    expect(screen.getAllByText("发布 9 条，丢弃 3 条，支持率 75%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("无支持论断已删除；部分来源已降级").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "cite，降级完成，查看详情" })).toBeTruthy();
  });

  it("localizes every production v3 branch and publish-gate reason in the compact row", () => {
    const diagnosticCodes = [
      "deadline_exhausted", "search_port_unavailable", "provider_failure",
      "direct_failure", "fetch_failure", "blob_unavailable", "low_quality_source",
      "low_quality_content", "search_degraded", "support_rate_below_threshold",
      "published_factual_below_threshold", "citation_count_below_threshold",
      "domain_count_below_threshold", "body_bytes_below_threshold",
    ];
    const expectedReasons = [
      "本阶段已达到时间上限", "搜索服务暂不可用", "搜索来源调用失败",
      "一手来源查询失败", "来源正文抓取失败", "已抓取正文暂不可读取",
      "低质量来源已过滤", "低质量正文已过滤", "搜索能力已降级",
      "论断证据支持率未达发布标准", "可发布事实论断数量未达标准",
      "有效引用数量未达标准", "独立来源域名数量未达标准",
      "报告正文长度未达发布标准",
    ];
    render(
      <WorkflowProgressGroup
        runId="run-group"
        summary={summary({ workflow_stage_id: "cite", workflow_seq: 8 })}
        stages={[stage(8, "cite", {
          workflow_degraded: true,
          workflow_diagnostic_codes: diagnosticCodes,
        })]}
      />,
    );

    const compactRow = screen.getByTestId("timeline-row-event-8");
    expectedReasons.forEach((reason) => expect(compactRow.textContent).toContain(reason));
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
          workflow_started_at: 0,
          workflow_updated_at: 10_000,
        })}
        stages={[]}
      />,
    );
    expect(screen.getByText(/10 绉?/)).toBeTruthy();
    act(() => vi.advanceTimersByTime(5_000));
    expect(screen.getByText(/15 绉?/)).toBeTruthy();
  });

  it("shows failed v4 coverage and skipped stages without presenting them as success", () => {
    render(
      <WorkflowProgressGroup
        runId="failed-v4"
        summary={summary({
          workflow_run_id: "failed-v4",
          workflow_version: "v4",
          workflow_status: "failed",
          workflow_error: "coverage too low",
          workflow_metrics: {
            actual_requests: 7,
            empty: 3,
            timeouts: 1,
            cooldown_skips: 4,
            probes: 2,
          },
          workflow_skipped_stage_ids: ["fetch", "score", "persist"],
          workflow_retry_action_id: "retry_from_start",
        })}
        stages={[]}
        onWorkflowRetry={vi.fn()}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("coverage too low");
    for (const value of ["7", "3", "1", "4", "2"]) {
      expect(alert.textContent).toContain(value);
    }
    expect(alert.textContent).toContain("cooldown");
    expect(alert.textContent).toContain("probe");

    const skippedToggle = document.querySelector<HTMLButtonElement>(
      'button[aria-controls^="workflow-skipped-"]',
    );
    expect(skippedToggle).toBeTruthy();
    expect(skippedToggle?.closest("div")?.getAttribute("style")).not.toContain("52, 211, 153");
    const skippedDetails = document.getElementById(skippedToggle!.getAttribute("aria-controls")!);
    expect(skippedDetails?.hidden).toBe(true);
    fireEvent.click(skippedToggle!);
    expect(skippedDetails?.hidden).toBe(false);
    expect(skippedDetails?.querySelectorAll("span")).toHaveLength(3);
  });

  it("keeps the same retry key when a slow response becomes ambiguous", () => {
    vi.useFakeTimers();
    const pending = new Promise<{ run_id: string }>(() => undefined);
    const onWorkflowRetry = vi.fn<(
      runId: string,
      actionId: "generate_now" | "continue_research" | "retry_from_start" | "cancel_settle",
      retryKey: string,
    ) => Promise<{ run_id: string; accepted?: boolean }>>(() => pending);
    render(
      <WorkflowProgressGroup
        runId="failed-v4-retry"
        summary={summary({
          workflow_run_id: "failed-v4-retry",
          workflow_version: "v4",
          workflow_status: "failed",
          workflow_error: "coverage too low",
          workflow_retry_action_id: "retry_from_start",
        })}
        stages={[]}
        onWorkflowRetry={onWorkflowRetry}
      />,
    );

    const retryButton = screen.getByRole("alert").querySelector("button");
    expect(retryButton).toBeTruthy();
    fireEvent.click(retryButton!);
    expect(onWorkflowRetry).toHaveBeenCalledTimes(1);
    const firstKey = onWorkflowRetry.mock.calls[0][2];
    expect(firstKey).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);

    act(() => vi.advanceTimersByTime(15_000));
    fireEvent.click(retryButton!);
    expect(onWorkflowRetry).toHaveBeenCalledTimes(2);
    expect(onWorkflowRetry.mock.calls[1][2]).toBe(firstKey);
  });
});
