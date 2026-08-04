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
import { SlashDropdown, type SlashCommand } from "./code-panel/SlashDropdown";
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
import { Icon } from "./components/Icon";
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
import {
  VOICE_INPUT_ENABLED,
  VOICE_UNAVAILABLE_MESSAGE,
} from "./voiceAvailability";
// W3.3 (relay integration): lazy-mount the relay edition UI only when
// the active adapter is RelayAuthAdapter. OSS default (`manual` /
// `null` editions) never instantiates this component, so its presence
// here is a zero-cost import at build time and a no-op at runtime.
import { RelayAuthAdapter } from "./auth/RelayAuthAdapter";
import { RelayEdition } from "./auth/RelayEdition";
import { relayProviderRegistration } from "./auth/relayProviderRegistration";
import { friendlyChatErrorMessage } from "./auth/relayErrorText";
import {
  isWorkflowLifecycleOnlyMessage,
  workflowFinalAssistant,
} from "./workflowFinalAssistant";

const DEFAULT_SESSION_ID = "default";

function App() {
  // W5 (R17): silent self-update on startup. No-op under dev-browser or
  // when the updater endpoint isn't reachable.
  useUpdateChecker();

  const [chatText, setChatText] = useState("");
  // #4 slash 命令面板（与 code-panel InputBar 同款；桌宠主输入框也支持 /命令 自动补全）
  const chatInputRef = useRef<HTMLInputElement>(null);
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([]);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashIdx, setSlashIdx] = useState(0);
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
  const [messages, setMessages] = useState<
    { role: "user" | "assistant"; text: string }[]
  >([]);
  // T4：messages 的读端（DialogBar 的 latestAssistant 派生）随桌宠删除
  // 而消失；写端（大 switch 各分支）保留至 T8 收缩。void 引用过渡。
  void messages;
  const workflowFinalEventIdsRef = useRef(new Set<string>());
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

  // 2026-05-18: 连接(或重连)后从 SessionDB 回灌 default 会话历史，
  // 使左侧消息面板重启后也显示历史记录（后端 session_messages_load
  // → session_messages_response，已在上面 lastMessage switch 处理）。
  const historyLoadedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (state !== "connected") {
      historyLoadedRef.current.clear();
      return;
    }
    if (historyLoadedRef.current.has(activeSid)) return;
    const ch = getControlChannel();
    if (!ch) return;
    try {
      ch.send({
        type: "session_messages_load",
        payload: { session_id: activeSid, limit: 200 },
      });
      // 2026-05-31 restore — pull cached context-usage on connect so the
      // ring gauge in toolbar hydrates immediately.
      try {
        ch.send({
          type: "context_usage_request",
          payload: { session_id: activeSid },
        });
      } catch { /* best-effort */ }
      historyLoadedRef.current.add(activeSid);
    } catch (e) {
      console.warn("[Pet] session_messages_load send failed:", e);
    }
  }, [state, getControlChannel, activeSid]);

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

  // Audio channel (voice pipeline)
  const {
    state: audioState,
    lastMessage: audioMessage,
    sendAudio,
    getChannel,
  } = useAudioChannel(BACKEND_PORT, secret, VOICE_INPUT_ENABLED);

  // Audio recorder (microphone → PCM16 → backend)
  const { isRecording, startRecording, stopRecording } =
    useAudioRecorder(sendAudio);

  // Audio player — P2-2-M2 起走 PCM16 24kHz 流式播放（jitter buffer →
  // WebAudio 时间轴调度），不再需要等 tts_end 做整段 MP3 解码。
  const {
    isPlaying,
    stop: stopPlayback,
    reset: resetPlaybackBuffer,
    primeContext,
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
          setMessages([]);
        }
        break;
      }
      case "chat_response":
        if (!isActivePayload) break;
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: (lastMessage as any).payload.text },
        ]);
        // Update route indicator based on which provider actually served.
        if ((lastMessage as any).payload.provider) {
          setRouteKind((lastMessage as any).payload.provider);
        }
        break;
      // T4：emotion_change / action_trigger 分支删除 —— 均为 Live2D
      // 角色驱动（桌宠删除例外清单）。
      // P4-S20 chat_v2 stream events
      case "tool_use_event": {
        if (!isActivePayload) break;
        const payload = (lastMessage as any).payload || {};
        const kind = payload.kind || "";
        const tool = payload.tool_name || "";
        if (kind === "request") {
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              text: `🔧 调用 ${tool}(${JSON.stringify(payload.params || {})})`,
            },
          ]);
        } else if (kind === "result") {
          const r = payload.result;
          const ok = (r && typeof r === "object" && (r as any).ok === false) ? "❌" : "✅";
          setMessages((prev) => [
            ...prev,
            { role: "assistant", text: `${ok} ${tool} 结果` },
          ]);
        }
        break;
      }
      case "context_usage": {
        // 2026-05-31 restore — context-usage ring update from backend.
        const cu = (lastMessage as any).payload;
        if (cu?.session_id) {
          useSessionsStore.getState().ensure(cu.session_id);
          useSessionsStore.getState().upsert_context_usage(cu);
        }
        break;
      }
      case "chat_v2_user_echo": {
        if (!isActivePayload) break;
        // 2026-05-31 restore — multi-window sync: peer typed a user message.
        // Backend skips originator so receiving means peer-origin → push.
        const echoText = (lastMessage as any).payload?.text;
        if (typeof echoText === "string" && echoText.length > 0) {
          setMessages((prev) => [...prev, { role: "user", text: echoText }]);
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
      case "workflow_event": {
        if (!isActivePayload) break;
        const finalAssistant = workflowFinalAssistant(lastMessage);
        if (!finalAssistant) break;
        if (
          finalAssistant.eventId &&
          workflowFinalEventIdsRef.current.has(finalAssistant.eventId)
        ) break;
        if (finalAssistant.eventId) {
          workflowFinalEventIdsRef.current.add(finalAssistant.eventId);
        }
        setMessages((prev) => {
          const withoutCompletionPlaceholder = prev.filter(
            (message, index) => !(
              index === prev.length - 1 &&
              message.role === "assistant" &&
              message.text === "(完成)"
            ),
          );
          return [
            ...withoutCompletionPlaceholder,
            { role: "assistant", text: finalAssistant.text },
          ];
        });
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
        const finalText = finalPayload.text || "(完成)";
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: finalText,
          },
        ]);
        break;
      }
      case "slash_command_result": {
        // #4 slash 命令结果：渲染成一条 assistant 气泡（result.message）。
        if (!isActivePayload) break;
        const _r = (lastMessage as any).payload?.result || {};
        const _cmd = (lastMessage as any).payload?.command || "";
        const _msg =
          (typeof _r.message === "string" && _r.message) ||
          (_r.type === "error" ? `/${_cmd} 执行出错` : `/${_cmd} 已完成`);
        setMessages((prev) => [...prev, { role: "assistant", text: String(_msg) }]);
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
      case "session_messages_response": {
        // 2026-05-18: 重启/连接后从 SessionDB 回灌历史会话 → 左侧消息
        // 面板显示历史记录（之前只有实时消息，重启即空）。只取
        // user/assistant（tool 行非对话，streamChat 的 forPet 也会滤）；
        // 这是权威近 200 条快照，直接替换 messages。
        const p: any = (lastMessage as any).payload || {};
        const targetSid = p.session_id;
        if (targetSid && targetSid !== activeSidRef.current) break;
        const rows: any[] = Array.isArray(p.messages) ? p.messages : [];
        const hist = rows
          .filter((r) =>
            r &&
            (r.role === "user" || r.role === "assistant") &&
            !isWorkflowLifecycleOnlyMessage(r)
          )
          .map((r) => ({
            role: r.role as "user" | "assistant",
            text: String(r.text ?? ""),
          }));
        setMessages(hist);
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
        setMessages((prev) => [
          ...prev,
          {
            role: audioMessage.payload.role,
            text: audioMessage.payload.text,
          },
        ]);
        // 语音链路只经由 audio 通道，不走 control 通道的 chat_response ——
        // 这里复用 assistant transcript 上捎带的 provider 字段来刷新路由
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

  const handleSend = () => {
    if (!chatText.trim()) return;
    setMessages((prev) => [...prev, { role: "user", text: chatText }]);
    // P4-S21 #14: backend unified chat / chat_v2 — both route to tool_use
    // AgentLoop. Always send via sendChatV2 (the toolbar toggle is gone).
    const ch = getControlChannel();
    const trimmed = chatText.trim();
    if (trimmed.startsWith("/")) {
      // #4 slash 命令：路由到 slash_command（后端直接 dispatch，不走 AgentLoop）。
      const m = trimmed.slice(1).match(/^(\S+)\s*(.*)$/);
      const cmd = m ? m[1] : "";
      const args = m ? (m[2] ?? "") : "";
      ch?.send({
        type: "slash_command",
        payload: { command: cmd, args, session_id: activeSidRef.current },
      });
    } else {
      ch?.send({
        type: "chat_v2",
        payload: { text: chatText, session_id: activeSidRef.current },
      });
    }
    setSlashOpen(false);
    setChatText("");
  };

  // T4：handleNewTopic 死代码删除（主界面「新话题」按钮早已移除，
  // 会话新建链路由 T7 SessionList 的 startNewTopic 承接）。

  const handleSwitchDefault = useCallback(() => {
    switchActiveSid(DEFAULT_SESSION_ID);
    setMessages([]);
  }, [switchActiveSid]);

  // #4 slash：拉命令清单。后端在前端挂载时可能还没起来（boot 早期）→ fetch 失败静默，
  // 由首次输入 "/" 按需重试（loadSlashCommands），避免"挂载只拉一次、失败后永远空"的坑。
  const loadSlashCommands = useCallback(() => {
    fetch(`http://127.0.0.1:${BACKEND_PORT}/api/commands/help`)
      .then((r) => (r.ok ? r.json() : { commands: [] }))
      .then((d) => {
        const cs = Array.isArray(d?.commands) ? d.commands : [];
        if (cs.length) setSlashCommands(cs);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    loadSlashCommands();
  }, [loadSlashCommands]);

  // 当前候选命令（仅在输入以 / 开头、且还没打空格进入参数阶段时显示）。
  const slashCandidates = useMemo<SlashCommand[]>(() => {
    if (!slashOpen || !chatText.startsWith("/")) return [];
    const q = chatText.slice(1).split(/\s+/)[0] ?? "";
    if (chatText.length > q.length + 1) return []; // 已输空格 → 进参数阶段，关候选
    const lower = q.toLowerCase();
    if (!lower) return slashCommands;
    const prefix = slashCommands.filter((c) => c.name.toLowerCase().startsWith(lower));
    const substr = slashCommands.filter(
      (c) => !c.name.toLowerCase().startsWith(lower) && c.name.toLowerCase().includes(lower),
    );
    return [...prefix, ...substr];
  }, [slashOpen, chatText, slashCommands]);

  const acceptSlash = useCallback(
    (idx: number) => {
      const cmd = slashCandidates[idx];
      if (!cmd) return;
      setChatText(`/${cmd.name} `);
      setSlashOpen(false);
      setSlashIdx(0);
      chatInputRef.current?.focus();
    },
    [slashCandidates],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // slash 候选打开时优先拦截方向键 / Tab / Enter / ESC（别让 Enter 直接发送）。
    if (slashOpen && slashCandidates.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIdx((i) => (i + 1) % slashCandidates.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIdx((i) => (i - 1 + slashCandidates.length) % slashCandidates.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        acceptSlash(slashIdx);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashOpen(false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

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

  const toggleRecording = async () => {
    if (!VOICE_INPUT_ENABLED) return;
    if (isRecording) {
      stopRecording();
      setVadStatus("idle");
    } else {
      // Warm up the AudioContext inside the user-gesture handler so Chrome's
      // autoplay policy allows later `source.start()` to actually emit audio.
      // Creating/resuming the context from a WebSocket onmessage callback
      // instead leaves it "suspended" and playback is silent.
      await primeContext();
      startRecording();
      setVadStatus("listening");
    }
  };

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

      {/* 过渡期（T4→T8 之间）：底部输入条 / Toolbar 等 absolute 覆盖层
          以根 div 为定位上下文继续挂载，T8/T13 按 plan 收走。 */}

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

      <div
        style={{
          position: "absolute",
          bottom: 8,
          left: 8,
          right: 8,
          display: "flex",
          alignItems: "center",
          gap: 6,
          zIndex: 20,
          background:
            "linear-gradient(180deg, rgba(30,35,52,0.82) 0%, rgba(17,20,30,0.88) 100%)",
          padding: "7px 9px",
          borderRadius: 24,
          border: "1px solid rgba(255,255,255,0.10)",
          boxShadow:
            "0 12px 36px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.08)",
          backdropFilter: "blur(20px) saturate(1.5)",
          WebkitBackdropFilter: "blur(20px) saturate(1.5)",
        }}
      >
        {/* Mic button — pulses while recording */}
        <button
          data-testid="mic-button"
          onClick={toggleRecording}
          disabled={
            !VOICE_INPUT_ENABLED ||
            (audioState !== "connected" && state !== "connected")
          }
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            border: `1px solid ${
              isRecording
                ? "rgba(239,68,68,0.55)"
                : vadStatus === "speaking"
                  ? "rgba(245,158,11,0.55)"
                  : "rgba(255,255,255,0.12)"
            }`,
            background: isRecording
              ? "linear-gradient(180deg, #f87171, #ef4444)"
              : vadStatus === "speaking"
                ? "linear-gradient(180deg, #fbbf24, #f59e0b)"
                : "rgba(255,255,255,0.07)",
            color: isRecording || vadStatus === "speaking" ? "#fff" : "#cbd5e1",
            cursor: "pointer",
            animation: isRecording ? "pulse 1.5s infinite" : "none",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            boxShadow: isRecording
              ? "0 0 14px rgba(239,68,68,0.5)"
              : "none",
            transition: "background 160ms ease, box-shadow 160ms ease",
          }}
          title={
            !VOICE_INPUT_ENABLED
              ? VOICE_UNAVAILABLE_MESSAGE
              : isRecording
                ? "停止录音"
                : "按住录音"
          }
          aria-label={
            !VOICE_INPUT_ENABLED
              ? VOICE_UNAVAILABLE_MESSAGE
              : isRecording
                ? "停止录音"
                : "按住录音"
          }
        >
          <Icon
            name={
              !VOICE_INPUT_ENABLED
                ? "mic-off"
                : isRecording
                  ? "stop"
                  : "mic"
            }
            size={17}
          />
        </button>

        {/* Interrupt button — TTS playing */}
        {isPlaying && (
          <button
            data-testid="interrupt-button"
            onClick={handleInterrupt}
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              border: "1px solid rgba(239,68,68,0.55)",
              background: "linear-gradient(180deg, #ef4444, #dc2626)",
              color: "white",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              boxShadow: "0 0 14px rgba(239,68,68,0.45)",
            }}
            title="打断 (Esc)"
          >
            <Icon name="hand" size={17} />
          </button>
        )}

        {/* 主界面「新话题」按钮已移除（仅保留左侧消息面板内的）。handleNewTopic 仍保留
            供潜在调用 / 兼容；此处不再渲染。 */}

        {activeSid !== DEFAULT_SESSION_ID && (
          <button
            type="button"
            onClick={handleSwitchDefault}
            title="回到默认话题"
            aria-label="回到默认话题"
            style={{
              height: 36,
              width: 36,
              borderRadius: "50%",
              border: "1px solid rgba(148,163,184,0.28)",
              background: "rgba(255,255,255,0.07)",
              color: "#cbd5e1",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <Icon name="refresh" size={15} />
          </button>
        )}

        {/* SlashDropdown 绝对定位浮在整条输入条上方（composer row 已是 position:absolute
            作定位上下文）；input 回到直接 flex:1 子元素，撑满中间、不与发送按钮重叠。 */}
        <SlashDropdown
          candidates={slashCandidates}
          selectedIdx={slashIdx}
          onAccept={acceptSlash}
        />
        <input
          ref={chatInputRef}
          data-testid="chat-input"
          className="bp-chat-input"
          type="text"
          value={chatText}
          onChange={(e) => {
            const v = e.target.value;
            setChatText(v);
            // #4 slash 状态机：/ 开头且还没打空格 → 开候选面板。
            if (v.startsWith("/")) {
              if (slashCommands.length === 0) loadSlashCommands(); // 按需重试（boot 早期失败兜底）
              const firstWord = v.slice(1).split(/\s+/)[0] ?? "";
              setSlashOpen(v.length <= firstWord.length + 1);
              setSlashIdx(0);
            } else {
              setSlashOpen(false);
            }
          }}
          onKeyDown={handleKeyDown}
          placeholder={
            state === "connected" ? "说点什么…" : "连接中…"
          }
          disabled={state !== "connected"}
          style={{
            flex: 1,
            minWidth: 0,
            height: 36,
            padding: "0 15px",
            borderRadius: 18,
            border: "1px solid rgba(255,255,255,0.10)",
            fontSize: 13,
            background: "rgba(255,255,255,0.06)",
            color: "#e8edf6",
            outline: "none",
            fontFamily: "inherit",
            transition: "border-color 140ms ease, box-shadow 140ms ease, background 140ms ease",
          }}
        />
        {(() => {
          const active = state === "connected" && !!chatText.trim();
          return (
            <button
              data-testid="send-button"
              onClick={handleSend}
              disabled={!active}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "0 15px",
                height: 36,
                flexShrink: 0,
                borderRadius: 18,
                border: `1px solid ${active ? "rgba(96,165,250,0.6)" : "rgba(255,255,255,0.07)"}`,
                background: active
                  ? "linear-gradient(180deg, #4f93ff 0%, #2563eb 100%)"
                  : "rgba(255,255,255,0.05)",
                color: active ? "#fff" : "rgba(148,163,184,0.6)",
                fontSize: 13,
                fontWeight: 600,
                cursor: active ? "pointer" : "not-allowed",
                boxShadow: active ? "0 4px 14px rgba(37,99,235,0.42)" : "none",
                transition: "background 140ms ease, box-shadow 140ms ease, transform 120ms ease",
              }}
              onMouseEnter={(e) => {
                if (active) {
                  e.currentTarget.style.background =
                    "linear-gradient(180deg, #60a5fa 0%, #1d4ed8 100%)";
                  e.currentTarget.style.transform = "translateY(-1px)";
                }
              }}
              onMouseLeave={(e) => {
                if (active) {
                  e.currentTarget.style.background =
                    "linear-gradient(180deg, #4f93ff 0%, #2563eb 100%)";
                  e.currentTarget.style.transform = "translateY(0)";
                }
              }}
            >
              <Icon name="send" size={14} />
              发送
            </button>
          );
        })()}
      </div>

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
