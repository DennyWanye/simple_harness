// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { V2_CLIENT_VERSION } from "./ws/ControlChannel";
void V2_CLIENT_VERSION; // silence unused import (re-exported for diagnostics later)
import { MemoryPanel } from "./components/MemoryPanel";
import { ModelDownloadBanner } from "./components/ModelDownloadBanner";
import { ContextBreakdownModal } from "./components/ContextBreakdownModal";
import { ContextTracePanel } from "./components/ContextTracePanel";
import { WorkbenchShell, type WorkbenchView } from "./components/WorkbenchShell";
import { dark, bannerStyle } from "./theme/components";
import { StartupOverlay, type BootState } from "./components/StartupOverlay";
import { useBudgetToast } from "./hooks/useBudgetToast";
import { useContextCompactedToast } from "./hooks/useContextCompactedToast";
import { getAuthAdapter } from "./auth";
import {
  buildIdentityBind,
  identityBindRetryDelayMs,
  isTransientIdentityBindError,
  type IdentityChallenge,
} from "./auth/companionIdentityBridge";
import { useControlChannel } from "./hooks/useWebSocket";
import { usePermissionRequests } from "./hooks/usePermissionRequests";
import { useExternalWaitRequests } from "./hooks/useExternalWaitRequests";
import { useClarificationRequests } from "./hooks/useClarificationRequests";
import { PermissionPopup } from "./components/PermissionPopup";
import { ExternalWaitDialog } from "./components/ExternalWaitDialog";
import { ApprovalCenterPanel } from "./components/ApprovalCenterPanel";
import { ClarificationDialog } from "./components/ClarificationDialog";
import { Toolbar } from "./components/Toolbar";
// WI-01/02 (beta-100): first-run onboarding wizard + in-app feedback.
import { OnboardingWizard } from "./components/OnboardingWizard";
import { FeedbackPanel } from "./components/FeedbackPanel";
import { onboardingStatus, onboardingComplete } from "./bindings/onboarding";
import { buildDiagnosticBundle } from "./bindings/diagnostics";
import { updateCloudConfig } from "./bindings/config";
import { useAudioChannel } from "./hooks/useAudioChannel";
import { useAudioRecorder } from "./hooks/useAudioRecorder";
import { useAudioPlayer } from "./hooks/useAudioPlayer";
import { useUpdateChecker } from "./hooks/useUpdateChecker";
import { useAutostart } from "./hooks/useAutostart";
import { useBackendLifecycle } from "./hooks/useBackendLifecycle";
import { useSessionsStore } from "./stores/sessionsStore";
import { BACKEND_PORT } from "./backendPort";
import { VOICE_INPUT_ENABLED } from "./voiceAvailability";
// W3.3 (relay integration): lazy-mount the relay edition UI only when
// the active adapter is RelayAuthAdapter. OSS default (`manual` /
// `null` editions) never instantiates this component, so its presence
// here is a zero-cost import at build time and a no-op at runtime.
import { RelayAuthAdapter } from "./auth/RelayAuthAdapter";
import { RelayEdition } from "./auth/RelayEdition";
import { relayProviderRegistration } from "./auth/relayProviderRegistration";
import { friendlyChatErrorMessage } from "./auth/relayErrorText";

const DEFAULT_SESSION_ID = "default";

function App() {
  // W5 (R17): silent self-update on startup. No-op under dev-browser or
  // when the updater endpoint isn't reachable.
  useUpdateChecker();

  // T8：App 自建输入条/slash 状态机删除 —— 输入统一走 ChatView 内嵌的
  // code-panel InputBar（自带 SlashDropdown/ArgHintBar/输入历史）。
  const [activeSid, setActiveSid] = useState(DEFAULT_SESSION_ID);
  const activeSidRef = useRef(DEFAULT_SESSION_ID);
  // Track whether the backend is routing through cloud or local.
  // "cloud" | "local" | null (unknown)
  const [routeKind, setRouteKind] = useState<"cloud" | "local" | null>(null);
  // Shared secret — fetched from Tauri backend command after it has read the
  // SHARED_SECRET line from the spawned Python process. Empty string while
  // polling; once populated, the WebSocket hooks reconnect with proper auth.
  const [secret, setSecret] = useState("");

  // P3-S8 — visible startup state so users see a spinner / actionable
  // error instead of a silent black transparent window.
  const [bootState, setBootState] = useState<BootState>("starting");
  const [bootError, setBootError] = useState<string | null>(null);
  const [bootAttempt, setBootAttempt] = useState(0);

  // Poll the Rust side for the shared secret. Pure polling — no side
  // effects on the backend process. Safe to replay on HMR, F5, and the
  // backend-restarted supervisor event.
  const refreshSecret = useCallback(async () => {
    // P4-S18 dev hatch: `?secret=xxx` URL param lets us run the SPA in
    // a plain browser (Tauri runtime absent → no get_shared_secret).
    // No effect inside the real Tauri shell because the URL there has
    // no query string. Only honoured in dev (import.meta.env.DEV).
    if (import.meta.env.DEV) {
      const urlSecret = new URLSearchParams(window.location.search).get("secret");
      if (urlSecret) {
        setSecret(urlSecret);
        return;
      }
    }
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (!core) return;
    for (let i = 0; i < 60; i++) {
      try {
        const s = await core.invoke<string>("get_shared_secret");
        if (s) {
          setSecret(s);
          return;
        }
      } catch {
        // backend not yet up; retry
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }, []);

  // Bootstrap Python backend. Rust 的 start_backend 现在幂等 —— 用
  // shared_secret 而非 state.child 做判据，所以 F5 / StrictMode / HMR
  // 场景下重复触发只会返回现任 secret，不会抢端口 spawn 第二条 Python。
  // 正是因为幂等，前端这里无需 useRef 守卫也无需 "先查后启" 两段式
  // 逻辑，直接 invoke 即可。
  //
  // P3-S3: backend path 不再由前端传 —— Rust 侧 backend_launch::resolve
  // 按 bundle → env → dev-fallback 优先级自己定位，打包版走 Bundled
  // exe，dev 走 DESKPET_DEV_ROOT。前端 invoke 无参。
  useEffect(() => {
    (async () => {
      const core = await import("@tauri-apps/api/core").catch(() => null);
      if (!core) {
        // Not inside Tauri (e.g. vite dev browser preview) — skip boot
        // state machine entirely so the app still loads for UI work.
        setBootState("ready");
        return;
      }
      setBootState("starting");
      setBootError(null);
      try {
        const secret = await core.invoke<string>("start_backend");
        if (secret) {
          setSecret(secret);
          setBootState("ready");
          return;
        }
        // Empty-secret success is an unexpected branch (Rust always
        // returns Err on timeout now); fall through to error state.
        setBootError("Backend returned an empty SHARED_SECRET");
        setBootState("failed");
      } catch (e) {
        const msg = typeof e === "string" ? e : (e as Error)?.message ?? String(e);
        console.warn("[bootstrap] start_backend failed:", msg);
        // Also peek at Rust's cached error (richer if spawn_once tripped
        // port-in-use or SHARED_SECRET timeout) — prefer that message.
        try {
          const cached = await core.invoke<string | null>("get_startup_error");
          setBootError(cached || msg);
        } catch {
          setBootError(msg);
        }
        setBootState("failed");
      }
    })();
  }, [bootAttempt]);

  // P3-S8 — handlers bound to the startup error card buttons.
  const handleBootRetry = useCallback(async () => {
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (core) {
      try {
        await core.invoke("clear_startup_error");
      } catch {
        /* ignore */
      }
    }
    // Bump attempt counter so the bootstrap effect re-runs.
    setBootAttempt((n) => n + 1);
    // Fall back to the polling helper in case Rust's idempotent
    // start_backend returns the stale secret of a half-dead supervisor.
    void refreshSecret();
  }, [refreshSecret]);

  const handleBootOpenLog = useCallback(async () => {
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (!core) return;
    try {
      await core.invoke("open_log_dir");
    } catch (e) {
      console.warn("[bootstrap] open_log_dir failed:", e);
    }
  }, []);

  const handleBootExit = useCallback(async () => {
    // P4-S21 #7: prefer the dedicated `app_exit` Rust command so the
    // backend supervisor gets a clean shutdown (no orphan deskpet-backend.exe
    // hanging onto port 8100). Falls through to window.close on older
    // builds that don't have the command registered yet.
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (core?.invoke) {
      try {
        await core.invoke("app_exit");
        return;
      } catch {
        // Command might not be registered (older Rust binary). Fall
        // through to window.close which triggers WindowEvent::Destroyed
        // and the same kill_child path as a backup.
      }
    }
    const api = await import("@tauri-apps/api/window").catch(() => null);
    if (api?.getCurrentWindow) {
      try {
        await api.getCurrentWindow().close();
        return;
      } catch {
        /* noop */
      }
    }
    window.close();
  }, []);

  // S12: react to supervisor events — on crash, clear the secret so any
  // active WebSockets see a reconnect cue; on restarted, poll for the
  // new secret and let the WS hooks re-handshake.
  useBackendLifecycle((kind) => {
    if (kind === "crashed") {
      setSecret("");
    } else if (kind === "restarted") {
      void refreshSecret();
    } else if (kind === "dead") {
      console.warn("[backend] supervisor gave up — manual restart required");
      // Re-surface as a startup error so the user gets the same dialog
      // affordances (retry / open log dir) without having to re-invoke.
      (async () => {
        const core = await import("@tauri-apps/api/core").catch(() => null);
        let msg =
          "Backend supervisor gave up after repeated crashes. 请打开日志目录排查。";
        if (core) {
          try {
            const cached = await core.invoke<string | null>("get_startup_error");
            if (cached) msg = cached;
          } catch {
            /* ignore */
          }
        }
        setBootError(msg);
        setBootState("failed");
      })();
    }
  });

  // Autostart toggle (enable run-on-login via plugin-autostart).
  const autostart = useAutostart();
  // T8：App 自建 messages 数组删除 —— 消息渲染统一由 sessionsStore →
  // ChatView(MessageStreamPanel) 承担（单一权威读模型）。
  const [vadStatus, setVadStatus] = useState<
    "idle" | "listening" | "speaking" | "thinking"
  >("idle");

  const sessions = useSessionsStore((s) => s.sessions);
  const applySupervisorAlert = useSessionsStore((s) => s.apply_supervisor_alert);
  const ensureSession = useSessionsStore((s) => s.ensure);
  const switchActiveSid = useCallback(
    (sid: string) => {
      ensureSession(sid);
      useSessionsStore.getState().set_active(sid);
      activeSidRef.current = sid;
      setActiveSid(sid);
    },
    [ensureSession],
  );
  // T8：store.active_sid → App activeSid 单向纠偏（MessagePanelRoot 的
  // storeActiveSid 同步 effect 随迁）。store 可能被 controlWs dispatch /
  // SessionList 先行推进，App 指针跟上，视图 props 才不分叉。
  const storeActiveSid = useSessionsStore((s) => s.active_sid);
  useEffect(() => {
    if (storeActiveSid && storeActiveSid !== activeSidRef.current) {
      activeSidRef.current = storeActiveSid;
      setActiveSid(storeActiveSid);
    }
  }, [storeActiveSid]);
  // 2026-05-31: 删除前端 resize → invoke("set_window_geometry") 兜底循环。
  // 该 useEffect 是"拉伸后自动缩小两次"的根因 —— set_window_geometry 命令
  // 内部调用 win.set_size(LogicalSize)，会再触发一次 WindowEvent::Resized，
  // 而 window.innerWidth (CSS inner) 与 set_size (outer) 在 DPI/边框场景
  // 下相差几像素，每轮收缩一些 → 用户感知"缩小两次"后稳定。
  // 持久化已由 Rust 端 ResizeDebouncer（lib.rs WindowEvent::Resized 钩子）
  // 独家负责，不需要前端冗余写盘。

  // Control channel (text chat + interrupt + emotion/action events)
  const { state, lastMessage, getChannel: getControlChannel } =
    useControlChannel(BACKEND_PORT, secret);

  // The main Tauri window is the only renderer allowed to bind the current
  // human identity. The shared secret opens the socket; the first privileged
  // command is separately signed by Rust with the injected real window label.
  useEffect(() => {
    if (state !== "connected") return;
    const channel = getControlChannel();
    if (!channel) return;

    const adapter = getAuthAdapter();
    let challenge: IdentityChallenge | null = null;
    let bindInFlight = false;
    let rebindPending = false;
    let restoreRetryTimer: ReturnType<typeof setTimeout> | null = null;
    let restoreRetryAttempts = 0;
    let bindRetryTimer: ReturnType<typeof setTimeout> | null = null;
    let bindRetryAttempts = 0;
    let disposed = false;
    let relayLogoutObserved = false;

    const scheduleRelayRestoreRetry = () => {
      if (
        disposed ||
        restoreRetryTimer !== null ||
        restoreRetryAttempts >= 40
      ) {
        return;
      }
      restoreRetryAttempts += 1;
      restoreRetryTimer = setTimeout(() => {
        restoreRetryTimer = null;
        void sendCurrentIdentity();
      }, 500);
    };

    const scheduleTransientBindRetry = () => {
      if (
        disposed ||
        bindRetryTimer !== null ||
        bindRetryAttempts >= 10
      ) {
        return;
      }
      const delayMs = identityBindRetryDelayMs(bindRetryAttempts);
      bindRetryAttempts += 1;
      bindRetryTimer = setTimeout(() => {
        bindRetryTimer = null;
        void sendCurrentIdentity();
      }, delayMs);
    };

    const sendCurrentIdentity = async () => {
      if (disposed || !challenge) {
        rebindPending = true;
        return;
      }
      if (bindInFlight) {
        rebindPending = true;
        return;
      }
      const currentUser = adapter.currentUser();
      // Relay restoration is asynchronous. Binding the local profile during
      // that gap would briefly expose the wrong human's Companion state.
      if (
        adapter.id === "relay" &&
        currentUser === null &&
        !relayLogoutObserved
      ) {
        rebindPending = true;
        scheduleRelayRestoreRetry();
        return;
      }
      restoreRetryAttempts = 0;
      if (restoreRetryTimer !== null) {
        clearTimeout(restoreRetryTimer);
        restoreRetryTimer = null;
      }
      bindInFlight = true;
      rebindPending = false;
      try {
        const command = await buildIdentityBind(
          challenge,
          currentUser,
        );
        const sent = channel.send({
          type: command.kind,
          payload: {
            auth_snapshot: command.auth_snapshot,
            credential: command.credential,
          },
        });
        if (!sent) bindInFlight = false;
      } catch (error) {
        bindInFlight = false;
        console.warn("[companion-identity] signed bind failed:", error);
      }
    };

    const handleIdentityMessage = (incoming: unknown) => {
      const message = incoming as unknown as {
        type?: string;
        payload?: Record<string, unknown>;
      };
      const payload = message.payload ?? {};
      if (message.type === "companion_control_challenge") {
        const connectionId = String(
          payload.connection_id ?? payload.connectionId ?? "",
        );
        const controlEpoch = String(
          payload.control_epoch ?? payload.controlEpoch ?? "",
        );
        const rawChallenge = String(payload.challenge ?? "");
        const requestSeq = String(
          payload.request_seq ?? payload.requestSeq ?? "",
        );
        const bindingEpoch = String(
          payload.binding_epoch ?? payload.bindingEpoch ?? "",
        );
        if (
          connectionId &&
          controlEpoch &&
          rawChallenge &&
          requestSeq &&
          bindingEpoch
        ) {
          challenge = {
            connectionId,
            controlEpoch,
            challenge: rawChallenge,
            requestSeq,
            bindingEpoch,
          };
          bindInFlight = false;
          void sendCurrentIdentity();
        }
      } else if (message.type === "companion_profile_bound" && challenge) {
        challenge = {
          ...challenge,
          requestSeq: String(
            payload.next_request_seq ??
              payload.nextRequestSeq ??
              BigInt(challenge.requestSeq) + 1n,
          ),
          bindingEpoch: String(
            payload.binding_epoch ??
              payload.bindingEpoch ??
              challenge.bindingEpoch,
          ),
        };
        bindInFlight = false;
        bindRetryAttempts = 0;
        if (bindRetryTimer !== null) {
          clearTimeout(bindRetryTimer);
          bindRetryTimer = null;
        }
        restoreRetryAttempts = 0;
        if (restoreRetryTimer !== null) {
          clearTimeout(restoreRetryTimer);
          restoreRetryTimer = null;
        }
        if (rebindPending) void sendCurrentIdentity();
      } else if (message.type === "companion_control_rechallenge") {
        challenge = null;
        bindInFlight = false;
        rebindPending = true;
      } else if (message.type === "companion_control_error") {
        // Trusted auth may be temporarily unavailable even though the Rust
        // credential itself was valid. Keep the current challenge, stop the
        // in-flight latch, and retry only when the AuthAdapter reports a real
        // login/logout transition instead of spinning a rechallenge loop.
        bindInFlight = false;
        rebindPending = true;
        if (isTransientIdentityBindError(payload.code)) {
          scheduleTransientBindRetry();
        }
      }
    };
    const offMessage = channel.onMessage(handleIdentityMessage);
    const pendingIdentityChallenge = channel.getLatestMessage(
      "companion_control_challenge",
    );
    if (pendingIdentityChallenge) {
      handleIdentityMessage(pendingIdentityChallenge);
    }
    const offAuth = adapter.onEvent((event) => {
      if (event.type === "login" || event.type === "logout") {
        relayLogoutObserved = event.type === "logout";
        bindRetryAttempts = 0;
        if (bindRetryTimer !== null) {
          clearTimeout(bindRetryTimer);
          bindRetryTimer = null;
        }
        restoreRetryAttempts = 0;
        if (restoreRetryTimer !== null) {
          clearTimeout(restoreRetryTimer);
          restoreRetryTimer = null;
        }
        void sendCurrentIdentity();
      }
    });
    return () => {
      disposed = true;
      if (restoreRetryTimer !== null) clearTimeout(restoreRetryTimer);
      if (bindRetryTimer !== null) clearTimeout(bindRetryTimer);
      offMessage();
      offAuth();
    };
  }, [state, getControlChannel]);

  // T8：App 侧 ControlChannel 历史回灌 effect 删除 —— 回灌四连发
  // （消息/Run 投影/上下文/provider）统一由常挂载的 ChatView 经
  // controlWS（chat_v2 实际通道）发起，避免双通道双份加载。

  // P4-S20: toggle to route chat through the new tool_use loop
  // P4-S20-LLM-Unified: chat 路径已统一 — backend `chat` 和 `chat_v2`
  // msg_type 都走 tool_use AgentLoop。
  // P4-S21 #14: 删掉 useToolUseLoop 状态 + Toolbar toggle。前端永远走
  // chat_v2 路径（即 sendChatV2）。

  // Reset route kind when disconnected.
  useEffect(() => {
    if (state !== "connected") setRouteKind(null);
  }, [state]);

  // S14 — memory management panel toggle.
  const [memoryOpen, setMemoryOpen] = useState(false);
  // 2026-05-31 restore — context-usage breakdown modal (ring drill-down).
  const [contextModalOpen, setContextModalOpen] = useState(false);
  // P4-S11 §16.5 — ContextTrace panel (decision timeline + token budget)
  const [traceOpen, setTraceOpen] = useState(false);

  // T6 (workbench-ui) — 工作台视图 state（D2：App 层 useState 下传
  // WorkbenchShell，不引路由库）。设置/技能中心由浮层改为视图页面。
  const [view, setView] = useState<WorkbenchView>("chat");

  // WI-01 (beta-100): first-run onboarding. `onboardingNeeded` flips
  // true only when Rust reports no completion marker. Conservative on
  // error (stay false) so a path-resolution hiccup never traps the
  // user in a wizard.
  const [onboardingNeeded, setOnboardingNeeded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    onboardingStatus()
      .then((s) => {
        if (!cancelled) setOnboardingNeeded(s.status === "needs_onboarding");
      })
      .catch(() => {
        /* conservative: never show the wizard if status can't be read */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // WI-02 (beta-100): in-app feedback panel visibility.
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  // 2026-05-19 — pet-window error banner. chat_v2_error used to be
  // pushed as a "⚠ ..." assistant bubble, which then dominated the
  // bottom DialogBar and visually blocked the pet until the next
  // message. Now errors surface in a dedicated dismissible banner
  // pinned to the TOP of the pet column, ABOVE the toolbar — never
  // over the character. User-dismissed only (no auto-clear: an error
  // shouldn't silently vanish before the user notices it).
  const [petError, setPetError] = useState<string | null>(null);

  // P2-1-S8 — budget-exceeded toast. Auto-clears after 6s.
  const [budgetToast, setBudgetToast] = useState<string | null>(null);
  const showBudgetToast = useCallback((msg: string) => {
    setBudgetToast(msg);
  }, []);
  useEffect(() => {
    if (!budgetToast) return;
    const t = setTimeout(() => setBudgetToast(null), 6000);
    return () => clearTimeout(t);
  }, [budgetToast]);
  useBudgetToast(getControlChannel, showBudgetToast);

  // WI-1B-2 压缩可观测 — 上下文压缩命中时浮「已压缩，省 N token」。
  // 后端仅在 features.ctx_observability ON 时发 context_compacted（OFF=BC 不发）。
  // 复用预算 toast 的渲染槽（同一 fixed 角标），auto-clear 4s。
  const [ctxToast, setCtxToast] = useState<string | null>(null);
  const showCtxToast = useCallback((msg: string) => {
    setCtxToast(msg);
  }, []);
  useEffect(() => {
    if (!ctxToast) return;
    const t = setTimeout(() => setCtxToast(null), 4000);
    return () => clearTimeout(t);
  }, [ctxToast]);
  useContextCompactedToast(getControlChannel, showCtxToast);

  // P4-S20 Wave 1c — permission popup IPC wiring. Runs only when the
  // control channel is open; backend sends `permission_request`, hook
  // queues them and shows one at a time. ESC denies.
  const permissionChannel =
    state === "connected" ? getControlChannel() : null;
  const { current: permissionCurrent, resolve: resolvePermission } =
    usePermissionRequests(permissionChannel);
  const { current: externalWaitCurrent, complete: completeExternalWait } =
    useExternalWaitRequests(permissionChannel);
  const { current: clarificationCurrent, resolve: resolveClarification } =
    useClarificationRequests(permissionChannel);

  // T6：能力中心 / SkillStore 浮层 open state 已拆除 —— 二者页面化为
  // SkillsView（T10 实装，互跳 state 本地化在视图内部）。

  // Audio channel (voice pipeline)。T8：state(audioState) 的唯一读端
  // （底部 mic 按钮 disabled 判定）随输入条退役，不再解构。
  const {
    lastMessage: audioMessage,
    sendAudio,
    getChannel,
  } = useAudioChannel(BACKEND_PORT, secret, VOICE_INPUT_ENABLED);

  // Audio recorder (microphone → PCM16 → backend)。T8：start/stop 的
  // 调用点（toggleRecording）随底部输入条退役；isRecording 仍供
  // Toolbar 状态徽章消费（T13 一并收走）。
  const { isRecording } = useAudioRecorder(sendAudio);

  // Audio player — P2-2-M2 起走 PCM16 24kHz 流式播放（jitter buffer →
  // WebAudio 时间轴调度），不再需要等 tts_end 做整段 MP3 解码。
  const {
    isPlaying,
    stop: stopPlayback,
    reset: resetPlaybackBuffer,
    bargeIn,
  } = useAudioPlayer(getChannel());

  // Handle control channel messages (text chat + emotion/action drive)
  useEffect(() => {
    if (!lastMessage) return;
    const t = (lastMessage as { type?: string }).type;
    const payloadSid = (lastMessage as any).payload?.session_id;
    const isActivePayload = !payloadSid || payloadSid === activeSidRef.current;
    switch (t) {
      case "session_switched":
      case "task_session_started": {
        const nextSid = (lastMessage as any).payload?.new_sid;
        if (typeof nextSid === "string" && nextSid) {
          switchActiveSid(nextSid);
        }
        break;
      }
      case "chat_response":
        if (!isActivePayload) break;
        // T8：消息气泡渲染归 ChatView（sessionsStore 读模型）；此处仅
        // 保留路由指示灯（云端/本地）刷新。
        if ((lastMessage as any).payload.provider) {
          setRouteKind((lastMessage as any).payload.provider);
        }
        break;
      // T4：emotion_change / action_trigger 分支删除 —— 均为 Live2D
      // 角色驱动（桌宠删除例外清单）。
      // T8：tool_use_event / chat_v2_user_echo / workflow_event /
      // slash_command_result / session_messages_response 分支删除 ——
      // 全部只喂 App 自建 messages 数组（已废）；对应能力由
      // sessionsStore（controlWs dispatch 权威写入）→ ChatView 呈现。
      case "context_usage": {
        // 2026-05-31 restore — context-usage ring update from backend.
        const cu = (lastMessage as any).payload;
        if (cu?.session_id) {
          useSessionsStore.getState().ensure(cu.session_id);
          useSessionsStore.getState().upsert_context_usage(cu);
        }
        break;
      }
      case "chat_v2_run_reserved":
      case "chat_v2_run_started": {
        const p = (lastMessage as any).payload || {};
        const sid = String(p.session_id || activeSidRef.current);
        const runId = String(p.run_id || "").trim();
        if (runId) {
          useSessionsStore.getState().ensure(sid);
          const session = useSessionsStore.getState().sessions[sid];
          const selectedRunId = session?.selected_run_id;
          const requestId = String(p.request_id || "").trim();
          const locallyOriginated = Boolean(
            requestId &&
              session?.messages.some(
                (message) =>
                  message.role === "user" &&
                  message.request_id === requestId &&
                  (!message.run_id || message.run_id === runId),
              ),
          );
          const selectedProjection = selectedRunId
            ? session?.run_projections[selectedRunId]
            : undefined;
          const shouldSelect =
            locallyOriginated ||
            !selectedRunId ||
            selectedRunId === runId ||
            Boolean(
              selectedProjection &&
                ["completed", "failed", "cancelled"].includes(
                  selectedProjection.status,
                ),
            );
          useSessionsStore.getState().upsert_run_projection(sid, runId, {
            task_scope_id: String(p.task_scope_id || ""),
            request_id: requestId || undefined,
            turn_id: String(p.turn_id || "") || undefined,
            conversation_boundary_ref:
              String(p.conversation_boundary_ref || "") || undefined,
            conversation_boundary_version:
              typeof p.conversation_boundary_version === "number"
                ? p.conversation_boundary_version
                : undefined,
            version:
              typeof p.projection_version === "number"
                ? p.projection_version
                : 0,
            status:
              (lastMessage as any).type === "chat_v2_run_reserved"
                ? "starting"
                : "running",
            inflight: true,
            ui_state: shouldSelect ? "open" : "background",
          });
          if (shouldSelect) {
            useSessionsStore.getState().select_run_projection(sid, runId);
          }
        }
        break;
      }
      case "chat_v2_final": {
        if (!isActivePayload) break;
        const finalPayload = (lastMessage as any).payload || {};
        const finalSid = String(
          finalPayload.session_id || activeSidRef.current,
        );
        const finalRunId = String(finalPayload.run_id || "").trim();
        if (finalRunId) {
          useSessionsStore
            .getState()
            .upsert_run_projection(finalSid, finalRunId, {
              task_scope_id: String(finalPayload.task_scope_id || ""),
              status: "completed",
              inflight: false,
            });
        }
        break;
      }
      case "session_messages_error":
      case "sessions_list_error": {
        const p: any = (lastMessage as any).payload || {};
        if (
          p.session_id &&
          String(p.session_id) !== activeSidRef.current
        ) break;
        // Keep the last known view instead of replacing it with stale or
        // empty data. The user can retry once the projection gate recovers.
        setPetError(
          String(
            p.error ||
              "会话记录暂时无法确认是最新状态，请稍后重试。",
          ),
        );
        break;
      }
      case "chat_v2_error": {
        if (!isActivePayload) break;
        const p: any = (lastMessage as any).payload || {};
        const errorSid = String(p.session_id || activeSidRef.current);
        const errorRunId = String(p.run_id || "").trim();
        if (errorRunId) {
          useSessionsStore
            .getState()
            .upsert_run_projection(errorSid, errorRunId, {
              task_scope_id: String(p.task_scope_id || ""),
              status: "failed",
              inflight: false,
            });
        }
        // P4-S22 fix: render whatever the backend sent — `error`
        // (catch-all path), `detail` (AgentLoop ErrorEvent), or
        // `reason`. WI-R5: a relay `error_class` (insufficient_balance /
        // relay_key_invalid) is translated into a friendly Chinese
        // message via friendlyChatErrorMessage instead of a raw HTTP
        // error string.
        const msg = friendlyChatErrorMessage(p);
        // Surface in the top error banner（T4 起以 WorkbenchShell 顶部
        // 横幅形态渲染，bannerStyle("error")）。
        setPetError(msg);
        // WI-3: key 失效 → registration.recover（force 重铸 + 镜像进
        // registry，带 60s/≥2 次熔断防死循环）。取代旧 relayProviderBridge
        // 旁路（后者仅在 relay_managed_provider flag OFF 时回退，WI-6）。
        // insufficient_balance 只显示充值提示，不触发重签/重登引导。
        if (p.error_class === "relay_key_invalid" && relayAdapter) {
          void relayProviderRegistration.recover(relayAdapter);
        }
        break;
      }
      case "supervisor_alert": {
        // P5-S2/S3 — supervisor pushed a diagnosis. Cache it on the
        // session store（消息面板/后续视图消费）。T4：桌宠气泡与
        // PetStateMachine 已删，仅保留 store 缓存链路。
        const p: any = (lastMessage as any).payload || {};
        if (p.session_id) {
          // Make sure the session exists in the store (supervisor may
          // alert on a panel-only sid this window hasn't seen yet).
          ensureSession(p.session_id);
          applySupervisorAlert(p.session_id, {
            alert_id: String(p.alert_id || ""),
            severity: (p.severity as "green" | "yellow" | "red") || "yellow",
            action: (p.action as "nudge" | "ask_user") || "nudge",
            diagnosis: String(p.diagnosis || ""),
            user_message: String(p.user_message || ""),
            suggested_buttons: Array.isArray(p.suggested_buttons)
              ? p.suggested_buttons.map((b: any) => String(b)).slice(0, 2)
              : [],
            received_at: Date.now(),
          });
        }
        break;
      }
    }
  }, [lastMessage, applySupervisorAlert, ensureSession, switchActiveSid]);

  // Handle audio channel JSON messages
  useEffect(() => {
    if (!audioMessage) return;

    switch (audioMessage.type) {
      case "vad_event":
        if (audioMessage.payload.status === "speech_start") {
          setVadStatus("speaking");
          // 前端 VAD 在后端 BargeInFilter 之前先触发：立刻淡出在播音频
          // + 清 jitter buffer，避免给后端 TTS 打断事件到达前还在灌声。
          if (isPlaying) {
            bargeIn();
          }
          resetPlaybackBuffer();
        } else {
          setVadStatus("thinking");
        }
        break;

      case "run_event": {
        const p: any = audioMessage.payload || {};
        const sid = String(p.session_id || activeSidRef.current);
        const runId = String(p.run_id || "").trim();
        const status = String(p.status || "").toLowerCase();
        useSessionsStore.getState().ensure(sid);
        if (runId) {
          const terminal = ["completed", "failed", "cancelled"].includes(
            status,
          );
          useSessionsStore.getState().upsert_run_projection(sid, runId, {
            task_scope_id: String(p.task_scope_id || ""),
            status:
              status === "failed"
                ? "failed"
                : status === "cancelled"
                  ? "cancelled"
                  : terminal
                    ? "completed"
                    : "running",
            inflight: !terminal,
          });
        }
        break;
      }

      case "transcript":
        // T8：自建 messages 数组已废——transcript 气泡渲染归 store→ChatView
        // 链路。语音链路只经由 audio 通道，不走 control 通道的 chat_response
        // —— 这里复用 assistant transcript 上捎带的 provider 字段来刷新路由
        // 指示灯的颜色（green=local / blue=cloud），否则纯语音用户会一直
        // 停在灰色 "connected"。
        if (
          audioMessage.payload.role === "assistant" &&
          audioMessage.payload.provider
        ) {
          setRouteKind(audioMessage.payload.provider);
        }
        break;

      case "tts_end":
        // T4：口型驱动（fadeMouthToZero/flushVisemeQueue/mouthOpenY）
        // 随桌宠删除；音频播放状态机保留。
        setVadStatus("listening");
        break;

      // T4：tts_viseme / pet_milestone 分支删除 —— 均为桌宠口型/庆祝
      // 驱动（桌宠删除例外清单）。音频播放本身不受影响。

      case "tts_barge_in":
        // P2-2: backend VAD detected user speech during TTS — stop playback.
        console.log("[App] TTS barge-in — stopping playback");
        bargeIn();
        break;
    }
  }, [audioMessage, isPlaying, resetPlaybackBuffer, bargeIn]);

  // T4：lip_sync 订阅 effect 删除 —— 仅驱动桌宠口型（音频播放由
  // useAudioPlayer 独立消费二进制帧，不经此路径）。

  // T8：handleSend / handleSwitchDefault / slash 命令面板
  // （loadSlashCommands/slashCandidates/acceptSlash/handleKeyDown）删除
  // —— 发送与 slash 支持由 ChatView 内嵌 InputBar 全量承接
  // （InputBar 自带 SlashDropdown/ArgHintBar/输入历史，无需迁移）。
  // 回默认话题入口由 SessionList 的会话行承接（default 行常在）。

  // Barge-in: stop local playback + notify backend to cancel in-flight LLM/TTS.
  // Bound to a button (shown while TTS is playing) and to the Escape key.
  const handleInterrupt = useCallback(() => {
    stopPlayback();
    resetPlaybackBuffer();
    const ch = getControlChannel();
    ch?.send({
      type: "chat_v2_interrupt",
      payload: {
        session_id: activeSidRef.current,
        run_id:
          useSessionsStore.getState().sessions[activeSidRef.current]
            ?.selected_run_id ??
          useSessionsStore.getState().sessions[activeSidRef.current]
            ?.active_run_id,
      },
    });
    setVadStatus("idle");
  }, [stopPlayback, resetPlaybackBuffer, getControlChannel]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isPlaying) {
        handleInterrupt();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isPlaying, handleInterrupt]);

  // T8：toggleRecording 删除 —— 主窗 mic 按钮随底部输入条退役；语音
  // 占位（禁用 mic + tooltip）在 ChatView 输入栏旁（B9）。

  // T4：PetStateMachine tick / supervisor 气泡派生与选择回调、
  // handleBubbleClickBackground（open_message_panel 最后前端调用点）
  // 随桌宠删除；supervisor_alert 的 store 缓存链路保留（见上方 switch）。

  // W3.3 (relay integration): identify the active adapter once. Memoised
  // by the auth/index.ts singleton, so re-renders are free. We only
  // mount the relay UI when the adapter is concrete RelayAuthAdapter —
  // OSS default returns a ManualAuthAdapter and `relayAdapter` is null,
  // so the JSX guard below is dead code in that build.
  const relayAdapter = useMemo(() => {
    const a = getAuthAdapter();
    return a instanceof RelayAuthAdapter ? a : null;
  }, []);

  // 2026-05-26: 账户面板触发器 — RelayEdition mount 时把 setShowAccount
  // 写入 .current；Toolbar 的 onAccount 通过这个 ref 触发。pill 视觉挪
  // 进 Toolbar 后两边解耦，RelayEdition 只管 modal。
  const openAccountRef = useRef<(() => void) | null>(null);

  // WI-R3: in relay edition the forced login modal must come BEFORE the
  // onboarding wizard. Track auth state so the wizard is gated on it.
  // Non-relay editions: no adapter → `relayAuthed` stays true → wizard
  // shows normally (zero behaviour change).
  const [relayAuthed, setRelayAuthed] = useState(
    relayAdapter ? relayAdapter.isAuthenticated() : true,
  );
  useEffect(() => {
    if (!relayAdapter) return;
    setRelayAuthed(relayAdapter.isAuthenticated());
    // WI-3: mirror the relay device key into the backend provider
    // registry (so relay shows up as a normal, managed provider). attach
    // injects the control channel (resolved lazily per send) + the pet
    // error toast for the recover circuit-breaker. `login` (also emitted
    // by restoreSession / dev auto-login) triggers the ensure mirror;
    // `logout` tears down the local key so account A's long-lived key is
    // never reused by a subsequently logged-in account B. Manual edition
    // (relayAdapter === null) returns early above → never attached (inert).
    relayProviderRegistration.attach(getControlChannel, setPetError);
    if (relayAdapter.isAuthenticated()) {
      // Already authed at mount (restore/auto-login fired before we
      // subscribed). Idempotent: inflight + lastEnsured cache make a
      // duplicate login a no-op.
      void relayProviderRegistration.ensure(relayAdapter, "login");
    }
    return relayAdapter.onEvent((e) => {
      if (e.type === "login") {
        setRelayAuthed(true);
        void relayProviderRegistration.ensure(relayAdapter, "login");
      }
      if (e.type === "logout") {
        setRelayAuthed(false);
        relayProviderRegistration.onLogout();
        getControlChannel()?.send({
          type: "settings_providers_relay_logout",
        });
      }
    });
  }, [relayAdapter]);

  // WI-3 cold-start race fix: on a fresh launch the relay `login` event
  // (restoreSession / dev auto-login) can fire BEFORE the control WS is
  // connected, so the first ensure aborts at "no channel". Re-fire ensure
  // when the ws transitions to "connected" (idempotent: lastEnsured cache
  // makes a duplicate a no-op). Without this, 收编 silently never happens
  // on a real user's cold start (it only worked under HMR because the
  // channel was already up).
  useEffect(() => {
    if (!relayAdapter || state !== "connected") return;
    if (relayAdapter.isAuthenticated()) {
      void relayProviderRegistration.ensure(relayAdapter, "login");
    }
  }, [state, relayAdapter]);

  // WI-3 (B-C2 self-heal): backend signals the local relay key is gone
  // (keychain cleared / never minted) via settings_providers_error
  // {reason:key_missing}. Re-mint via recover (force). The recover
  // circuit-breaker (60s/≥2) stops a runaway loop if it keeps failing.
  useEffect(() => {
    if (!relayAdapter || !lastMessage) return;
    if ((lastMessage as { type?: string }).type !== "settings_providers_error")
      return;
    const p = (lastMessage as { payload?: Record<string, unknown> }).payload ?? {};
    if (p.reason === "key_missing" && p.provider_id === "relay-cloud") {
      void relayProviderRegistration.recover(relayAdapter);
    }
  }, [lastMessage, relayAdapter]);

  return (
    <div
      style={{
        position: "relative",
        width: "100vw",
        height: "100vh",
        // T6 (workbench-ui)：主窗普通化 — 不再透明/drag-region，工作台
        // 深色底（WB-1/WB-3）。
        background: dark.bgSolid,
        overflow: "hidden",
      }}
    >
      {/* W3.3: relay-edition UI lives entirely under this single
          conditional. Manual / null editions render zero relay nodes
          and pay zero runtime cost beyond one instanceof check above. */}
      {relayAdapter && (
        <RelayEdition adapter={relayAdapter} openAccountRef={openAccountRef} />
      )}

      {/* T6 — 工作台壳：Sidebar 240px + 内容区。view state 在 App 层
          （D2），视图 props 走显式合同（见 WorkbenchShell 文件头）。
          petError 横幅插槽由 banner prop 承接（T4 搬迁落位）。 */}
      <WorkbenchShell
        view={view}
        onViewChange={setView}
        connectionState={state}
        banner={
          petError ? (
            // T4 搬迁：原桌宠列顶部错误条 → WorkbenchShell 顶部横幅插槽
            // （bannerStyle("error")，主题单源；用户手动 ✕ 关闭不自动消失）。
            <div
              role="alert"
              style={{
                ...bannerStyle("error"),
                display: "flex",
                alignItems: "flex-start",
                gap: 6,
                borderRadius: 0,
                maxHeight: 60,
                overflowY: "auto",
              }}
            >
              <span style={{ flexShrink: 0 }}>⚠</span>
              <span
                data-bp-selectable=""
                style={{
                  flex: 1,
                  wordBreak: "break-word",
                  whiteSpace: "pre-wrap",
                }}
              >
                {petError}
              </span>
              <button
                type="button"
                onClick={() => setPetError(null)}
                title="关闭"
                aria-label="关闭错误提示"
                style={{
                  flexShrink: 0,
                  background: "transparent",
                  border: "none",
                  color: "inherit",
                  cursor: "pointer",
                  fontSize: 13,
                  lineHeight: 1,
                  padding: "0 2px",
                }}
              >
                ✕
              </button>
            </div>
          ) : undefined
        }
        chatProps={{ activeSid, secret }}
        sessionProps={{ activeSid, onSwitchSid: switchActiveSid }}
        skillsProps={{ channel: permissionChannel }}
        settingsProps={{
          getChannel: getControlChannel,
          lastMessage,
          secret,
          relayAdapter,
          onConfigChanged: () => setRouteKind(null),
          autostart: {
            ready: autostart.ready,
            enabled: autostart.enabled,
            toggle: autostart.toggle,
          },
        }}
      />

      {/* 过渡期（T8→T13 之间）：Toolbar absolute 覆盖层以根 div 为定位
          上下文继续挂载，T13 按 plan 收走（入口移侧栏）。 */}

      {/* P4-S20 — 权限请求弹窗（最高 zIndex） */}
      <PermissionPopup
        request={permissionCurrent}
        onResolve={resolvePermission}
        onStopRun={(runId) => {
          permissionChannel?.send({
            type: "chat_v2_interrupt",
            payload: {
              session_id: activeSidRef.current,
              run_id: runId,
            },
          });
        }}
      />
      <ExternalWaitDialog
        request={externalWaitCurrent}
        onComplete={completeExternalWait}
      />
      {/* WI-TG-2 — 审批聚合视图。BC：默认 enabled={false} → 不渲染，
          现有单弹窗路径不受影响。要开聚合 UX 把 prop 翻成 true。 */}
      <ApprovalCenterPanel channel={permissionChannel} enabled={false} />
      <ClarificationDialog
        current={clarificationCurrent}
        onResolve={resolveClarification}
      />

      {/* T4：PetDebugOverlay / PetSupervisorBubble / PetCelebrationBubble /
          PetDNDBadge / DialogBar / UserBubble 桌宠覆盖层全部删除
          （acceptance only-add 显式删除例外清单）。 */}

      {/* T6：CapabilityCenterPanel / SkillStorePanel 浮层挂载已拆除 ——
          页面化为 SkillsView（T10 实装；互跳本地化在视图内部）。 */}

      {/* T8：底部输入条（mic/打断/回默认/SlashDropdown/input/发送）整体
          退役 —— 输入统一走 ChatView 内嵌 InputBar；mic 禁用占位在
          ChatView（B9）；打断入口保留 Esc 快捷键（handleInterrupt）。 */}


      {/* Toolbar — P4-S20-UI revamp: token-based, grouped, hover/focus states.
          P4-S21 #7: now includes a Quit (⏻) button so users don't need
          Task Manager to close the pet after startup. */}
      <Toolbar
        onMemory={() => setMemoryOpen(true)}
        onTrace={() => setTraceOpen(true)}
        onSettings={() => setView("settings")}
        onSkillStore={() => setView("skills")}
        onFeedback={() => setFeedbackOpen(true)}
        onExit={handleBootExit}
        onAccount={
          relayAdapter && relayAuthed
            ? () => openAccountRef.current?.()
            : undefined
        }
        vadStatus={vadStatus}
        isPlaying={isPlaying}
        isRecording={isRecording}
        connectionState={state}
        routeKind={routeKind}
        topOffset={petError ? 66 : undefined}
        contextUsage={sessions[activeSid]?.context_usage ?? null}
        onContextRingClick={() => setContextModalOpen(true)}
      />

      {/* S14 memory management overlay */}
      <MemoryPanel
        open={memoryOpen}
        onClose={() => setMemoryOpen(false)}
        sessionId={activeSid}
        getChannel={getControlChannel}
      />

      {/* Option A: 首启模型下载进度（瘦包后台从 hf-mirror 拉模型时显示） */}
      <ModelDownloadBanner getChannel={getControlChannel} />

      {/* 2026-05-31 restore — Context usage breakdown (ring drill-down) */}
      <ContextBreakdownModal
        open={contextModalOpen}
        onClose={() => setContextModalOpen(false)}
        sessionId={activeSid}
        snapshot={sessions[activeSid]?.context_usage ?? null}
        send={(m) => {
          const ch = getControlChannel();
          if (ch) {
            try { ch.send(m); } catch (e) { console.warn("[ContextModal] send failed:", e); }
          }
        }}
        onMessage={(fn) => {
          const ch = getControlChannel();
          if (!ch) return () => {};
          return ch.onMessage(fn);
        }}
      />

      {/* P4-S11 ContextTrace overlay */}
      <ContextTracePanel
        open={traceOpen}
        onClose={() => setTraceOpen(false)}
        getChannel={getControlChannel}
      />

      {/* T6：SettingsPanel 浮层挂载已拆除 —— 页面化为 SettingsView
          （T11 实装 SettingsPanel variant:"page" 宿主，props 经
          WorkbenchShell settingsProps 合同下传）。 */}

      {/* P2-1-S8 budget-exceeded toast */}
      {budgetToast && (
        <div
          role="status"
          aria-live="polite"
          style={{
            position: "fixed",
            top: 16,
            right: 16,
            maxWidth: 320,
            padding: "10px 14px",
            background: "#b91c1c",
            color: "white",
            borderRadius: 6,
            fontSize: 13,
            boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
            zIndex: 2000,
          }}
        >
          {budgetToast}
        </div>
      )}

      {/* WI-1B-2 压缩可观测 toast —「已压缩，省 N token」。绿色区分于红色预算
          toast；下移避免与预算 toast 重叠（圈圈 gauge 区域上方角标）。 */}
      {ctxToast && (
        <div
          role="status"
          aria-live="polite"
          style={{
            position: "fixed",
            top: budgetToast ? 64 : 16,
            right: 16,
            maxWidth: 280,
            padding: "8px 14px",
            background: "#15803d",
            color: "white",
            borderRadius: 6,
            fontSize: 13,
            boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
            zIndex: 2000,
          }}
        >
          {ctxToast}
        </div>
      )}

      {/* P3-S8 — splash / error overlay. Renders above everything while
          the backend is still starting or has failed to start. */}
      <StartupOverlay
        state={bootState}
        errorMessage={bootError}
        onRetry={handleBootRetry}
        onOpenLogDir={handleBootOpenLog}
        onExit={handleBootExit}
      />

      {/* WI-01 (beta-100) — first-run onboarding wizard. Shows only on a
          brand-new install (no completion marker). "Test connection"
          reuses the existing update_cloud_config IPC — a successful
          test also persists the config, so the user isn't asked to
          save separately. */}
      {onboardingNeeded && relayAuthed && (
        <OnboardingWizard
          edition={relayAdapter ? "relay" : "manual"}
          onTestConnection={async (cfg) => {
            try {
              const r = await updateCloudConfig("", {
                base_url: cfg.base_url,
                model: cfg.model,
                api_key: cfg.api_key,
              });
              return { ok: !!r.ok };
            } catch (e) {
              return { ok: false, error: String(e) };
            }
          }}
          onComplete={() => {
            void onboardingComplete("0.6.0-beta").catch(() => {});
            setOnboardingNeeded(false);
          }}
          onSkip={() => {
            void onboardingComplete("0.6.0-beta").catch(() => {});
            setOnboardingNeeded(false);
          }}
        />
      )}

      {/* WI-02 (beta-100) — in-app feedback / diagnostic bundle panel. */}
      {feedbackOpen && (
        <FeedbackPanel
          onBuildBundle={(note) => buildDiagnosticBundle(note)}
          onClose={() => setFeedbackOpen(false)}
        />
      )}

      {/* Pulse animation for recording button */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.7; transform: scale(1.1); }
        }
      `}</style>
    </div>
  );
}

export default App;
