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
import { useEffect, useLayoutEffect, useMemo, useRef, type CSSProperties } from "react";
// 瀛愪唬鐞嗗苟鍙戣繘搴﹀崱鐗囷紙娣辫壊鍙樹綋锛屼笌鏈潰鏉跨幓鐠冩嫙鎬佷竴鑷达級銆俽uns 绌烘椂鑷覆鏌?null锛?
// Shared control WS dispatches subagent_progress into subagentStore.
import { SubagentProgressPanel } from "../code-panel/SubagentProgressPanel";
import { WorkflowProgressGroup } from "./workflow/WorkflowProgressGroup";
import { CompanionCard } from "./companion/CompanionCard";
import {
  normalizePublicRunSnapshot,
  useHarnessPublicSnapshot,
} from "../stores/harnessPublicSnapshotStore";
import type { StreamFilter, MessageStreamPanelProps } from "./messageStreamTypes";
import { buildRows } from "./buildStreamRows";
import { ChatRow, ThinkingProcessGroup, AlertRow } from "./MessageStreamRows";
import { ProjectDirectoryCard } from "./ProjectDirectoryCard";

// 娑堟伅娴佹粴鍔ㄤ綅缃寔涔呴敭(杩涘叆娑堟伅鐣岄潰鎭㈠涓婃浣嶇疆,瑙佷笅 useLayoutEffect)銆?
const MSGSTREAM_SCROLL_KEY = "deskpet.msgstream.scroll.v1";
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
    () =>
      buildRows(
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
      localStorage.setItem(MSGSTREAM_SCROLL_KEY, JSON.stringify({ top: el.scrollTop, atBottom }));
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

      <div ref={listRef} data-testid="msgstream-list" style={listStyle} onScroll={handleListScroll}>
        {rows.length === 0 ? (
          <div style={emptyStyle}>{emptyMessage(filter)}</div>
        ) : (
          rows.map((r) =>
            r.kind === "chat" ? (
              <ChatRow key={r.key} msg={r.msg} onWorkflowRetry={onWorkflowRetry} />
            ) : r.kind === "thinking_group" ? (
              <ThinkingProcessGroup
                key={r.key}
                runId={r.runId}
                messages={r.messages}
                runMessages={chatMessages.filter(
                  (message) => "runId" in message && message.runId === r.runId,
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
                onAction={(event, action, allow) => onCompanionAction?.(event, action, allow)}
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
                onChoice={(idx, text) => onChoice(r.item.session_id, r.item.alert_id, idx, text)}
              />
            ),
          )
        )}
      </div>
    </div>
  );
}
function emptyMessage(f: StreamFilter): string {
  switch (f) {
    case "chat":
      return "本次还没聊过。";
    case "warn":
      return "暂无未处理的提醒。";
    case "err":
      return "暂无未处理的错误。";
    default:
      return "（消息流为空）";
  }
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
const emptyStyle: CSSProperties = {
  textAlign: "center",
  color: "#9ca3af",
  fontSize: 11.5,
  padding: "16px 8px",
  lineHeight: 1.6,
};
