// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S23 / WI-T2-B1 v2 — bottom input bar for the code panel.
 *
 * Multi-line textarea with Enter-to-send (Shift+Enter newline).
 *
 * v2 升级（plans/2026-05-25-companion-code-skill-upgrade/10-tool-layer-...）:
 *  - 输入 `/` → fetch /api/commands/help → 显示 filterable SlashDropdown
 *  - ↑/↓ 在 dropdown 移动；Tab/Enter 接受；ESC 关闭
 *  - 接受后显示 ArgHintBar 显参数 inline
 *  - 输入历史：空输入 + ↑ → 浏览 last /命令 (max 50)
 *  - 普通聊天 (不以 / 开头) 行为不变 — backward compatible
 *
 * Concurrency-limited via `chatLimiter` so 5 tiles all sending at once
 * won't smash the relay with parallel requests.
 */
import { useState, useCallback, useRef, useEffect } from "react";

import { useSessionsStore } from "../stores/sessionsStore";
import { BACKEND_PORT } from "../backendPort";
import { controlWS } from "./controlWs";
import { SlashDropdown, type SlashCommand } from "./SlashDropdown";
import { ArgHintBar, type ArgSchema } from "./ArgHintBar";
import { createClientTurnIdentity } from "../ws/clientTurnIdentity";
import { turnTextRejection } from "../primary/turnText";
import { Icon } from "../components/Icon";
import { INTERACTIVE_CLASS, dark, transition } from "../theme/components";
import { tokens } from "../theme/tokens";

// 输入历史 — module-scope，跨 InputBar 实例共享 (max 50 entries)
const _slashInputHistory: string[] = [];
const HISTORY_MAX = 50;

const ATTACHMENT_BLOCK_LIMIT = 8 * 1024 * 1024;
const ATTACHMENT_RUN_LIMIT = 16 * 1024 * 1024;

type TextAttachment = {
  id: string;
  name: string;
  mediaType: string;
  size: number;
  data: string;
};

function isSupportedTextFile(file: File): boolean {
  const extension = file.name.toLowerCase().split(".").pop() ?? "";
  return (
    file.type.startsWith("text/") ||
    ["txt", "md", "csv", "json", "yaml", "yml", "log"].includes(extension) ||
    ["application/json", "application/yaml", "application/x-yaml"].includes(file.type)
  );
}

function readTextFile(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("读取附件失败"));
    reader.readAsText(file);
  });
}

// 输入框与唯一主操作按钮严格等高。

function pushHistory(entry: string) {
  if (!entry.startsWith("/")) return;
  if (_slashInputHistory[_slashInputHistory.length - 1] === entry) return;
  _slashInputHistory.push(entry);
  while (_slashInputHistory.length > HISTORY_MAX) _slashInputHistory.shift();
}

// commands 缓存 (页面级；输入一个新的 "/" 时强制从本机全局 catalog 刷新)
// 2026-06-26 修复"输入 /g 无候选"：原实现把**失败/空**结果也缓存进
// _cachedCommands/_cachedCommandsPromise → boot 早期后端没起来 fetch 空一次后，
// 模块级缓存永久为空、再也不重试。改为**只缓存非空成功结果**，空/失败时返回空但
// 不污染缓存 → 下次（首次输入 "/"）可重拉。
let _cachedCommands: SlashCommand[] | null = null;

async function fetchCommands(forceRefresh = false): Promise<SlashCommand[]> {
  if (
    !forceRefresh &&
    _cachedCommands !== null &&
    _cachedCommands.length > 0
  ) return _cachedCommands;
  try {
    // WI-T2-B fix v2.1: backend 绝对 URL，复用 backendPort.ts 单一源.
    // 相对路径在 Tauri WebView2 (tauri://) 或 vite dev 跨 5473→8400 都失效；
    // 必须显式 http://127.0.0.1:${BACKEND_PORT}/api/... 走 CORS.
    const resp = await fetch(
      `http://127.0.0.1:${BACKEND_PORT}/api/commands/help`,
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    const out: SlashCommand[] = Array.isArray(data.commands) ? data.commands : [];
    if (out.length > 0) _cachedCommands = out;
    return out;
  } catch {
    return [];
  }
}

function filterCommands(all: SlashCommand[], q: string): SlashCommand[] {
  if (!q) return all;
  const lower = q.toLowerCase();
  // 排序：prefix-match 优先，substring-match 次之
  const prefix = all.filter((c) => c.name.toLowerCase().startsWith(lower));
  const substr = all.filter(
    (c) =>
      !c.name.toLowerCase().startsWith(lower) &&
      c.name.toLowerCase().includes(lower),
  );
  return [...prefix, ...substr];
}

export interface PrimaryComposer {
  submit(text: string, attachments: Record<string, unknown>[]): Promise<void>;
  stop?: () => void;
  status?: string;
}

export function InputBar({
  placeholder,
  sessionId,
  disabled = false,
  primary,
}: {
  placeholder?: string;
  sessionId?: string;
  disabled?: boolean;
  primary?: PrimaryComposer;
} = {}) {
  const submittingRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const inputDisabled = disabled || submitting;
  const isPrimary = Boolean(primary);
  const [text, set_text] = useState("");
  const [attachments, setAttachments] = useState<TextAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const suppressCompositionCommitEnterRef = useRef(false);
  const compositionReleaseTimerRef = useRef<number | null>(null);

  // v2 slash state
  const [allCommands, setAllCommands] = useState<SlashCommand[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [argHintCmd, setArgHintCmd] = useState<SlashCommand | null>(null);
  const [historyIdx, setHistoryIdx] = useState<number | null>(null);

  // Incident G: an over-long draft used to be accepted here and dropped by the
  // Host. Say so while it is still being written, not after it disappears.
  const oversizeDraft = isPrimary ? turnTextRejection(text.trim()) : "";
  const composerError = attachmentError || oversizeDraft;

  const active_sid = useSessionsStore((s) => s.active_sid);
  const sid = sessionId ?? active_sid;
  const session = useSessionsStore((s) => s.sessions[sessionId ?? s.active_sid]);
  const selectedProjection = session?.selected_run_id
    ? session.run_projections?.[session.selected_run_id]
    : undefined;
  const inflight = primary ? Boolean(primary.stop) :
    !!session?.inflight ||
    selectedProjection?.status === "starting" ||
    selectedProjection?.status === "running" ||
    selectedProjection?.status === "waiting";

  // Commands are user-global and installs can complete while this view is
  // mounted. Fetch when the active Session changes; opening slash autocomplete
  // refreshes again so a newly installed global Skill is visible immediately.
  useEffect(() => {
    if (isPrimary) return;
    let current = true;
    fetchCommands().then((commands) => {
      if (current) setAllCommands(commands);
    }).catch(() => {
      if (current) setAllCommands([]);
    });
    return () => { current = false; };
  }, [sid, isPrimary]);

  useEffect(
    () => () => {
      if (compositionReleaseTimerRef.current !== null) {
        window.clearTimeout(compositionReleaseTimerRef.current);
      }
    },
    [],
  );

  // Auto-grow textarea。空草稿时清掉内联高度，交给 minHeight（36）——
  // 首帧布局未稳时读到的 scrollHeight 会把空输入框撑成多行。
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    if (!text) {
      el.style.height = "";
      return;
    }
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 148) + "px";
  }, [text]);

  // 计算当前 filter + candidates
  const candidates: SlashCommand[] = (() => {
    if (!dropdownOpen) return [];
    if (!text.startsWith("/")) return [];
    const q = text.slice(1).split(/\s+/)[0] ?? "";
    return filterCommands(allCommands, q);
  })();

  // 计算 current arg index (空格数)
  const currentArgIndex = (() => {
    if (!argHintCmd) return 0;
    // text 形如 "/cmd arg1 arg2 ..."
    const parts = text.split(/\s+/);
    return Math.max(0, parts.length - 2);
  })();

  const acceptCandidate = useCallback(
    (idx: number) => {
      const cmd = candidates[idx];
      if (!cmd) return;
      set_text(`/${cmd.name} `);
      setDropdownOpen(false);
      setSelectedIdx(0);
      const hasArgs = cmd.args_schema && cmd.args_schema.length > 0;
      setArgHintCmd(hasArgs ? cmd : null);
      // 重新 focus 让 textarea 接收后续键入
      taRef.current?.focus();
    },
    [candidates],
  );

  const send = useCallback(async () => {
    if (disabled || submittingRef.current) return;
    const t = text.trim();
    if (!t) return;
    const attachmentBlocks = attachments.map((attachment) => ({
      type: "input_text",
      data: attachment.data,
      name: attachment.name,
      media_type: attachment.mediaType,
      size: attachment.size,
    }));
    if (primary) {
      if (t.startsWith("/")) {
        setAttachmentError("主对话命令接线尚未就绪；草稿已保留。");
        return;
      }
      const oversize = turnTextRejection(t);
      if (oversize) {
        setAttachmentError(oversize);
        return;
      }
      submittingRef.current = true;
      setSubmitting(true);
      setAttachmentError("");
      try {
        await primary.submit(t, attachmentBlocks);
        // Only a durable enqueue ACK may consume this exact submitted draft.
        set_text((current) => current === text ? "" : current);
        setAttachments((current) => current.filter((item) => !attachments.includes(item)));
        setHistoryIdx(null);
        setDropdownOpen(false);
        setArgHintCmd(null);
      } catch (error) {
        setAttachmentError(error instanceof Error ? error.message : "发送失败，草稿已保留。");
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
      }
      return;
    }
    if (!sid) {
      // 空态直发（保留会话 `default` 移除后的唯一入口）：没有当前会话时，
      // 让后端在同一条 chat_v2 里新建会话再投递 —— `new_session: true` 走
      // task_session_manager 派一个 uuid sid，随后回推 session_switched
      // （SessionList 据此切过去）+ chat_v2_user_echo（用户气泡落进新会话）。
      // 所以这里**不做**本地乐观 push：没有 sid 可写，且会与回声重复。
      // 发送失败时保留输入框内容，避免用户白打一段字。
      const identity = createClientTurnIdentity();
      const sent = controlWS.send({
        type: "chat_v2",
        payload: {
          text: t,
          session_id: "",
          new_session: true,
          request_id: identity.request_id,
          turn_id: identity.turn_id,
          ...(attachmentBlocks.length > 0
            ? { attachments: attachmentBlocks }
            : {}),
        },
      });
      if (!sent) return;
      pushHistory(t);
      setHistoryIdx(null);
      set_text("");
      setAttachments([]);
      setAttachmentError("");
      setDropdownOpen(false);
      setArgHintCmd(null);
      return;
    }
    pushHistory(t);
    setHistoryIdx(null);
    set_text("");
    setDropdownOpen(false);
    setArgHintCmd(null);
    const identity = createClientTurnIdentity();
    const currentSession = useSessionsStore.getState().sessions[sid];
    const activeSelectedProjection = (() => {
      const current = currentSession;
      const selectedRunId = current?.selected_run_id;
      if (!selectedRunId) return undefined;
      const projection = current?.run_projections?.[selectedRunId];
      return projection &&
        (projection.status === "starting" ||
          projection.status === "waiting" ||
          projection.status === "running")
        ? projection
        : undefined;
    })();
    const selectedProjection =
      activeSelectedProjection &&
      typeof activeSelectedProjection.conversation_boundary_version === "number"
        ? activeSelectedProjection
        : undefined;
    const pendingParentRequestId =
      !activeSelectedProjection
        ? currentSession?.pending_root_request_id
        : undefined;
    const deferredProjection =
      !selectedProjection && activeSelectedProjection?.status === "starting"
        ? activeSelectedProjection
        : undefined;
    const shouldDefer =
      !t.startsWith("/") &&
      attachmentBlocks.length === 0 &&
      Boolean(deferredProjection || pendingParentRequestId);
    useSessionsStore.getState().push_message(sid, {
      role: "user",
      text: t,
      request_id: identity.request_id,
      turn_id: identity.turn_id,
      run_id: (selectedProjection || deferredProjection)?.run_id,
      task_scope_id: (selectedProjection || deferredProjection)?.task_scope_id,
      conversation_boundary_ref:
        (selectedProjection || deferredProjection)?.conversation_boundary_ref,
      continuation_status:
        selectedProjection || shouldDefer ? "waiting" : undefined,
      deferred_send: shouldDefer || undefined,
      deferred_parent_request_id:
        pendingParentRequestId || undefined,
    });
    if (t.startsWith("/")) {
      const m = t.slice(1).match(/^(\S+)\s*(.*)$/);
      const cmd = m ? m[1] : "";
      const args = m ? (m[2] ?? "") : "";
      useSessionsStore.getState().upsert(sid, {
        status: "thinking",
        inflight: true,
      });
      controlWS.send({
        type: "slash_command",
        payload: {
          command: cmd,
          args,
          session_id: sid,
          request_id: identity.request_id,
          turn_id: identity.turn_id,
        },
      });
      return;
    }
    useSessionsStore.getState().upsert(sid, {
      status: "thinking",
    });
    if (shouldDefer) {
      return;
    }
    const startsNewRoot = !selectedProjection;
    if (startsNewRoot) {
      useSessionsStore.getState().upsert(sid, {
        pending_root_request_id: identity.request_id,
        pending_root_turn_id: identity.turn_id,
      });
    }
    const sent = controlWS.send({
      type: "chat_v2",
      payload: {
        text: t,
        session_id: sid,
        request_id: identity.request_id,
        turn_id: identity.turn_id,
        ...(attachmentBlocks.length > 0
          ? { attachments: attachmentBlocks }
          : {}),
        ...(selectedProjection
          ? {
              target_root_run_id: selectedProjection.run_id,
              task_scope_id: selectedProjection.task_scope_id,
              conversation_boundary_version:
                selectedProjection.conversation_boundary_version,
            }
          : {}),
      },
    });
    if (!sent) {
      if (startsNewRoot) {
        useSessionsStore.getState().upsert(sid, {
          pending_root_request_id: undefined,
          pending_root_turn_id: undefined,
        });
      }
      if (selectedProjection) {
        useSessionsStore.getState().set_continuation_status(
          sid,
          identity.request_id,
          "failed",
          "控制通道未连接",
        );
      }
      useSessionsStore.getState().push_message(sid, {
        role: "error",
        text: "消息发送失败：控制通道未连接，请稍后重试。",
      });
      useSessionsStore.getState().upsert(sid, {
        status: "error",
        inflight: false,
      });
    } else {
      setAttachments([]);
      setAttachmentError("");
    }
  }, [text, sid, disabled, attachments, primary]);

  const onAttachmentChange = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
      if (inputDisabled || files.length === 0) return;
      const unsupported = files.find((file) => !isSupportedTextFile(file));
      if (unsupported) {
        setAttachmentError(`不支持 ${unsupported.name}：请选择文本文件`);
        return;
      }
      const oversized = files.find((file) => file.size > ATTACHMENT_BLOCK_LIMIT);
      if (oversized) {
        setAttachmentError(`${oversized.name} 超过单文件 8 MiB 限制`);
        return;
      }
      const currentSize = attachments.reduce((sum, item) => sum + item.size, 0);
      const addedSize = files.reduce((sum, file) => sum + file.size, 0);
      if (currentSize + addedSize > ATTACHMENT_RUN_LIMIT) {
        setAttachmentError("附件总大小超过每轮 16 MiB 限制");
        return;
      }
      try {
        const loaded = await Promise.all(
          files.map(async (file, index): Promise<TextAttachment> => ({
            id: `${file.name}:${file.lastModified}:${file.size}:${index}`,
            name: file.name,
            mediaType: file.type || "text/plain",
            size: file.size,
            data: await readTextFile(file),
          })),
        );
        setAttachments((current) => [...current, ...loaded]);
        setAttachmentError("");
      } catch {
        setAttachmentError("读取附件失败，请重新选择");
      }
    },
    [attachments, inputDisabled],
  );

  const stop = useCallback(() => {
    if (primary) { primary.stop?.(); return; }
    if (!sid) return;
    controlWS.send({
      type: "chat_v2_interrupt",
      payload: {
        session_id: sid,
        run_id:
          useSessionsStore.getState().sessions[sid]?.selected_run_id ??
          useSessionsStore.getState().sessions[sid]?.active_run_id,
      },
    });
  }, [sid, primary]);

  const onChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    set_text(v);
    setHistoryIdx(null);
    // 状态机：开/关 dropdown + arg hint
    if (!primary && v.startsWith("/")) {
      // 每次开始一个新的 slash 输入都刷新本机全局 catalog。这样 Skill 在
      // 页面挂载后安装、或切换到显式目录的新 Session 时不会沿用旧缓存。
      if (v === "/" || allCommands.length === 0) {
        fetchCommands(v === "/").then((cs) => {
          if (cs.length) setAllCommands(cs);
        }).catch(() => {});
      }
      const firstWord = v.slice(1).split(/\s+/)[0] ?? "";
      const hasSpace = v.length > firstWord.length + 1;
      if (!hasSpace) {
        // 还在打命令名 → 显 dropdown
        setDropdownOpen(true);
        setArgHintCmd(null);
        setSelectedIdx(0);
      } else {
        // 已输空格 → 关 dropdown，看是否需 arg hint
        setDropdownOpen(false);
        const cmdMatch = allCommands.find(
          (c) => c.name.toLowerCase() === firstWord.toLowerCase(),
        );
        if (cmdMatch && cmdMatch.args_schema && cmdMatch.args_schema.length > 0) {
          setArgHintCmd(cmdMatch);
        } else {
          setArgHintCmd(null);
        }
      }
    } else {
      setDropdownOpen(false);
      setArgHintCmd(null);
    }
  };

  const onCompositionStart = () => {
    if (compositionReleaseTimerRef.current !== null) {
      window.clearTimeout(compositionReleaseTimerRef.current);
      compositionReleaseTimerRef.current = null;
    }
    isComposingRef.current = true;
    suppressCompositionCommitEnterRef.current = false;
  };

  const onCompositionEnd = () => {
    isComposingRef.current = false;
    // macOS WebKit may emit compositionend immediately before the Enter
    // keydown used to accept the IME candidate. Keep a one-event latch so that
    // commit Enter cannot fall through to chat submission. Release it at the
    // end of the event turn so a later, intentional Enter still sends.
    suppressCompositionCommitEnterRef.current = true;
    compositionReleaseTimerRef.current = window.setTimeout(() => {
      suppressCompositionCommitEnterRef.current = false;
      compositionReleaseTimerRef.current = null;
    }, 0);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const nativeKeyCode = (e.nativeEvent as KeyboardEvent).keyCode;
    // Chromium reports isComposing; macOS WebKit can instead expose the IME
    // sentinel keyCode 229. The ref covers engines that omit both flags while
    // composition is still active.
    if (
      e.nativeEvent.isComposing ||
      isComposingRef.current ||
      nativeKeyCode === 229
    ) {
      return;
    }
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      suppressCompositionCommitEnterRef.current
    ) {
      e.preventDefault();
      suppressCompositionCommitEnterRef.current = false;
      if (compositionReleaseTimerRef.current !== null) {
        window.clearTimeout(compositionReleaseTimerRef.current);
        compositionReleaseTimerRef.current = null;
      }
      return;
    }
    // dropdown 打开时拦截 ↑↓ Tab Enter ESC
    if (dropdownOpen && candidates.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((i) => (i + 1) % candidates.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx((i) => (i - 1 + candidates.length) % candidates.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        acceptCandidate(selectedIdx);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDropdownOpen(false);
        return;
      }
    }

    // 历史浏览 — 空输入 + ↑ → 上一条 history
    if (!primary && !dropdownOpen && e.key === "ArrowUp" && _slashInputHistory.length > 0) {
      const ta = e.currentTarget;
      const atTop = ta.selectionStart === 0 && ta.selectionEnd === 0;
      // 仅在空输入 或 光标在最顶且无 selection 时启 history
      if (text === "" || atTop) {
        e.preventDefault();
        const nextIdx =
          historyIdx === null
            ? _slashInputHistory.length - 1
            : Math.max(0, historyIdx - 1);
        set_text(_slashInputHistory[nextIdx]);
        setHistoryIdx(nextIdx);
        return;
      }
    }
    if (!primary && !dropdownOpen && e.key === "ArrowDown" && historyIdx !== null) {
      e.preventDefault();
      const nextIdx = historyIdx + 1;
      if (nextIdx >= _slashInputHistory.length) {
        set_text("");
        setHistoryIdx(null);
      } else {
        set_text(_slashInputHistory[nextIdx]);
        setHistoryIdx(nextIdx);
      }
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      // 有文字时始终提交；若选中了运行中的 Run，后端会把消息耐久化加入
      // 该 Run 的 FIFO，并在 Driver 释放安全边界后绑定。空文字时才停止。
      if (text.trim()) {
        void send();
      } else if (inflight) {
        stop();
      }
    }
  };

  const continuationTarget =
    selectedProjection &&
    (selectedProjection.status === "waiting" ||
      selectedProjection.status === "running") &&
    typeof selectedProjection.conversation_boundary_version === "number"
      ? selectedProjection
      : undefined;
  const status = (() => {
    if (primary) return submitting ? "等待入队确认" : primary.status ?? "等待主对话就绪";
    if (!selectedProjection) return session?.status ?? "idle";
    if (
      selectedProjection.inflight ||
      selectedProjection.status === "starting" ||
      selectedProjection.status === "running"
    ) {
      return session?.status === "thinking" ? "thinking" : "running";
    }
    if (selectedProjection.status === "waiting") {
      return session?.status === "permission" ? "permission" : "thinking";
    }
    // Failure remains visible in the Harness timeline, but it is no longer
    // the current execution state. Keep the composer ready for a retry.
    if (
      selectedProjection.status === "failed" ||
      selectedProjection.status === "cancelled"
    ) {
      return "idle";
    }
    return "idle";
  })();
  return (
    <div
      // 悬浮输入卡片：一层 hairline + 12 圆角，聚焦时描边转强调色。
      // 发送按钮内嵌在卡片里，不再是并排的第三个方块。
      style={{
        position: "relative", // for absolute SlashDropdown
        background: dark.card,
        border: `1px solid ${dark.borderStrong}`,
        borderRadius: tokens.radius.lg,
        padding: tokens.space.sm,
        display: "flex",
        flexDirection: "column",
        gap: tokens.space.sm,
        transition,
      }}
      onFocusCapture={(event) => {
        event.currentTarget.style.borderColor = tokens.color.accent.border;
      }}
      onBlurCapture={(event) => {
        event.currentTarget.style.borderColor = dark.borderStrong;
      }}
    >
      {argHintCmd && (
        <ArgHintBar
          commandName={argHintCmd.name}
          argSchema={(argHintCmd.args_schema ?? []) as ArgSchema[]}
          currentArgIndex={currentArgIndex}
        />
      )}
      {attachments.length > 0 && (
        <div
          data-testid="attachment-list"
          style={{ display: "flex", flexWrap: "wrap", gap: tokens.space.xs }}
        >
          {attachments.map((attachment) => (
            <span
              key={attachment.id}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: tokens.space.xs + 2,
                padding: "3px 8px",
                borderRadius: tokens.radius.pill,
                background: "transparent",
                border: `1px solid ${dark.borderStrong}`,
                color: dark.textMuted,
                fontSize: tokens.text.sm.size,
              }}
            >
              {attachment.name}
              <button
                type="button"
                aria-label={`移除附件 ${attachment.name}`}
                disabled={inputDisabled}
                className={INTERACTIVE_CLASS}
                onClick={() =>
                  setAttachments((current) =>
                    current.filter((item) => item.id !== attachment.id),
                  )
                }
                style={{
                  border: 0,
                  padding: 0,
                  background: "transparent",
                  color: dark.textFaint,
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      {composerError && (
        <div role="alert" data-testid="composer-error" style={{ color: dark.danger, fontSize: tokens.text.sm.size }}>
          {composerError}
        </div>
      )}
      {/* 输入行：附件 · 文本域 · 发送，三者内嵌在同一张卡片里。 */}
      <div style={{ display: "flex", gap: tokens.space.xs, alignItems: "flex-end", position: "relative" }}>
        <SlashDropdown
          candidates={candidates}
          selectedIdx={selectedIdx}
          onAccept={acceptCandidate}
        />
        {/* 2026-09-25：主对话的附件发送尚未接通（发送必被拒），按钮先不显示，免得点了没反应。 */}
        {!primary && <label
          aria-label="附加文本文件"
          title="附加文本文件"
          className={INTERACTIVE_CLASS}
          style={{
            height: tokens.controlHeight,
            minWidth: tokens.controlHeight,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid transparent",
            borderRadius: tokens.radius.md,
            background: "transparent",
            color: disabled ? dark.textFaint : dark.textMuted,
            cursor: disabled ? "not-allowed" : "pointer",
            boxSizing: "border-box",
            flexShrink: 0,
            transition,
          }}
        >
          <Icon name="folder" size={16} />
          <input
            ref={fileInputRef}
            data-testid="text-attachment-input"
            type="file"
            multiple
            accept=".txt,.md,.csv,.json,.yaml,.yml,.log,text/plain,text/markdown,text/csv,application/json,application/yaml"
            disabled={inputDisabled}
            onChange={(event) => void onAttachmentChange(event)}
            style={{ display: "none" }}
          />
        </label>}
        <textarea
          ref={taRef}
          value={text}
          onChange={onChange}
          onCompositionStart={onCompositionStart}
          onCompositionEnd={onCompositionEnd}
          onKeyDown={onKeyDown}
          disabled={inputDisabled}
          placeholder={
            disabled && placeholder
              ? placeholder
              : continuationTarget
              ? "补充当前任务…"
              : placeholder ??
                (session?.project_root
                  ? `跟 LLM 说点什么 — 当前项目: ${session.project_name}（输 / 命令）`
                  : "输入消息开始一个任务... (输 / 弹命令补全)")
          }
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            background: "transparent",
            color: dark.text,
            border: 0,
            outline: "none",
            borderRadius: 0,
            padding: `${tokens.space.sm}px ${tokens.space.xs}px`,
            fontSize: tokens.text.md.size,
            lineHeight: tokens.text.md.lh,
            fontFamily: tokens.font.ui,
            minHeight: tokens.controlHeight,
            maxHeight: 148,
            boxSizing: "border-box",
          }}
        />
        <button
          type="button"
          // 与 Enter 一致：有文字发送/续接；只有空文字 + inflight 才停止。
          onClick={() =>
            text.trim() ? void send() : inflight ? stop() : undefined
          }
          disabled={inputDisabled || (!inflight && !text.trim())}
          className={`${INTERACTIVE_CLASS}${text.trim() ? " sh-accent-fill" : ""}`}
          style={{
            height: tokens.controlHeight,
            background: text.trim()
              ? tokens.color.accent.bg
              : "transparent",
            color: text.trim()
              ? tokens.color.accent.on
              : inflight
                ? dark.danger
                : dark.textFaint,
            border: text.trim() ? "1px solid transparent" : `1px solid ${dark.borderStrong}`,
            borderRadius: tokens.radius.md,
            padding: `0 ${tokens.space.lg}px`,
            fontSize: tokens.text.base.size,
            fontWeight: tokens.weight.semibold,
            cursor: inflight || text.trim() ? "pointer" : "not-allowed",
            flexShrink: 0,
            boxSizing: "border-box",
            transition,
          }}
        >
          {text.trim() ? "发送" : inflight ? "■ 停止" : "发送"}
        </button>
      </div>
      {/* 第二排：低调的单行 meta（12 / 次级色）。 */}
      <div style={{ display: "flex", alignItems: "center", gap: tokens.space.md, padding: `0 ${tokens.space.xs}px` }}>
        {continuationTarget && (
          <span
            data-testid="continuation-target"
            style={{
              color: tokens.color.accent.fg,
              fontSize: tokens.text.sm.size,
              whiteSpace: "nowrap",
            }}
          >
            ↳ 发送到当前任务 · 将在安全边界读取
          </span>
        )}
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: tokens.space.md,
            fontSize: tokens.text.sm.size,
            fontVariantNumeric: tokens.font.numeric,
            color: dark.textFaint,
            minWidth: 0,
          }}
        >
          <StatusPill status={status} />
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {primary ? "Enter 发送 · Shift+Enter 换行" : "Enter 发送 · Shift+Enter 换行 · 文本附件 · / 命令"}
          </span>
        </div>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  // 状态色只用于状态，且不做填充色块 —— 只染一个 6px 圆点。
  const map: Record<string, { label: string; color: string }> = {
    idle: { label: "空闲", color: dark.success },
    thinking: { label: "思考中", color: dark.warning },
    running: { label: "工具执行中", color: dark.info },
    permission: { label: "等待授权", color: dark.warning },
    error: { label: "错误", color: dark.danger },
  };
  const m = map[status] ?? { label: status, color: dark.textFaint };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: dark.textMuted, whiteSpace: "nowrap" }}>
      <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: m.color, flexShrink: 0 }} />
      {m.label}
    </span>
  );
}

// Exports for test
export const _testing = {
  pushHistory,
  getHistory: () => [..._slashInputHistory],
  clearHistory: () => {
    _slashInputHistory.length = 0;
  },
  resetCache: () => {
    _cachedCommands = null;
  },
  fetchCommands,
  filterCommands,
  HISTORY_MAX,
};
