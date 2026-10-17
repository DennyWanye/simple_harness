import type { InboxItem } from "../stores/sessionSelectors";
import type { Message, TaskRunProjectionState } from "../stores/sessionTypes";
import { buildAgentActivityTrace, buildWorkflowTaskTraces } from "./AgentActivityMessage";
import type { CompanionEvent, PublicRunSnapshotV3 } from "../types/messages";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";
import type {
  StreamFilter,
  ChatStreamMessage,
  ThinkingStreamMessage,
  StreamRow,
} from "./messageStreamTypes";

export const LEGACY_SYNTHETIC_TOOL_PROGRESS = [
  /^准备使用\s+\S+\s+处理当前步骤。$/,
  /^\S+\s+已完成，正在根据结果继续处理。$/,
  /^\S+\s+未成功，正在检查原因并调整后续步骤。$/,
];

export function isLegacySyntheticToolProgress(text: string): boolean {
  const normalized = text.trim();
  return LEGACY_SYNTHETIC_TOOL_PROGRESS.some((pattern) => pattern.test(normalized));
}

// ----------------------------------------------------------------------
// Row builders + sub-components
// ----------------------------------------------------------------------
export function buildRows(
  chats: ChatStreamMessage[],
  warns: InboxItem[],
  errs: InboxItem[],
  companionEvents: CompanionEvent[],
  filter: StreamFilter,
  agentSnapshot: PublicRunSnapshotV3 | null = null,
  projectDirectoryRequest: ProjectDirectoryRequest["payload"] | null = null,
  runProjections: Record<string, TaskRunProjectionState> = {},
): StreamRow[] {
  const rows: StreamRow[] = [];
  if (filter === "all" || filter === "chat") {
    const taskTraces = agentSnapshot ? buildWorkflowTaskTraces(agentSnapshot) : [];
    const taskTraceByRun = new Map(taskTraces.map((trace) => [trace.runId, trace]));
    const consumedTaskTraceRunIds = new Set<string>();
    companionEvents.forEach((event) => {
      const parsed = event.occurred_at ? Date.parse(event.occurred_at) : NaN;
      rows.push({
        kind: "companion",
        ts: Number.isFinite(parsed) ? parsed : (event.received_at ?? Date.now()),
        event,
        key: `companion:${event.profile_id}:${event.profile_generation}:${event.event_id}`,
      });
    });
    const groups = new Map<
      string,
      {
        summary?: Message;
        stages: Message[];
        anchor: number;
        ts: number;
      }
    >();
    chats.forEach((message, index) => {
      if (message.role !== "workflow_progress" && message.role !== "workflow_stage") return;
      const runId = String(message.message.workflow_run_id || "").trim();
      if (!runId) return;
      const group = groups.get(runId) ?? {
        stages: [],
        anchor: index,
        ts: message.ts,
      };
      group.anchor = Math.min(group.anchor, index);
      group.ts = Math.min(group.ts, message.ts);
      if (message.role === "workflow_progress") {
        if (
          !group.summary ||
          (message.message.workflow_seq ?? -1) >= (group.summary.workflow_seq ?? -1)
        ) {
          group.summary = message.message;
        }
      } else {
        const duplicate = group.stages.some(
          (stage) =>
            (stage.workflow_event_id &&
              stage.workflow_event_id === message.message.workflow_event_id) ||
            (stage.workflow_seq !== undefined &&
              stage.workflow_seq === message.message.workflow_seq),
        );
        if (!duplicate) group.stages.push(message.message);
      }
      groups.set(runId, group);
    });

    const emitted = new Set<string>();
    const thinkingGroups = new Map<
      string,
      {
        anchor: number;
        ts: number;
        messages: ThinkingStreamMessage[];
        identities: Set<string>;
      }
    >();
    chats.forEach((message, index) => {
      if (!isThinkingStreamMessage(message) || !message.runId) {
        return;
      }
      if (
        message.role === "progress" &&
        (!message.text.trim() || isLegacySyntheticToolProgress(message.text))
      ) {
        return;
      }
      const runId = message.runId.trim();
      if (!runId) return;
      const group = thinkingGroups.get(runId) ?? {
        anchor: index,
        ts: message.ts,
        messages: [],
        identities: new Set<string>(),
      };
      group.anchor = Math.min(group.anchor, index);
      group.ts = Math.min(group.ts, message.ts);
      const identity = [
        message.role,
        message.ts,
        message.text,
        message.role === "tool" ? (message.toolName ?? "") : (message.status ?? ""),
        message.role === "tool" ? (message.toolResultRaw ?? "") : (message.phase ?? ""),
        message.role === "tool" ? JSON.stringify(message.toolArgs ?? null) : "",
      ].join("\u001f");
      if (!group.identities.has(identity)) {
        group.identities.add(identity);
        group.messages.push(message);
      }
      thinkingGroups.set(runId, group);
    });
    const emittedThinkingGroups = new Set<string>();
    chats.forEach((message, index) => {
      if (isThinkingStreamMessage(message) && message.runId) {
        const runId = message.runId.trim();
        const group = thinkingGroups.get(runId);
        if (group && !emittedThinkingGroups.has(runId) && index === group.anchor) {
          emittedThinkingGroups.add(runId);
          rows.push({
            kind: "thinking_group",
            ts: group.ts,
            runId,
            messages: [...group.messages].sort((left, right) => left.ts - right.ts),
            runProjection: runProjections[runId],
            key: `thinking_group:${runId}`,
          });
          return;
        }
        if (group || message.role === "progress") return;
      }
      if (message.role === "workflow_progress" || message.role === "workflow_stage") {
        const runId = String(message.message.workflow_run_id || "").trim();
        const group = groups.get(runId);
        if (group && !emitted.has(runId) && index === group.anchor) {
          emitted.add(runId);
          const rootRunId = String(
            group.summary?.run_id || group.stages[0]?.run_id || runId,
          ).trim();
          const taskTrace = taskTraceByRun.get(runId) ?? taskTraceByRun.get(rootRunId);
          const childRunProjection = runProjections[runId];
          const rootRunProjection = runProjections[rootRunId];
          const childHasTerminalProjection =
            childRunProjection != null &&
            ["completed", "failed", "cancelled"].includes(childRunProjection.status);
          const rootHasTerminalProjection =
            rootRunProjection != null &&
            ["completed", "failed", "cancelled"].includes(rootRunProjection.status);
          // A workflow card describes the child Run, so its own durable terminal
          // outcome must win over the Root's aggregate outcome. The Root remains
          // a fallback for legacy child summaries that are still marked running
          // after the overall task has already settled.
          const runProjection = childHasTerminalProjection
            ? childRunProjection
            : rootHasTerminalProjection
              ? rootRunProjection
              : (childRunProjection ?? rootRunProjection);
          if (taskTrace) consumedTaskTraceRunIds.add(taskTrace.runId);
          rows.push({
            kind: "workflow_group",
            ts: group.ts,
            runId,
            summary: group.summary,
            stages: [...group.stages].sort(
              (a, b) =>
                (a.workflow_seq ?? Number.MAX_SAFE_INTEGER) -
                  (b.workflow_seq ?? Number.MAX_SAFE_INTEGER) ||
                String(a.workflow_event_id || a.id).localeCompare(
                  String(b.workflow_event_id || b.id),
                ),
            ),
            taskTrace,
            runProjectionStatus: runProjection?.status,
            runProjection,
            key: `workflow_group:${runId}`,
          });
        } else if (!group) {
          rows.push({
            kind: "chat",
            ts: message.ts,
            msg: message,
            key: `c:${index}:${message.ts}`,
          });
        }
        return;
      }
      rows.push({
        kind: "chat",
        ts: message.ts,
        msg: message,
        key: `c:${index}:${message.ts}`,
      });
    });
    for (const trace of taskTraces) {
      if (emitted.has(trace.runId) || consumedTaskTraceRunIds.has(trace.runId)) {
        continue;
      }
      emitted.add(trace.runId);
      rows.push({
        kind: "workflow_group",
        ts: trace.startedAt,
        runId: trace.runId,
        stages: [],
        taskTrace: trace,
        runProjectionStatus: runProjections[trace.runId]?.status,
        runProjection: runProjections[trace.runId],
        key: `workflow_group:${trace.runId}`,
      });
    }
    if (agentSnapshot) {
      const tracedRunIds = new Set(taskTraces.map((trace) => trace.runId));
      for (const item of buildAgentActivityTrace(agentSnapshot, tracedRunIds)) {
        const msg: ChatStreamMessage = {
          role: "assistant",
          text: item.text,
          ts: item.ts,
        };
        rows.push({
          kind: "chat",
          ts: item.ts,
          msg,
          key: `agent_trace:${agentSnapshot.root_run_id}:${item.id}`,
        });
      }
    }
    if (projectDirectoryRequest) {
      rows.push({
        kind: "project_directory",
        ts: projectDirectoryRequest.received_at ?? Date.now(),
        request: projectDirectoryRequest,
        key: `project_directory:${projectDirectoryRequest.decision_id}`,
      });
    }
  }
  if (filter === "all" || filter === "warn") {
    warns.forEach((a) =>
      rows.push({
        kind: "alert",
        ts: a.received_at,
        severity: "yellow",
        item: a,
        key: `y:${a.session_id}:${a.alert_id}`,
      }),
    );
  }
  if (filter === "all" || filter === "err") {
    errs.forEach((a) =>
      rows.push({
        kind: "alert",
        ts: a.received_at,
        severity: "red",
        item: a,
        key: `r:${a.session_id}:${a.alert_id}`,
      }),
    );
  }
  // Oldest 鈫?newest so newest sits at the bottom (chat-stream UX).
  rows.sort((a, b) => a.ts - b.ts);
  return rows;
}

export function isThinkingStreamMessage(
  message: ChatStreamMessage,
): message is ThinkingStreamMessage {
  return message.role === "progress" || message.role === "tool";
}
