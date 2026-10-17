// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { ChatMissionCard } from "../views/ChatMissionCard";
import { MISSION_CARD_TOOLS, missionIdFromToolResult } from "../views/chatMission";
import { useEffect, useState, type CSSProperties } from "react";
import type { InboxItem } from "../stores/sessionSelectors";
import type { TaskRunProjectionState } from "../stores/sessionTypes";
import { PPTOutlineCard } from "../code-panel/PPTOutlineCard";
import { ArtifactCard } from "../code-panel/ArtifactCard";
import { extractArtifactsFromResult } from "../code-panel/artifactExtract";
import { ToolCallCard, ToolResultCard } from "../code-panel/MessageBubble";
import { CopyMessageButton } from "./CopyMessageButton";
import { MarkdownMessage } from "./MarkdownMessage";
import { WorkflowProgressGroup } from "./workflow/WorkflowProgressGroup";
import { formatRelativeMs } from "../relativeTime";
import type {
  ChatStreamMessage,
  ThinkingStreamMessage,
  MessageStreamPanelProps,
} from "./messageStreamTypes";
import { isLegacySyntheticToolProgress } from "./buildStreamRows";
import { PALETTE, rowBaseStyle } from "./messageStreamStyles";
import "./MessageStreamRows.styles.css";

function toolArtifactStatus(toolName?: string): string {
  if ((toolName || "").toLowerCase() === "deepresearch") {
    return "deepresearch 报告已保存，正在整理答复";
  }
  return `${toolName || "工具"} 已生成文件`;
}

export function ChatRow({
  msg,
  onWorkflowRetry,
}: {
  msg: ChatStreamMessage;
  onWorkflowRetry?: MessageStreamPanelProps["onWorkflowRetry"];
}) {
  if (msg.role === "workflow_progress" || msg.role === "workflow_stage") {
    return (
      <WorkflowProgressGroup
        runId={msg.message.workflow_run_id || msg.message.id}
        summary={msg.role === "workflow_progress" ? msg.message : undefined}
        stages={msg.role === "workflow_stage" ? [msg.message] : []}
        onWorkflowRetry={onWorkflowRetry}
      />
    );
  }
  if (msg.role === "ppt_outline") {
    const m = msg.message;
    return (
      <div className="sh-MessageStreamRows-1">
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
        <div data-role="tool" className="sh-MessageStreamRows-2">
          <ToolCallCard name={msg.toolName || "(工具)"} args={msg.toolArgs} />
        </div>
      );
    }
    // 2026-09-29：主 Agent 发起的后台任务在对话里显示成任务卡片（进度 + 人亲手点的确认/批准）
    const missionId =
      MISSION_CARD_TOOLS.has(msg.toolName ?? "") && msg.toolOk !== false
        ? missionIdFromToolResult(msg.toolResultRaw)
        : "";
    if (missionId) {
      return (
        <div data-role="tool" className="sh-MessageStreamRows-3">
          <ChatMissionCard missionId={missionId} />
        </div>
      );
    }
    const artifacts =
      msg.toolOk !== false && msg.toolResultRaw
        ? extractArtifactsFromResult(msg.toolResultRaw)
        : [];
    if (artifacts.length > 0) {
      return (
        <div className="sh-MessageStreamRows-4" data-role="tool">
          <div data-bp-selectable="" className="sh-MessageStreamRows-5">
            <span>{toolArtifactStatus(msg.toolName)}</span>
            <span className="sh-MessageStreamRows-6">{format_relative(ts)}</span>
          </div>
          <div className="sh-MessageStreamRows-7">
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
        <div data-role="tool" className="sh-MessageStreamRows-2">
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
      <div className="sh-MessageStreamRows-8" data-role="tool">
        <div data-bp-selectable="" className="sh-MessageStreamRows-9">
          {text || "(宸ュ叿)"}
          <span className="sh-MessageStreamRows-10">{format_relative(ts)}</span>
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
          borderColor: role === "user" ? "rgba(125, 211, 252, 0.20)" : "rgba(255,255,255,0.06)",
          boxShadow:
            role === "user"
              ? "inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 18px rgba(8,47,73,0.22)"
              : undefined,
        }}
      >
        {/* data-bp-selectable: 璁╂秷鎭鏂囧彲琚紶鏍囨嫋閫夊鍒讹紙index.css 鐨?
          鍏ㄥ眬 user-select:none 榛樿浼氭尅浣忥級銆?
          assistant 鍥炲璧?markdown 娓叉煋锛堜唬鐮佸潡/鍒楄〃/鍔犵矖/鏈湴鏂囦欢閾炬帴锛夛紝
          涓?code 妯″紡涓€鑷达紱user 杈撳叆淇濇寔绾枃鏈紙pre-wrap 淇濈暀鎹㈣锛夈€?*/}
        {role === "assistant" && text ? (
          <div data-bp-selectable="" className="sh-MessageStreamRows-11">
            <MarkdownMessage>{text}</MarkdownMessage>
          </div>
        ) : (
          <div data-bp-selectable="" className="sh-MessageStreamRows-12">
            {text || "(空)"}
          </div>
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
    .filter(
      (message): message is Extract<ChatStreamMessage, { role: "progress" }> =>
        message.role === "progress",
    )
    .map((message) => message.status)
    .filter((status): status is NonNullable<typeof status> => status != null);
  // A durable terminal summary wins over a stale or out-of-order running
  // summary when the Run projection is unavailable during hydration.
  if (progressStatuses.includes("failed")) return "failed";
  if (progressStatuses.includes("completed")) return "completed";
  if (progressStatuses.includes("running")) return "running";
  const hasToolResult = messages.some(
    (message) => message.role === "tool" && message.toolResultRaw !== undefined,
  );
  return hasToolResult ? "completed" : "running";
}

function formatThinkingDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

export function ThinkingProcessGroup({
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
  const [previousStatus, setPreviousStatus] = useState(status);
  const [now, setNow] = useState(() => Date.now());

  // 渲染期按上一次的状态调整展开（进入终态收起、离开终态展开），不在 effect 里同步 setState。
  if (status !== previousStatus) {
    setPreviousStatus(status);
    const wasTerminal = TERMINAL_THINKING_STATUSES.has(previousStatus);
    if (!wasTerminal && terminal) setExpanded(false);
    if (wasTerminal && !terminal) setExpanded(true);
  }

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
  const startedAt =
    runProjection?.started_at ?? (timestamps.length > 0 ? Math.min(...timestamps) : undefined);
  const endedAt = terminal
    ? (runProjection?.last_activity ??
      (timestamps.length > 0 ? Math.max(...timestamps) : undefined))
    : now;
  const elapsed = startedAt != null && endedAt != null ? Math.max(0, endedAt - startedAt) : 0;
  const elapsedKnown = startedAt != null && endedAt != null && endedAt > startedAt;
  const headerLabel = terminal
    ? elapsedKnown
      ? `耗时 ${formatThinkingDuration(elapsed)}`
      : "已完成"
    : elapsedKnown
      ? `思考中 · ${formatThinkingDuration(elapsed)}`
      : "思考中";
  const bodyId = `thinking-process-body-${runId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

  return (
    <section
      data-testid={`thinking-process-${runId}`}
      data-status={status}
      className="sh-MessageStreamRows-13"
    >
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={bodyId}
        aria-label={headerLabel}
        onClick={() => setExpanded((value) => !value)}
        className="sh-MessageStreamRows-14"
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
          className="sh-MessageStreamRows-15"
        >
          {messages.map((message, index) => (
            <ChatRow key={`${message.role}:${message.ts}:${message.text}:${index}`} msg={message} />
          ))}
        </div>
      )}
    </section>
  );
}

export function PublicProgressRow({
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
    <div data-role="progress" className="sh-MessageStreamRows-16">
      <div className="sh-MessageStreamRows-17">
        <div className="sh-MessageStreamRows-18">
          <span>{phaseLabel}</span>
          <span className="sh-MessageStreamRows-19">{format_relative(message.ts)}</span>
        </div>
        <div data-bp-selectable="" className="sh-MessageStreamRows-20">
          <MarkdownMessage>{message.text}</MarkdownMessage>
        </div>
      </div>
    </div>
  );
}

export function MessageActionRow({
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
        <span className="sh-MessageStreamRows-21">{format_relative(ts)}</span>
      )}
      <CopyMessageButton text={copyText} align={align} tone={tone} inline />
    </div>
  );
}

export function AlertRow({
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
      <div className="sh-MessageStreamRows-22">
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
        <span className="sh-MessageStreamRows-23">{format_relative(item.received_at)}</span>
      </div>
      {/* data-bp-selectable: 鍛婅姝ｆ枃鍙鎷栭€夊鍒躲€傛嫋閫夋椂娴忚鍣ㄤ笉瑙﹀彂
          click锛岃绾?onClick 璺宠浆涓嶅彈褰卞搷銆?*/}
      <div data-bp-selectable="" className="sh-MessageStreamRows-12">
        {item.user_message || item.diagnosis || "(supervisor 未提供详情)"}
      </div>
      <div className="sh-MessageStreamRows-24" onClick={(e) => e.stopPropagation()}>
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
          className="sh-MessageStreamRows-25"
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
