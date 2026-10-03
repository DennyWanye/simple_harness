// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S3-Inbox v2 鈥?Left-side message stream.
 *
 * Replaces the modal `ChatHistoryPanel` + the floating `PetSupervisorBubble`
 * with a single persistent panel that lives on the left side of the
 * pet window. It carries every message the user might want to see:
 *
 *   鈥?companion chat (user 鈫?assistant)
 *   鈥?supervisor warnings  (yellow severity)
 *   鈥?supervisor errors    (red severity)
 *
 * A row of filter chips at the top lets the user narrow the stream:
 * "鍏ㄩ儴 / 瀵硅瘽 / 鎻愰啋(N) / 閿欒(N)". The toolbar buttons (馃挰 / 鈿?/ 馃毃)
 * call `onSetFilter` to switch tabs without forcing the user to click
 * inside the panel.
 *
 * Visibility model:
 *   鈥?The panel is always *mounted* 鈥?chat history doesn't disappear
 *     just because the user collapsed it.
 *   鈥?Pressing 鉁?collapses to a thin handle on the left edge; clicking
 *     the handle (or any toolbar button) re-expands.
 *   鈥?A fresh red error auto-expands the panel + switches to "閿欒".
 */
import { ChatMissionCard } from "../views/ChatMissionCard";
import { MISSION_CARD_TOOLS, missionIdFromToolResult } from "../views/chatMission";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { invoke } from "@tauri-apps/api/core";

// 娑堟伅娴佹粴鍔ㄤ綅缃寔涔呴敭(杩涘叆娑堟伅鐣岄潰鎭㈠涓婃浣嶇疆,瑙佷笅 useLayoutEffect)銆?
const MSGSTREAM_SCROLL_KEY = "deskpet.msgstream.scroll.v1";

import type {
  InboxItem,
  Message,
  TaskRunProjectionState,
  WorkflowV5ControlAction,
} from "../stores/sessionsStore";
// 瀛愪唬鐞嗗苟鍙戣繘搴﹀崱鐗囷紙娣辫壊鍙樹綋锛屼笌鏈潰鏉跨幓鐠冩嫙鎬佷竴鑷达級銆俽uns 绌烘椂鑷覆鏌?null锛?
// Shared control WS dispatches subagent_progress into subagentStore.
import { SubagentProgressPanel } from "../code-panel/SubagentProgressPanel";
import { PPTOutlineCard } from "../code-panel/PPTOutlineCard";
import { ArtifactCard, extractArtifactsFromResult } from "../code-panel/ArtifactCard";
import { ToolCallCard, ToolResultCard } from "../code-panel/MessageBubble";
import { CopyMessageButton } from "./CopyMessageButton";
import { MarkdownMessage } from "./MarkdownMessage";
import { WorkflowProgressGroup } from "./workflow/WorkflowProgressGroup";
import {
  buildAgentActivityTrace,
  buildWorkflowTaskTraces,
  type WorkflowTaskTrace,
} from "./AgentActivityMessage";
import { CompanionCard } from "./companion/CompanionCard";
import { formatRelativeMs } from "../relativeTime";
import type { CompanionEvent, PublicRunSnapshotV3 } from "../types/messages";
import type { ProjectDirectoryRequest } from "../types/skillPlatform";
import {
  normalizePublicRunSnapshot,
  useHarnessPublicSnapshot,
} from "../stores/harnessPublicSnapshotStore";

export type StreamFilter = "all" | "chat" | "warn" | "err";

export type ChatStreamMessage =
  | {
      // 2026-06-12: 鍔?"tool" 鈥?宸ュ叿鎵ц杞ㄨ抗(璋冪敤/缁撴灉)杩涗富娑堟伅娴?
      // 鐢ㄦ埛鍏ㄧ▼鍙娴?姝ゅ墠娲剧敓灞傛妸 tool_call/tool_result 婊ゆ帀浜?銆?
      role: "user" | "assistant" | "tool";
      text: string;
      ts: number;
      runId?: string;
      toolName?: string;
      toolArgs?: Record<string, unknown>;
      toolOk?: boolean;
      toolResultRaw?: string;
      continuationStatus?: Message["continuation_status"];
      continuationError?: string;
    }
  | {
      role: "progress";
      text: string;
      ts: number;
      runId?: string;
      phase?: Message["reasoning_phase"];
      status?: Message["reasoning_status"];
    }
  | {
      role: "ppt_outline";
      message: Message;
      session_id: string;
      ts: number;
    }
  | {
      role: "workflow_progress";
      message: Message;
      ts: number;
    }
  | {
      role: "workflow_stage";
      message: Message;
      ts: number;
    };

type ThinkingStreamMessage =
  | Extract<ChatStreamMessage, { role: "progress" }>
  | (Extract<ChatStreamMessage, { role: "user" | "assistant" | "tool" }> & {
      role: "tool";
    });

const LEGACY_SYNTHETIC_TOOL_PROGRESS = [
  /^准备使用\s+\S+\s+处理当前步骤。$/,
  /^\S+\s+已完成，正在根据结果继续处理。$/,
  /^\S+\s+未成功，正在检查原因并调整后续步骤。$/,
];

export function isLegacySyntheticToolProgress(text: string): boolean {
  const normalized = text.trim();
  return LEGACY_SYNTHETIC_TOOL_PROGRESS.some((pattern) =>
    pattern.test(normalized),
  );
}

export interface MessageStreamPanelProps {
  filter: StreamFilter;
  /** Companion-mode chat messages, oldest 鈫?newest. Internally we sort
   * by ts when merging with alerts. */
  chatMessages: ChatStreamMessage[];
  warnings: InboxItem[];
  errors: InboxItem[];
  companionEvents?: CompanionEvent[];
  onSetFilter: (f: StreamFilter) => void;
  onDismiss: (sid: string, alert_id: string) => void;
  onDismissAll: (severity: "yellow" | "red") => void;
  onJumpToSession: (sid: string) => void;
  onChoice: (
    sid: string,
    alert_id: string,
    button_index: number,
    button_text: string,
  ) => void;
  onCompanionDetail?: (event: CompanionEvent) => void;
  onCompanionAction?: (
    event: CompanionEvent,
    action: string,
    allow?: boolean,
  ) => void | Promise<void>;
  /** 2026-05-16: pet window = floating absolute overlay (default,
   * unchanged). Code-mode window embeds this in a 3-column flex layout
   * 鈫?embedded=true switches the wrapper from absolute to a relative
   * flex-fill panel (no top/left/width hardcode). Visual styling
   * identical; only positioning differs. Default false keeps the pet
   * window byte-identical (zero regression). */
  embedded?: boolean;
  onWorkflowRetry?: (
    runId: string,
    actionId: Exclude<WorkflowV5ControlAction, "none">,
    retryKey: string,
  ) => Promise<{ run_id: string; accepted?: boolean }>;
  sessionId?: string;
  selectedRunId?: string | null;
  runProjections?: Record<string, TaskRunProjectionState>;
  /** @deprecated Test-only compatibility seam. Production uses the shared store. */
  agentSnapshot?: unknown;
  projectDirectoryRequest?: ProjectDirectoryRequest["payload"] | null;
  projectDirectoryError?: string | null;
  onProjectDirectoryConfirm?: (
    parentDirectory: string,
    folderName: string,
  ) => boolean;
}

type StreamRow =
  | {
      kind: "chat";
      ts: number;
      msg: ChatStreamMessage;
      key: string;
    }
  | {
      kind: "alert";
      ts: number;
      severity: "yellow" | "red";
      item: InboxItem;
      key: string;
    }
  | {
      kind: "thinking_group";
      ts: number;
      runId: string;
      messages: ThinkingStreamMessage[];
      runProjection?: TaskRunProjectionState;
      key: string;
    }
  | {
      kind: "workflow_group";
      ts: number;
      runId: string;
      summary?: Message;
      stages: Message[];
      taskTrace?: WorkflowTaskTrace;
      runProjectionStatus?: TaskRunProjectionState["status"];
      runProjection?: TaskRunProjectionState;
      key: string;
    }
  | {
      kind: "companion";
      ts: number;
      event: CompanionEvent;
      key: string;
    }
  | {
      kind: "project_directory";
      ts: number;
      request: ProjectDirectoryRequest["payload"];
      key: string;
    };

const PALETTE = {
  warn: { accent: "#f59e0b", soft: "rgba(245, 158, 11, 0.18)", border: "rgba(245, 158, 11, 0.45)" },
  err:  { accent: "#ef4444", soft: "rgba(239, 68, 68, 0.18)", border: "rgba(239, 68, 68, 0.45)" },
  user: { bg: "linear-gradient(135deg, rgba(15, 76, 129, 0.96), rgba(12, 58, 105, 0.96))", fg: "#eaf6ff" },
  asst: { bg: "rgba(255, 255, 255, 0.055)", fg: "#e8edf6" },
} as const;

export function MessageStreamPanel({
  filter,
  chatMessages,
  warnings,
  errors,
  companionEvents = [],
  onDismiss,
  onJumpToSession,
  onChoice,
  onCompanionDetail,
  onCompanionAction,
  embedded = false,
  onWorkflowRetry,
  sessionId = "",
  selectedRunId = null,
  runProjections = {},
  agentSnapshot: injectedSnapshot,
  projectDirectoryRequest = null,
  projectDirectoryError = null,
  onProjectDirectoryConfirm,
}: MessageStreamPanelProps) {
  const storedSnapshot = useHarnessPublicSnapshot(sessionId, selectedRunId);
  const agentSnapshot = storedSnapshot ?? normalizePublicRunSnapshot(injectedSnapshot);
  // 娉細onSetFilter / onDismissAll 浠嶄繚鐣欏湪 MessageStreamPanelProps 绫诲瀷閲?
  // 锛堣皟鐢ㄦ柟鐓у父浼狅級锛屼絾褰撳墠 render 鏈敤鍒扳€斺€斿崐鎺ョ嚎鐨勮繃婊ゆ潯鐗规€с€傚厛涓嶈В鏋?
  // 浠ラ€氳繃 tsc noUnusedParameters锛涜鎭㈠杩囨护鏉?UI 鏃跺啀鎺ュ洖銆?
  const rows = useMemo(
    () => buildRows(
      chatMessages,
      warnings,
      errors,
      companionEvents,
      filter,
      agentSnapshot,
      projectDirectoryRequest,
      runProjections,
    ),
    [
      chatMessages,
      warnings,
      errors,
      companionEvents,
      filter,
      agentSnapshot,
      projectDirectoryRequest,
      runProjections,
    ],
  );
  const tailKey = rows[rows.length - 1]?.key;

  // Auto-scroll to newest row when it changes (only if user hasn't
  // scrolled up 鈥?the simple heuristic is "we're already near the
  // bottom"). Resilient to React batching; we read scrollTop just
  // after layout.
  const listRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef(true);
  useEffect(() => {
    const el = listRef.current;
    if (!el || !shouldStickToBottomRef.current) return;
    if (shouldStickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [rows.length, tailKey]);

  // 杩涘叆娑堟伅鐣岄潰鏃舵仮澶嶄笂娆℃粴鍔ㄤ綅缃?鍏虫帀娑堟伅闈㈡澘鍐嶆墦寮€浠嶅洖鍒板師澶?銆?
  // 娑堟伅澶ф鏄嫭绔?webview,寮€鍏冲彲鑳介噸寤?鈫?鐢?localStorage 鎸佷箙,璺?webview 閲嶅缓鏈夋晥銆?
  // 涓婃鍦ㄥ簳閮?鈫?浠嶈创搴?闅忔柊娑堟伅璺熼殢);鍚﹀垯鎭㈠鍒板綋鏃剁殑 scrollTop銆備粎鎸傝浇鏃惰窇涓€娆°€?
  const scrollRestoredRef = useRef(false);
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || scrollRestoredRef.current) return;
    scrollRestoredRef.current = true;
    let saved: { top: number; atBottom: boolean } | null = null;
    try {
      saved = JSON.parse(localStorage.getItem(MSGSTREAM_SCROLL_KEY) || "null");
    } catch {
      saved = null;
    }
    if (!saved || saved.atBottom) {
      shouldStickToBottomRef.current = true;
      el.scrollTop = el.scrollHeight; // 榛樿/涓婃璐村簳 鈫?搴曢儴
    } else {
      shouldStickToBottomRef.current = false;
      el.scrollTop = Math.max(0, Math.min(saved.top, el.scrollHeight));
    }
  }, []);

  const handleListScroll = () => {
    const el = listRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    shouldStickToBottomRef.current = atBottom;
    try {
      localStorage.setItem(
        MSGSTREAM_SCROLL_KEY,
        JSON.stringify({ top: el.scrollTop, atBottom }),
      );
    } catch {
      /* localStorage 涓嶅彲鐢ㄦ椂蹇界暐,涓嶅奖鍝嶅姛鑳?*/
    }
  };

  return (
    <div
      role="region"
      aria-label="消息流"
      data-testid="msgstream-panel"
      style={embedded ? embeddedWrapperStyle : wrapperStyle}
    >
      {/* 2026-05-31 restore 鈥?鐢ㄦ埛瑕佹眰鍒犳帀 4 涓?filter tab锛堝叏閮?瀵硅瘽/鈿?馃毃锛夈€?
          filter prop 淇濈暀涓?"all"锛坈aller 榛樿鍊硷級锛屾墍鏈夊唴瀹规贩鎺掋€俿weep bar
          (filter==warn|err 鎵嶆樉绀? 鍦?filter 閿佸畾 "all" 鍚庤嚜鐒朵笉鍐嶆覆鏌撱€?
          瀵瑰簲 onSetFilter / onDismissAll / FilterChip / sweepBarStyle / pillButton
          鍙樻垚鏈娇鐢紝浣?prop 鎺ュ彛淇濈暀浠ュ吋瀹?caller銆?*/}

      {/* 瀛愪唬鐞嗗苟鍙戝疄鏃惰繘搴︼細閽夊湪娑堟伅娴侀《閮紙list 涔嬩笂锛屼笉闅忔粴鍔級锛屽缁堝彲瑙併€?
          鏃犲苟鍙戜换鍔℃椂璇ョ粍浠惰繑鍥?null锛屼笉鍗犱綅銆?*/}
      <SubagentProgressPanel variant="dark" />

      <div
        ref={listRef}
        data-testid="msgstream-list"
        style={listStyle}
        onScroll={handleListScroll}
      >
        {rows.length === 0 ? (
          <div style={emptyStyle}>
            {emptyMessage(filter)}
          </div>
        ) : (
          rows.map((r) =>
            r.kind === "chat" ? (
              <ChatRow key={r.key} msg={r.msg} onWorkflowRetry={onWorkflowRetry} />
            ) : r.kind === "thinking_group" ? (
              <ThinkingProcessGroup
                key={r.key}
                runId={r.runId}
                messages={r.messages}
                runMessages={chatMessages.filter((message) =>
                  "runId" in message && message.runId === r.runId
                )}
                runProjection={r.runProjection}
              />
            ) : r.kind === "project_directory" ? (
              agentSnapshot?.root_run_id === r.request.run_id &&
              agentSnapshot.aggregate_outcome.status !== "waiting" ? null : (
                <ProjectDirectoryCard
                  key={r.key}
                  request={r.request}
                  error={projectDirectoryError}
                  onConfirm={onProjectDirectoryConfirm}
                />
              )
            ) : r.kind === "companion" ? (
              <CompanionCard
                key={r.key}
                event={r.event}
                onOpenDetail={(event) => onCompanionDetail?.(event)}
                onAction={(event, action, allow) =>
                  onCompanionAction?.(event, action, allow)}
              />
            ) : r.kind === "workflow_group" ? (
              <WorkflowProgressGroup
                key={r.key}
                runId={r.runId}
                summary={r.summary}
                stages={r.stages}
                taskTrace={r.taskTrace}
                runProjectionStatus={r.runProjectionStatus}
                runProjection={r.runProjection}
                onWorkflowRetry={onWorkflowRetry}
              />
            ) : (
              <AlertRow
                key={r.key}
                item={r.item}
                severity={r.severity}
                onDismiss={() => onDismiss(r.item.session_id, r.item.alert_id)}
                onJump={() => onJumpToSession(r.item.session_id)}
                onChoice={(idx, text) =>
                  onChoice(r.item.session_id, r.item.alert_id, idx, text)
                }
              />
            ),
          )
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Row builders + sub-components
// ----------------------------------------------------------------------

function buildRows(
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
    const taskTraces = agentSnapshot
      ? buildWorkflowTaskTraces(agentSnapshot)
      : [];
    const taskTraceByRun = new Map(
      taskTraces.map((trace) => [trace.runId, trace]),
    );
    const consumedTaskTraceRunIds = new Set<string>();
    companionEvents.forEach((event) => {
      const parsed = event.occurred_at ? Date.parse(event.occurred_at) : NaN;
      rows.push({
        kind: "companion",
        ts: Number.isFinite(parsed) ? parsed : event.received_at ?? Date.now(),
        event,
        key: `companion:${event.profile_id}:${event.profile_generation}:${event.event_id}`,
      });
    });
    const groups = new Map<string, {
      summary?: Message;
      stages: Message[];
      anchor: number;
      ts: number;
    }>();
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
        if (!group.summary ||
            (message.message.workflow_seq ?? -1) >= (group.summary.workflow_seq ?? -1)) {
          group.summary = message.message;
        }
      } else {
        const duplicate = group.stages.some((stage) =>
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
    const thinkingGroups = new Map<string, {
      anchor: number;
      ts: number;
      messages: ThinkingStreamMessage[];
      identities: Set<string>;
    }>();
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
        message.role === "tool" ? message.toolName ?? "" : message.status ?? "",
        message.role === "tool" ? message.toolResultRaw ?? "" : message.phase ?? "",
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
          const taskTrace = taskTraceByRun.get(runId) ??
            taskTraceByRun.get(rootRunId);
          const childRunProjection = runProjections[runId];
          const rootRunProjection = runProjections[rootRunId];
          const childHasTerminalProjection = childRunProjection != null &&
            ["completed", "failed", "cancelled"].includes(childRunProjection.status);
          const rootHasTerminalProjection = rootRunProjection != null &&
            ["completed", "failed", "cancelled"].includes(rootRunProjection.status);
          // A workflow card describes the child Run, so its own durable terminal
          // outcome must win over the Root's aggregate outcome. The Root remains
          // a fallback for legacy child summaries that are still marked running
          // after the overall task has already settled.
          const runProjection = childHasTerminalProjection
            ? childRunProjection
            : rootHasTerminalProjection
              ? rootRunProjection
              : childRunProjection ?? rootRunProjection;
          if (taskTrace) consumedTaskTraceRunIds.add(taskTrace.runId);
          rows.push({
            kind: "workflow_group",
            ts: group.ts,
            runId,
            summary: group.summary,
            stages: [...group.stages].sort((a, b) =>
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
      for (const item of buildAgentActivityTrace(
        agentSnapshot,
        tracedRunIds,
      )) {
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

function isThinkingStreamMessage(
  message: ChatStreamMessage,
): message is ThinkingStreamMessage {
  return message.role === "progress" || message.role === "tool";
}

function emptyMessage(f: StreamFilter): string {
  switch (f) {
    case "chat": return "本次还没聊过。";
    case "warn": return "暂无未处理的提醒。";
    case "err":  return "暂无未处理的错误。";
    default:     return "（消息流为空）";
  }
}

function toolArtifactStatus(toolName?: string): string {
  if ((toolName || "").toLowerCase() === "deepresearch") {
    return "deepresearch 报告已保存，正在整理答复";
  }
  return `${toolName || "工具"} 已生成文件`;
}

function ChatRow({
  msg,
  onWorkflowRetry,
}: {
  msg: ChatStreamMessage;
  onWorkflowRetry?: MessageStreamPanelProps["onWorkflowRetry"];
}) {
  if (msg.role === "workflow_progress" || msg.role === "workflow_stage") {
    return <WorkflowProgressGroup
      runId={msg.message.workflow_run_id || msg.message.id}
      summary={msg.role === "workflow_progress" ? msg.message : undefined}
      stages={msg.role === "workflow_stage" ? [msg.message] : []}
      onWorkflowRetry={onWorkflowRetry}
    />;
  }
  if (msg.role === "ppt_outline") {
    const m = msg.message;
    return (
      <div style={{ width: "100%" }}>
        <PPTOutlineCard
          outlineId={m.outline_id ?? ""}
          topic={m.topic ?? ""}
          outlineMd={m.outline_md ?? ""}
          sourcesCount={m.sources_count ?? 0}
          noResearch={!!m.no_research}
          history={m.history ?? []}
          awaiting={!!m.ppt_outline_awaiting}
          decisionStatus={m.ppt_outline_decision_status}
          sessionId={msg.session_id}
          runId={m.ppt_run_id}
          decisionId={m.ppt_decision_id}
          nonce={m.ppt_nonce}
          version={m.ppt_version}
        />
        <CopyMessageButton
          text={[m.topic, m.outline_md].filter(Boolean).join("\n\n")}
          tone="dark"
        />
      </div>
    );
  }
  if (msg.role === "progress") {
    if (isLegacySyntheticToolProgress(msg.text)) {
      return null;
    }
    return <PublicProgressRow message={msg} />;
  }

  const { role, text, ts } = msg;
  if (role === "tool") {
    if (msg.toolArgs) {
      return (
        <div data-role="tool" style={{ alignSelf: "flex-start", maxWidth: "96%" }}>
          <ToolCallCard
            name={msg.toolName || "(工具)"}
            args={msg.toolArgs}
          />
        </div>
      );
    }
    // 2026-09-29：主 Agent 发起的后台任务在对话里显示成任务卡片（进度 + 人亲手点的确认/批准）
    const missionId = MISSION_CARD_TOOLS.has(msg.toolName ?? "") && msg.toolOk !== false
      ? missionIdFromToolResult(msg.toolResultRaw)
      : "";
    if (missionId) {
      return (
        <div data-role="tool" style={{ alignSelf: "flex-start", width: "96%", maxWidth: "96%" }}>
          <ChatMissionCard missionId={missionId} />
        </div>
      );
    }
    const artifacts = msg.toolOk !== false && msg.toolResultRaw
      ? extractArtifactsFromResult(msg.toolResultRaw)
      : [];
    if (artifacts.length > 0) {
      return (
        <div
          style={{
            ...rowBaseStyle,
            alignSelf: "flex-start",
            background: "rgba(20, 28, 40, 0.82)",
            color: "#dbeafe",
            borderColor: "rgba(103, 232, 249, 0.25)",
            padding: 10,
            maxWidth: "96%",
          }}
          data-role="tool"
        >
          <div
            data-bp-selectable=""
            style={{
              marginBottom: 8,
              fontSize: 12,
              color: "#a5b4fc",
              display: "flex",
              justifyContent: "space-between",
              gap: 8,
            }}
          >
            <span>{toolArtifactStatus(msg.toolName)}</span>
            <span style={{ opacity: 0.55 }}>{format_relative(ts)}</span>
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {artifacts.map((artifact, i) => (
              <ArtifactCard
                key={`${artifact.path || artifact.url || artifact.title || i}`}
                artifact={artifact}
                toolName={msg.toolName || "tool"}
              />
            ))}
          </div>
        </div>
      );
    }
    if (msg.toolResultRaw !== undefined) {
      return (
        <div data-role="tool" style={{ alignSelf: "flex-start", maxWidth: "96%" }}>
          <ToolResultCard
            name={msg.toolName || "(工具)"}
            ok={msg.toolOk !== false}
            result={msg.toolResultRaw}
          />
        </div>
      );
    }
    // Tool trace rows are compact and do not expose a copy action.
    return (
      <div
        style={{
          ...rowBaseStyle,
          alignSelf: "flex-start",
          background: "rgba(20, 28, 40, 0.7)",
          color: "#94a3b8",
          borderColor: "rgba(103, 232, 249, 0.18)",
          padding: "4px 10px",
          fontSize: 11.5,
          fontFamily: "Consolas, 'Courier New', monospace",
          maxWidth: "92%",
        }}
        data-role="tool"
      >
        <div data-bp-selectable="" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {text || "(宸ュ叿)"}
          <span style={{ marginLeft: 8, opacity: 0.5 }}>{format_relative(ts)}</span>
        </div>
      </div>
    );
  }
  const tone = role === "user" ? PALETTE.user : PALETTE.asst;
  return (
    <div
      style={{
        alignSelf: role === "user" ? "flex-end" : "flex-start",
        maxWidth: "84%",
        display: "flex",
        flexDirection: "column",
        alignItems: role === "user" ? "flex-end" : "flex-start",
      }}
      data-role={role}
    >
      <div
        style={{
          ...rowBaseStyle,
          maxWidth: "100%",
          background: tone.bg,
          color: tone.fg,
          borderColor: role === "user"
            ? "rgba(125, 211, 252, 0.20)"
            : "rgba(255,255,255,0.06)",
          boxShadow: role === "user"
            ? "inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 18px rgba(8,47,73,0.22)"
            : undefined,
        }}
      >
      {/* data-bp-selectable: 璁╂秷鎭鏂囧彲琚紶鏍囨嫋閫夊鍒讹紙index.css 鐨?
          鍏ㄥ眬 user-select:none 榛樿浼氭尅浣忥級銆?
          assistant 鍥炲璧?markdown 娓叉煋锛堜唬鐮佸潡/鍒楄〃/鍔犵矖/鏈湴鏂囦欢閾炬帴锛夛紝
          涓?code 妯″紡涓€鑷达紱user 杈撳叆淇濇寔绾枃鏈紙pre-wrap 淇濈暀鎹㈣锛夈€?*/}
      {role === "assistant" && text ? (
        <div data-bp-selectable="" style={{ ...bodyStyle, whiteSpace: "normal" }}>
          <MarkdownMessage>{text}</MarkdownMessage>
        </div>
      ) : (
          <div data-bp-selectable="" style={bodyStyle}>{text || "(空)"}</div>
      )}
      </div>
      <MessageActionRow
        copyText={text || ""}
        ts={ts}
        align={role === "user" ? "right" : "left"}
        tone={role === "user" ? "blue" : "dark"}
      />
      {role === "user" && msg.continuationStatus && (
        <div
          role="status"
          title={msg.continuationError || undefined}
          style={{
            marginTop: 3,
            padding: "0 3px",
            color:
              msg.continuationStatus === "failed"
                ? "#fca5a5"
                : msg.continuationStatus === "bound"
                  ? "#86efac"
                  : "#fde68a",
            fontSize: 11,
            lineHeight: 1.35,
          }}
        >
          {msg.continuationStatus === "bound"
            ? "✓ Agent 已读取"
            : msg.continuationStatus === "failed"
              ? "未能加入当前任务"
              : "等待 Agent 读取…"}
        </div>
      )}
    </div>
  );
}

const TERMINAL_THINKING_STATUSES = new Set<TaskRunProjectionState["status"]>([
  "completed",
  "failed",
  "cancelled",
]);

function thinkingStatus(
  messages: ThinkingStreamMessage[],
  runProjection?: TaskRunProjectionState,
): TaskRunProjectionState["status"] {
  if (runProjection) return runProjection.status;
  const progressStatuses = messages
    .filter((message): message is Extract<ChatStreamMessage, { role: "progress" }> =>
      message.role === "progress")
    .map((message) => message.status)
    .filter((status): status is NonNullable<typeof status> => status != null);
  // A durable terminal summary wins over a stale or out-of-order running
  // summary when the Run projection is unavailable during hydration.
  if (progressStatuses.includes("failed")) return "failed";
  if (progressStatuses.includes("completed")) return "completed";
  if (progressStatuses.includes("running")) return "running";
  const hasToolResult = messages.some((message) =>
    message.role === "tool" && message.toolResultRaw !== undefined);
  return hasToolResult ? "completed" : "running";
}

function formatThinkingDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function ThinkingProcessGroup({
  runId,
  messages,
  runMessages,
  runProjection,
}: {
  runId: string;
  messages: ThinkingStreamMessage[];
  runMessages: ChatStreamMessage[];
  runProjection?: TaskRunProjectionState;
}) {
  const status = thinkingStatus(messages, runProjection);
  const terminal = TERMINAL_THINKING_STATUSES.has(status);
  const [expanded, setExpanded] = useState(() => !terminal);
  const previousStatus = useRef(status);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const wasTerminal = TERMINAL_THINKING_STATUSES.has(previousStatus.current);
    if (!wasTerminal && terminal) setExpanded(false);
    if (wasTerminal && !terminal) setExpanded(true);
    previousStatus.current = status;
  }, [status, terminal]);

  useEffect(() => {
    if (terminal) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [terminal]);

  if (messages.length === 0) return null;
  // Durable SDK history does not always have a task projection (tool-only
  // provider turns are persisted as ordinary Run-scoped messages).  Use the
  // complete Run boundary for that fallback so a restart cannot shrink the
  // elapsed time to only the interval between the first and last tool card.
  const timestamps = runMessages
    .map((message) => message.ts)
    .filter((value) => Number.isFinite(value) && value >= 0);
  const startedAt = runProjection?.started_at ?? (
    timestamps.length > 0 ? Math.min(...timestamps) : undefined
  );
  const endedAt = terminal
    ? runProjection?.last_activity ?? (timestamps.length > 0 ? Math.max(...timestamps) : undefined)
    : now;
  const elapsed = startedAt != null && endedAt != null
    ? Math.max(0, endedAt - startedAt)
    : 0;
  const elapsedKnown = startedAt != null && endedAt != null && endedAt > startedAt;
  const headerLabel = terminal
    ? elapsedKnown ? `耗时 ${formatThinkingDuration(elapsed)}` : "已完成"
    : elapsedKnown ? `思考中 · ${formatThinkingDuration(elapsed)}` : "思考中";
  const bodyId = `thinking-process-body-${runId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

  return (
    <section
      data-testid={`thinking-process-${runId}`}
      data-status={status}
      style={{ alignSelf: "stretch", width: "100%", color: "#cbd5e1" }}
    >
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={bodyId}
        aria-label={headerLabel}
        onClick={() => setExpanded((value) => !value)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 2px",
          border: 0,
          borderBottom: "1px solid rgba(148, 163, 184, 0.14)",
          background: "transparent",
          color: "#94a3b8",
          cursor: "pointer",
          font: "inherit",
          fontSize: 13,
          textAlign: "left",
        }}
      >
        <span>{headerLabel}</span>
        <span aria-hidden="true" style={{ transform: expanded ? "rotate(90deg)" : "none" }}>
          ›
        </span>
      </button>
      {expanded && (
        <div
          id={bodyId}
          data-testid={`thinking-process-body-${runId}`}
          style={{
            display: "grid",
            gap: 8,
            padding: "10px 2px 12px",
            maxHeight: 360,
            overflowY: "auto",
          }}
        >
          {messages.map((message, index) => (
            <ChatRow
              key={`${message.role}:${message.ts}:${message.text}:${index}`}
              msg={message}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export function ProjectDirectoryCard({
  request,
  error,
  onConfirm,
  submittedLabel = "项目位置已确认",
}: {
  request: ProjectDirectoryRequest["payload"];
  error?: string | null;
  onConfirm?: MessageStreamPanelProps["onProjectDirectoryConfirm"];
  submittedLabel?: string;
}) {
  const [parent, setParent] = useState(request.parent_directory ?? "");
  const [folderName, setFolderName] = useState(request.folder_name);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [pickerError, setPickerError] = useState<string | null>(null);
  useEffect(() => {
    if (error) setSubmitted(false);
  }, [error]);
  const usesExistingProject = request.directory_mode === "use_existing";
  const trimmedParent = parent.trim();
  const displayParent =
    trimmedParent.length > 1
      ? trimmedParent.replace(/[\\/]+$/, "")
      : trimmedParent;
  const pathSeparator =
    trimmedParent.includes("\\") && !trimmedParent.includes("/") ? "\\" : "/";
  const finalPath = parent
    ? usesExistingProject
      ? displayParent
      : displayParent === "/"
        ? `/${folderName.trim()}`
        : `${displayParent}${pathSeparator}${folderName.trim()}`
    : "";

  const chooseParent = async () => {
    if (busy || submitted) return;
    setBusy(true);
    setPickerError(null);
    try {
      const selected = await invoke<string | null>("open_directory_dialog");
      if (selected?.trim()) setParent(selected.trim());
    } catch {
      setPickerError("无法打开文件夹选择器，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const confirm = () => {
    if (!parent || (!usesExistingProject && !folderName.trim()) || !onConfirm)
      return;
    if (onConfirm(parent, folderName.trim())) setSubmitted(true);
  };

  return (
    <div
      data-testid="project-directory-card"
      style={{
        alignSelf: "flex-start",
        width: "min(96%, 520px)",
        padding: "10px 12px",
        borderRadius: 10,
        border: "1px solid rgba(125, 211, 252, 0.24)",
        background: "rgba(15, 23, 42, 0.72)",
        color: "#e2e8f0",
        display: "grid",
        gap: 8,
        fontSize: 12,
      }}
    >
      <div style={{ fontWeight: 650 }}>
        {submitted ? submittedLabel : request.title || "选择项目保存位置"}
      </div>
      <div style={{ color: "#94a3b8", lineHeight: 1.45 }}>
        {request.directory_mode === "use_existing"
          ? `${request.project_kind}「${request.project_name}」将继续使用你确认的现有项目文件夹。`
          : `${request.project_kind}「${request.project_name}」将创建在你选择的文件夹下面。`}
      </div>
      {!submitted && (
        <>
          <div style={{ display: "flex", gap: 7 }}>
            {!usesExistingProject && (
              <input
                aria-label="项目文件夹名"
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
                style={{
                  minWidth: 0,
                  flex: 1,
                  border: "1px solid rgba(148,163,184,0.24)",
                  borderRadius: 7,
                  background: "rgba(2,6,23,0.55)",
                  color: "#e2e8f0",
                  padding: "6px 8px",
                }}
              />
            )}
            <button
              type="button"
              onClick={() => void chooseParent()}
              disabled={busy}
              style={projectDirectoryButtonStyle}
            >
              {busy ? "选择中…" : parent ? "更换位置" : "选择文件夹"}
            </button>
          </div>
          {finalPath && (
            <div
              title={finalPath}
              style={{
                color: "#bae6fd",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {request.directory_mode === "use_existing"
                ? `将使用：${finalPath}`
                : `将创建到：${finalPath}`}
            </div>
          )}
          <button
            type="button"
            disabled={!parent || (!usesExistingProject && !folderName.trim())}
            onClick={confirm}
            style={{
              ...projectDirectoryButtonStyle,
              justifySelf: "start",
              opacity:
                !parent || (!usesExistingProject && !folderName.trim())
                  ? 0.45
                  : 1,
            }}
          >
            {request.directory_mode === "use_existing"
              ? "使用此文件夹"
              : "在这里创建"}
          </button>
        </>
      )}
      {submitted && (
        <div style={{ color: "#86efac" }}>
          {request.directory_mode === "use_existing"
            ? "Agent 已收到位置，正在继续现有项目。"
            : "Agent 已收到位置，正在继续创建项目。"}
        </div>
      )}
      {(error || pickerError) && (
        <div role="alert" style={{ color: "#fca5a5" }}>
          {error || pickerError}
        </div>
      )}
    </div>
  );
}

const projectDirectoryButtonStyle: CSSProperties = {
  border: "1px solid rgba(125,211,252,0.3)",
  borderRadius: 7,
  background: "rgba(14,116,144,0.22)",
  color: "#bae6fd",
  padding: "6px 9px",
  cursor: "pointer",
};

function PublicProgressRow({
  message,
}: {
  message: Extract<ChatStreamMessage, { role: "progress" }>;
}) {
  const phaseLabel =
    message.phase === "observation"
      ? "结果摘要"
      : message.phase === "status"
        ? "状态更新"
        : "执行进度";
  return (
    <div
      data-role="progress"
      style={{
        alignSelf: "flex-start",
        maxWidth: "84%",
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
      }}
    >
      <div
        style={{
          ...rowBaseStyle,
          maxWidth: "100%",
          padding: "9px 11px 10px",
          background: "rgba(20, 28, 40, 0.76)",
          borderColor: "rgba(103, 232, 249, 0.16)",
          color: "#cbd5e1",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 12,
            marginBottom: 5,
            color: "#67e8f9",
            fontSize: 11,
            lineHeight: 1.35,
          }}
        >
          <span>{phaseLabel}</span>
          <span style={{ color: "#94a3b8", opacity: 0.55 }}>
            {format_relative(message.ts)}
          </span>
        </div>
        <div
          data-bp-selectable=""
          style={{ fontSize: 12.5, lineHeight: 1.65 }}
        >
          <MarkdownMessage>{message.text}</MarkdownMessage>
        </div>
      </div>
    </div>
  );
}

function MessageActionRow({
  copyText,
  ts,
  align,
  tone,
}: {
  copyText: string;
  ts?: number;
  align: "left" | "right";
  tone: "dark" | "blue" | "muted";
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: align === "right" ? "flex-end" : "flex-start",
        gap: 8,
        marginTop: 4,
        minHeight: 22,
        width: "100%",
        color: "#7f8794",
        fontSize: 10.5,
      }}
    >
      {typeof ts === "number" && (
        <span style={{ opacity: 0.72 }}>{format_relative(ts)}</span>
      )}
      <CopyMessageButton text={copyText} align={align} tone={tone} inline />
    </div>
  );
}

function AlertRow({
  item,
  severity,
  onDismiss,
  onJump,
  onChoice,
}: {
  item: InboxItem;
  severity: "yellow" | "red";
  onDismiss: () => void;
  onJump: () => void;
  onChoice: (idx: number, text: string) => void;
}) {
  const tone = severity === "yellow" ? PALETTE.warn : PALETTE.err;
  const sid_short =
    item.project_name && item.project_name !== item.session_id
      ? item.project_name
      : item.session_id.length > 18
      ? `${item.session_id.slice(0, 16)}...`
      : item.session_id;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onJump}
      style={{
        ...rowBaseStyle,
        alignSelf: "stretch",
        background: "rgba(255,255,255,0.03)",
        border: `1px solid ${tone.border}`,
        cursor: "pointer",
      }}
    >
      <div style={metaStyle}>
        <span
          style={{
            padding: "1px 5px",
            background: tone.soft,
            color: tone.accent,
            borderRadius: 4,
            fontWeight: 600,
            marginRight: 6,
          }}
        >
          {severity === "yellow" ? "提醒" : "错误"} {sid_short}
        </span>
        <span style={{ marginLeft: "auto", opacity: 0.6 }}>
          {format_relative(item.received_at)}
        </span>
      </div>
      {/* data-bp-selectable: 鍛婅姝ｆ枃鍙鎷栭€夊鍒躲€傛嫋閫夋椂娴忚鍣ㄤ笉瑙﹀彂
          click锛岃绾?onClick 璺宠浆涓嶅彈褰卞搷銆?*/}
      <div data-bp-selectable="" style={bodyStyle}>
        {item.user_message || item.diagnosis || "(supervisor 未提供详情)"}
      </div>
      <div
        style={actionRowStyle}
        onClick={(e) => e.stopPropagation()}
      >
        {item.suggested_buttons.slice(0, 2).map((b, i) => (
          <button
            key={i}
            type="button"
            data-testid={`msgstream-choice-${i}`}
            onClick={(e) => {
              e.stopPropagation();
              onChoice(i, b);
            }}
            style={{
              ...pillButton(tone.accent, tone.border),
              padding: "3px 9px",
              fontWeight: 600,
            }}
          >
            {b}
          </button>
        ))}
        <button
          type="button"
          data-testid="msgstream-dismiss"
          onClick={(e) => {
            e.stopPropagation();
            onDismiss();
          }}
          style={pillButton("#9ca3af", "rgba(148, 163, 184, 0.30)")}
          title="标记已处理"
        >
          已知道
        </button>
        <CopyMessageButton
          text={item.user_message || item.diagnosis || ""}
          align="right"
          tone="dark"
        />
      </div>
    </div>
  );
}

function pillButton(color: string, border: string): CSSProperties {
  return {
    fontSize: 10.5,
    fontWeight: 500,
    padding: "2px 8px",
    borderRadius: 5,
    border: `1px solid ${border}`,
    background: "transparent",
    color,
    cursor: "pointer",
    whiteSpace: "nowrap",
  };
}

// 实现已抽到 ../relativeTime（会话列表行也要同一套 "14h 前" 口径）。
// 保留本地别名，免动下面 5 个调用点。
const format_relative = formatRelativeMs;

// ----------------------------------------------------------------------
// Style constants
// ----------------------------------------------------------------------

const wrapperStyle: CSSProperties = {
  position: "absolute",
  top: 56,
  left: 8,
  width: 248,
  maxHeight: "calc(100vh - 110px)",
  zIndex: 22,
  pointerEvents: "auto",
  background: "rgba(15, 18, 28, 0.92)",
  border: "1px solid rgba(148, 163, 184, 0.25)",
  borderRadius: 10,
  boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
  backdropFilter: "blur(12px)",
  color: "#e5e7eb",
  fontSize: 11.5,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

// embedded variant 鈥?濉弧鐖?flex 鏍硷紙鐖舵牸缁欏畾瀹介珮锛夈€?
const embeddedWrapperStyle: CSSProperties = {
  position: "relative",
  height: "100%",
  width: "100%",
  background: "rgba(15, 18, 28, 0.92)",
  borderRight: "1px solid rgba(148, 163, 184, 0.18)",
  color: "#e5e7eb",
  fontSize: 11.5,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const listStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  padding: "14px 14px 8px",
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const rowBaseStyle: CSSProperties = {
  maxWidth: "84%",
  padding: "9px 13px",
  borderRadius: 14,
  border: "1px solid transparent",
  display: "flex",
  flexDirection: "column",
  gap: 4,
  wordBreak: "break-word",
  whiteSpace: "pre-wrap",
};

const metaStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  fontSize: 10,
  opacity: 0.65,
  color: "#9ca3af",
};

const bodyStyle: CSSProperties = {
  fontSize: 12.5,
  lineHeight: 1.6,
  color: "inherit",
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 4,
  alignItems: "center",
  justifyContent: "flex-end",
  marginTop: 2,
};

const emptyStyle: CSSProperties = {
  textAlign: "center",
  color: "#9ca3af",
  fontSize: 11.5,
  padding: "16px 8px",
  lineHeight: 1.6,
};
