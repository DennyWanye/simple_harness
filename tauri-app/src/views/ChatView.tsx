// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * ChatView（T8，WB-4 / B2 / B4 / B9）— 工作台会话视图。
 *
 * 从 MessagePanelRoot.tsx 抽出内容区（JSX 结构 + 派生逻辑）：
 *  · MessageStreamPanel(embedded) + InputBar(sessionId=activeSid)
 *  · 头部条：当前会话标题 / 模型按钮(ChangeModelModal) / ContextRing /
 *    🔧 工具轨迹开关 / ◌ 进度开关 / 🐞 Harness 巡检开关（默认关）
 *  · chatMessages/companionEvents 派生（petText.forPet 位于 src/ 根，
 *    仅改 import 相对路径，文件不迁）
 *  · CompanionDetailModal / ContextBreakdownModal / PermissionPopup 随迁
 *  · companion_identity_status 就绪促升（companion_action_ready）由本
 *    视图独家发送（SessionList 只读订阅，避免双发）
 *  · mic 禁用占位（B9：语音待中转站 Realtime 接入）
 *  · 连接状态源 = controlWS.state()（chat_v2 实际通道，挑战轮 P1）；
 *    后端未就绪（App secret 未到位）显示状态条 + 重试入口
 *
 * 不迁：真语音块（toggleRecording/audioMessage，语音不在范围）、窗口
 * 三件套（拖拽/最大化/关闭，主窗普通化后由系统标题栏承担）、重复秘钥
 * 轮询（用 App 下传的 secret prop）。
 *
 * 样式纪律（WB-11）：颜色一律取 theme/tokens + dark 套件，零硬编码色值。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { tokens } from "../theme/tokens";
import { dark, bannerStyle } from "../theme/components";
import { Icon } from "../components/Icon";
import { ContextRing } from "../components/ContextRing";
import { ContextBreakdownModal } from "../components/ContextBreakdownModal";
import {
  useSessionsStore,
  collect_inbox,
  type InboxItem,
  type Message,
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
import { PermissionPopup } from "../components/PermissionPopup";
import { usePermissionRequests } from "../hooks/usePermissionRequests";
import {
  formatContextWindow,
} from "../code-panel/sessionModelsStore";
import { controlWS } from "../code-panel/controlWs";
import { useControlWsState } from "../hooks/useControlWsState";
import { topicDisplayLabel } from "../chat/topicTitle";
import { VOICE_UNAVAILABLE_MESSAGE } from "../voiceAvailability";
import {
  DEFAULT_HIDE_TOOL_TRACE,
  isMessageVisibleForSelectedRun,
  shouldHideToolTrace,
} from "../chat/messageVisibility";
import { HarnessInspectorPanel } from "../chat/HarnessInspectorPanel";
import {
  CompanionDetailModal,
  type CompanionDetailQuery,
} from "../components/companion/CompanionDetailModal";
import type {
  CompanionDetailErrorResponse,
  CompanionDetailResponse,
  CompanionEvent,
} from "../types/messages";
import type {
  ProjectDirectoryRequest,
  ProjectDirectoryResponse,
} from "../types/skillPlatform";
import {
  companionActionCommand,
  resolveCompanionDetailSelection,
  shouldRefreshCompanionProjection,
} from "../components/companion/actionProjection";
import { useCompanionProvisionalValues } from "../stores/companionSelectors";
import { sessionHydrationCommands } from "../chat/sessionHydration";
import {
  selectVisibleProjectDirectoryRequest,
  storeProjectDirectoryRequest,
  type ProjectDirectoryRequestsBySession,
} from "../chat/projectDirectoryState";

const EMPTY_COMPANION_EVENTS: CompanionEvent[] = [];
/** 空态兜底必须是**模块级常量**，不能写成 `?? []`。
 *
 * zustand 用引用相等判断快照是否变化：selector 里现造的 `[]` 每次渲染都是新对象
 * ⇒ 快照恒"变化" ⇒ 无限重渲（React 报 Maximum update depth exceeded）。
 * 保留会话 `default` 还在时这条路走不到（`sessions["default"]` 恒存在，取到的是
 * blank_session 里那个稳定数组）；取消保留会话后 `sessions[""]` 为 undefined，
 * 兜底分支第一次真正生效，2026-08-09 真机冷启动当场打爆 ChatView。 */
const EMPTY_MESSAGES: Message[] = [];

type WorkflowRetryDeferred = {
  promise: Promise<{ run_id: string }>;
  resolve: (value: { run_id: string }) => void;
  reject: (reason: Error) => void;
};

function workflowRetryError(message: string, code: string, definitive: boolean): Error {
  return Object.assign(new Error(message), { code, definitive });
}

type IncomingCtrlMsg = {
  type?: string;
  payload?: { sessions?: unknown; session_id?: string; title?: string } & Record<string, unknown>;
};

export interface ChatViewProps {
  activeSid: string;
  /** App 下传的后端共享秘钥（空 = 后端未就绪，显示状态条）。 */
  secret: string;
}

export function ChatView({ activeSid, secret }: ChatViewProps) {
  const permissionRequests = usePermissionRequests(controlWS);
  const [filter, setFilter] = useState<StreamFilter>("all");
  const [showModelModal, setShowModelModal] = useState(false);
  // T8：Harness 巡检面板开关默认关（plan 修正；原消息面板默认开）。
  const [harnessInspectorOpen, setHarnessInspectorOpen] = useState(false);
  const [projectDirectoryRequests, setProjectDirectoryRequests] =
    useState<ProjectDirectoryRequestsBySession>({});
  const [projectDirectoryErrors, setProjectDirectoryErrors] = useState<
    Record<string, string | undefined>
  >({});
  const [contextModalOpen, setContextModalOpen] = useState(false);
  const [companionIdentityReady, setCompanionIdentityReady] = useState(false);
  const contextUsage = useSessionsStore((s) => s.sessions[activeSid]?.context_usage ?? null);
  const workflowRetryDeferreds = useRef(new Map<string, WorkflowRetryDeferred>());

  const sessions = useSessionsStore((s) => s.sessions);
  const messages = useSessionsStore(
    (s) => s.sessions[activeSid]?.messages ?? EMPTY_MESSAGES,
  );
  const companionEvents = useSessionsStore(
    (s) => s.sessions[activeSid]?.companion_events ?? EMPTY_COMPANION_EVENTS,
  );
  const companionOwner = useSessionsStore((s) => s.companion_owner);
  const companionProvisional = useCompanionProvisionalValues();
  const [detailEvent, setDetailEvent] = useState<CompanionEvent | null>(null);
  const selectedRunId = useSessionsStore(
    (s) => s.sessions[activeSid]?.selected_run_id ?? null,
  );
  const runProjectionMap = useSessionsStore(
    (s) => s.sessions[activeSid]?.run_projections,
  );
  const runProjections = useMemo(
    () =>
      Object.values(runProjectionMap ?? {})
        .filter((projection) => projection.ui_state !== "closed")
        .sort((left, right) => left.started_at - right.started_at),
    [runProjectionMap],
  );
  const projectDirectoryRequest = useMemo(
    () => selectVisibleProjectDirectoryRequest(
      projectDirectoryRequests,
      activeSid,
      selectedRunId,
      runProjectionMap,
    ),
    [activeSid, projectDirectoryRequests, runProjectionMap, selectedRunId],
  );
  const projectDirectoryError = projectDirectoryRequest
    ? projectDirectoryErrors[projectDirectoryRequest.decision_id] ?? null
    : null;
  const visibleMessages = useMemo(
    () => messages.filter(
      (message) => isMessageVisibleForSelectedRun(message, selectedRunId),
    ),
    [messages, selectedRunId],
  );
  const preferred_model = useSessionsStore(
    (s) => s.sessions[activeSid]?.preferred_model ?? null,
  );
  const model_params = useSessionsStore(
    (s) => s.sessions[activeSid]?.model_params ?? null,
  );
  const selectTaskProjection = useCallback(
    (runId: string) => {
      const session = useSessionsStore.getState().sessions[activeSid];
      const previousId = session?.selected_run_id ?? null;
      const previous = previousId
        ? session?.run_projections?.[previousId]
        : undefined;
      const next = session?.run_projections?.[runId];
      if (!next) return;
      useSessionsStore.getState().select_run_projection(activeSid, runId);
      if (
        previous &&
        previous.run_id !== runId &&
        previous.ui_state === "open"
      ) {
        controlWS.send({
          type: "task_projection_update",
          payload: {
            session_id: activeSid,
            run_id: previous.run_id,
            ui_state: "background",
            expected_version: previous.version,
          },
        });
      }
      if (next.ui_state !== "open") {
        controlWS.send({
          type: "task_projection_update",
          payload: {
            session_id: activeSid,
            run_id: next.run_id,
            ui_state: "open",
            expected_version: next.version,
          },
        });
      }
    },
    [activeSid],
  );
  // 模型按钮显示「模型-上下文长度(K/M)」。未固定 preferred_model 时显示
  // 生效的 provider 默认模型，让用户看到「当前真正在用的模型」。
  // Header authority is Session-local. A process-global catalog default may
  // belong to another Session/provider and must never masquerade as binding.
  const eff_model = preferred_model?.trim() || contextUsage?.model?.trim() || "";
  const is_following_default = !((preferred_model ?? "").trim());
  const ctx_window = contextUsage?.context_window && contextUsage.context_window > 0
    ? contextUsage.context_window
    : null;
  const ctx_label = formatContextWindow(ctx_window);

  // ── 会话标题（sessions_list 旁听；SessionList 常驻同一单例通道，
  //    其挂载/刷新请求的响应这里同样收到）──────────────────────────
  const [sessionMeta, setSessionMeta] = useState<
    Record<string, { title?: string; preview?: string }>
  >({});
  useEffect(() => controlWS.on_message((msg: IncomingCtrlMsg) => {
    if (msg?.type === "sessions_list_response") {
      const arr = Array.isArray(msg?.payload?.sessions) ? msg.payload.sessions : [];
      const next: Record<string, { title?: string; preview?: string }> = {};
      for (const s of arr) {
        if (s?.session_id) {
          next[String(s.session_id)] = { title: s.title, preview: s.preview };
        }
      }
      setSessionMeta(next);
    } else if (msg?.type === "session_renamed" && msg?.payload?.ok) {
      const sid = String(msg.payload.session_id || "");
      if (sid) {
        setSessionMeta((prev) => ({
          ...prev,
          [sid]: { ...prev[sid], title: String(msg.payload?.title ?? "") },
        }));
      }
    }
  }), []);
  const activeMeta = sessionMeta[activeSid];
  // 空态（无会话）时 topicDisplayLabel 会退回空串，标题栏就成了一片空白 ——
  // 给一句明确的引导，告诉用户直接打字就能开始。
  const activeTitle = activeSid
    ? topicDisplayLabel({
        title: activeMeta?.title,
        preview: activeMeta?.preview,
        session_id: activeSid,
      })
    : "新对话（直接输入即可开始）";

  // ── companion 身份 / 目录确认 / 投影刷新监听（MessagePanelRoot 随迁）──
  useEffect(() => controlWS.on_message((raw: unknown) => {
    const message = raw as {
      type?: unknown;
      payload?: Record<string, unknown>;
    };
    if (message.type === "project_directory_request") {
      const payload = (message as ProjectDirectoryRequest).payload;
      if (payload?.decision_id && payload.session_id) {
        const request = { ...payload, received_at: Date.now() };
        setProjectDirectoryRequests((current) =>
          storeProjectDirectoryRequest(current, request),
        );
        setProjectDirectoryErrors((current) => ({
          ...current,
          [payload.decision_id]: undefined,
        }));
      }
    } else if (message.type === "project_directory_error") {
      const decisionId = String(message.payload?.decision_id || "");
      if (decisionId) {
        setProjectDirectoryErrors((current) => ({
          ...current,
          [decisionId]: String(
            message.payload?.error || "项目位置不可用，请重新选择。",
          ),
        }));
      }
    } else if (message.type === "companion_identity_status") {
      const ready =
        message.payload?.ready === true ||
        message.payload?.status === "ready";
      setCompanionIdentityReady(ready);
      if (ready) {
        // Promote this window's challenged lease with a Rust-signed,
        // no-side-effect readiness command（ChatView 独家发送，避免与
        // SessionList 双发）。
        void controlWS.send_companion_action(
          "companion_action_ready",
          { ready: true },
        );
      }
    } else if (message.type === "companion_identity_unready") {
      setCompanionIdentityReady(false);
    } else if (shouldRefreshCompanionProjection(message)) {
      // A detail fence may legitimately advance after the card was projected.
      // Refresh the durable projection instead of disabling the whole view.
      // 空态（无会话）没有可刷新的投影，直接跳过——保留会话移除后
      // 不再有可兜底的固定 sid。
      const sid = useSessionsStore.getState().active_sid;
      if (sid) {
        controlWS.send({
          type: "session_messages_load",
          payload: { session_id: sid, limit: 200 },
        });
      }
    }
  }), []);

  const confirmProjectDirectory = useCallback(
    (parentDirectory: string, folderName: string) => {
      const request = projectDirectoryRequest;
      if (!request) return false;
      const response: ProjectDirectoryResponse = {
        type: "project_directory_response",
        payload: {
          session_id: request.session_id,
          run_id: request.run_id,
          request_id: request.request_id,
          decision_id: request.decision_id,
          nonce: request.nonce,
          version: request.version,
          wait_ref: request.wait_ref,
          parent_directory: parentDirectory,
          folder_name: folderName,
          directory_mode: request.directory_mode ?? "create_new",
        },
      };
      setProjectDirectoryErrors((current) => ({
        ...current,
        [request.decision_id]: undefined,
      }));
      return controlWS.send(response);
    },
    [projectDirectoryRequest],
  );

  const companionOwnerKey = companionOwner
    ? `${companionOwner.profile_id}:${companionOwner.profile_generation}`
    : "";
  useEffect(() => {
    // 迁移自 MessagePanelRoot（已退役）的原样逻辑：owner 变更时重置详情
    // 选择。render 期调整模式的重构列入后续任务，此处保持行为等价。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetailEvent(null);
  }, [companionOwnerKey]);

  useEffect(() => {
    if (!detailEvent) return;
    const current = resolveCompanionDetailSelection(
      detailEvent,
      companionEvents,
    );
    // 同上：MessagePanelRoot 原样迁移的选中派生逻辑，行为等价优先。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (current !== detailEvent) setDetailEvent(current);
  }, [companionEvents, detailEvent]);

  const queryCompanionDetail = useCallback<CompanionDetailQuery>((request) => {
    const requestId =
      globalThis.crypto?.randomUUID?.() ??
      `companion-detail-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve) => {
      let settled = false;
      let timeout: ReturnType<typeof setTimeout> | null = null;
      const finish = (
        response: CompanionDetailResponse | CompanionDetailErrorResponse,
      ) => {
        if (settled) return;
        settled = true;
        if (timeout) clearTimeout(timeout);
        off();
        resolve(response);
      };
      const off = controlWS.on_message((raw: unknown) => {
        const response = raw as
          | CompanionDetailResponse
          | CompanionDetailErrorResponse;
        if (
          response.request_id === requestId &&
          (
            response.type === "companion_detail_response" ||
            response.type === "companion_detail_error"
          )
        ) {
          finish(response);
        }
      });
      timeout = setTimeout(() => finish({
        type: "companion_detail_error",
        request_id: requestId,
        payload: {
          code: "unavailable",
          message: "companion_detail_timeout",
        },
      }), 15_000);
      if (!controlWS.send_command({
        type: "companion_detail_get",
        request_id: requestId,
        // The owner is intentionally absent. The trusted connection binding
        // supplies it server-side.
        payload: request,
      })) {
        finish({
          type: "companion_detail_error",
          request_id: requestId,
          payload: {
            code: "unavailable",
            message: "companion_control_disconnected",
          },
        });
      }
    });
  }, []);

  const handleCompanionAction = useCallback(
    async (event: CompanionEvent, action: string, allow?: boolean) => {
      const command = companionActionCommand(event, action, allow);
      if (!command) return;
      await controlWS.send_companion_action(command.commandKind, command.body);
    },
    [],
  );

  useEffect(() => {
    const deferreds = workflowRetryDeferreds.current;
    const off = controlWS.on_message((raw: unknown) => {
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
        deferred.reject(workflowRetryError("视图已卸载", "window_unmounted", false));
      }
      deferreds.clear();
    };
  }, []);

  const retryWorkflow = useCallback((
    runId: string,
    actionId: Exclude<WorkflowV5ControlAction, "none">,
    retryKey: string,
  ): Promise<{ run_id: string; accepted?: boolean }> => {
    if (controlWS.state() !== "connected") {
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
      const off = controlWS.on_message((raw: unknown) => {
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
      if (!controlWS.send_command({ type, request_id: requestId, payload })) {
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

  // ── 会话切换 hydration（历史消息 / Run 投影 / 上下文 / provider 四连发；
  //    T8 归位：ChatView 常挂载、持有 activeSid，是回灌的唯一发起方）──
  useEffect(() => {
    if (!activeSid) return;
    useSessionsStore.getState().ensure(activeSid);
    for (const command of sessionHydrationCommands(activeSid)) {
      controlWS.send(command);
    }
  }, [activeSid]);

  // A modal opened for one Session cannot survive an ownership switch.
  useEffect(() => {
    setContextModalOpen(false);
  }, [activeSid]);

  // ── 工具轨迹 / 进度可见性（MessagePanelRoot 派生逻辑随迁）──────────
  const [hideTools, setHideTools] = useState(DEFAULT_HIDE_TOOL_TRACE);
  const [hideProgress, setHideProgress] = useState(false);
  useEffect(() => {
    try {
      localStorage.removeItem("msgpanel.hideTools");
    } catch {
      // localStorage may be disabled; visibility still defaults to shown.
    }
  }, []);
  const toggleHideTools = () => {
    setHideTools((hidden) => !hidden);
  };

  // Companion-stream derivation（strip <think> via forPet；工具执行轨迹
  // tool_call/tool_result 全程可观测——与原消息面板口径一致）。
  const chatMessages = useMemo(() => {
    const out: ChatStreamMessage[] = [];
    visibleMessages.forEach((m) => {
      const ts = m.ts;
      const runId = m.run_id?.trim() || undefined;
      if (m.role === "user") {
        out.push({
          role: "user",
          text: m.text ?? "",
          ts,
          runId,
          continuationStatus: m.continuation_status,
          continuationError: m.continuation_error,
        });
        return;
      }
      if (shouldHideToolTrace(m, hideTools)) {
        return;
      }
      if (m.role === "reasoning_summary") {
        if (!hideProgress && m.text) {
          out.push({
            role: "progress",
            text: m.text,
            ts,
            runId,
            phase: m.reasoning_phase,
            status: m.reasoning_status,
          });
        }
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
          runId,
          toolName: m.tool_name,
          toolArgs: m.tool_args ?? {},
        });
        return;
      }
      if ((m.role as string) === "tool_result") {
        out.push({
          role: "tool",
          text: `${m.tool_ok === false ? "❌" : "✅"} ${m.tool_name || "(工具)"} 完成`,
          ts,
          runId,
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
      if (clean) out.push({ role: "assistant", text: clean, ts, runId });
    });
    // provisional 条目排在全部已落库消息之后即可；用已有最大 ts 派生
    // 纯函数时间基（渲染期不得调用 Date.now，react-hooks/purity）。
    const provisionalBaseTs = (out.length ? out[out.length - 1].ts : 0) + 1;
    companionProvisional.forEach((text, index) => {
      const clean = forPet(text);
      if (clean) {
        out.push({
          role: "assistant",
          text: clean,
          ts: provisionalBaseTs + index,
        });
      }
    });
    return out;
  }, [
    visibleMessages,
    hideTools,
    hideProgress,
    activeSid,
    companionProvisional,
  ]);
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
    controlWS.send({
      type: "supervisor_user_choice",
      payload: {
        session_id: sid,
        alert_id,
        button_index,
        button_text,
        run_id:
          useSessionsStore.getState().sessions[sid]?.selected_run_id ??
          useSessionsStore.getState().sessions[sid]?.active_run_id,
      },
    });
    useSessionsStore.getState().dismiss_alert(sid, alert_id);
  };

  // ── 连接状态（源 = controlWS.state()，chat_v2 实际通道）────────────
  const wsState = useControlWsState();
  const backendReady = secret !== "";
  const retryConnect = useCallback(() => {
    // controlWS 无显式 reconnect API：send 在断开态会入队并内部触发
    // schedule_reconnect，用无副作用的 sessions_list 作为重连触发器。
    controlWS.send({ type: "sessions_list" });
  }, []);

  return (
    <section
      data-testid="view-chat"
      aria-label="会话"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        fontFamily: tokens.font.ui,
        color: dark.text,
        position: "relative",
      }}
    >
      {/* ── 头部条：标题 / 模型 / 可见性开关 / Harness / ContextRing ── */}
      <header
        data-testid="chat-header"
        style={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          gap: tokens.space.sm,
          padding: `${tokens.space.sm + 3}px ${tokens.space.md}px`,
          borderBottom: `1px solid ${dark.hairline}`,
          fontSize: tokens.text.base.size,
          fontWeight: tokens.weight.semibold,
        }}
      >
        <span
          data-testid="chat-title"
          style={{
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={activeTitle}
        >
          {activeTitle}
        </span>
        <span
          title="Session ID，可选中复制"
          style={{
            flexShrink: 0,
            maxWidth: 150,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            userSelect: "all",
            WebkitUserSelect: "all",
            cursor: "text",
            border: `1px solid ${dark.border}`,
            borderRadius: tokens.radius.sm,
            padding: "1px 5px",
            background: dark.inset,
            color: dark.textMuted,
            fontFamily: tokens.font.mono,
            fontSize: tokens.text.xs.size,
            fontWeight: tokens.weight.regular,
            lineHeight: 1.35,
          }}
        >
          {activeSid}
        </span>
        <button
          type="button"
          data-testid="chat-model-button"
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
          onClick={toggleHideTools}
          title={hideTools ? "显示工具消息" : "隐藏工具消息"}
          aria-label={hideTools ? "显示工具消息" : "隐藏工具消息"}
          aria-pressed={hideTools}
          style={{
            ...iconBtnStyle,
            color: hideTools ? dark.textFaint : dark.accent,
          }}
        >
          <span style={{ fontSize: tokens.text.sm.size, lineHeight: 1 }}>🔧</span>
        </button>
        <button
          type="button"
          onClick={() => setHideProgress((hidden) => !hidden)}
          title={hideProgress ? "显示执行进度" : "隐藏执行进度"}
          aria-label={hideProgress ? "显示执行进度" : "隐藏执行进度"}
          aria-pressed={hideProgress}
          style={{
            ...iconBtnStyle,
            color: hideProgress ? dark.textFaint : dark.accent,
          }}
        >
          <span style={{ fontSize: tokens.text.sm.size, lineHeight: 1 }}>◌</span>
        </button>
        <button
          type="button"
          data-testid="harness-inspector-toggle"
          onClick={() => setHarnessInspectorOpen((open) => !open)}
          title={
            harnessInspectorOpen
              ? "收起 Harness 运行观察"
              : "打开 Harness 运行观察"
          }
          aria-label={
            harnessInspectorOpen
              ? "收起 Harness 运行观察"
              : "打开 Harness 运行观察"
          }
          aria-pressed={harnessInspectorOpen}
          style={{
            ...iconBtnStyle,
            color: harnessInspectorOpen ? dark.accent : dark.textFaint,
          }}
        >
          <Icon name="bug" size={13} />
        </button>
        <span style={{ display: "inline-flex", alignItems: "center" }}>
          <ContextRing
            snapshot={contextUsage}
            size={18}
            showLabel
            onClick={() => setContextModalOpen(true)}
          />
        </span>
      </header>

      {/* ── 后端/通道状态条（controlWS.state() 为源；WB-4 错误态）── */}
      {(!backendReady || wsState !== "connected") && (
        <div
          data-testid="chat-conn-status"
          role="status"
          style={{
            ...bannerStyle(!backendReady ? "warning" : "error"),
            display: "flex",
            alignItems: "center",
            gap: tokens.space.sm,
            borderRadius: 0,
            flexShrink: 0,
          }}
        >
          <span style={{ flex: 1, minWidth: 0 }}>
            {!backendReady
              ? "后端启动中，聊天通道等待就绪…"
              : wsState === "connecting"
                ? "聊天通道连接中…"
                : "聊天通道已断开，消息暂时无法发送。"}
          </span>
          <button
            type="button"
            data-testid="chat-conn-retry"
            onClick={retryConnect}
            style={{
              flexShrink: 0,
              background: "transparent",
              border: `1px solid ${dark.borderStrong}`,
              borderRadius: tokens.radius.sm,
              color: "inherit",
              cursor: "pointer",
              font: "inherit",
              padding: `2px ${tokens.space.sm}px`,
            }}
          >
            重试
          </button>
        </div>
      )}

      {/* ── 内容区：Harness 巡检面板 + 消息流 + 输入栏 ── */}
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        <HarnessInspectorPanel
          open={harnessInspectorOpen}
          sessionId={activeSid}
          selectedRunId={selectedRunId}
          runProjections={runProjections}
          onSelectRun={selectTaskProjection}
          onClose={() => setHarnessInspectorOpen(false)}
          sendCommand={controlWS.send_command}
          subscribe={controlWS.on_message}
        />

        <div
          style={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
            <MessageStreamPanel
              embedded
              filter={filter}
              chatMessages={chatMessages}
              warnings={warnings}
              errors={errors}
              companionEvents={companionEvents}
              sessionId={activeSid}
              selectedRunId={selectedRunId}
              runProjections={runProjectionMap}
              projectDirectoryRequest={projectDirectoryRequest}
              projectDirectoryError={projectDirectoryError}
              onProjectDirectoryConfirm={confirmProjectDirectory}
              onSetFilter={setFilter}
              onDismiss={(sid, id) =>
                useSessionsStore.getState().dismiss_alert(sid, id)
              }
              onDismissAll={(sev) =>
                useSessionsStore.getState().dismiss_all_alerts(sev)
              }
              onJumpToSession={() => {
                /* workbench 单线程视图 — nothing to jump to */
              }}
              onChoice={onChoice}
              onCompanionDetail={setDetailEvent}
              onCompanionAction={handleCompanionAction}
              onWorkflowRetry={retryWorkflow}
            />
          </div>

          {/* 输入栏 + mic 禁用占位（B9：语音待中转站 Realtime 接入）。 */}
          <div
            style={{
              display: "flex",
              alignItems: "flex-end",
              flexShrink: 0,
              minWidth: 0,
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <InputBar
                placeholder={
                  companionIdentityReady
                    ? "输入消息，Enter 发送…"
                    : "正在恢复身份…"
                }
                disabled={!companionIdentityReady}
                sessionId={activeSid}
              />
            </div>
            <button
              type="button"
              data-testid="chat-mic-disabled"
              disabled
              title={VOICE_UNAVAILABLE_MESSAGE}
              aria-label={VOICE_UNAVAILABLE_MESSAGE}
              style={{
                ...iconBtnStyle,
                width: 34,
                height: 34,
                margin: `0 ${tokens.space.md}px ${tokens.space.md}px 0`,
                color: dark.textFaint,
                cursor: "not-allowed",
              }}
            >
              <Icon name="mic-off" size={15} />
            </button>
          </div>
        </div>
      </div>

      {showModelModal && (
        <ChangeModelModal
          session_id={activeSid}
          current_model={preferred_model}
          current_params={model_params}
          onClose={() => setShowModelModal(false)}
        />
      )}

      {detailEvent && (
        <CompanionDetailModal
          event={detailEvent}
          query={queryCompanionDetail}
          onClose={() => setDetailEvent(null)}
        />
      )}

      <ContextBreakdownModal
        open={contextModalOpen}
        onClose={() => setContextModalOpen(false)}
        sessionId={activeSid}
        projectName={sessions[activeSid]?.project_name}
        projectRoot={sessions[activeSid]?.project_root}
        snapshot={contextUsage}
        send={(m) => controlWS.send(m)}
        onMessage={(fn) => controlWS.on_message(fn)}
      />

      <PermissionPopup
        request={permissionRequests.current}
        onResolve={permissionRequests.resolve}
        resolving={permissionRequests.resolving}
        resolveError={permissionRequests.resolveError}
        onStopRun={permissionRequests.stopCurrentRun}
      />
    </section>
  );
}

// 统一图标按钮 — 深色套件低对比（零硬编码色值，WB-11）。
const iconBtnStyle: CSSProperties = {
  width: 28,
  height: 28,
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: dark.card,
  color: dark.textMuted,
  border: `1px solid ${dark.border}`,
  borderRadius: tokens.radius.md,
  cursor: "pointer",
  padding: 0,
};

const modelChipStyle: CSSProperties = {
  flexShrink: 0,
  maxWidth: 170,
  height: 28,
  padding: `0 ${tokens.space.md - 1}px`,
  display: "flex",
  alignItems: "center",
  gap: tokens.space.xs + 1,
  background: dark.card,
  color: dark.textMuted,
  border: `1px solid ${dark.cardBorder}`,
  borderRadius: tokens.radius.pill,
  fontSize: tokens.text.xs.size,
  fontWeight: tokens.weight.semibold,
  fontFamily: tokens.font.ui,
  cursor: "pointer",
};

export default ChatView;
