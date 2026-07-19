// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 2026-05-19 — slim message panel as its OWN Tauri window, docked to
 * the pet's left. Independent always-on-top transparent window so the
 * pet window stays exactly pet-sized & fully transparent (zero dead
 * click area).
 *
 * Parity with the pet's main thread (user requests #3/#5/#6):
 *  · full MessageStreamPanel — 全部 / 对话 / ⚠ 警告 / 🚨 错误 tabs,
 *    fed by THIS window's own store ("default" companion session +
 *    supervisor inbox), same derivation as App.tsx.
 *  · InputBar wired to the default companion session (same backend
 *    chat_v2 path the pet's main input uses).
 *  · model + params switcher (the Cursor-style ChangeModelModal) for
 *    the current session, exactly like Code mode.
 *  · header is a drag region; ⛶ maximizes / restores the window.
 */
import { useMemo, useState, useEffect, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";

import { Icon } from "../components/Icon";
import { ContextRing } from "../components/ContextRing";
import { ContextBreakdownModal } from "../components/ContextBreakdownModal";

import {
  useSessionsStore,
  collect_inbox,
  type InboxItem,
  type WorkflowV5ControlAction,
} from "../stores/sessionsStore";
import { forPet } from "../petText";
import {
  MessageStreamPanel,
  type ChatStreamMessage,
  type StreamFilter,
} from "../components/MessageStreamPanel";
import { InputBar } from "../code-panel/InputBar";
import { ChangeModelModal } from "../code-panel/ChangeModelModal";
import { ConfirmDialog } from "../code-panel/ConfirmDialog";
import {
  useCodeModelsStore,
  contextWindowForModel,
  formatContextWindow,
  effectiveModelId,
} from "../code-panel/codeModelsStore";
import { codePanelWS } from "../code-panel/ws";
import {
  MAX_TITLE_LEN,
  normalizeTopicTitle,
  topicDisplayLabel,
} from "./topicTitle";
import { useAudioChannel } from "../hooks/useAudioChannel";
import { BACKEND_PORT } from "../backendPort";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { useAudioPlayer } from "../hooks/useAudioPlayer";
import { shouldHideToolTrace } from "./messageVisibility";

const DEFAULT_SID = "default"; // the pet's companion main thread

type SessionEntry = {
  session_id: string;
  turn_count: number;
  last_message_at: number;
  preview: string;
  /** User-set custom title (empty = unnamed → fall back to preview). */
  title?: string;
};

type WorkflowRetryDeferred = {
  promise: Promise<{ run_id: string }>;
  resolve: (value: { run_id: string }) => void;
  reject: (reason: Error) => void;
};

function workflowRetryError(message: string, code: string, definitive: boolean): Error {
  return Object.assign(new Error(message), { code, definitive });
}

export function MessagePanelRoot() {
  const [activeSid, setActiveSid] = useState(DEFAULT_SID);
  const [filter, setFilter] = useState<StreamFilter>("all");
  // 历史会话下拉（选择 / 删除之前的会话）。
  const [sessionList, setSessionList] = useState<SessionEntry[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  // 「重命名话题」内联编辑：一次只编辑一行。
  const [editingSid, setEditingSid] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [pendingDelete, setPendingDelete] = useState<SessionEntry | null>(null);
  // Enter/Esc 会把 editingSid 置空 → input 卸载触发 onBlur；用这个标记让那次
  // 善后 blur 不要再二次提交。startRename 时清零，避免污染下一次编辑。
  const skipBlurRef = useRef(false);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const [showModelModal, setShowModelModal] = useState(false);
  // 2026-05-31 restore — context breakdown modal state + snapshot subscriber.
  const [contextModalOpen, setContextModalOpen] = useState(false);
  const contextUsage = useSessionsStore((s) => s.sessions[activeSid]?.context_usage ?? null);
  const workflowRetryDeferreds = useRef(new Map<string, WorkflowRetryDeferred>());

  const sessions = useSessionsStore((s) => s.sessions);
  const storeActiveSid = useSessionsStore((s) => s.active_sid);
  const messages = useSessionsStore((s) => s.sessions[activeSid]?.messages ?? []);
  const preferred_model = useSessionsStore(
    (s) => s.sessions[activeSid]?.preferred_model ?? null,
  );
  const model_params = useSessionsStore(
    (s) => s.sessions[activeSid]?.model_params ?? null,
  );
  // 模型按钮显示「模型-上下文长度(K/M)」。未固定 preferred_model 时,显示
  // 生效的 provider 默认模型(后端经 code_models_list_response 下发),让用户
  // 看到「当前真正在用的模型」而非「默认模型」占位词。
  const modelCatalog = useCodeModelsStore((s) => s.models);
  const default_model = useCodeModelsStore((s) => s.default_model);
  const eff_model = effectiveModelId(preferred_model, default_model);
  const is_following_default = !((preferred_model ?? "").trim());
  const ctx_window = contextWindowForModel(eff_model, modelCatalog);
  const ctx_label = formatContextWindow(ctx_window);

  // ── Voice pipeline (parity with the pet's main mic) ──────────────
  // The panel is its own window, so it runs its own audio channel +
  // recorder + player. It connects with the default session_id, so the
  // backend persists voice turns to the SAME "default" session the
  // pet main + panel text use. transcript/TTS come back over THIS
  // window's audio_ws → we echo transcripts into the store so the
  // voice exchange shows in the panel, consistent with text.
  const [secret, setSecret] = useState("");
  useEffect(() => {
    let alive = true;
    let tries = 0;
    const poll = async () => {
      try {
        const s = await invoke<string>("get_shared_secret");
        if (alive && s) {
          setSecret(s);
          return;
        }
      } catch {
        /* backend not up yet */
      }
      if (alive && tries++ < 40) setTimeout(poll, 500);
    };
    void poll();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const deferreds = workflowRetryDeferreds.current;
    const off = codePanelWS.on_message((raw: unknown) => {
      const msg = raw as {
        request_id?: unknown;
        type?: unknown;
        ok?: unknown;
        request_type?: unknown;
        payload?: { run_id?: unknown };
        error?: { message?: unknown; code?: unknown };
      };
      const requestId = typeof msg?.request_id === "string" ? msg.request_id : "";
      const deferred = deferreds.get(requestId);
      if (!deferred) return;
      if (msg?.type === "workflow_run_retry_from_start_response" && msg?.ok === true) {
        const runId = typeof msg?.payload?.run_id === "string" ? msg.payload.run_id : "";
        deferreds.delete(requestId);
        if (runId) deferred.resolve({ run_id: runId });
        else deferred.reject(workflowRetryError("重试响应缺少新 run", "invalid_response", true));
      } else if (
        msg?.type === "workflow_ipc_error" &&
        msg?.request_type === "workflow_run_retry_from_start"
      ) {
        deferreds.delete(requestId);
        deferred.reject(workflowRetryError(
          String(msg?.error?.message || "重试请求被拒绝"),
          String(msg?.error?.code || "workflow_retry_rejected"),
          true,
        ));
      }
    });
    return () => {
      off();
      for (const deferred of deferreds.values()) {
        deferred.reject(workflowRetryError("窗口已关闭", "window_unmounted", false));
      }
      deferreds.clear();
    };
  }, []);

  const retryWorkflow = useCallback((
    runId: string,
    actionId: Exclude<WorkflowV5ControlAction, "none">,
    retryKey: string,
  ): Promise<{ run_id: string; accepted?: boolean }> => {
    if (codePanelWS.state() !== "connected") {
      return Promise.reject(workflowRetryError("连接后重试", "workflow_command_not_sent", false));
    }
    const request = <T extends Record<string, unknown>>(
      type: string,
      requestId: string,
      payload: Record<string, unknown>,
      responseType: string,
    ): Promise<T> => new Promise((resolve, reject) => {
      let settled = false;
      const finish = () => {
        if (settled) return false;
        settled = true;
        off();
        window.clearTimeout(timer);
        return true;
      };
      const off = codePanelWS.on_message((raw: unknown) => {
        const message = raw as {
          type?: unknown; request_id?: unknown; request_type?: unknown;
          ok?: unknown; payload?: unknown; error?: { message?: unknown; code?: unknown };
        };
        if (message.request_id !== requestId) return;
        if (message.type === responseType && message.ok === true && message.payload && typeof message.payload === "object") {
          if (finish()) resolve(message.payload as T);
        } else if (message.type === "workflow_ipc_error" && message.request_type === type) {
          if (finish()) reject(workflowRetryError(
            String(message.error?.message || "操作被拒绝"),
            String(message.error?.code || "workflow_action_rejected"),
            true,
          ));
        }
      });
      const timer = window.setTimeout(() => {
        if (finish()) reject(workflowRetryError("响应较慢，请重新连接后确认状态", "workflow_action_timeout", false));
      }, 20_000);
      if (!codePanelWS.send_command({ type, request_id: requestId, payload })) {
        if (finish()) reject(workflowRetryError("连接后重试", "workflow_command_not_sent", false));
      }
    });
    return request<{ run_id: string; run_version?: number; run?: { run_version?: number } }>(
      "workflow_run_detail",
      `workflow-detail:${runId}:${retryKey}`,
      { run_id: runId },
      "workflow_run_detail_response",
    ).then((detail) => {
      const expectedVersion = detail.run_version ?? detail.run?.run_version;
      if (!Number.isInteger(expectedVersion) || (expectedVersion as number) < 0) {
        throw workflowRetryError("任务详情缺少版本号", "workflow_run_version_missing", true);
      }
      return request<{ run_id: string; accepted?: boolean }>(
        "workflow_run_action",
        `workflow-action:${runId}:${retryKey}`,
        {
          run_id: runId,
          action_id: actionId,
          idempotency_key: retryKey,
          expected_version: expectedVersion,
        },
        "workflow_run_action_response",
      );
    });
  }, []);

  const {
    state: audioState,
    lastMessage: audioMessage,
    sendAudio,
    getChannel,
  } = useAudioChannel(BACKEND_PORT, secret);
  const { isRecording, startRecording, stopRecording } =
    useAudioRecorder(sendAudio);
  const {
    isPlaying,
    reset: resetPlaybackBuffer,
    primeContext,
    bargeIn,
  } = useAudioPlayer(getChannel());

  useEffect(() => {
    if (storeActiveSid && storeActiveSid !== activeSid) {
      setActiveSid(storeActiveSid);
    }
  }, [storeActiveSid, activeSid]);

  useEffect(() => {
    return codePanelWS.on_message((msg: any) => {
      const p = msg?.payload || {};
      let nextSid = "";
      if (msg?.type === "session_switched" || msg?.type === "task_session_started") {
        nextSid = typeof p.new_sid === "string" ? p.new_sid : "";
      } else if (
        msg?.type === "chat_response" ||
        msg?.type === "chat_v2_final" ||
        msg?.type === "tool_call" ||
        msg?.type === "tool_result" ||
        msg?.type === "ppt_outline_proposed"
      ) {
        const payloadSid =
          typeof p.session_id === "string"
            ? p.session_id
            : typeof p.code_session_id === "string"
              ? p.code_session_id
              : "";
        if (payloadSid && payloadSid !== DEFAULT_SID && payloadSid !== "message-panel-main") {
          nextSid = payloadSid;
        }
      }
      if (!nextSid) return;
      const store = useSessionsStore.getState();
      store.ensure(nextSid);
      store.set_active(nextSid);
      setActiveSid(nextSid);
    });
  }, []);

  const switchToDefault = useCallback(() => {
    useSessionsStore.getState().ensure(DEFAULT_SID);
    useSessionsStore.getState().set_active(DEFAULT_SID);
    setActiveSid(DEFAULT_SID);
  }, []);

  // 历史会话下拉：拉清单 / 切会话 / 删会话。
  const loadSessions = useCallback(() => {
    codePanelWS.send({ type: "sessions_list" });
  }, []);

  const switchToSession = useCallback((sid: string) => {
    useSessionsStore.getState().ensure(sid);
    useSessionsStore.getState().set_active(sid);
    setActiveSid(sid);
    setPickerOpen(false);
  }, []);

  useEffect(() => {
    if (!activeSid) return;
    useSessionsStore.getState().ensure(activeSid);
    codePanelWS.send({
      type: "session_messages_load",
      payload: { session_id: activeSid, limit: 200 },
    });
    codePanelWS.send({
      type: "context_usage_request",
      payload: { session_id: activeSid },
    });
  }, [activeSid]);

  const deleteSession = useCallback(
    (sid: string) => {
      codePanelWS.send({ type: "session_delete", payload: { session_id: sid } });
      // 乐观移除 + 若删的是当前会话则切回 default。
      setSessionList((prev) => prev.filter((s) => s.session_id !== sid));
      if (sid === activeSid && sid !== DEFAULT_SID) {
        switchToDefault();
      }
    },
    [activeSid, switchToDefault],
  );

  const activeSession = useMemo<SessionEntry>(() => {
    const found = sessionList.find((s) => s.session_id === activeSid);
    if (found) return found;
    return {
      session_id: activeSid,
      turn_count: messages.length,
      last_message_at: 0,
      preview: "",
      title: "",
    };
  }, [activeSid, messages.length, sessionList]);

  const activeTitle = topicDisplayLabel({
    isDefault: activeSid === DEFAULT_SID,
    title: activeSession.title,
    preview: activeSession.preview,
    session_id: activeSid,
  });

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
      codePanelWS.send({
        type: "session_rename",
        payload: { session_id: sid, title: next },
      });
    },
    [draftTitle, sessionList],
  );

  // 关闭下拉时丢弃未完成的编辑态，避免下次打开残留。
  useEffect(() => {
    if (!pickerOpen && editingSid !== null) setEditingSid(null);
  }, [pickerOpen, editingSid]);

  // 进入编辑后聚焦 + 选中文本。
  useEffect(() => {
    if (editingSid !== null) {
      const el = renameInputRef.current;
      el?.focus();
      el?.select();
    }
  }, [editingSid]);

  // 监听后端 sessions_list_response / session_deleted / session_renamed；
  // 面板打开时拉一次清单。
  useEffect(() => {
    const off = codePanelWS.on_message((msg: any) => {
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

  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      stopRecording();
    } else {
      await primeContext(); // unlock AudioContext inside the gesture
      startRecording();
    }
  }, [isRecording, startRecording, stopRecording, primeContext]);

  // Mirror App.tsx's audio-message handling, but echo transcripts into
  // the shared store so MessageStreamPanel renders the voice turns.
  useEffect(() => {
    if (!audioMessage) return;
    switch (audioMessage.type) {
      case "vad_event":
        if (audioMessage.payload.status === "speech_start") {
          if (isPlaying) bargeIn();
          resetPlaybackBuffer();
        }
        break;
      case "transcript":
        useSessionsStore.getState().push_message(activeSid, {
          role: audioMessage.payload.role,
          text: audioMessage.payload.text,
        });
        break;
      case "tts_barge_in":
        bargeIn();
        break;
    }
  }, [audioMessage, isPlaying, resetPlaybackBuffer, bargeIn, activeSid]);

  // Companion-stream derivation (strip <think> via forPet; synth ts
  // since the store has none)。2026-06-12: 工具执行轨迹(tool_call/
  // tool_result)不再丢弃 —— 用户要求「我让它生成PPT 和 它生成完之间
  // 的工具调用」在主消息流全程可观测(此前只有桌宠小气泡显示)。
  // 「隐藏工具消息」开关: 开=只看对话(隐藏 🔧/✅ 工具轨迹行),
  // 关=全程可观测。localStorage 持久化,重开面板记住选择。
  const [hideTools, setHideTools] = useState<boolean>(
    () => {
      try {
        return localStorage.getItem("msgpanel.hideTools") === "1";
      } catch {
        return false;
      }
    },
  );
  const toggleHideTools = () => {
    setHideTools((v) => {
      const next = !v;
      try {
        localStorage.setItem("msgpanel.hideTools", next ? "1" : "0");
      } catch { /* 忽略 */ }
      return next;
    });
  };

  const chatMessages = useMemo(() => {
    const out: ChatStreamMessage[] = [];
    messages.forEach((m) => {
      const ts = m.ts;
      if (m.role === "user") {
        out.push({ role: "user", text: m.text ?? "", ts });
        return;
      }
      if (shouldHideToolTrace(m, hideTools)) {
        return;
      }
      if ((m.role as string) === "tool_call") {
        const args = m.tool_args
          ? JSON.stringify(m.tool_args).slice(0, 120)
          : "";
        out.push({
          role: "tool",
          text: `🔧 调用 ${m.tool_name || "(工具)"}${args ? ` ${args}${args.length >= 120 ? "…" : ""}` : ""}`,
          ts,
        });
        return;
      }
      if ((m.role as string) === "tool_result") {
        out.push({
          role: "tool",
          text: `${m.tool_ok === false ? "❌" : "✅"} ${m.tool_name || "(工具)"} 完成`,
          ts,
          toolName: m.tool_name,
          toolOk: m.tool_ok,
          toolResultRaw: m.tool_result,
        });
        return;
      }
      if (m.role === "ppt_outline") {
        out.push({ role: "ppt_outline", message: m, session_id: activeSid, ts });
        return;
      }
      if (m.role === "workflow_progress" || m.role === "workflow_stage") {
        out.push({ role: m.role, message: m, ts });
        return;
      }
      const clean = forPet(m.text);
      if (clean) out.push({ role: "assistant", text: clean, ts });
    });
    return out;
  }, [messages, hideTools, activeSid]);
  const warnings = useMemo<InboxItem[]>(
    () => collect_inbox(sessions, "yellow"),
    [sessions],
  );
  const errors = useMemo<InboxItem[]>(
    () => collect_inbox(sessions, "red"),
    [sessions],
  );

  const onChoice = (
    sid: string,
    alert_id: string,
    button_index: number,
    button_text: string,
  ) => {
    codePanelWS.send({
      type: "supervisor_user_choice",
      payload: { session_id: sid, alert_id, button_index, button_text },
    });
    useSessionsStore.getState().dismiss_alert(sid, alert_id);
  };

  // Drag the frameless window via startDragging() — the idiomatic Windows
  // move-loop API, robust to the cursor leaving the window, multi-monitor & DPI.
  //
  // 2026-06-26 修复"拖动不行"(回归)：原实现在 onMouseDown 里**异步** `import(
  // "@tauri-apps/api/window")` 再调 startDragging() —— 动态 import 的 promise 要到
  // 下一个 microtask 才 resolve，而 Windows 的 SC_MOVE 移动循环**必须在 mousedown
  // 同步帧内**发起才能接管拖动；晚一拍 → OS 不进入移动循环 → 拖不动。改为像
  // Live2DCanvas(FIX-R3) 一样**预加载** startDragging 到 ref，onMouseDown 里**同步**调用。
  const startDraggingRef = useRef<(() => Promise<unknown>) | null>(null);
  useEffect(() => {
    let alive = true;
    import("@tauri-apps/api/window")
      .then(({ getCurrentWindow }) => {
        if (!alive) return;
        const w = getCurrentWindow();
        startDraggingRef.current = () => w.startDragging();
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const startDrag = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    // 同步调用预加载好的 startDragging（晚一拍就拖不动，见上方注释）。
    startDraggingRef.current?.().catch((err) =>
      console.warn("[msg-panel] startDragging failed:", err),
    );
  };

  const toggleMaximize = async () => {
    try {
      const mod = await import("@tauri-apps/api/window");
      const w = mod.getCurrentWindow?.();
      if (!w) return;
      // toggleMaximize() no-ops on this window type — drive size
      // explicitly: fullscreen toggle, falling back gracefully.
      const isFs = await w.isFullscreen();
      await w.setFullscreen(!isFs);
    } catch (e) {
      console.warn("[msg-panel] toggleMaximize failed:", e);
    }
  };

  return (
    <div style={outerStyle}>
      <div style={cardStyle}>
        {/* Header = drag region (move the window). Buttons stop the
            drag so clicks register. */}
        <header
          style={headerStyle}
          data-tauri-drag-region
          onMouseDown={startDrag}
        >
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() =>
              invoke("close_message_panel").catch(() => {})
            }
            title="收起消息面板"
            aria-label="收起消息面板"
            style={iconBtnStyle}
          >
            <Icon name="chevron-left" size={14} />
          </button>
          {/* 历史会话选择器：点标题展开下拉，列出之前的会话，可切换 / 删除(每项 ×)。 */}
          <div
            onMouseDown={(e) => e.stopPropagation()}
            style={{ position: "relative", flex: 1, minWidth: 0 }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
              <button
                type="button"
                onClick={() => {
                  if (!pickerOpen) loadSessions();
                  setPickerOpen((v) => !v);
                }}
                title="选择历史会话"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 7,
                  flex: 1,
                  minWidth: 0,
                  background: "transparent",
                  border: "none",
                  color: "inherit",
                  font: "inherit",
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                <Icon name="message" size={14} style={{ color: "#a5b4fc" }} />
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                    textAlign: "left",
                  }}
                >
                  {`消息 · ${activeTitle}`}
                </span>
                <span style={{ flexShrink: 0, opacity: 0.7, fontSize: 10 }}>▾</span>
              </button>
              <span
                title="Session ID，可选中复制"
                onMouseDown={(e) => e.stopPropagation()}
                style={sessionIdChipStyle}
              >
                {activeSid}
              </span>
            </div>
            {pickerOpen && (
              <>
                {/* 点空白处关闭 */}
                <div
                  onClick={() => setPickerOpen(false)}
                  style={{ position: "fixed", inset: 0, zIndex: 998 }}
                />
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    left: 0,
                    marginTop: 6,
                    minWidth: 260,
                    maxWidth: 360,
                    maxHeight: 340,
                    overflowY: "auto",
                    background: "rgba(20,24,36,0.99)",
                    border: "1px solid rgba(148,163,184,0.3)",
                    borderRadius: 10,
                    boxShadow: "0 8px 28px rgba(0,0,0,0.5)",
                    zIndex: 999,
                    padding: 4,
                  }}
                >
                  {sessionList.length === 0 && (
                    <div style={{ padding: "10px 12px", color: "#64748b", fontSize: 12 }}>
                      暂无历史会话
                    </div>
                  )}
                  {sessionList.map((s) => {
                    const selected = s.session_id === activeSid;
                    const isDefault = s.session_id === DEFAULT_SID;
                    const autoLabel = isDefault
                      ? "默认话题"
                      : s.preview || s.session_id;
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
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          padding: "7px 8px",
                          borderRadius: 7,
                          background: selected
                            ? "rgba(37,99,235,0.22)"
                            : "transparent",
                          borderLeft: selected
                            ? "3px solid #60a5fa"
                            : "3px solid transparent",
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
                              fontSize: 13,
                              padding: "4px 7px",
                              borderRadius: 6,
                              border: "1px solid #60a5fa",
                              background: "#0f172a",
                              color: "#e2e8f0",
                              outline: "none",
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
                              style={{ flex: 1, minWidth: 0, cursor: "pointer" }}
                            >
                              <div
                                style={{
                                  color: selected ? "#bfdbfe" : "#e2e8f0",
                                  fontSize: 13,
                                  fontWeight: selected ? 600 : 500,
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
                                  gap: 6,
                                  color: "#94a3b8",
                                  fontSize: 11,
                                  marginTop: 3,
                                  minWidth: 0,
                                }}
                              >
                                <span style={{ flexShrink: 0 }}>{s.turn_count} 条</span>
                                <span
                                  title="Session ID，可选中复制"
                                  onClick={(e) => e.stopPropagation()}
                                  style={sessionIdChipStyle}
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
                              style={{
                                flexShrink: 0,
                                width: 22,
                                height: 22,
                                borderRadius: 6,
                                border: "none",
                                background: "transparent",
                                color: "#94a3b8",
                                cursor: "pointer",
                                fontSize: 13,
                                lineHeight: "20px",
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "rgba(96,165,250,0.18)";
                                e.currentTarget.style.color = "#93c5fd";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "transparent";
                                e.currentTarget.style.color = "#94a3b8";
                              }}
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
                              style={{
                                flexShrink: 0,
                                width: 22,
                                height: 22,
                                borderRadius: 6,
                                border: "none",
                                background: "transparent",
                                color: "#94a3b8",
                                cursor: "pointer",
                                fontSize: 15,
                                lineHeight: "20px",
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "rgba(239,68,68,0.18)";
                                e.currentTarget.style.color = "#f87171";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "transparent";
                                e.currentTarget.style.color = "#94a3b8";
                              }}
                            >
                              ×
                            </button>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => setShowModelModal(true)}
            title={
              eff_model
                ? is_following_default
                  ? `模型与参数（当前 ${eff_model} · 跟随 provider 默认）`
                  : `模型与参数（当前 ${eff_model}）`
                : "选择模型与参数"
            }
            aria-label="模型与参数"
            style={modelChipStyle}
          >
            <span
              style={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {eff_model
                ? ctx_label
                  ? `${eff_model}-${ctx_label}`
                  : eff_model
                : "默认模型"}
            </span>
            <Icon name="edit" size={11} style={{ flexShrink: 0 }} />
          </button>
          {/* 隐藏/显示工具消息(🔧 调用轨迹行) */}
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={toggleHideTools}
            title={hideTools ? "显示工具消息" : "隐藏工具消息"}
            aria-label={hideTools ? "显示工具消息" : "隐藏工具消息"}
            aria-pressed={hideTools}
            style={{
              ...iconBtnStyle,
              color: hideTools ? "#64748b" : "#67e8f9",
            }}
          >
            <span style={{ fontSize: 12, lineHeight: 1 }}>🔧</span>
          </button>
          {/* 2026-05-31 restore — context ring in header */}
          <span
            onMouseDown={(e) => e.stopPropagation()}
            style={{ display: "inline-flex", alignItems: "center" }}
          >
            <ContextRing
              snapshot={contextUsage}
              size={18}
              showLabel
              onClick={() => setContextModalOpen(true)}
            />
          </span>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => void toggleMaximize()}
            title="放大 / 还原"
            aria-label="放大 / 还原"
            style={iconBtnStyle}
          >
            <Icon name="expand" size={13} />
          </button>
        </header>

        <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
          <MessageStreamPanel
            embedded
            filter={filter}
            chatMessages={chatMessages}
            warnings={warnings}
            errors={errors}
            onSetFilter={setFilter}
            onDismiss={(sid, id) =>
              useSessionsStore.getState().dismiss_alert(sid, id)
            }
            onDismissAll={(sev) =>
              useSessionsStore.getState().dismiss_all_alerts(sev)
            }
            onJumpToSession={() => {
              /* single-thread panel — nothing to jump to */
            }}
            onChoice={onChoice}
            onWorkflowRetry={retryWorkflow}
          />
        </div>

        {/* Same companion chat_v2 path as the pet's main input —
            sessionId="default" pins send/echo/stop to the SAME session
            the pet main uses and MessageStreamPanel renders.
            leftAccessory = the mic button, parity with the pet bar. */}
        <InputBar
          placeholder="和桌宠说点什么…"
          sessionId={activeSid}
          leftAccessory={
            <button
              type="button"
              onClick={() => void toggleRecording()}
              disabled={audioState !== "connected"}
              title={
                audioState !== "connected"
                  ? "语音通道连接中…"
                  : isRecording
                    ? "停止录音"
                    : "按住说话"
              }
              aria-label={isRecording ? "停止录音" : "语音输入"}
              style={{
                width: 36,
                height: 36,
                flexShrink: 0,
                borderRadius: "50%",
                border: `1px solid ${
                  isRecording
                    ? "rgba(239,68,68,0.55)"
                    : "rgba(255,255,255,0.12)"
                }`,
                background: isRecording
                  ? "linear-gradient(180deg,#f87171,#ef4444)"
                  : audioState === "connected"
                    ? "rgba(129,140,248,0.20)"
                    : "rgba(255,255,255,0.06)",
                color: isRecording ? "#fff" : "#c7d2fe",
                cursor:
                  audioState === "connected" ? "pointer" : "not-allowed",
                boxShadow: isRecording ? "0 0 13px rgba(239,68,68,0.5)" : "none",
                animation: isRecording ? "pulse 1.5s infinite" : "none",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Icon name={isRecording ? "stop" : "mic"} size={16} />
            </button>
          }
        />
      </div>

      {showModelModal && (
        <ChangeModelModal
          session_id={activeSid}
          current_model={preferred_model}
          current_params={model_params}
          onClose={() => setShowModelModal(false)}
        />
      )}

      {/* 2026-05-31 restore — context-usage breakdown modal */}
      <ContextBreakdownModal
        open={contextModalOpen}
        onClose={() => setContextModalOpen(false)}
        sessionId={activeSid}
        snapshot={contextUsage}
        send={(m) => codePanelWS.send(m)}
        onMessage={(fn) => codePanelWS.on_message(fn)}
      />

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
              <span style={{ color: "#facc15", userSelect: "text" }}>
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

      {/* Recording-button pulse — this window has its own DOM, so it
          needs its own copy of the keyframes (App's is pet-window only). */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.7; transform: scale(1.1); }
        }
      `}</style>
    </div>
  );
}

const outerStyle: React.CSSProperties = {
  width: "100vw",
  height: "100vh",
  padding: 6,
  boxSizing: "border-box",
  background: "transparent",
  overflow: "hidden",
};

const cardStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  width: "100%",
  height: "100%",
  borderRadius: 16,
  overflow: "hidden",
  // 极简：扁平双段深色背景，去掉多段渐变与内高光噪点。
  background:
    "linear-gradient(180deg, rgba(22,26,40,0.97) 0%, rgba(15,17,26,0.98) 100%)",
  border: "1px solid rgba(255,255,255,0.07)",
  boxShadow: "0 16px 44px rgba(0,0,0,0.55)",
  backdropFilter: "blur(22px) saturate(1.4)",
  WebkitBackdropFilter: "blur(22px) saturate(1.4)",
};

const headerStyle: React.CSSProperties = {
  flexShrink: 0,
  padding: "11px 14px",
  fontSize: 13,
  fontWeight: 600,
  letterSpacing: 0.2,
  color: "#e8edf6",
  display: "flex",
  alignItems: "center",
  gap: 8,
  borderBottom: "1px solid rgba(255,255,255,0.05)",
  // 极简：去掉靛蓝渐变，扁平透明，靠分隔线区分。
  background: "transparent",
};

const sessionIdChipStyle: React.CSSProperties = {
  flexShrink: 0,
  maxWidth: 150,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  userSelect: "text",
  cursor: "text",
  border: "1px solid rgba(250, 204, 21, 0.7)",
  borderRadius: 4,
  padding: "1px 5px",
  background: "rgba(250, 204, 21, 0.12)",
  color: "#facc15",
  fontFamily:
    'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
  fontSize: 11,
  lineHeight: 1.35,
};

// 统一图标按钮：扁平、低对比、一致尺寸（28），无重边框。
const iconBtnStyle: React.CSSProperties = {
  width: 28,
  height: 28,
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(255,255,255,0.045)",
  color: "#c7d2fe",
  border: "1px solid rgba(255,255,255,0.07)",
  borderRadius: 9,
  cursor: "pointer",
  padding: 0,
};

const modelChipStyle: React.CSSProperties = {
  flexShrink: 0,
  maxWidth: 150,
  height: 28,
  padding: "0 11px",
  display: "flex",
  alignItems: "center",
  gap: 5,
  // 与图标按钮同款低对比底色，保持工整一致（不再单独用靛蓝高亮）。
  background: "rgba(255,255,255,0.045)",
  color: "#c7d2fe",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 999,
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
};
