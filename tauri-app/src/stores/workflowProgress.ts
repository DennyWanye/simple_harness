// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type {
  WorkflowProgressStatus,
  WorkflowV5ControlAction,
  WorkflowV5ProgressProjection,
  WorkflowEventEnvelope,
  Message,
} from "./sessionTypes";
import { dedupe_ppt_outline_messages } from "./historyReconcile";
import {
  WORKFLOW_CARD_EVENTS,
  workflowEventTime,
  workflowErrorText,
  workflowNumber,
  parseWorkflowDeliveryAggregate,
  workflowSafeText,
  workflowResultCode,
  workflowDiagnosticCodes,
  workflowSkippedStageIds,
  sanitizeWorkflowMetrics,
  V5_CONTROL_ACTIONS,
  parseV5Progress,
  isDeepResearchCompletedStage,
  parseV7Children,
} from "./workflowProgressParse";

export function appendWorkflowStage(
  messages: Message[],
  event: WorkflowEventEnvelope,
  payload: Record<string, unknown>,
  runId: string,
  seq: number,
): Message[] {
  const rootRunId = String(event.root_run_id || event.task_root_run_id || "").trim();
  const taskScopeId = String(event.task_scope_id || "").trim();
  const v5 = parseV5Progress(payload);
  if (v5 && payload.kind === "stage" && payload.status === "completed") {
    const stageInstanceId = String(payload.stage_instance_id || "");
    const eventId = String(event.event_id || "").trim();
    const visibility =
      payload.visibility === "visible"
        ? "visible"
        : payload.visibility === "hidden"
          ? "hidden"
          : undefined;
    if (!eventId || !/^[0-9a-f]{24}$/.test(stageInstanceId) || !visibility) return messages;
    const duplicate = messages.some(
      (message) =>
        message.role === "workflow_stage" &&
        message.workflow_run_id === runId &&
        (message.workflow_stage_instance_id === stageInstanceId ||
          message.workflow_event_id === eventId),
    );
    if (duplicate) return messages;
    const stageId = String(payload.public_stage_id || payload.stage_id || "");
    const child: Message = {
      id: `workflow-stage:${runId}:${stageInstanceId}`,
      role: "workflow_stage",
      text: String(payload.summary || "阶段已完成"),
      ts: workflowEventTime(event),
      run_id: rootRunId || undefined,
      task_scope_id: taskScopeId || undefined,
      workflow_run_id: runId,
      workflow_name: "深度调研",
      workflow_version: "v5",
      workflow_status: "completed",
      workflow_stage: String(payload.stage || "已完成阶段"),
      workflow_stage_id: stageId,
      workflow_stage_instance_id: stageInstanceId,
      workflow_transition: "completed",
      workflow_ordinal: workflowNumber(payload.ordinal),
      workflow_total: workflowNumber(payload.total) ?? 9,
      workflow_seq: seq,
      workflow_event_id: eventId,
      workflow_action: v5.action,
      workflow_result: v5.result,
      workflow_discarded: typeof v5.discarded === "number" ? v5.discarded : undefined,
      workflow_next_stage: v5.next_step === "none" ? undefined : v5.next_step,
      workflow_degraded:
        v5.hard_failures.length > 0 ||
        ["partial", "insufficient_evidence"].includes(v5.predicted_delivery),
      workflow_capability: "deep_research_progress_v5",
      workflow_visibility: visibility,
      workflow_v5: v5,
    };
    const insertAt = messages.findIndex((message) => message.ts > child.ts);
    const updated = [...messages];
    updated.splice(insertAt < 0 ? updated.length : insertAt, 0, child);
    return updated;
  }
  if (!isDeepResearchCompletedStage(payload)) return messages;
  const eventId = String(event.event_id || "").trim();
  if (!eventId) return messages;
  const duplicate = messages.some(
    (message) =>
      message.role === "workflow_stage" &&
      message.workflow_run_id === runId &&
      (message.workflow_event_id === eventId || message.workflow_seq === seq),
  );
  if (duplicate) return messages;

  const stageId = String(payload.stage_id || "").trim();
  const summary = String(payload.summary || payload.text || "").trim();
  const child: Message = {
    id: `workflow-stage:${eventId}`,
    role: "workflow_stage",
    text: summary,
    ts: workflowEventTime(event),
    run_id: rootRunId || undefined,
    task_scope_id: taskScopeId || undefined,
    workflow_run_id: runId,
    workflow_name: String(payload.workflow_label || "深度调研"),
    workflow_version: String(payload.workflow_version || ""),
    workflow_status: "completed",
    workflow_stage: String(payload.stage || stageId || "已完成阶段"),
    workflow_stage_id: stageId,
    workflow_stage_instance_id: String(payload.stage_instance_id || ""),
    workflow_transition: "completed",
    workflow_ordinal: workflowNumber(payload.ordinal),
    workflow_total: workflowNumber(payload.total) ?? 13,
    workflow_completed_count: workflowNumber(payload.completed_count),
    workflow_seq: seq,
    workflow_event_id: eventId,
    workflow_metrics: sanitizeWorkflowMetrics(stageId, payload.metrics),
    workflow_duration_ms: workflowNumber(payload.duration_ms),
    workflow_degraded: Boolean(payload.degraded),
    workflow_action: workflowSafeText(payload.action),
    workflow_result: workflowSafeText(payload.result),
    workflow_result_code: workflowResultCode(payload.result_code),
    workflow_diagnostic_codes: workflowDiagnosticCodes(payload.diagnostic_codes),
    workflow_published: workflowNumber(payload.published),
    workflow_discarded: workflowNumber(payload.discarded),
    workflow_repaired: workflowNumber(payload.repaired),
    workflow_next_stage: typeof payload.next_stage === "string" ? payload.next_stage : undefined,
  };
  const insertAt = messages.findIndex((message) => message.ts > child.ts);
  const updated = [...messages];
  updated.splice(insertAt < 0 ? updated.length : insertAt, 0, child);
  return updated;
}

export function applyWorkflowEvent(
  messages: Message[],
  event: WorkflowEventEnvelope,
): { messages: Message[]; handled: boolean } {
  const eventType = String(event.event_type || "");
  if (!WORKFLOW_CARD_EVENTS.has(eventType)) {
    return { messages, handled: false };
  }
  if (eventType === "workflow.decision") {
    const payload = event.payload || {};
    const prompt =
      payload.prompt && typeof payload.prompt === "object"
        ? (payload.prompt as Record<string, unknown>)
        : {};
    if (String(payload.decision_kind || prompt.kind || "") !== "ppt_outline") {
      return { messages, handled: true };
    }
    const outlineId = String(prompt.outline_id || prompt.decision_id || "").trim();
    if (!outlineId) return { messages, handled: true };
    const decisionStatus = String(payload.status || "open");
    const next: Message = {
      id: `workflow-decision:${String(payload.decision_id || outlineId)}`,
      role: "ppt_outline",
      ts: workflowEventTime(event),
      outline_id: outlineId,
      topic: String(prompt.topic || ""),
      outline_md: String(prompt.outline_markdown || ""),
      sources_count: Number(prompt.sources_count || 0),
      no_research: Boolean(prompt.no_research),
      history: [],
      ppt_outline_awaiting: decisionStatus === "open",
      ppt_outline_decision_status: decisionStatus === "open" ? undefined : decisionStatus,
      ppt_run_id: String(event.run_id || payload.run_id || "") || undefined,
      ppt_decision_id: String(payload.decision_id || "") || undefined,
      ppt_nonce: String(payload.nonce || "") || undefined,
      ppt_version: workflowNumber(payload.version),
    };
    return {
      messages: dedupe_ppt_outline_messages([
        ...messages.filter((message) => message.outline_id !== outlineId),
        next,
      ]),
      handled: true,
    };
  }
  const runId = String(event.run_id || "").trim();
  const seq = Number(event.seq);
  if (!runId || !Number.isInteger(seq) || seq < 0) {
    return { messages, handled: false };
  }

  const payload = event.payload || {};
  const rootRunId = String(event.root_run_id || event.task_root_run_id || "").trim();
  const taskScopeId = String(event.task_scope_id || "").trim();
  const incomingDelivery = parseWorkflowDeliveryAggregate(event.delivery_aggregate);
  const v5Projection = parseV5Progress(payload);
  const cardId = `workflow-run:${runId}`;
  let messagesWithStage = appendWorkflowStage(messages, event, payload, runId, seq);
  const index = messagesWithStage.findIndex(
    (message) => message.role === "workflow_progress" && message.workflow_run_id === runId,
  );
  let previous = index >= 0 ? messagesWithStage[index] : undefined;
  const incomingV7Children = parseV7Children(payload);
  if (previous && incomingV7Children && seq > (previous.workflow_v7_children_seq ?? -1)) {
    const updatedPrevious: Message = {
      ...previous,
      workflow_v7_children: incomingV7Children,
      workflow_v7_children_seq: seq,
    };
    messagesWithStage = [...messagesWithStage];
    messagesWithStage[index] = updatedPrevious;
    previous = updatedPrevious;
  }
  if (
    previous?.workflow_terminal ||
    (typeof previous?.workflow_seq === "number" && seq <= previous.workflow_seq)
  ) {
    if (
      previous &&
      incomingDelivery &&
      incomingDelivery.run_id === runId &&
      (!previous.workflow_delivery ||
        (previous.workflow_delivery.manifest_ref === incomingDelivery.manifest_ref &&
          incomingDelivery.updated_at >= previous.workflow_delivery.updated_at))
    ) {
      const updated = [...messagesWithStage];
      updated[index] = { ...previous, workflow_delivery: incomingDelivery };
      return { messages: updated, handled: true };
    }
    return { messages: messagesWithStage, handled: true };
  }

  const incomingOrdinal = Number(payload.ordinal);
  const ordinal =
    Number.isFinite(incomingOrdinal) && incomingOrdinal >= 0
      ? incomingOrdinal
      : (previous?.workflow_ordinal ?? 0);
  const incomingTotal = Number(payload.total);
  const total =
    Number.isFinite(incomingTotal) && incomingTotal >= 0
      ? incomingTotal
      : (previous?.workflow_total ?? 0);
  let status: WorkflowProgressStatus = previous?.workflow_status ?? "running";
  let terminal = false;
  if (eventType === "workflow.progress") {
    const transition = String(payload.status || "started");
    status =
      transition === "waiting"
        ? "waiting"
        : transition === "failed"
          ? "failed"
          : transition === "cancelled"
            ? "cancelled"
            : "running";
  } else if (eventType === "workflow.final") {
    const finalStatus = String(payload.status || "failed");
    status =
      finalStatus === "completed"
        ? "completed"
        : finalStatus === "cancelled"
          ? "cancelled"
          : "failed";
    terminal = true;
  } else {
    status = "running";
  }

  const incomingVersion = String(payload.workflow_version || previous?.workflow_version || "");
  const preserveActiveV5Control = Boolean(
    previous?.workflow_v5 &&
      previous.workflow_v5.control_action !== "none" &&
      ["open", "accepted", "observed"].includes(previous.workflow_v5.control_status) &&
      v5Projection?.control_action === "none" &&
      v5Projection.control_status === "none" &&
      eventType === "workflow.progress" &&
      payload.kind !== "stage",
  );
  let mergedV5 = v5Projection
    ? (payload.kind === "stage" && payload.status === "completed") || !previous?.workflow_v5
      ? v5Projection
      : {
          ...previous.workflow_v5,
          action: v5Projection.action,
          result: v5Projection.result,
          next_step: v5Projection.next_step,
          elapsed_seconds: Math.max(
            previous.workflow_v5.elapsed_seconds,
            v5Projection.elapsed_seconds,
          ),
          soft_checkpoint: v5Projection.soft_checkpoint,
          lease_reason: v5Projection.lease_reason,
          // A hidden next-stage `started` projection carries none/none because it
          // does not own control state. Keep the last server-projected action
          // actionable until an explicit control update or terminal event closes it.
          control_action: preserveActiveV5Control
            ? previous.workflow_v5.control_action
            : v5Projection.control_action,
          control_status: preserveActiveV5Control
            ? previous.workflow_v5.control_status
            : v5Projection.control_status,
          parent_operation: v5Projection.parent_operation,
        }
    : previous?.workflow_v5;
  if (eventType === "workflow.final" && incomingVersion === "v5" && mergedV5) {
    const matrix = Array.isArray(payload.action_matrix) ? payload.action_matrix : [];
    const projected = matrix.find(
      (item) =>
        item &&
        typeof item === "object" &&
        (item as Record<string, unknown>).enabled === true &&
        V5_CONTROL_ACTIONS.has(
          String((item as Record<string, unknown>).action_id) as WorkflowV5ControlAction,
        ),
    ) as Record<string, unknown> | undefined;
    const recovery =
      payload.recovery_action === "retry_from_start" ? "retry_from_start" : undefined;
    const terminalAction = String(
      projected?.action_id || recovery || "none",
    ) as WorkflowV5ControlAction;
    const delivery = String(payload.delivery_status || "");
    mergedV5 = {
      ...mergedV5,
      control_action: terminalAction,
      control_status: terminalAction === "none" ? "none" : "open",
      predicted_delivery: ["completed", "partial", "insufficient_evidence"].includes(delivery)
        ? (delivery as WorkflowV5ProgressProjection["predicted_delivery"])
        : mergedV5.predicted_delivery,
    };
  }
  const incomingElapsedMs =
    v5Projection &&
    (!previous?.workflow_v5 || (payload.kind === "stage" && payload.status === "completed"))
      ? v5Projection.elapsed_seconds * 1000
      : workflowNumber(payload.elapsed_ms);
  const monotonicElapsedMs = Math.max(previous?.workflow_elapsed_ms ?? 0, incomingElapsedMs ?? 0);

  const next: Message = {
    id: cardId,
    role: "workflow_progress",
    ts: previous?.ts ?? workflowEventTime(event),
    run_id: rootRunId || previous?.run_id,
    task_scope_id: taskScopeId || previous?.task_scope_id,
    workflow_run_id: runId,
    workflow_name: String(
      eventType === "workflow.progress"
        ? payload.workflow_label || payload.workflow_name || previous?.workflow_name || "任务"
        : previous?.workflow_name || payload.workflow_label || payload.workflow_name || "任务",
    ),
    workflow_version: String(payload.workflow_version || previous?.workflow_version || ""),
    workflow_status: status,
    workflow_stage: String(payload.stage || previous?.workflow_stage || "准备中"),
    workflow_stage_id: workflowSafeText(payload.stage_id, 64) ?? previous?.workflow_stage_id,
    workflow_ordinal: ordinal,
    // Graph revision loops may return to an earlier stage. Keep the stage
    // truthful while the visual percentage remains monotonic.
    workflow_display_ordinal: Math.max(
      previous?.workflow_display_ordinal ?? previous?.workflow_ordinal ?? 0,
      ordinal,
    ),
    workflow_total: total,
    workflow_completed_count:
      workflowNumber(payload.completed_count) ?? previous?.workflow_completed_count,
    workflow_seq: seq,
    workflow_terminal: terminal,
    workflow_event_id: String(event.event_id || ""),
    workflow_elapsed_ms: monotonicElapsedMs,
    workflow_started_at:
      previous?.workflow_started_at ?? Math.max(0, workflowEventTime(event) - monotonicElapsedMs),
    workflow_updated_at: workflowEventTime(event),
    workflow_warning_count:
      workflowNumber(payload.warning_count) ?? previous?.workflow_warning_count,
    workflow_metrics:
      sanitizeWorkflowMetrics(
        workflowSafeText(payload.stage_id, 64) ?? previous?.workflow_stage_id ?? "finalize",
        payload.metrics,
      ) ?? previous?.workflow_metrics,
    workflow_action:
      v5Projection?.action ?? workflowSafeText(payload.action) ?? previous?.workflow_action,
    workflow_result:
      v5Projection?.result ??
      workflowSafeText(payload.result ?? payload.text) ??
      previous?.workflow_result,
    workflow_result_code:
      workflowResultCode(payload.result_code ?? payload.status) ?? previous?.workflow_result_code,
    workflow_diagnostic_codes:
      workflowDiagnosticCodes(payload.diagnostic_codes) ?? previous?.workflow_diagnostic_codes,
    workflow_skipped_stage_ids:
      workflowSkippedStageIds(payload.skipped_stage_ids) ?? previous?.workflow_skipped_stage_ids,
    workflow_retry_action_id:
      String(payload.workflow_version || previous?.workflow_version || "") === "v4" &&
      payload.retry_action_id === "retry_from_start"
        ? "retry_from_start"
        : previous?.workflow_retry_action_id,
    workflow_error:
      eventType === "workflow.final" &&
      String(payload.workflow_version || previous?.workflow_version || "") !== "v5"
        ? (workflowErrorText(payload.terminal_error ?? payload.error) ?? previous?.workflow_error)
        : previous?.workflow_error,
    workflow_recovery_action:
      eventType === "workflow.final" && payload.recovery_action
        ? String(payload.recovery_action)
        : previous?.workflow_recovery_action,
    workflow_capability: v5Projection ? "deep_research_progress_v5" : previous?.workflow_capability,
    workflow_visibility:
      v5Projection && (payload.visibility === "hidden" || payload.visibility === "visible")
        ? payload.visibility
        : previous?.workflow_visibility,
    workflow_v5: mergedV5,
    workflow_v7_children: incomingV7Children ?? previous?.workflow_v7_children,
    workflow_v7_children_seq: incomingV7Children ? seq : previous?.workflow_v7_children_seq,
    workflow_delivery: incomingDelivery ?? previous?.workflow_delivery,
  };

  if (index >= 0) {
    const updated = [...messagesWithStage];
    updated[index] = next;
    return { messages: updated, handled: true };
  }
  const insertAt = messagesWithStage.findIndex((message) => message.ts > next.ts);
  const updated = [...messagesWithStage];
  updated.splice(insertAt < 0 ? updated.length : insertAt, 0, next);
  return { messages: updated, handled: true };
}
