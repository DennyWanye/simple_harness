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
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  type CSSProperties,
} from "react";

// 娑堟伅娴佹粴鍔ㄤ綅缃寔涔呴敭(杩涘叆娑堟伅鐣岄潰鎭㈠涓婃浣嶇疆,瑙佷笅 useLayoutEffect)銆?
const MSGSTREAM_SCROLL_KEY = "deskpet.msgstream.scroll.v1";

import type { InboxItem, Message } from "../stores/sessionsStore";
// 瀛愪唬鐞嗗苟鍙戣繘搴﹀崱鐗囷紙娣辫壊鍙樹綋锛屼笌鏈潰鏉跨幓鐠冩嫙鎬佷竴鑷达級銆俽uns 绌烘椂鑷覆鏌?null锛?
// 闆朵镜鍏ワ紱鏁版嵁鐢辨湰绐楀彛 codePanelWS 鐨?subagent_progress 娲惧彂鍠?subagentStore銆?
import { SubagentProgressPanel } from "../code-panel/SubagentProgressPanel";
import { PPTOutlineCard } from "../code-panel/PPTOutlineCard";
import { ArtifactCard, extractArtifactsFromResult } from "../code-panel/ArtifactCard";
import { CopyMessageButton } from "./CopyMessageButton";
import { MarkdownMessage } from "./MarkdownMessage";

export type StreamFilter = "all" | "chat" | "warn" | "err";

export type ChatStreamMessage =
  | {
      // 2026-06-12: 鍔?"tool" 鈥?宸ュ叿鎵ц杞ㄨ抗(璋冪敤/缁撴灉)杩涗富娑堟伅娴?
      // 鐢ㄦ埛鍏ㄧ▼鍙娴?姝ゅ墠娲剧敓灞傛妸 tool_call/tool_result 婊ゆ帀浜?銆?
      role: "user" | "assistant" | "tool";
      text: string;
      ts: number;
      toolName?: string;
      toolOk?: boolean;
      toolResultRaw?: string;
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
    };

export interface MessageStreamPanelProps {
  filter: StreamFilter;
  /** Companion-mode chat messages, oldest 鈫?newest. Internally we sort
   * by ts when merging with alerts. */
  chatMessages: ChatStreamMessage[];
  warnings: InboxItem[];
  errors: InboxItem[];
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
  /** 2026-05-16: pet window = floating absolute overlay (default,
   * unchanged). Code-mode window embeds this in a 3-column flex layout
   * 鈫?embedded=true switches the wrapper from absolute to a relative
   * flex-fill panel (no top/left/width hardcode). Visual styling
   * identical; only positioning differs. Default false keeps the pet
   * window byte-identical (zero regression). */
  embedded?: boolean;
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
  onDismiss,
  onJumpToSession,
  onChoice,
  embedded = false,
}: MessageStreamPanelProps) {
  // 娉細onSetFilter / onDismissAll 浠嶄繚鐣欏湪 MessageStreamPanelProps 绫诲瀷閲?
  // 锛堣皟鐢ㄦ柟鐓у父浼狅級锛屼絾褰撳墠 render 鏈敤鍒扳€斺€斿崐鎺ョ嚎鐨勮繃婊ゆ潯鐗规€с€傚厛涓嶈В鏋?
  // 浠ラ€氳繃 tsc noUnusedParameters锛涜鎭㈠杩囨护鏉?UI 鏃跺啀鎺ュ洖銆?
  const rows = useMemo(
    () => buildRows(chatMessages, warnings, errors, filter),
    [chatMessages, warnings, errors, filter],
  );

  // Auto-scroll to newest row when it changes (only if user hasn't
  // scrolled up 鈥?the simple heuristic is "we're already near the
  // bottom"). Resilient to React batching; we read scrollTop just
  // after layout.
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [rows.length]);

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
      el.scrollTop = el.scrollHeight; // 榛樿/涓婃璐村簳 鈫?搴曢儴
    } else {
      el.scrollTop = Math.max(0, Math.min(saved.top, el.scrollHeight));
    }
  }, []);

  const handleListScroll = () => {
    const el = listRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
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
      aria-label="桌宠消息流"
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

      <div ref={listRef} style={listStyle} onScroll={handleListScroll}>
        {rows.length === 0 ? (
          <div style={emptyStyle}>
            {emptyMessage(filter)}
          </div>
        ) : (
          rows.map((r) =>
            r.kind === "chat" ? (
              <ChatRow key={r.key} msg={r.msg} />
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
  filter: StreamFilter,
): StreamRow[] {
  const rows: StreamRow[] = [];
  if (filter === "all" || filter === "chat") {
    chats.forEach((m, i) =>
      rows.push({
        kind: "chat",
        ts: m.ts,
        msg: m,
        key: m.role === "workflow_progress"
          ? `workflow:${m.message.workflow_run_id}`
          : `c:${i}:${m.ts}`,
      }),
    );
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

function ChatRow({ msg }: { msg: ChatStreamMessage }) {
  if (msg.role === "workflow_progress") {
    return <WorkflowProgressRow message={msg.message} />;
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
        />
        <CopyMessageButton
          text={[m.topic, m.outline_md].filter(Boolean).join("\n\n")}
          tone="dark"
        />
      </div>
    );
  }

  const { role, text, ts } = msg;
  if (role === "tool") {
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

function format_relative(ts: number, now: number = Date.now()): string {
  const delta_s = Math.max(0, Math.round((now - ts) / 1000));
  if (delta_s < 60) return `${delta_s}s 前`;
  if (delta_s < 3600) return `${Math.round(delta_s / 60)}m 前`;
  if (delta_s < 86400) return `${Math.round(delta_s / 3600)}h 前`;
  return `${Math.round(delta_s / 86400)}d 前`;
}

function WorkflowProgressRow({ message }: { message: Message }) {
  const status = message.workflow_status ?? "running";
  const total = Math.max(0, message.workflow_total ?? 0);
  const ordinal = Math.max(0, message.workflow_ordinal ?? 0);
  const displayOrdinal = Math.max(
    ordinal,
    message.workflow_display_ordinal ?? ordinal,
  );
  const percent = status === "completed"
    ? 100
    : total > 0
      ? Math.min(99, Math.round((displayOrdinal / total) * 100))
      : 0;
  const tones: Record<NonNullable<Message["workflow_status"]>, {
    label: string;
    accent: string;
    soft: string;
  }> = {
    running: { label: "进行中", accent: "#38bdf8", soft: "rgba(56,189,248,0.14)" },
    waiting: { label: "等待操作", accent: "#fbbf24", soft: "rgba(251,191,36,0.14)" },
    completed: { label: "已完成", accent: "#34d399", soft: "rgba(52,211,153,0.14)" },
    failed: { label: "失败", accent: "#f87171", soft: "rgba(248,113,113,0.14)" },
    cancelled: { label: "已取消", accent: "#94a3b8", soft: "rgba(148,163,184,0.14)" },
  };
  const tone = tones[status];
  const position = total > 0 ? `${ordinal}/${total}` : "准备中";
  const detail = message.workflow_error || message.workflow_stage || "准备中";

  return (
    <div
      data-testid={`workflow-progress-${message.workflow_run_id}`}
      data-role="workflow_progress"
      data-status={status}
      style={{
        alignSelf: "stretch",
        height: 104,
        minHeight: 104,
        maxHeight: 104,
        boxSizing: "border-box",
        overflow: "hidden",
        padding: "11px 13px",
        borderRadius: 8,
        border: `1px solid ${tone.accent}55`,
        background: "rgba(18, 24, 35, 0.92)",
        display: "grid",
        gridTemplateRows: "22px 18px 8px 18px",
        gap: 4,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <strong
          style={{
            minWidth: 0,
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: "#e8edf6",
            fontSize: 12.5,
            letterSpacing: 0,
          }}
        >
          {message.workflow_name || "任务"}
        </strong>
        <span
          style={{
            flexShrink: 0,
            padding: "2px 7px",
            borderRadius: 4,
            color: tone.accent,
            background: tone.soft,
            fontSize: 10.5,
            fontWeight: 600,
            whiteSpace: "nowrap",
          }}
        >
          {tone.label}
        </span>
      </div>
      <div
        title={detail}
        style={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: message.workflow_error ? tone.accent : "#b9c2d0",
          fontSize: 11.5,
          lineHeight: "18px",
        }}
      >
        {detail}
      </div>
      <div
        role="progressbar"
        aria-label={`${message.workflow_name || "任务"}进度`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${tone.label}，${message.workflow_stage || "准备中"}，${position}`}
        style={{
          height: 6,
          alignSelf: "center",
          borderRadius: 3,
          overflow: "hidden",
          background: "rgba(148,163,184,0.18)",
        }}
      >
        <div
          style={{
            width: `${percent}%`,
            height: "100%",
            borderRadius: 3,
            background: tone.accent,
            transition: status === "running" ? "width 220ms ease" : "none",
          }}
        />
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "end",
          color: "#7f8a99",
          fontSize: 10.5,
          lineHeight: "18px",
        }}
      >
        <span>{position}</span>
        <span>{percent}%</span>
      </div>
    </div>
  );
}

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
