// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ToolPublicView } from "../types/messages";
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
