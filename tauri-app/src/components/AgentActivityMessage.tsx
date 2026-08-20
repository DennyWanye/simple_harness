// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { PublicRunActivityItem, ToolPublicView } from "../types/messages";
import { normalizePublicRunSnapshot } from "../stores/harnessPublicSnapshotStore";

export interface AgentPublicOutput {
  id: string;
  text: string;
  ts: number;
}

export type AgentActivityTraceItem = {
  kind: "assistant";
  id: string;
  text: string;
  ts: number;
};

export interface WorkflowTaskTool {
  id: string;
  callId: string;
  name: string;
  action: string;
  target?: string;
  args?: unknown;
  ok?: boolean;
  status: string;
  result?: unknown;
  detailRef?: string;
  detailsAvailable: boolean;
  resultAvailable: boolean;
  truncated: boolean;
  unavailableReason?: string;
  durationMs?: number;
  ts: number;
}

export interface WorkflowTaskStep {
  id: string;
  index: number;
  title: string;
  status: string;
  current: boolean;
  messages: string[];
  tools: WorkflowTaskTool[];
  substeps: Array<{
    id: string;
    label: string;
    status: string;
    current: boolean;
    index: number;
    total?: number;
  }>;
}

export interface WorkflowTaskTrace {
  runId: string;
  status: string;
  steps: WorkflowTaskStep[];
  startedAt: number;
  endedAt?: number;
  projectionComplete: boolean;
}

export interface ActivityTimelineItem {
  id: string;
  kind: string;
  title: string;
  status: string;
  phaseId?: string;
  action?: string;
  toolName?: string;
  target?: string;
  text?: string;
  input?: unknown;
  result?: unknown;
  detailRef?: string;
  durationMs?: number;
  createdAt?: number;
  truncated: boolean;
  contextVisibility: "exclude";
}

function toolOk(status: string): boolean | undefined {
  if (["completed", "succeeded", "settled"].includes(status)) return true;
  if (["failed", "cancelled", "rejected"].includes(status)) return false;
  return undefined;
}

function taskTool(view: ToolPublicView, status = view.status): WorkflowTaskTool {
  return {
    id: view.stable_id,
    callId: view.tool_ref,
    name: view.public_name,
    action: view.action_label,
    target: view.safe_target_label ?? undefined,
    args: view.public_input,
    ok: toolOk(status),
    status,
    result: view.public_result,
    detailRef: view.detail_ref ?? undefined,
    detailsAvailable: view.details_available === true,
    resultAvailable: view.result_available === true,
    truncated: view.truncated === true,
    unavailableReason: view.unavailable_reason ?? undefined,
    durationMs: view.duration_ms ?? undefined,
    ts: (view.created_at ?? 0) * 1000,
  };
}

function boundedText(value: unknown): { text?: string; truncated: boolean } {
  if (typeof value !== "string" || !value.trim()) return { truncated: false };
  const codepoints = [...value];
  if (codepoints.length <= 2048) return { text: value, truncated: false };
  return { text: codepoints.slice(0, 2048).join("") + "…", truncated: true };
}

function settleTimelineStatus(status: string, aggregateStatus: string): string {
  if (![
    "pending", "prepared", "accepted", "running", "waiting", "unknown",
  ].includes(status)) return status;
  if (["running", "waiting", "unknown"].includes(aggregateStatus)) return status;
  if (aggregateStatus === "cancelled") return "cancelled";
  if (aggregateStatus === "failed") return "failed";
  return "completed";
}

function fromPublicActivity(
  item: PublicRunActivityItem,
  aggregateStatus: string,
): ActivityTimelineItem {
  const text = boundedText(item.safe_text);
  const inputText = boundedText(typeof item.public_input === "string" ? item.public_input : undefined);
  const resultText = boundedText(typeof item.public_result === "string" ? item.public_result : undefined);
  return {
    id: item.stable_id,
    kind: item.kind,
    title: item.title || "执行记录",
    status: settleTimelineStatus(item.status || "unknown", aggregateStatus),
    phaseId: item.phase_id ?? undefined,
    action: item.action_code ?? undefined,
    toolName: item.tool_name ?? undefined,
    target: item.safe_target_label ?? undefined,
    text: text.text,
    input: typeof item.public_input === "string" ? inputText.text : item.public_input,
    result: typeof item.public_result === "string" ? resultText.text : item.public_result,
    detailRef: item.detail_ref ?? undefined,
    durationMs: item.duration_ms ?? undefined,
    createdAt: item.created_at ?? undefined,
    truncated: item.truncated === true || text.truncated || inputText.truncated || resultText.truncated,
    contextVisibility: "exclude",
  };
}

/**
 * Pure, display-only projection of the normalized V3 snapshot.  It has no
 * access to transport messages, raw ledger rows, or context assembly.
 */
export function buildActivityTimeline(value: unknown): ActivityTimelineItem[] {
  const snapshot = normalizePublicRunSnapshot(value);
  if (!snapshot) return [];
  const aggregateStatus = snapshot.aggregate_outcome.status;
  const toolsByStableId = new Map(snapshot.tool_public_views.map((tool) => [tool.stable_id, tool]));
  const explicit = (snapshot.activity_items ?? []).map((item) => {
    const timelineItem = fromPublicActivity(item, aggregateStatus);
    const tool = toolsByStableId.get(item.stable_id);
    return tool ? {
      ...timelineItem,
      title: tool.public_name || timelineItem.title,
      status: settleTimelineStatus(tool.status, aggregateStatus),
      action: tool.action_label || timelineItem.action,
      target: tool.safe_target_label ?? timelineItem.target,
      input: tool.public_input ?? timelineItem.input,
      result: tool.public_result ?? timelineItem.result,
      detailRef: tool.detail_ref ?? timelineItem.detailRef,
      durationMs: tool.duration_ms ?? timelineItem.durationMs,
      truncated: tool.truncated === true || timelineItem.truncated,
    } : timelineItem;
  });
  const phaseIndex = new Map(snapshot.semantic_phases.map((phase, index) => [phase.phase_id, index]));
  const fallback: ActivityTimelineItem[] = [];
  if (explicit.length === 0) {
    for (const phase of snapshot.semantic_phases) {
      for (const item of phase.items) {
        const text = boundedText(item.safe_text);
        fallback.push({
          id: item.stable_id,
          kind: item.kind,
          title: item.public_name ?? item.tool_name ?? item.action_code ?? "执行记录",
          status: settleTimelineStatus(item.status ?? "unknown", aggregateStatus),
          phaseId: phase.phase_id,
          action: item.action_code ?? undefined,
          toolName: item.tool_name ?? undefined,
          target: item.safe_target_label ?? undefined,
          text: text.text,
          detailRef: item.detail_ref ?? undefined,
          createdAt: item.created_at ?? undefined,
          truncated: text.truncated,
          contextVisibility: "exclude",
        });
      }
    }
    for (const tool of snapshot.tool_public_views) {
      fallback.push(fromPublicActivity({
        stable_id: tool.stable_id,
        kind: "tool",
        title: tool.public_name,
        status: tool.status,
        phase_id: tool.phase_id,
        action_code: tool.action_label,
        tool_name: tool.public_name,
        safe_target_label: tool.safe_target_label,
        detail_ref: tool.detail_ref,
        public_input: tool.public_input,
        public_result: tool.public_result,
        duration_ms: tool.duration_ms,
        created_at: tool.created_at,
        truncated: tool.truncated,
        context_visibility: "exclude",
      }, aggregateStatus));
    }
    for (const message of snapshot.public_messages) {
      fallback.push(fromPublicActivity({
        stable_id: message.stable_id,
        kind: "narration",
        title: "更新进度",
        status: aggregateStatus,
        phase_id: message.phase_id,
        safe_text: message.text,
        created_at: message.created_at,
        context_visibility: "exclude",
      }, aggregateStatus));
    }
  }
  const terminalStatuses = new Set(["completed", "completed_with_recovery", "failed", "cancelled"]);
  const all = explicit.length > 0 ? explicit : fallback;
  const terminalId = `run-terminal:${snapshot.root_run_id}`;
  if (terminalStatuses.has(aggregateStatus) && !all.some((item) => item.id === terminalId)) {
    all.push({
      id: terminalId,
      kind: "run_terminal",
      title: "任务结束",
      status: aggregateStatus,
      createdAt: snapshot.aggregate_outcome.ended_at ?? undefined,
      truncated: false,
      contextVisibility: "exclude",
    });
  }
  const deduped = new Map<string, ActivityTimelineItem>();
  for (const item of all) {
    if (!deduped.has(item.id)) deduped.set(item.id, item);
  }
  return [...deduped.values()].sort((left, right) => {
    const leftTime = left.createdAt ?? Number.POSITIVE_INFINITY;
    const rightTime = right.createdAt ?? Number.POSITIVE_INFINITY;
    return leftTime - rightTime
      || (phaseIndex.get(left.phaseId ?? "") ?? Number.MAX_SAFE_INTEGER)
        - (phaseIndex.get(right.phaseId ?? "") ?? Number.MAX_SAFE_INTEGER)
      || left.id.localeCompare(right.id);
  });
}

export function buildAgentPublicOutputs(
  value: unknown,
): AgentPublicOutput[] {
  const snapshot = normalizePublicRunSnapshot(value);
  if (!snapshot) return [];
  return [...new Map(snapshot.public_messages.map((message) => [message.stable_id, message])).values()]
    .map((message) => ({
      id: message.stable_id,
      text: message.text,
      ts: message.created_at * 1000,
    }))
    .sort((left, right) => left.ts - right.ts || left.id.localeCompare(right.id));
}

/**
 * The right-hand activity UI is a view of the same semantic phases as the
 * inspector graph. It does not rebuild phases or terminal status from facts.
 */
export function buildWorkflowTaskTraces(
  value: unknown,
): WorkflowTaskTrace[] {
  const snapshot = normalizePublicRunSnapshot(value);
  if (!snapshot) return [];
  if (snapshot.semantic_phases.length === 0 && snapshot.tool_public_views.length === 0) {
    return [];
  }
  const aggregateActive = ["running", "waiting", "unknown"].includes(
    snapshot.aggregate_outcome.status,
  );
  const settleOpenToolStatus = (status: string): string => {
    if (
      aggregateActive ||
      !["pending", "prepared", "accepted", "running", "waiting", "unknown"].includes(status)
    ) {
      return status;
    }
    if (snapshot.aggregate_outcome.status === "cancelled") return "cancelled";
    if (snapshot.aggregate_outcome.status === "failed") return "failed";
    return "completed";
  };
  const settleOpenStepStatus = (status: string): string => {
    if (
      aggregateActive ||
      !["pending", "prepared", "accepted", "running", "waiting", "unknown"].includes(status)
    ) {
      return status;
    }
    if (snapshot.aggregate_outcome.status === "cancelled") return "cancelled";
    if (snapshot.aggregate_outcome.status === "failed") return "failed";
    return "completed";
  };
  const toolsByPhase = new Map<string, WorkflowTaskTool[]>();
  const seenTools = new Set<string>();
  for (const publicTool of snapshot.tool_public_views) {
    if (seenTools.has(publicTool.stable_id)) continue;
    seenTools.add(publicTool.stable_id);
    const phaseId = publicTool.phase_id ?? snapshot.semantic_phases.at(-1)?.phase_id ?? "legacy";
    const bucket = toolsByPhase.get(phaseId) ?? [];
    bucket.push(taskTool(publicTool, settleOpenToolStatus(publicTool.status)));
    toolsByPhase.set(phaseId, bucket);
  }
  const messagesByPhase = new Map<string, string[]>();
  const seenMessages = new Set<string>();
  for (const message of snapshot.public_messages) {
    if (seenMessages.has(message.stable_id)) continue;
    seenMessages.add(message.stable_id);
    const phaseId = message.phase_id ?? snapshot.semantic_phases.at(-1)?.phase_id ?? "legacy";
    const bucket = messagesByPhase.get(phaseId) ?? [];
    bucket.push(message.text);
    messagesByPhase.set(phaseId, bucket);
  }
  const currentPhase = snapshot.semantic_phases.find((phase) =>
    ["running", "waiting", "failed", "cancelled"].includes(phase.status),
  ) ?? snapshot.semantic_phases.at(-1);
  const steps: WorkflowTaskStep[] = snapshot.semantic_phases.map((phase, index) => ({
    id: phase.phase_id,
    index,
    title: phase.title,
    status: settleOpenStepStatus(phase.status),
    current: aggregateActive && phase.phase_id === currentPhase?.phase_id,
    messages: messagesByPhase.get(phase.phase_id) ?? [],
    tools: (toolsByPhase.get(phase.phase_id) ?? []).sort((left, right) =>
      left.ts - right.ts || left.id.localeCompare(right.id),
    ),
    substeps: [...new Map(
      phase.workflow_steps.map((step) => [step.workflow_step_id, step]),
    ).values()].map((step, stepIndex) => ({
      id: step.workflow_step_id,
      label: step.label ?? `步骤 ${(step.step_index ?? stepIndex) + 1}`,
      status: settleOpenStepStatus(step.status ?? "unknown"),
      current: aggregateActive && (
        ["running", "waiting"].includes(step.status ?? "") ||
        (phase.current_step != null && step.step_index === phase.current_step)
      ),
      index: step.step_index ?? stepIndex,
      total: step.step_total ?? phase.total_steps ?? undefined,
    })),
  }));
  if (steps.length === 0) {
    steps.push({
      id: "legacy",
      index: 0,
      title: "历史运行记录",
      status: snapshot.aggregate_outcome.status,
      current: false,
      messages: [],
      tools: toolsByPhase.get("legacy") ?? [],
      substeps: [],
    });
  }
  const activityTimestamps = [
    ...snapshot.public_messages.map((message) => message.created_at * 1000),
    ...snapshot.tool_public_views.map((tool) => (tool.created_at ?? 0) * 1000),
  ].filter((timestamp) => Number.isFinite(timestamp) && timestamp > 0);
  return [{
    runId: snapshot.root_run_id,
    status: snapshot.aggregate_outcome.status,
    steps,
    startedAt: typeof snapshot.aggregate_outcome.started_at === "number"
      ? snapshot.aggregate_outcome.started_at * 1000
      : activityTimestamps.length > 0
      ? Math.min(...activityTimestamps)
      : Date.now(),
    endedAt: typeof snapshot.aggregate_outcome.ended_at === "number"
      ? snapshot.aggregate_outcome.ended_at * 1000
      : snapshot.projection_complete && activityTimestamps.length > 0
      ? Math.max(...activityTimestamps)
      : undefined,
    projectionComplete: snapshot.projection_complete,
  }];
}

export function buildAgentActivityTrace(
  value: unknown,
  excludedRunIds: ReadonlySet<string> = new Set(),
): AgentActivityTraceItem[] {
  const snapshot = normalizePublicRunSnapshot(value);
  if (!snapshot) return [];
  if (excludedRunIds.has(snapshot.root_run_id)) return [];
  return buildAgentPublicOutputs(snapshot).map((output) => ({
    kind: "assistant",
    ...output,
  }));
}
