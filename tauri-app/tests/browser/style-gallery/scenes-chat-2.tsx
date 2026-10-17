// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// chat 车道场景（二）：工作流进度、执行步骤、消息流。
// 场景 → 挂载的组件文件（src/ 下）：
//   workflow-progress       → components/workflow/WorkflowProgressGroup.tsx（无详情紧凑态、时间线、跳过阶段、失败重试等）
//   workflow-progress-more  → components/workflow/WorkflowProgressGroup.tsx（v5 概览/控制、v7 子方向、能力卡、交付状态）
//   durable-task-steps      → components/workflow/DurableTaskSteps.tsx、WorkflowProgressGroup.tsx（taskTrace 形态）
//   message-stream          → components/MessageStreamPanel.tsx、components/MessageStreamRows.tsx（嵌入式）
//   message-stream-float    → components/MessageStreamPanel.tsx（浮层式、空态）、MessageStreamRows.tsx
import "./chat-gallery.css";
import { WorkflowProgressGroup } from "../../../src/components/workflow/WorkflowProgressGroup";
import { DurableTaskSteps } from "../../../src/components/workflow/DurableTaskSteps";
import { MessageStreamPanel } from "../../../src/components/MessageStreamPanel";
import { useSubagentStore } from "../../../src/code-panel/subagentStore";
import { noop, useOnce } from "./galleryUtil";
import {
  NOW,
  V7_CHILDREN,
  capabilityOperation,
  delivery,
  inboxItem,
  richStages,
  streamMessages,
  taskTrace,
  v5Projection,
  wfStage,
  wfSummary,
} from "./chat-fixtures";

const pendingRetry = () => new Promise<{ run_id: string }>(() => undefined);

export function WorkflowProgressScene() {
  return (
    <div className="chat-col">
      {/* 无详情紧凑态：运行中 + 交付状态 */}
      <WorkflowProgressGroup
        runId="run-compact"
        summary={wfSummary({ workflow_run_id: "run-compact", workflow_version: undefined, workflow_delivery: delivery("delivering") })}
        stages={[]}
      />
      {/* 无详情紧凑态：失败 + 错误文案 */}
      <WorkflowProgressGroup
        runId="run-compact-fail"
        summary={wfSummary({
          workflow_run_id: "run-compact-fail",
          workflow_version: undefined,
          workflow_status: "failed",
          workflow_error: "调研服务不可用",
          workflow_elapsed_ms: 0,
        })}
        stages={[]}
      />
      {/* 无 summary、无阶段：耗时未记录 */}
      <WorkflowProgressGroup runId="run-empty" stages={[]} />
      {/* 时间线：完成、降级、v5 五字段、指标、下一步 + 当前运行行 + 跳过阶段 */}
      <WorkflowProgressGroup
        runId="run-rich"
        summary={wfSummary({
          workflow_run_id: "run-rich",
          workflow_stage_id: "rerank",
          workflow_action: "重排证据",
          workflow_result: "正在重排",
          workflow_seq: 10,
          workflow_warning_count: 2,
          workflow_skipped_stage_ids: ["synth", "cite", "custom_stage"],
          workflow_diagnostic_codes: ["provider_degraded"],
        })}
        stages={richStages("run-rich")}
      />
      {/* 等待操作 */}
      <WorkflowProgressGroup
        runId="run-wait"
        summary={wfSummary({ workflow_run_id: "run-wait", workflow_status: "waiting", workflow_stage: undefined, workflow_stage_id: "gap" })}
        stages={[wfStage(2, "search", { workflow_run_id: "run-wait" })]}
      />
      {/* 失败：v4 从头重试 + 指标 */}
      <WorkflowProgressGroup
        runId="run-fail"
        summary={wfSummary({
          workflow_run_id: "run-fail",
          workflow_version: "v4",
          workflow_status: "failed",
          workflow_error: "所有来源均超时",
          workflow_retry_action_id: "retry_from_start",
          workflow_metrics: { actual_requests: 12, empty: 3, timeouts: 9, probes: 1 },
        })}
        stages={[wfStage(2, "search", { workflow_run_id: "run-fail" })]}
        onWorkflowRetry={pendingRetry}
      />
      {/* 失败：无重试、无指标 */}
      <WorkflowProgressGroup
        runId="run-fail-plain"
        summary={wfSummary({ workflow_run_id: "run-fail-plain", workflow_status: "failed" })}
        stages={[]}
      />
      {/* 已取消 / 已完成 */}
      <WorkflowProgressGroup
        runId="run-cancel"
        summary={wfSummary({ workflow_run_id: "run-cancel", workflow_status: "cancelled" })}
        stages={[wfStage(2, "search", { workflow_run_id: "run-cancel" })]}
      />
      <WorkflowProgressGroup
        runId="run-done"
        summary={wfSummary({ workflow_run_id: "run-done", workflow_status: "completed" })}
        stages={[wfStage(2, "search", { workflow_run_id: "run-done" }), wfStage(3, "finalize", { workflow_run_id: "run-done" })]}
      />
    </div>
  );
}

export function WorkflowProgressMoreScene() {
  return (
    <div className="chat-col">
      {/* v5 概览 + 控制（立即生成） */}
      <WorkflowProgressGroup
        runId="run-v5"
        summary={wfSummary({
          workflow_run_id: "run-v5",
          workflow_version: "v5",
          workflow_v5: v5Projection({
            predicted_delivery: "insufficient_evidence",
            hard_failures: ["budget"] as never,
            failed_dimensions: [
              { dimension_ordinal: 1, status: "uncovered", reason_codes: ["first_party_requirement_unsatisfied", "evidence_gap"] },
              { dimension_ordinal: 3, status: "partially_covered", reason_codes: ["duplicate_rate_above_threshold"] },
            ],
            rejection_reasons: [
              { reason_code: "dimension_relevance_below_threshold", count: 6 },
              { reason_code: "body_too_short", count: 2 },
            ],
          } as never),
        })}
        stages={[]}
        onWorkflowRetry={pendingRetry}
      />
      {/* v5 收敛中（accepted）+ 继续调研；概览字段取"无/待评估" */}
      <WorkflowProgressGroup
        runId="run-v5-settle"
        summary={wfSummary({
          workflow_run_id: "run-v5-settle",
          workflow_version: "v5",
          workflow_updated_at: NOW - 12_000,
          workflow_v5: v5Projection({
            control_action: "continue_research",
            control_status: "accepted",
            active_gap: "none",
            quality_score: "none",
            soft_checkpoint: "before",
            lease_reason: "none",
          } as never),
        })}
        stages={[]}
        onWorkflowRetry={pendingRetry}
      />
      <WorkflowProgressGroup
        runId="run-v5-cancel"
        summary={wfSummary({
          workflow_run_id: "run-v5-cancel",
          workflow_version: "v5",
          workflow_v5: v5Projection({ control_action: "cancel_settle", control_status: "observed", soft_checkpoint: "none" } as never),
        })}
        stages={[]}
        onWorkflowRetry={pendingRetry}
      />
      <WorkflowProgressGroup
        runId="run-v5-retry"
        summary={wfSummary({
          workflow_run_id: "run-v5-retry",
          workflow_version: "v5",
          workflow_v5: v5Projection({ control_action: "retry_from_start" } as never),
        })}
        stages={[]}
        onWorkflowRetry={pendingRetry}
      />
      {/* v6 运行中：立即生成 */}
      <WorkflowProgressGroup
        runId="run-v6"
        summary={wfSummary({ workflow_run_id: "run-v6", workflow_name: "deep_research", workflow_version: "v6" })}
        stages={[]}
        onWorkflowRetry={pendingRetry}
      />
      {/* v7 子方向 */}
      <WorkflowProgressGroup
        runId="run-v7"
        summary={wfSummary({ workflow_run_id: "run-v7", workflow_version: "v7", workflow_stage: "子代理调研与补救", workflow_total: 6, workflow_v7_children: V7_CHILDREN })}
        stages={[]}
      />
      {/* 能力操作卡 + 交付状态（已完成） */}
      <WorkflowProgressGroup
        runId="run-cap"
        summary={wfSummary({ workflow_run_id: "run-cap", workflow_version: undefined, workflow_status: "completed", workflow_delivery: delivery("retrying") })}
        stages={[]}
        capabilityOperation={capabilityOperation()}
        onCapabilityOperationAction={noop}
      />
      {/* 运行投影终态覆盖 */}
      <WorkflowProgressGroup
        runId="run-proj"
        summary={wfSummary({ workflow_run_id: "run-proj", workflow_completed_count: 6, workflow_total: 9 })}
        stages={[]}
        runProjectionStatus="cancelled"
        runProjection={{
          run_id: "run-proj",
          task_scope_id: "scope",
          version: 4,
          status: "cancelled",
          inflight: false,
          ui_state: "open",
          started_at: NOW - 89_000,
          last_activity: NOW - 1_000,
        }}
      />
    </div>
  );
}

export function DurableTaskStepsScene() {
  return (
    <div className="chat-col">
      <WorkflowProgressGroup runId="run-trace" stages={[]} taskTrace={taskTrace("run-trace")} />
      <WorkflowProgressGroup
        runId="run-trace-done"
        summary={wfSummary({ workflow_run_id: "run-trace-done", workflow_name: "Agent 任务" })}
        stages={[]}
        taskTrace={taskTrace("run-trace-done", {
          status: "completed_with_recovery",
          endedAt: NOW - 1_000,
          steps: taskTrace("x").steps.map((step) => ({ ...step, current: false })),
        })}
      />
      <DurableTaskSteps trace={taskTrace("run-loading", { projectionComplete: false, steps: [] })} />
      <DurableTaskSteps
        trace={taskTrace("run-record", {
          steps: taskTrace("y").steps.slice(0, 2).map((step) => ({ ...step, current: false })),
        })}
      />
    </div>
  );
}

const STREAM_WARNINGS = [inboxItem("w1", "yellow"), inboxItem("w2", "yellow", { project_name: "", session_id: "sid-with-a-very-long-identifier-0001" })];
const STREAM_ERRORS = [inboxItem("e1", "red", { project_name: "sid-stream", diagnosis: "" }), inboxItem("e2", "red", { project_name: "", session_id: "short-sid", user_message: "", diagnosis: "" })];

export function MessageStreamScene() {
  useOnce(() => {
    useSubagentStore.getState().clear();
  });
  return (
    <div className="chat-stream-box">
      <MessageStreamPanel
        embedded
        filter="all"
        chatMessages={streamMessages()}
        warnings={STREAM_WARNINGS}
        errors={STREAM_ERRORS}
        sessionId="sid-stream"
        onSetFilter={noop}
        onDismiss={noop}
        onDismissAll={noop}
        onJumpToSession={noop}
        onChoice={noop}
        onWorkflowRetry={pendingRetry}
      />
    </div>
  );
}

export function MessageStreamFloatScene() {
  useOnce(() => {
    const store = useSubagentStore.getState();
    store.clear();
    store.upsert({ run_id: "f1", task_id: "浮层里的子代理", kind: "research", status: "running", ts: NOW - 5_000 });
  });
  return (
    <div className="chat-grid2">
      <div className="chat-float-box">
        <MessageStreamPanel
          filter="all"
          chatMessages={[{ role: "user", text: "浮层里的消息", ts: NOW - 60_000 }]}
          warnings={[]}
          errors={[]}
          onSetFilter={noop}
          onDismiss={noop}
          onDismissAll={noop}
          onJumpToSession={noop}
          onChoice={noop}
        />
      </div>
      <div className="chat-float-box">
        <MessageStreamPanel
          embedded
          filter="warn"
          chatMessages={[]}
          warnings={[]}
          errors={[]}
          onSetFilter={noop}
          onDismiss={noop}
          onDismissAll={noop}
          onJumpToSession={noop}
          onChoice={noop}
        />
      </div>
    </div>
  );
}
