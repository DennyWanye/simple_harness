// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SessionList（T7，WB-5 / B3）— 会话列表侧栏组件。
 *
 * 从 MessagePanelRoot.tsx 的 header 会话下拉抽出并「列表化」改造：
 * 协议不变（sessions_list / chat_v2+new_session / session_rename /
 * session_delete），同功能移入侧栏「会话」展开区。
 *
 * 抽取物（plan T7 标识符清单）：loadSessions、sessions_list_response/
 * session_deleted/session_renamed 监听 effect、switchToSession（含
 * hydration 归 ChatView，见 T8）、startNewTopic、deleteSession、
 * finishRename、switchToDefault、随迁编辑/确认 state、下拉 JSX 列表化。
 * 真语音块（toggleRecording/audioMessage）不迁（T8 口径一致）。
 *
 * activeSid 的单一所有者是 App（switchActiveSid：ensure + set_active +
 * setState）；本组件通过 onSwitchSid 回调上抛，不自持会话指针。
 *
 * 样式纪律（WB-11）：颜色一律取自 theme/tokens + dark 套件，零硬编码色值。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { controlWS, CONTROL_SESSION_ID } from "../code-panel/controlWs";
import { useSessionsStore } from "../stores/sessionsStore";
import { ConfirmDialog } from "../code-panel/ConfirmDialog";
import { createClientTurnIdentity } from "../ws/clientTurnIdentity";
import {
  MAX_TITLE_LEN,
  normalizeTopicTitle,
  topicDisplayLabel,
} from "../chat/topicTitle";

/** 后端 default 会话（companion 主线程）。 */
const DEFAULT_SID = "default";

type IncomingCtrlMsg = {
  type?: string;
  payload?: { sessions?: unknown; session_id?: string; title?: string } & Record<string, unknown>;
};

export type SessionEntry = {
  session_id: string;
  turn_count: number;
  last_message_at: number;
  preview: string;
  /** User-set custom title (empty = unnamed → fall back to preview). */
  title?: string;
};

export interface SessionListProps {
  activeSid: string;
  /** 会话切换上抛（App.switchActiveSid：ensure + set_active + setState）。 */
  onSwitchSid: (sid: string) => void;
}

export function SessionList({ activeSid, onSwitchSid }: SessionListProps) {
  const [sessionList, setSessionList] = useState<SessionEntry[]>([]);
  // 「重命名话题」内联编辑：一次只编辑一行。
  const [editingSid, setEditingSid] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [pendingDelete, setPendingDelete] = useState<SessionEntry | null>(null);
  const [newTopicPending, setNewTopicPending] = useState(false);
  const [companionIdentityReady, setCompanionIdentityReady] = useState(false);
  // Enter/Esc 会把 editingSid 置空 → input 卸载触发 onBlur；用这个标记让那次
  // 善后 blur 不要再二次提交。startRename 时清零，避免污染下一次编辑。
  const skipBlurRef = useRef(false);
  const renameInputRef = useRef<HTMLInputElement | null>(null);

  // ── 会话数据链路 ────────────────────────────────────────────────
  const loadSessions = useCallback(() => {
    controlWS.send({ type: "sessions_list" });
  }, []);

  // 监听后端 sessions_list_response / session_deleted / session_renamed；
  // 挂载时拉一次清单（原「面板打开时」语义 → 侧栏常驻改为 mount 时）。
  useEffect(() => {
    const off = controlWS.on_message((msg: IncomingCtrlMsg) => {
      if (msg?.type === "sessions_list_response") {
        const arr = Array.isArray(msg?.payload?.sessions) ? msg.payload.sessions : [];
        setSessionList(arr);
      } else if (msg?.type === "session_deleted") {
        // 后端确认删除 → 重新拉清单保持一致。
        loadSessions();
      } else if (msg?.type === "session_renamed" && msg?.payload?.ok) {
        // 后端确认 → 用规范化后的 title 校正本地(防 trim/clamp 漂移)。
        const sid = msg.payload.session_id as string;
        const title = (msg.payload.title as string) ?? "";
        setSessionList((prev) =>
          prev.map((x) => (x.session_id === sid ? { ...x, title } : x)),
        );
      }
    });
    loadSessions();
    return off;
  }, [loadSessions]);

  // companion 身份就绪状态（新话题按钮 gate）。只读订阅——就绪时的
  // companion_action_ready 促升由 ChatView（常挂载）独家发送，避免双发。
  useEffect(() => controlWS.on_message((raw: unknown) => {
    const message = raw as { type?: unknown; payload?: Record<string, unknown> };
    if (message.type === "companion_identity_status") {
      setCompanionIdentityReady(
        message.payload?.ready === true || message.payload?.status === "ready",
      );
    } else if (message.type === "companion_identity_unready") {
      setCompanionIdentityReady(false);
    }
  }), []);

  // 会话指针同步（MessagePanelRoot :574-609 迁移）：chat 通道（controlWS）
  // 上的 session_switched/task_session_started 及带 session_id 的消息
  // 驱动 activeSid 上抛；同时消解 newTopicPending。
  useEffect(() => {
    return controlWS.on_message((msg: IncomingCtrlMsg) => {
      const p = msg?.payload || {};
      if (
        msg?.type === "session_switched" ||
        msg?.type === "task_session_started" ||
        msg?.type === "chat_v2_run_started" ||
        msg?.type === "chat_v2_error"
      ) {
        setNewTopicPending(false);
      }
      let nextSid = "";
      if (msg?.type === "session_switched" || msg?.type === "task_session_started") {
        nextSid = typeof p.new_sid === "string" ? p.new_sid : "";
        // 新会话诞生 → 刷新清单（侧栏常驻，不能等下次手动展开）。
        loadSessions();
      } else if (
        msg?.type === "chat_response" ||
        msg?.type === "chat_v2_final" ||
        msg?.type === "tool_call" ||
        msg?.type === "tool_result" ||
        msg?.type === "ppt_outline_proposed"
      ) {
        const payloadSid = typeof p.session_id === "string" ? p.session_id : "";
        if (payloadSid && payloadSid !== DEFAULT_SID && payloadSid !== CONTROL_SESSION_ID) {
          nextSid = payloadSid;
        }
      }
      if (!nextSid) return;
      const store = useSessionsStore.getState();
      store.ensure(nextSid);
      store.set_active(nextSid);
      onSwitchSid(nextSid);
    });
  }, [loadSessions, onSwitchSid]);

  const switchToDefault = useCallback(() => {
    onSwitchSid(DEFAULT_SID);
  }, [onSwitchSid]);

  const switchToSession = useCallback((sid: string) => {
    onSwitchSid(sid);
  }, [onSwitchSid]);

  const startNewTopic = useCallback(() => {
    if (!companionIdentityReady || newTopicPending) return;
    const identity = createClientTurnIdentity();
    setNewTopicPending(true);
    const sent = controlWS.send({
      type: "chat_v2",
      payload: {
        session_id: activeSid,
        new_session: true,
        text: "",
        request_id: identity.request_id,
        turn_id: identity.turn_id,
      },
    });
    if (sent) return;
    setNewTopicPending(false);
    useSessionsStore.getState().push_message(activeSid, {
      role: "error",
      text: "新话题创建失败：控制通道未连接，请稍后重试。",
    });
  }, [activeSid, companionIdentityReady, newTopicPending]);

  const deleteSession = useCallback(
    (sid: string) => {
      controlWS.send({ type: "session_delete", payload: { session_id: sid } });
      // 乐观移除 + 若删的是当前会话则切回 default。
      setSessionList((prev) => prev.filter((s) => s.session_id !== sid));
      if (sid === activeSid && sid !== DEFAULT_SID) {
        switchToDefault();
      }
    },
    [activeSid, switchToDefault],
  );

  // ── 重命名话题 ───────────────────────────────────────────────────
  const startRename = useCallback((s: SessionEntry) => {
    skipBlurRef.current = false; // clear any stale blur-skip from a prior edit
    setEditingSid(s.session_id);
    setDraftTitle((s.title || "").trim());
  }, []);

  // commit=false → 取消(不改)。commit=true → trim/clamp 后若有变化才发 ws +
  // 乐观更新；空串表示清除自定义名(后端删行、回退 preview)。
  const finishRename = useCallback(
    (sid: string, commit: boolean) => {
      skipBlurRef.current = true; // the unmount-blur that follows must not re-commit
      setEditingSid(null);
      if (!commit) return;
      const next = normalizeTopicTitle(draftTitle);
      const current = (
        sessionList.find((x) => x.session_id === sid)?.title || ""
      ).trim();
      if (next === current) return; // unchanged → no-op
      setSessionList((prev) =>
        prev.map((x) => (x.session_id === sid ? { ...x, title: next } : x)),
      );
      controlWS.send({
        type: "session_rename",
        payload: { session_id: sid, title: next },
      });
    },
    [draftTitle, sessionList],
  );

  // 进入编辑后聚焦 + 选中文本。
  useEffect(() => {
    if (editingSid !== null) {
      const el = renameInputRef.current;
      el?.focus();
      el?.select();
    }
  }, [editingSid]);

  // WB-5：时间倒序展示（后端序不作假设，本地保证）。
  const sortedSessions = useMemo(
    () =>
      [...sessionList].sort(
        (a, b) => (b.last_message_at ?? 0) - (a.last_message_at ?? 0),
      ),
    [sessionList],
  );

  return (
    <div
      data-testid="session-list"
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        flex: 1,
        gap: tokens.space.xs,
        fontFamily: tokens.font.ui,
      }}
    >
      {/* 新建会话入口（WB-5） */}
      <button
        type="button"
        data-testid="session-new-topic"
        onClick={startNewTopic}
        disabled={!companionIdentityReady || newTopicPending}
        title={
          companionIdentityReady
            ? "新建会话"
            : "正在恢复身份，稍候可新建会话"
        }
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: tokens.space.xs,
          width: "100%",
          boxSizing: "border-box",
          padding: `${tokens.space.xs + 2}px ${tokens.space.sm}px`,
          borderRadius: tokens.radius.md,
          border: `1px dashed ${dark.borderStrong}`,
          background: "transparent",
          color:
            companionIdentityReady && !newTopicPending
              ? dark.accent
              : dark.textFaint,
          fontFamily: tokens.font.ui,
          fontSize: tokens.text.sm.size,
          fontWeight: tokens.weight.medium,
          cursor:
            companionIdentityReady && !newTopicPending
              ? "pointer"
              : "not-allowed",
          flexShrink: 0,
          opacity: newTopicPending ? 0.6 : 1,
        }}
      >
        {newTopicPending ? "创建中…" : "＋ 新建会话"}
      </button>

      {/* 会话清单 — 时间倒序，可切换/重命名/删除 */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {sortedSessions.length === 0 && (
          <div
            data-testid="session-list-empty"
            style={{
              padding: `${tokens.space.sm}px ${tokens.space.sm}px`,
              color: dark.textFaint,
              fontSize: tokens.text.sm.size,
              lineHeight: tokens.text.sm.lh,
            }}
          >
            暂无历史会话。点击上方「新建会话」开始第一段对话，或直接在右侧输入框发消息。
          </div>
        )}
        {sortedSessions.map((s) => {
          const selected = s.session_id === activeSid;
          const isDefault = s.session_id === DEFAULT_SID;
          const autoLabel = isDefault ? "默认话题" : s.preview || s.session_id;
          const label = topicDisplayLabel({
            isDefault,
            title: s.title,
            preview: s.preview,
            session_id: s.session_id,
          });
          const isEditing = editingSid === s.session_id;
          return (
            <div
              key={s.session_id}
              data-testid={`session-row-${s.session_id}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: tokens.space.xs,
                padding: `${tokens.space.xs + 2}px ${tokens.space.sm}px`,
                borderRadius: tokens.radius.md,
                background: selected ? dark.card : "transparent",
                borderLeft: selected
                  ? `3px solid ${dark.accent}`
                  : "3px solid transparent",
                flexShrink: 0,
              }}
            >
              {isEditing ? (
                <input
                  ref={renameInputRef}
                  value={draftTitle}
                  maxLength={MAX_TITLE_LEN}
                  placeholder={autoLabel}
                  aria-label="重命名话题"
                  data-testid={`session-rename-input-${s.session_id}`}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      finishRename(s.session_id, true);
                    } else if (e.key === "Escape") {
                      e.preventDefault();
                      finishRename(s.session_id, false);
                    }
                  }}
                  onBlur={() => {
                    if (skipBlurRef.current) {
                      skipBlurRef.current = false;
                      return;
                    }
                    finishRename(s.session_id, true);
                  }}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    fontSize: tokens.text.base.size,
                    padding: `${tokens.space.xs}px ${tokens.space.xs + 3}px`,
                    borderRadius: tokens.radius.sm,
                    border: `1px solid ${dark.accent}`,
                    background: dark.bgSolid,
                    color: dark.text,
                    outline: "none",
                    fontFamily: tokens.font.ui,
                  }}
                />
              ) : (
                <>
                  <div
                    onClick={() => switchToSession(s.session_id)}
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      startRename(s);
                    }}
                    title="单击切换 · 双击重命名"
                    data-testid={`session-switch-${s.session_id}`}
                    style={{ flex: 1, minWidth: 0, cursor: "pointer" }}
                  >
                    <div
                      style={{
                        color: selected ? dark.accent : dark.text,
                        fontSize: tokens.text.base.size,
                        fontWeight: selected
                          ? tokens.weight.semibold
                          : tokens.weight.medium,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {label}
                    </div>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: tokens.space.xs,
                        color: dark.textMuted,
                        fontSize: tokens.text.xs.size,
                        marginTop: 2,
                        minWidth: 0,
                      }}
                    >
                      <span style={{ flexShrink: 0 }}>{s.turn_count} 条</span>
                      <span
                        title="Session ID，可选中复制"
                        onClick={(e) => e.stopPropagation()}
                        style={{
                          flexShrink: 1,
                          minWidth: 0,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          userSelect: "text",
                          cursor: "text",
                          color: dark.textFaint,
                          fontFamily: tokens.font.mono,
                        }}
                      >
                        {s.session_id}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      startRename(s);
                    }}
                    title="重命名话题"
                    aria-label="重命名话题"
                    data-testid={`session-rename-btn-${s.session_id}`}
                    style={rowIconBtnStyle}
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setPendingDelete(s);
                    }}
                    title={isDefault ? "清空默认话题" : "删除该会话"}
                    aria-label="删除该会话"
                    data-testid={`session-delete-btn-${s.session_id}`}
                    style={rowIconBtnStyle}
                  >
                    ×
                  </button>
                </>
              )}
            </div>
          );
        })}
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title={pendingDelete.session_id === DEFAULT_SID ? "清空默认话题" : "删除会话"}
          message={
            <>
              确定要{pendingDelete.session_id === DEFAULT_SID ? "清空" : "删除"}会话{" "}
              <strong>{topicDisplayLabel({
                isDefault: pendingDelete.session_id === DEFAULT_SID,
                title: pendingDelete.title,
                preview: pendingDelete.preview,
                session_id: pendingDelete.session_id,
              })}</strong>
              吗？
              <br />
              <span style={{ color: dark.textMuted, userSelect: "text" }}>
                {pendingDelete.session_id}
              </span>
            </>
          }
          confirm_label={pendingDelete.session_id === DEFAULT_SID ? "清空" : "删除"}
          cancel_label="取消"
          variant="danger"
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => {
            const sid = pendingDelete.session_id;
            setPendingDelete(null);
            deleteSession(sid);
          }}
        />
      )}
    </div>
  );
}

// 行内小图标按钮（✎ / ×）— 低对比、悬停走内联事件避免样式表依赖。
const rowIconBtnStyle: CSSProperties = {
  flexShrink: 0,
  width: 20,
  height: 20,
  borderRadius: tokens.radius.sm,
  border: "none",
  background: "transparent",
  color: dark.textMuted,
  cursor: "pointer",
  fontSize: tokens.text.base.size,
  lineHeight: "18px",
  padding: 0,
};

export default SessionList;
