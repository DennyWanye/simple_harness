// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SettingsPanel —— Provider、数据目录、Agent 预算、自启与只读状态卡。
 *
 * Provider rows are owned by SettingsProviders and persisted through the
 * backend registry. This component has no account-login or renderer Keychain
 * path; the control channel carries provider operations to the backend.
 *
 * The 今日使用 section reads from `fetchDailyBudget`, which round-trips
 * through the control WS to the BillingLedger (S8). The DailyBudgetStatus
 * contract (snake_case fields) is frozen in types/messages.ts.
 */
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";

import { Icon } from "./Icon";
import { ModelContextCard } from "./ModelContextCard";
import { useConfirm } from "./useConfirm";
import { SettingsProviders } from "./SettingsProviders";
import { formatUpdaterError } from "./updaterError";
import { dark, titleText, transition } from "../theme/components";
import { tokens } from "../theme/tokens";
import type {
  DailyBudgetStatus,
  IncomingMessage,
} from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
  /** Accessor so the component can both `send` and subscribe without
   * recreating subscriptions every parent render. */
  getChannel: () => ControlChannel | null;
  /** The most recent incoming control message — we narrow to our reply
   * type inside an effect. Piggybacking the existing App-level state
   * avoids an extra onMessage listener that'd need manual teardown. */
  lastMessage: IncomingMessage | null;
  secret: string;
  onConfigChanged?: () => void;
  /** T11 (workbench-ui, D5 宿主模式)：
   * - "overlay"（默认）= 原浮层形态（backdrop + 居中模态）；
   * - "page" = 工作台 SettingsView 页面宿主 —— 去 backdrop/fixed，
   *   填满内容区，无关闭按钮（页面由侧栏导航切走）。 */
  variant?: "overlay" | "page";
  /** T11：自启开关从 Toolbar 移入设置页（B12 保留换位置）。
   * ready=false（如 dev 浏览器无 Tauri runtime）时整节隐藏。 */
  autostart?: {
    ready: boolean;
    enabled: boolean;
    toggle: () => void;
  };
}

export const CHAT_TURN_TIMEOUT_DEFAULT_MINUTES = 15;
export const CHAT_TURN_TIMEOUT_MIN_MINUTES = 1;
export const CHAT_TURN_TIMEOUT_MAX_MINUTES = 60;

export function clampChatTurnTimeoutMinutes(minutes: number): number {
  if (!Number.isFinite(minutes)) return CHAT_TURN_TIMEOUT_DEFAULT_MINUTES;
  return Math.min(
    CHAT_TURN_TIMEOUT_MAX_MINUTES,
    Math.max(CHAT_TURN_TIMEOUT_MIN_MINUTES, Math.round(minutes)),
  );
}

export function buildChatTurnTimeoutGetMessage(requestId: string) {
  return {
    type: "chat_turn_timeout_get",
    request_id: requestId,
    payload: {},
  };
}

export function buildChatTurnTimeoutSetMessage(
  minutes: number,
  requestId?: string,
) {
  return {
    type: "chat_turn_timeout_set",
    ...(requestId ? { request_id: requestId } : {}),
    payload: { minutes: clampChatTurnTimeoutMinutes(minutes) },
  };
}

/**
 * Send a `budget_status` request on the control channel and resolve with the
 * next `budget_status` reply (or reject after `timeoutMs`).
 *
 * P2-1-S8: replaced S3's hardcoded stub with the real control-WS roundtrip.
 * The DailyBudgetStatus contract (snake_case fields) is the cross-slice
 * import point locked in spec §1.3.
 */
export async function fetchDailyBudget(
  channel: ControlChannel,
  timeoutMs = 3000,
): Promise<DailyBudgetStatus> {
  return new Promise<DailyBudgetStatus>((resolve, reject) => {
    const timer = setTimeout(() => {
      unsub();
      reject(new Error("budget_status timeout"));
    }, timeoutMs);
    const unsub = channel.onMessage((msg: IncomingMessage) => {
      if (msg.type === "budget_status") {
        clearTimeout(timer);
        unsub();
        resolve(msg.payload);
      }
    });
    channel.send({ type: "budget_status" });
  });
}

export function SettingsPanel({
  open,
  onClose,
  getChannel,
  lastMessage,
  variant = "overlay",
  autostart,
}: SettingsPanelProps) {
  // 2026-05-26: 删除"今日使用"section — 用户要求，billing 状态不再在
  // Settings 里展示（如需查看请用后端 /budget_status 命令或 metrics）。
  if (!open) return null;

  const isPage = variant === "page";

  const panel = (
      <div
        style={isPage ? pagePanelStyle : panelStyle}
        onClick={(e) => e.stopPropagation()}
      >
        <header style={headerStyle}>
          <span style={{ display: "flex", alignItems: "center", gap: tokens.space.md }}>
            <Icon name="settings" size={18} style={{ color: dark.textMuted }} />
            <h2 style={titleText}>设置</h2>
          </span>
          {!isPage && (
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭设置"
              style={closeBtnStyle}
            >
              <Icon name="close" size={16} />
            </button>
          )}
        </header>

        {/* P5-S2 multi-provider-management: legacy single-provider LLM
            configuration section was removed. All provider config now lives
            under the "LLM Providers" section below (drag-drop reorder,
            multiple endpoints, per-card pinning). */}

        {/* T11：「桌宠形象」区块删除（形象清单模块三件套随桌宠退役，
            acceptance only-add 显式删除例外清单）。 */}

        {/* ================ 通用（T11：自启开关自 Toolbar 移入） ================ */}
        {autostart?.ready && (
          <section style={sectionStyle}>
            <h3 style={h3Style}>通用</h3>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 12.5,
                color: dark.text,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                data-testid="autostart-toggle"
                checked={autostart.enabled}
                onChange={() => autostart.toggle()}
              />
              开机自动启动
            </label>
            <p style={hintStyle}>
              登录系统后自动启动 Simple Harness。
            </p>
          </section>
        )}

        {/* ================ LLM Providers (P5-S2 Phase 4) ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>LLM Providers</h3>
          <SettingsProviders
            getChannel={getChannel}
            lastMessage={lastMessage}
          />
        </section>

        {/* ================ 模型状态 (P4-S16) ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>模型状态</h3>
          {/* 2026-09-10：BGE-M3 / WeMM 嵌入器状态卡随认知记忆 SDK 一并移除
              ——本构建不再注册任何 embedder。 */}
          {/* Phase 1.1.6（context-1m-rearch）：per-model 上下文窗口卡片 */}
          <ModelContextCard getChannel={getChannel} />
        </section>

        {/* ================ 自动模式 (P4-S21 #13) ================ */}
        <section style={sectionStyle}>
          <h3 style={h3Style}>权限</h3>
          <AutoModeToggle getChannel={getChannel} />
          <ChatTurnTimeoutSetting getChannel={getChannel} />
        </section>

        {/* ================ 数据目录 (2026-05-21) ================ */}
        <DataDirSection />

        {/* ================ 任务与会话数据 (2026-09-25 条目 7：只统计、只提醒，不删除) ================ */}
        <StorageUsageSection getChannel={getChannel} />

        {/* ================ 任务发布目录 (NEXT-TG-1.0 §9) ================ */}
        <PublishDirSection getChannel={getChannel} />

        {/* ================ 任务技能目录 (NEXT-TG-1.0 §11) ================ */}
        <SkillCatalogueSection getChannel={getChannel} />

        {/* ================ 关于与更新 (2026-06-05) ================ */}
        <UpdateSection />

        {/* ================ 危险区 (P3-S9) ================ */}
        <DangerZoneSection />

        {/* SettingsProviders directly persists each change; close with the
            header button instead of a misleading global save action. */}
      </div>
  );

  // page variant（D5 宿主模式）：无 backdrop、无 fixed 定位，直接填满
  // 工作台内容区；overlay variant 保持原浮层结构不变。
  if (isPage) return panel;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="设置"
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {panel}
    </div>
  );
}

// ----------------------------------------------------------------------
// 关于与更新 (2026-06-05) — 手动"检查更新"入口。
//
// 底层走 tauri-plugin-updater：check() 拉 latest.json 比版本号；有新版本
// 就 downloadAndInstall() 下载验签安装。Windows 下安装步骤执行时 app 会被
// 安装器自动关闭（官方限制），装完拉起新版本，所以这里不需要手动 relaunch。
//
// 启动时的静默检查在 hooks/useUpdateChecker.ts；本组件是用户主动点的入口。
// 两者用的是同一套 updater 配置（tauri.conf.json > plugins.updater）。
// 错误文案翻译在 ./updaterError（抽出来便于纯函数单测）。
// ----------------------------------------------------------------------

type UpdatePhase =
  | "idle"
  | "checking"
  | "latest"
  | "available"
  | "downloading"
  | "done"
  | "error";

function UpdateSection() {
  const [version, setVersion] = useState<string>("");
  const [phase, setPhase] = useState<UpdatePhase>("idle");
  const [errMsg, setErrMsg] = useState<string>("");
  const [newVersion, setNewVersion] = useState<string>("");
  const [notes, setNotes] = useState<string>("");
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(
    null,
  );
  // check() 返回的 Update 对象，留到用户点"下载并安装"时用。
  // 用 any 是因为只在 Tauri 运行时存在，避免给非 Tauri 预览构建引类型依赖。
  const [pending, setPending] = useState<{ downloadAndInstall: (cb?: (e: unknown) => void) => Promise<void> } | null>(
    null,
  );

  // 读当前版本号展示（来自 tauri.conf.json 的 version）。
  useEffect(() => {
    let alive = true;
    import("@tauri-apps/api/app")
      .then((m) => m.getVersion())
      .then((v) => {
        if (alive) setVersion(v);
      })
      .catch(() => {
        /* 非 Tauri 预览构建 / 读不到版本 —— 不显示即可 */
      });
    return () => {
      alive = false;
    };
  }, []);

  const onCheck = useCallback(async () => {
    setPhase("checking");
    setErrMsg("");
    try {
      const mod = await import("@tauri-apps/plugin-updater").catch(() => null);
      if (!mod) {
        // 预览构建无 Tauri —— 直接当作"已是最新"，避免误报错误。
        setPhase("latest");
        return;
      }
      const update = await mod.check();
      if (!update) {
        setPhase("latest");
        return;
      }
      setNewVersion(update.version);
      setNotes(update.body ?? "");
      setPending(update as unknown as typeof pending);
      setPhase("available");
    } catch (err) {
      console.info("[updater] manual check failed:", err);
      setErrMsg(formatUpdaterError(err));
      setPhase("error");
    }
  }, []);

  const onInstall = useCallback(async () => {
    if (!pending) return;
    setPhase("downloading");
    setProgress({ done: 0, total: 0 });
    try {
      let total = 0;
      let done = 0;
      await pending.downloadAndInstall((e: unknown) => {
        const ev = e as { event?: string; data?: { contentLength?: number; chunkLength?: number } };
        if (ev.event === "Started") {
          total = ev.data?.contentLength ?? 0;
          setProgress({ done: 0, total });
        } else if (ev.event === "Progress") {
          done += ev.data?.chunkLength ?? 0;
          setProgress({ done, total });
        } else if (ev.event === "Finished") {
          setProgress({ done: total, total });
        }
      });
      // Windows 上一般走不到这里：安装步骤会自动退出 app。
      setPhase("done");
    } catch (err) {
      console.info("[updater] download/install failed:", err);
      setErrMsg(formatUpdaterError(err));
      setPhase("error");
    }
  }, [pending]);

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.done / progress.total) * 100))
      : null;

  return (
    <section style={sectionStyle}>
      <h3 style={h3Style}>关于与更新</h3>
      <div style={statusStyle}>
        当前版本：<strong>{version ? `v${version}` : "—"}</strong>
      </div>

      {phase === "latest" && (
        <div style={statusStyle}>✅ 已是最新版本。</div>
      )}

      {phase === "available" && (
        <div style={statusStyle}>
          🎉 发现新版本 <strong>v{newVersion}</strong>
          {notes ? (
            <div style={{ marginTop: 6, color: dark.textMuted, whiteSpace: "pre-wrap" }}>
              {notes}
            </div>
          ) : null}
        </div>
      )}

      {phase === "downloading" && (
        <div style={statusStyle}>
          正在下载并安装{pct !== null ? ` … ${pct}%` : "…"}
          <div style={{ marginTop: 6, color: dark.textMuted, fontSize: tokens.text.sm.size }}>
            安装时 Simple Harness 会自动关闭，请稍候它重新启动。
          </div>
        </div>
      )}

      {phase === "done" && (
        <div style={statusStyle}>✅ 更新已安装，重启后生效。</div>
      )}

      {phase === "error" && (
        <div style={{ ...statusStyle, borderLeft: `2px solid ${dark.danger}`, color: dark.danger }}>
          {errMsg}
        </div>
      )}

      <div style={btnRowStyle}>
        {phase === "available" ? (
          <button type="button" style={primaryBtnStyle} onClick={onInstall}>
            下载并安装 v{newVersion}
          </button>
        ) : (
          <button
            type="button"
            style={btnStyle}
            onClick={onCheck}
            disabled={phase === "checking" || phase === "downloading"}
          >
            {phase === "checking"
              ? "检查中…"
              : phase === "downloading"
                ? "安装中…"
                : "检查更新"}
          </button>
        )}
      </div>

      <p style={hintStyle}>
        点击后会联网检查是否有新版本。发现新版本时下载安装包并自动校验签名，
        确保来自官方发布。
      </p>
    </section>
  );
}

// ----------------------------------------------------------------------
// P4-S21 #13 — Auto mode toggle.
//
// MM-D4（2026-09-09）：复选框只渲染后端权威策略（workflow.db 的
// CapabilityStore，见 backend/main.py `_authorization_policy_snapshot`）。
// 快照到达前 `policy === null` = 加载中：复选框 disabled 且不勾选，任何
// 点击都不会被解释成对某个默认值的翻转。localStorage 只写不读，仅为
// CapabilityCenterPanel 的显示缓存保鲜——它不再参与本开关的初始状态。
// Auto 只处理策略判定可自动放行的请求：deny、健康与平台闸门仍然生效。
// ----------------------------------------------------------------------
export interface AuthorizationPolicyView {
  mode: "auto" | "manual";
  generation: number;
  provenance: string;
}

export function buildAutoModeGetMessage(requestId: string) {
  return {
    type: "permission_auto_mode_get",
    request_id: requestId,
    payload: {},
  };
}

/** MM-D4：写入必须显式携带目标模式，而不是「翻转我以为的当前值」。 */
export function buildAutoModeSetMessage(enabled: boolean, requestId: string) {
  return {
    type: "permission_auto_mode_set",
    request_id: requestId,
    payload: { enabled },
  };
}

export function readAutoModePolicy(
  message: IncomingMessage,
): AuthorizationPolicyView | null {
  if (message.type !== "permission_auto_mode_response") return null;
  const payload = (message.payload ?? {}) as {
    enabled?: unknown;
    mode?: unknown;
    generation?: unknown;
    provenance?: unknown;
  };
  const mode: "auto" | "manual" =
    payload.mode === "auto" || payload.mode === "manual"
      ? payload.mode
      : payload.enabled
        ? "auto"
        : "manual";
  const generation = Number(payload.generation);
  return {
    mode,
    generation: Number.isFinite(generation) && generation >= 0 ? generation : 0,
    provenance:
      typeof payload.provenance === "string" ? payload.provenance : "unknown",
  };
}

export function AutoModeToggle({
  getChannel,
}: {
  getChannel: () => ControlChannel | null;
}) {
  // null = 尚未拿到权威快照（加载中）。绝不用默认值占位。
  const [policy, setPolicy] = useState<AuthorizationPolicyView | null>(null);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const requestSeqRef = useRef(0);
  const policyRef = useRef<AuthorizationPolicyView | null>(null);

  const nextRequestId = useCallback((kind: "get" | "set") => {
    requestSeqRef.current += 1;
    return `permission-auto-mode-${kind}-${requestSeqRef.current}`;
  }, []);

  const applyPolicy = useCallback((next: AuthorizationPolicyView) => {
    // 代次单调：CAS 只会让 generation 递增，落后的快照（例如写入
    // 途中到达的旧 get 回执）不得把界面拉回写前状态。
    const current = policyRef.current;
    if (current && next.generation < current.generation) return;
    policyRef.current = next;
    setPolicy(next);
    try {
      localStorage.setItem("deskpet.auto_mode", String(next.mode === "auto"));
    } catch {
      /* localStorage full / disabled — 后端仍是权威，非致命 */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let unsubMessage: (() => void) | null = null;
    let unsubState: (() => void) | null = null;

    const requestCurrentPolicy = (ch: ControlChannel) => {
      if (cancelled) return;
      // ControlChannel.send() 在 socket 未 OPEN 时静默丢帧并返回 false
      // （ws/ControlChannel.ts），旧实现只捕获异常 → get 被丢掉后永不重发，
      // 界面就一直停在默认值上。这里必须检查返回值并重试。
      let sent = false;
      try {
        sent = ch.send(buildAutoModeGetMessage(nextRequestId("get")));
      } catch {
        sent = false;
      }
      if (!sent) {
        retryTimer = setTimeout(() => requestCurrentPolicy(ch), 500);
      }
    };

    const attach = () => {
      if (cancelled) return;
      const ch = getChannel();
      if (!ch) {
        retryTimer = setTimeout(attach, 500);
        return;
      }
      unsubMessage?.();
      unsubState?.();
      unsubMessage = ch.onMessage((msg: IncomingMessage) => {
        const next = readAutoModePolicy(msg);
        if (!next) return;
        applyPolicy(next);
        setPending(false);
        setErr(null);
      });
      unsubState = ch.onStateChange((state) => {
        if (state === "connected") requestCurrentPolicy(ch);
      });
      // 重连/重挂载时通道已缓存了每种类型的最新一帧，先用它渲染。
      const cached = ch.getLatestMessage("permission_auto_mode_response");
      const cachedPolicy = cached ? readAutoModePolicy(cached) : null;
      if (cachedPolicy) applyPolicy(cachedPolicy);
      requestCurrentPolicy(ch);
    };

    attach();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      unsubMessage?.();
      unsubState?.();
    };
  }, [applyPolicy, getChannel, nextRequestId]);

  const onToggle = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      // 加载中不可交互（input disabled），这里再兜一层：没有权威快照
      // 就不写。写入携带用户实际想要的目标模式，而不是 !enabled。
      if (!policyRef.current) return;
      const target = event.target.checked;
      setErr(null);
      setPending(true);
      try {
        const ch = getChannel();
        if (!ch) throw new Error("控制通道未连接");
        if (!ch.send(buildAutoModeSetMessage(target, nextRequestId("set")))) {
          throw new Error("控制通道未连接");
        }
      } catch (e) {
        setErr(String(e));
        setPending(false);
      }
    },
    [getChannel, nextRequestId],
  );

  const loading = policy === null;
  const checked = policy?.mode === "auto";
  const disabled = loading || pending;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <input
          type="checkbox"
          data-testid="permission-auto-mode"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
        />
        <span>
          自动模式（推荐）：自动处理符合策略的请求
        </span>
      </label>
      <span
        data-testid="permission-auto-mode-status"
        style={{ fontSize: 11, color: dark.textMuted }}
      >
        {loading
          ? "正在读取授权策略…"
          : pending
            ? "保存中…"
            : `当前：${policy.mode === "auto" ? "自动" : "手动"}` +
              `（策略代次 ${policy.generation} · ${policy.provenance}）`}
      </span>
      <p style={{ fontSize: 11, color: dark.textMuted, margin: 0, lineHeight: 1.5 }}>
        开启后，读写文件、运行命令、联网、技能安装等 Agent 工具权限会直接放行；
        能力安装/生成卡片会标记“Auto 已授权”以便审计，不再弹 Simple Harness 授权窗口。
        关闭后恢复逐项确认。这个开关不等于 Windows 管理员权限，也不会绕过系统级限制。
      </p>
      {err && <span style={{ color: dark.danger, fontSize: tokens.text.sm.size }}>{err}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------
// Agent active-execution budget setting.
//
// The compatibility key still uses the historical chat-turn-timeout name,
// but the backend now counts only durable active execution intervals. This
// asks for the persisted value and sends clamped updates immediately.
// ----------------------------------------------------------------------
export function ChatTurnTimeoutSetting({
  getChannel,
}: { getChannel: () => ControlChannel | null }) {
  const [turnTimeoutMin, setTurnTimeoutMin] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatus] = useState("正在读取…");
  const requestSeqRef = useRef(0);
  const activeRequestRef = useRef<string | null>(null);

  const nextRequestId = useCallback((kind: "get" | "set") => {
    requestSeqRef.current += 1;
    return `chat-turn-timeout-${kind}-${requestSeqRef.current}`;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let unsubMessage: (() => void) | null = null;
    let unsubState: (() => void) | null = null;

    const requestCurrentValue = (ch: ControlChannel) => {
      try {
        const requestId = nextRequestId("get");
        activeRequestRef.current = requestId;
        setStatus("正在读取…");
        if (!ch.send(buildChatTurnTimeoutGetMessage(requestId))) {
          throw new Error("控制通道尚未连接");
        }
      } catch {
        /* retry on reconnect or next mount */
      }
    };

    const attach = () => {
      if (cancelled) return;
      const ch = getChannel();
      if (!ch) {
        retryTimer = setTimeout(attach, 500);
        return;
      }

      unsubMessage = ch.onMessage((msg: IncomingMessage) => {
        if (msg.type !== "chat_turn_timeout_response") return;
        if (
          msg.request_id &&
          activeRequestRef.current &&
          msg.request_id !== activeRequestRef.current
        ) {
          return;
        }
        activeRequestRef.current = null;
        setTurnTimeoutMin(clampChatTurnTimeoutMinutes(msg.payload.minutes));
        setErr(null);
        setStatus("已与后端同步");
      });
      unsubState = ch.onStateChange((state) => {
        if (state === "connected") requestCurrentValue(ch);
      });
      requestCurrentValue(ch);
    };

    attach();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      unsubMessage?.();
      unsubState?.();
    };
  }, [getChannel, nextRequestId]);

  const onChange = useCallback(
    (raw: string) => {
      setErr(null);
      const next = clampChatTurnTimeoutMinutes(Number(raw));
      setTurnTimeoutMin(next);
      setStatus("保存中…");
      try {
        const ch = getChannel();
        if (!ch) throw new Error("控制通道未连接");
        const requestId = nextRequestId("set");
        activeRequestRef.current = requestId;
        if (!ch.send(buildChatTurnTimeoutSetMessage(next, requestId))) {
          throw new Error("控制通道未连接");
        }
      } catch (e) {
        setErr(String(e));
        setStatus("保存失败");
      }
    },
    [getChannel, nextRequestId],
  );

  return (
    <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
      <label
        htmlFor="chat-turn-timeout-minutes"
        style={{ fontSize: 13, fontWeight: 600, color: dark.text }}
      >
        Agent 有效执行预算（分钟）
      </label>
      <input
        id="chat-turn-timeout-minutes"
        type="number"
        min={CHAT_TURN_TIMEOUT_MIN_MINUTES}
        max={CHAT_TURN_TIMEOUT_MAX_MINUTES}
        step={1}
        value={turnTimeoutMin ?? ""}
        disabled={turnTimeoutMin === null}
        placeholder="读取中"
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: 120,
          padding: "5px 8px",
          borderRadius: 4,
          border: `1px solid ${dark.border}`,
          background: dark.card,
          color: dark.text,
          fontSize: 12,
          outline: "none",
        }}
        data-testid="chat-turn-timeout-minutes"
      />
      <p style={hintStyle}>
        只累计 Agent、模型和工具实际工作的时间；等待你确认、选择文件夹或完成外部操作时暂停。默认 15 分钟，范围 1-60。
      </p>
      <span
        data-testid="chat-turn-timeout-status"
        style={{ color: dark.textMuted, fontSize: 11 }}
      >
        {status}
      </span>
      {err && <span style={{ color: dark.danger, fontSize: tokens.text.sm.size }}>{err}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------
// P3-S9 — Danger Zone: 完全卸载（清除用户数据）.
//
// `完全卸载` wipes the resolved Simple Harness data dir (config / SQLite / logs). A
// second opt-in checkbox additionally wipes %LocalAppData%\deskpet\
// models — that's ~9 GB so we require explicit consent.
//
// Two-step confirm via the in-app dialog (useConfirm) keeps the UI trivial while still
// preventing single-click destruction.
// ----------------------------------------------------------------------
function DangerZoneSection() {
  const [includeModels, setIncludeModels] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDialog, ask] = useConfirm();

  const handlePurge = useCallback(async () => {
    setErr(null);
    const scope = includeModels ? "用户数据 + 本地模型缓存" : "用户数据（配置 / 数据库 / 日志）";
    // 2026-09-25：不能用 window.confirm——桌面 WebView 里它不弹框、直接当"确认"。
    const confirmed = await ask({
      title: "完全卸载",
      message:
        `即将删除：${scope}\n\n` +
        "这将清除所有聊天历史、任务记录、配置和日志，无法撤销。\n" +
        "删除完成后 Simple Harness 将自动退出。",
      confirm_label: "确认删除",
    });
    if (!confirmed) return;

    setBusy(true);
    try {
      const core = await import("@tauri-apps/api/core");
      await core.invoke("purge_user_data", { includeModels });
      // Rust will exit the app shortly; nothing else to do here.
    } catch (e) {
      setErr(typeof e === "string" ? e : (e as Error)?.message ?? String(e));
      setBusy(false);
    }
  }, [includeModels, ask]);

  return (
    <section style={{ ...sectionStyle, borderTop: `1px solid ${dark.borderStrong}` }}>
      {confirmDialog}
      <h3 style={{ ...h3Style, color: dark.danger }}>危险区</h3>
      <p style={hintStyle}>
        "完全卸载" 会清除 Simple Harness 当前解析到的用户数据目录（配置、SQLite、日志）。
        ⚠️ 若为 portable 安装（数据实际在安装目录的 <code>userdata/</code>）或你
        自定义过数据目录，此按钮可能删不到真正的数据——最可靠的做法是直接删除
        整个安装目录。卸载安装包本身仍需在「应用和功能」里进行。
      </p>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
        }}
      >
        <input
          type="checkbox"
          checked={includeModels}
          onChange={(e) => setIncludeModels(e.target.checked)}
          data-testid="purge-include-models"
        />
        <span>
          同时删除本地模型缓存（若已用 <code>DESKPET_MODEL_ROOT</code> 迁移到
          自定义目录，则删该目录）
        </span>
      </label>
      {err && (
        <div role="alert" style={{ ...statusStyle, color: dark.danger }}>
          {err}
        </div>
      )}
      <div style={btnRowStyle}>
        <button
          type="button"
          data-testid="purge-user-data"
          onClick={handlePurge}
          disabled={busy}
          style={{
            ...btnStyle,
            background: dark.danger,
            color: "white",
            borderColor: dark.danger,
          }}
        >
          {busy ? "删除中…" : "完全卸载（清除用户数据）"}
        </button>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// 2026-05-21 — 数据目录设置.
//
// Lets the user relocate %AppData%\deskpet to a roomier drive
// without leaving the app. Persistence uses a small bootstrap preference
// outside the movable data root; explicit launcher env vars remain
// higher-priority and are surfaced as externally pinned.
//
// Why a separate section vs. nesting under DangerZone: the relocate
// flow is reversible (you can always set the var back) so it doesn't
// belong with the truly destructive purge action. We do keep a
// soft confirmation before kicking off the file copy though, because
// "I clicked the wrong button and now my chat history moved" is a
// crap user experience even if it's not technically dangerous.
// ----------------------------------------------------------------------
export interface DataDirSetting {
  effective: string;
  default: string | null;
  env_override: string | null;
  preference: string | null;
  externally_pinned: boolean;
  effective_exists: boolean;
  effective_size_bytes: number;
}

/**
 * Saving the bootstrap preference does not relocate the running backend.
 * Keep the current-process facts stable until the application restarts and
 * only surface the newly persisted next-launch preference.
 */
export function withNextLaunchDataDirPreference(
  current: DataDirSetting,
  updated: DataDirSetting,
): DataDirSetting {
  return {
    ...current,
    preference: updated.preference,
  };
}

export function isDataDirPreferenceNoop(
  current: DataDirSetting,
  target: string,
): boolean {
  return current.effective === target && current.preference === target;
}

// ---------------------------------------------------------------------------
// 任务与会话数据（2026-09-25 主流程优化条目 7）
//
// 用户决定：任务里 Agent 的会话数据（聊天记录、检索索引、检查记录）永久保留供审计，
// 不做自动回收、不做清理按钮。这里只显示占用，超过阈值（默认 5 GB）给一条提醒，
// 引导用户去「数据目录」迁移到更大的磁盘。数字由后台统计（orchestration_storage_get）。
// ---------------------------------------------------------------------------

export interface StorageUsageData {
  bytes: number;
  measured_at_ms: number | null;
  warn_bytes: number;
  over_warn: boolean;
  measured: boolean;
  breakdown: { key: string; label: string; bytes: number }[];
}

export function buildStorageUsageMessage(requestId: string, refresh = false) {
  return { type: "orchestration_storage_get", request_id: requestId, payload: refresh ? { refresh: true } : {} };
}

export function formatGb(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return formatMb(bytes);
}

export function StorageUsageSection({ getChannel }: { getChannel: () => ControlChannel | null }) {
  const [data, setData] = useState<StorageUsageData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const counter = useRef(0);

  const request = useCallback((refresh: boolean) => {
    const ch = getChannel();
    if (!ch) { setError("连接不可用"); return; }
    const id = `storage-${Date.now()}-${++counter.current}`;
    pending.current = id;
    setBusy(true);
    setError(null);
    if (!ch.send(buildStorageUsageMessage(id, refresh))) { setBusy(false); setError("连接不可用，请求未发送"); }
  }, [getChannel]);

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const off = ch.onMessage((raw: IncomingMessage) => {
      // 编排通道的应答不在 IncomingMessage 联合里（同其他编排视图一样宽松读取）
      const msg = raw as unknown as { type?: string; payload?: unknown };
      if (msg.type !== "orchestration_storage_get_response") return;
      const payload = (msg.payload ?? {}) as { ok?: boolean; request_id?: unknown; data?: StorageUsageData; error?: string };
      if (payload.request_id !== pending.current) return;
      setBusy(false);
      if (payload.ok === false || !payload.data) { setError(payload.error ?? "读取失败"); return; }
      setData(payload.data);
    });
    request(false);
    return () => { off(); };
  }, [getChannel, request]);

  const over = !!data?.over_warn;
  return (
    <section style={sectionStyle} data-testid="storage-usage">
      <h3 style={h3Style}>任务与会话数据</h3>
      {over && data ? (
        <div role="alert" data-testid="storage-usage-warning" style={{ ...hintStyle, color: dark.warning, border: `1px solid ${dark.warning}`, borderRadius: 6, padding: "6px 10px" }}>
          已超过 {formatGb(data.warn_bytes)}。这些数据会一直保留，请到「数据目录」迁移到更大的磁盘。{" "}
          <button type="button" style={{ ...btnStyle, marginLeft: 6 }} onClick={() => document.getElementById("settings-data-dir")?.scrollIntoView({ behavior: "smooth" })}>去数据目录</button>
        </div>
      ) : null}
      <p style={hintStyle}>
        {data ? (data.measured ? `任务与会话数据：${formatGb(data.bytes)}` : "任务与会话数据：尚未统计") : (error ?? "读取中…")}
        （含聊天记录、检索索引和检查记录；用于审计，不会自动删除）
      </p>
      {data?.measured ? (
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 10, rowGap: 2, fontSize: 12, color: dark.textMuted }}>
          {data.breakdown.map((row) => (<Fragment key={row.key}><div>{row.label}</div><div>{formatGb(row.bytes)}</div></Fragment>))}
        </div>
      ) : null}
      <div>
        <button type="button" data-testid="storage-usage-refresh" style={btnStyle} disabled={busy} onClick={() => request(true)}>
          {busy ? "统计中…" : "重新统计"}
        </button>
        {error && data ? <span style={{ ...hintStyle, marginLeft: 8 }}>{error}</span> : null}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 任务发布目录（NEXT-TG-1.0 §9）：带"发布"要求的任务只能把验收过的文件发布到这里。
// 后台校验目录存在、能硬链接、不与应用自己的数据目录重叠；保存后编排服务会重启一次
// （进行中的任务暂停几秒后自动接着做）。清空 = 撤销授权，已经发布的文件不动。
// ---------------------------------------------------------------------------

export type PublishDirData = {
  configured: string;
  publish: { enabled?: boolean; root?: string; reason?: string };
  active_missions: number;
};

// eslint-disable-next-line react-refresh/only-export-components -- message builder shared with its test
export function buildPublishDirMessage(requestId: string, path?: string) {
  return path === undefined
    ? { type: "orchestration_publish_dir_get", request_id: requestId, payload: {} }
    : { type: "orchestration_publish_dir_set", request_id: requestId, payload: { path } };
}

export function PublishDirSection({ getChannel }: { getChannel: () => ControlChannel | null }) {
  const [confirmDialog, ask] = useConfirm();
  const [data, setData] = useState<PublishDirData | null>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const counter = useRef(0);
  const filled = useRef(false);

  const send = useCallback((next?: string) => {
    const ch = getChannel();
    if (!ch) { setError("连接不可用"); return; }
    const id = `publish-dir-${Date.now()}-${++counter.current}`;
    pending.current = id;
    setBusy(true);
    setError(null);
    if (!ch.send(buildPublishDirMessage(id, next))) { setBusy(false); setError("连接不可用，请求未发送"); }
  }, [getChannel]);

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const off = ch.onMessage((raw: IncomingMessage) => {
      const msg = raw as unknown as { type?: string; payload?: unknown };
      if (msg.type !== "orchestration_publish_dir_get_response" && msg.type !== "orchestration_publish_dir_set_response") return;
      const payload = (msg.payload ?? {}) as { ok?: boolean; request_id?: unknown; data?: PublishDirData; error?: string };
      if (payload.request_id !== pending.current) return;
      pending.current = null;
      setBusy(false);
      if (payload.ok !== true || !payload.data) { setError(payload.error ?? "读取失败"); return; }
      setData(payload.data);
      if (!filled.current) { filled.current = true; setPath(payload.data.configured); }
      if (msg.type === "orchestration_publish_dir_set_response") {
        setPath(payload.data.configured);
        setDone(payload.data.configured ? "已授权，新建带发布的任务可以用了" : "已撤销授权");
      }
    });
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the first read starts on mount (sets "读取中…")
    send();
    return () => { off(); };
  }, [getChannel, send]);

  const pick = useCallback(async () => {
    try {
      const core = await import("@tauri-apps/api/core");
      const picked = await core.invoke<string | null>("open_directory_dialog");
      if (picked) setPath(picked);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const save = useCallback(async (next: string) => {
    setDone(null);
    const running = data?.active_missions ?? 0;
    const ok = await ask({
      title: next ? "授权这个发布目录？" : "撤销发布目录授权？",
      message: (next ? `带"发布"要求的任务会把验收过的文件发布到：${next}。` : "之后带发布要求的任务将无法发布；已经发布的文件不会被改动。")
        + (running > 0 ? `保存后任务服务会重启一次，正在进行的 ${running} 个任务会暂停几秒后自动接着做。` : "保存后任务服务会重启一次。"),
      confirm_label: next ? "授权" : "撤销", variant: next ? "primary" : "danger",
    });
    if (ok) send(next);
  }, [ask, data, send]);

  const publish = data?.publish ?? {};
  return (
    <section style={sectionStyle} data-testid="publish-dir">
      {confirmDialog}
      <h3 style={h3Style}>任务发布目录</h3>
      <p style={hintStyle}>任务里要求"发布到…"时，系统只会把审核通过的文件发布到这个目录（每次发布都要你批准）。</p>
      <div role="status" data-testid="publish-dir-status" style={{ ...statusStyle, color: publish.enabled ? dark.text : dark.textMuted }}>
        {data === null ? (error ?? "读取中…")
          : publish.enabled ? `已授权：${publish.root}`
          : data.configured ? `未生效：${publish.reason ?? "原因未知"}（${data.configured}）`
          : "未授权：带发布要求的任务无法发布"}
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <input type="text" aria-label="发布目录路径" value={path} disabled={busy}
          onChange={(e) => setPath(e.target.value)} placeholder="/Users/你/Documents/发布"
          style={{ flex: 1, padding: "5px 8px", borderRadius: 4, border: `1px solid ${dark.border}`, background: dark.card, color: dark.text, fontSize: 12, fontFamily: "monospace" }} />
        <button type="button" style={btnStyle} disabled={busy} onClick={pick}>选择目录…</button>
      </div>
      <div style={btnRowStyle}>
        <button type="button" data-testid="publish-dir-save" style={primaryBtnStyle}
          disabled={busy || !path.trim() || path.trim() === (data?.configured ?? "")} onClick={() => save(path.trim())}>
          {busy ? "处理中…" : "保存并授权"}
        </button>
        {data?.configured ? (
          <button type="button" data-testid="publish-dir-revoke" style={btnStyle} disabled={busy} onClick={() => save("")}>撤销授权</button>
        ) : null}
      </div>
      {error && data ? <p role="alert" style={{ ...hintStyle, color: dark.danger }}>{error}</p> : null}
      {done ? <p style={hintStyle}>{done}</p> : null}
    </section>
  );
}

// ================ 任务技能目录 (NEXT-TG-1.0 §11) ================
// 所有任务执行档位共用一个技能目录：在这里安装一次，各档位看到同一版本；暂停/退役一次，
// 各档位下一次使用就生效。准入要先经过评估（评估入口尚未接到界面，安装后显示"待评估"）。

export type SkillPoolView = { state: string | null; usable: boolean; reasons: string[] };
export type SkillCatalogueRow = {
  skill_ref: Record<string, unknown>; skill_id: string; version: number; name: string; description: string;
  state: string | null; pools: Record<string, SkillPoolView>;
};
export type SkillCatalogueData = { owner: string | null; members: string[]; skills: SkillCatalogueRow[] };

const SKILL_STATE: Record<string, string> = {
  QUARANTINED: "待评估", TRIAL: "评估中", ADMITTED: "可用", SUSPENDED: "已暂停", RETIRED: "已退役",
};

// eslint-disable-next-line react-refresh/only-export-components -- shared with its test
export function poolLabel(profileId: string): string {
  const m = /-(\d+)k-(thinking-)?v\d+$/.exec(profileId);
  return m ? `${m[1]}K${m[2] ? " 思考" : ""}` : profileId;
}

// eslint-disable-next-line react-refresh/only-export-components -- shared with its test
export function poolStatus(view: SkillPoolView): string {
  if (view.usable) return "可用";
  if (view.reasons.includes("DEPENDENCY_UNRESOLVED")) return "不可用（这个档位缺少所需工具）";
  if (view.reasons.includes("NOT_MIRRORED")) return "不可用（尚未同步）";
  return `不可用（${SKILL_STATE[view.state ?? ""] ?? "未知状态"}）`;
}

type SkillReply = { ok?: boolean; request_id?: unknown; data?: unknown; error?: string };

export function SkillCatalogueSection({ getChannel }: { getChannel: () => ControlChannel | null }) {
  const [confirmDialog, ask] = useConfirm();
  const [data, setData] = useState<SkillCatalogueData | null>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const counter = useRef(0);

  const send = useCallback((type: string, payload: Record<string, unknown>) => {
    const ch = getChannel();
    if (!ch) { setError("连接不可用"); return; }
    const id = `skill-${Date.now()}-${++counter.current}`;
    pending.current = id;
    setBusy(true);
    setError(null);
    if (!ch.send({ type, request_id: id, payload })) { setBusy(false); setError("连接不可用，请求未发送"); }
  }, [getChannel]);

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const types = ["orchestration_skill_catalogue_response", "orchestration_skill_install_file_response", "orchestration_skill_lifecycle_response"];
    const off = ch.onMessage((raw: IncomingMessage) => {
      const msg = raw as unknown as { type?: string; payload?: SkillReply };
      if (!msg.type || !types.includes(msg.type)) return;
      const payload = msg.payload ?? {};
      if (payload.request_id !== pending.current) return;
      pending.current = null;
      setBusy(false);
      if (payload.ok !== true || !payload.data) { setError(payload.error ?? "操作失败"); return; }
      if (msg.type === "orchestration_skill_catalogue_response") { setData(payload.data as SkillCatalogueData); return; }
      const body = payload.data as { response?: { error?: { code?: string } | null }; catalogue?: SkillCatalogueData };
      if (body.catalogue) setData(body.catalogue);
      const refused = body.response?.error?.code;
      if (refused) { setError(`操作被拒：${refused}`); return; }
      setDone(msg.type === "orchestration_skill_install_file_response" ? "已安装，各档位同步为同一版本（需评估准入后才能使用）" : "已完成，各档位同步生效");
    });
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the first read starts on mount (sets "读取中…")
    send("orchestration_skill_catalogue", {});
    return () => { off(); };
  }, [getChannel, send]);

  const act = useCallback(async (row: SkillCatalogueRow, action: "SUSPEND" | "RESUME" | "RETIRE") => {
    setDone(null);
    if (action === "RETIRE") {
      const ok = await ask({ title: `退役技能 ${row.name}？`, message: "退役后所有档位都不能再使用这个版本，且不能恢复。", confirm_label: "确认退役", variant: "danger" });
      if (!ok) return;
    }
    send("orchestration_skill_lifecycle", { skill_ref: row.skill_ref, action, command_id: `skill-${action.toLowerCase()}-${row.skill_id}-${row.version}-${Date.now()}` });
  }, [ask, send]);

  return (
    <section style={sectionStyle} data-testid="skill-catalogue">
      {confirmDialog}
      <h3 style={h3Style}>任务技能目录</h3>
      <p style={hintStyle}>所有任务执行档位共用这一个技能目录：安装一次，各档位看到同一版本；暂停或退役一次，各档位下一次使用即生效。</p>
      <div role="status" data-testid="skill-catalogue-status" style={statusStyle}>
        {data === null ? (error ?? "读取中…") : data.skills.length === 0 ? "还没有技能" : `共 ${data.skills.length} 个技能版本`}
      </div>
      {data?.skills.map((row) => (
        <div key={`${row.skill_id}@${row.version}`} data-testid="skill-row" style={{ borderTop: `1px solid ${dark.border}`, padding: "6px 0" }}>
          <div style={{ fontSize: 13, color: dark.text }}>
            {row.name} <span style={{ color: dark.textMuted }}>第 {row.version} 版 · {SKILL_STATE[row.state ?? ""] ?? "未知"}</span>
          </div>
          {row.description ? <div style={{ ...hintStyle, marginTop: 2 }}>{row.description}</div> : null}
          <ul style={{ margin: "4px 0", paddingLeft: 18, fontSize: 12, color: dark.textMuted }}>
            {Object.entries(row.pools).map(([pool, view]) => (
              <li key={pool}>{poolLabel(pool)}：{poolStatus(view)}</li>
            ))}
          </ul>
          <div style={btnRowStyle}>
            {row.state === "ADMITTED" ? <button type="button" style={btnStyle} disabled={busy} onClick={() => act(row, "SUSPEND")}>暂停</button> : null}
            {row.state === "SUSPENDED" ? <button type="button" style={btnStyle} disabled={busy} onClick={() => act(row, "RESUME")}>恢复</button> : null}
            {row.state !== "RETIRED" ? <button type="button" style={btnStyle} disabled={busy} onClick={() => act(row, "RETIRE")}>退役</button> : null}
          </div>
        </div>
      ))}
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <input type="text" aria-label="技能包路径" value={path} disabled={busy}
          onChange={(e) => setPath(e.target.value)} placeholder="/Users/你/Downloads/某技能.zip"
          style={{ flex: 1, padding: "5px 8px", borderRadius: 4, border: `1px solid ${dark.border}`, background: dark.card, color: dark.text, fontSize: 12, fontFamily: "monospace" }} />
        <button type="button" data-testid="skill-install" style={primaryBtnStyle} disabled={busy || !path.trim()}
          onClick={() => { setDone(null); send("orchestration_skill_install_file", { path: path.trim() }); }}>
          {busy ? "处理中…" : "安装"}
        </button>
      </div>
      {error && data ? <p role="alert" style={{ ...hintStyle, color: dark.danger }}>{error}</p> : null}
      {done ? <p style={hintStyle}>{done}</p> : null}
    </section>
  );
}

function formatMb(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}

function DataDirSection() {
  const [confirmDialog, ask] = useConfirm();
  const [setting, setSetting] = useState<DataDirSetting | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  const [moveData, setMoveData] = useState(true);
  const [busy, setBusy] = useState(false);
  const [opMsg, setOpMsg] = useState<string | null>(null);
  const [opErr, setOpErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoadErr(null);
    try {
      const core = await import("@tauri-apps/api/core");
      const s = await core.invoke<DataDirSetting>("get_data_dir_setting");
      setSetting(s);
      // Pre-fill the input with the current effective path so the
      // user can edit-in-place rather than retype from scratch.
      if (!newPath) setNewPath(s.effective);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
    // Intentionally not depending on newPath — we only want this to
    // pre-fill on the very first load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const handlePickDir = useCallback(async () => {
    setOpErr(null);
    try {
      const core = await import("@tauri-apps/api/core");
      const picked = await core.invoke<string | null>("open_directory_dialog");
      if (picked) setNewPath(picked);
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const handleApply = useCallback(async () => {
    setOpErr(null);
    setOpMsg(null);
    if (!setting) return;
    if (setting.externally_pinned) {
      setOpErr(
        "当前目录由启动环境变量固定；请移除 DESKPET_USER_DATA_DIR/DESKPET_USER_DATA 后重启，再从这里切换。",
      );
      return;
    }
    const target = newPath.trim();
    if (!target) {
      setOpErr("请先选择或输入新路径");
      return;
    }
    if (isDataDirPreferenceNoop(setting, target)) {
      setOpErr("新路径与当前路径相同，无需修改");
      return;
    }
    const sizeStr = formatMb(setting.effective_size_bytes);
    const confirmed = await ask({
      title: "切换数据目录",
      message:
        `即将把数据目录切换为：\n${target}\n\n` +
        (moveData
          ? `并将现有数据（约 ${sizeStr}）从\n${setting.effective}\n复制并删除原位置文件。\n\n`
          : "（不移动现有数据 — 旧目录保留，新目录从空开始）\n\n") +
        "Simple Harness 需要重启才能完全生效。",
      confirm_label: "确认切换",
    });
    if (!confirmed) return;

    setBusy(true);
    try {
      const core = await import("@tauri-apps/api/core");
      // Persist the bootstrap pointer first so the next launch resolves the
      // same target before the backend is spawned.
      const updated = await core.invoke<DataDirSetting>(
        "set_data_dir_preference",
        { newPath: target },
      );
      setSetting(withNextLaunchDataDirPreference(setting, updated));

      if (moveData && setting.effective_exists && setting.effective !== target) {
        const moved = await core.invoke<number>("move_data_dir_contents", {
          src: setting.effective,
          dst: target,
        });
        setOpMsg(
          `已保存并移动 ${formatMb(moved)} 数据。请重启 Simple Harness 让所有进程读到新路径。`,
        );
      } else {
        setOpMsg(
          "已保存。下次启动时 Simple Harness 会从新路径读写。",
        );
      }
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [setting, newPath, moveData, ask]);

  const handleReset = useCallback(async () => {
    if (!setting) return;
    if (setting.externally_pinned) {
      setOpErr(
        "当前目录由启动环境变量固定，应用内不能覆盖；请先移除外部环境变量。",
      );
      return;
    }
    const confirmed = await ask({
      title: "恢复默认数据目录",
      message:
        `将数据目录偏好切回默认路径：\n${setting.default ?? "（平台默认）"}\n\n` +
        "注意：现有数据不会被自动搬回去 —— 你需要手动移动，或先在上方填入默认路径并勾选「移动」。",
      confirm_label: "确认恢复",
      variant: "primary",
    });
    if (!confirmed) return;
    setBusy(true);
    setOpErr(null);
    setOpMsg(null);
    try {
      const core = await import("@tauri-apps/api/core");
      // Persist the platform default as the next-launch preference.
      if (setting.default) {
        const updated = await core.invoke<DataDirSetting>(
          "set_data_dir_preference",
          { newPath: setting.default },
        );
        setSetting(withNextLaunchDataDirPreference(setting, updated));
        setNewPath(setting.default);
        setOpMsg("已切回默认目录设置。请重启 Simple Harness 生效。");
      } else {
        setOpErr("无法识别默认目录（%AppData% 未设置？）");
      }
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [setting, ask]);

  if (loadErr) {
    return (
      <section style={sectionStyle}>
        <h3 style={h3Style}>数据目录</h3>
        <div style={{ ...statusStyle, color: dark.danger }}>
          加载失败：{loadErr}
        </div>
      </section>
    );
  }
  if (!setting) {
    return (
      <section style={sectionStyle}>
        <h3 style={h3Style}>数据目录</h3>
        <div style={statusStyle}>加载中…</div>
      </section>
    );
  }

  return (
    <section id="settings-data-dir" style={sectionStyle}>
      {confirmDialog}
      <h3 style={h3Style}>数据目录</h3>
      <p style={hintStyle}>
        Simple Harness 的聊天历史、配置、SQLite 数据库和设备 ID 都保存在这里。
        如果 C 盘空间紧张，可以搬到其他磁盘。
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          columnGap: 10,
          rowGap: 4,
          fontSize: 12,
        }}
      >
        <div style={{ color: dark.textMuted }}>当前生效</div>
        <div style={{ fontFamily: "monospace" }}>
          {setting.effective}{" "}
          <span style={{ color: dark.textMuted }}>
            ({formatMb(setting.effective_size_bytes)})
          </span>
        </div>

        <div style={{ color: dark.textMuted }}>环境变量</div>
        <div style={{ fontFamily: "monospace" }}>
          {setting.env_override ?? (
            <span style={{ color: dark.textFaint }}>(未设置 — 使用默认路径)</span>
          )}
        </div>

        <div style={{ color: dark.textMuted }}>已保存偏好</div>
        <div style={{ fontFamily: "monospace" }}>
          {setting.preference ?? (
            <span style={{ color: dark.textFaint }}>(未设置)</span>
          )}
        </div>

        <div style={{ color: dark.textMuted }}>默认路径</div>
        <div style={{ fontFamily: "monospace", color: dark.textMuted }}>
          {setting.default ?? "—"}
        </div>
      </div>

      {setting.externally_pinned && (
        <div role="status" style={{ ...statusStyle, color: dark.warning }}>
          当前目录由启动环境变量固定。为避免显示“已保存”但重启仍被覆盖，应用内切换已禁用。
        </div>
      )}

      <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
        <label style={{ fontSize: 12, color: dark.textMuted }}>新路径</label>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            type="text"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            disabled={busy || setting.externally_pinned}
            placeholder="F:\deskpet\data"
            style={{
              flex: 1,
              padding: "5px 8px",
              borderRadius: 4,
              border: `1px solid ${dark.border}`,
              background: dark.card,
              color: dark.text,
              fontSize: 12,
              fontFamily: "monospace",
              outline: "none",
            }}
            data-testid="data-dir-input"
          />
          <button
            type="button"
            onClick={handlePickDir}
            disabled={busy || setting.externally_pinned}
            style={btnStyle}
          >
            浏览…
          </button>
        </div>
        <label
          style={{
            display: "flex",
            gap: 6,
            alignItems: "center",
            fontSize: 12,
            color: dark.textMuted,
          }}
        >
          <input
            type="checkbox"
            checked={moveData}
            onChange={(e) => setMoveData(e.target.checked)}
            disabled={busy || setting.externally_pinned}
          />
          <span>同时移动现有数据到新位置（推荐）</span>
        </label>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 4, flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={handleApply}
          disabled={busy || setting.externally_pinned}
          style={{
            ...btnStyle,
            background: dark.accent,
            color: "white",
            borderColor: dark.accent,
          }}
          data-testid="data-dir-apply"
        >
          {busy ? "处理中…" : "应用"}
        </button>
        <button
          type="button"
          onClick={handleReset}
          disabled={busy || setting.externally_pinned}
          style={btnStyle}
          data-testid="data-dir-reset"
        >
          恢复默认
        </button>
        <button
          type="button"
          onClick={reload}
          disabled={busy}
          style={btnStyle}
        >
          刷新
        </button>
      </div>

      {opMsg && (
        <div
          role="status"
          style={{
            ...statusStyle,
            background: "transparent",
            border: `1px solid ${dark.borderStrong}`,
            borderLeft: `2px solid ${dark.success}`,
            color: dark.success,
          }}
        >
          {opMsg}
        </div>
      )}
      {opErr && (
        <div role="alert" style={{ ...statusStyle, color: dark.danger }}>
          {opErr}
        </div>
      )}
    </section>
  );
}

// ---- inline styles (kept local so the panel has no CSS imports to wire) ----

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: dark.scrim,
  display: "grid",
  placeItems: "center",
  padding: tokens.space.md,
  zIndex: 1000,
  animation: `bp-fade-in ${tokens.duration.base}ms ${tokens.easing.out}`,
};

const panelStyle: React.CSSProperties = {
  background: dark.panel,
  padding: `0 ${tokens.space.xl}px ${tokens.space.xl}px`,
  borderRadius: tokens.radius.lg,
  width: "min(94vw, 560px)",
  boxSizing: "border-box",
  maxHeight: "92vh",
  overflowY: "auto",
  overflowX: "hidden",
  color: dark.text,
  border: `1px solid ${dark.hairline}`,
  boxShadow: tokens.shadow.overlay,
  fontFamily: tokens.font.ui,
  animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
};

// T11 page variant：填满工作台内容区（无模态圆角/边框/阴影/弹入动画），
// 自身滚动；宽度上限交给内容区布局。
const pagePanelStyle: React.CSSProperties = {
  background: dark.bgSolid,
  padding: `0 ${tokens.space.xl}px ${tokens.space.xxl}px`,
  width: "100%",
  height: "100%",
  boxSizing: "border-box",
  overflowY: "auto",
  overflowX: "hidden",
  color: dark.text,
  fontFamily: tokens.font.ui,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  position: "sticky",
  top: 0,
  zIndex: 2,
  margin: `0 -${tokens.space.xl}px ${tokens.space.lg}px`,
  padding: `${tokens.space.lg}px ${tokens.space.xl}px`,
  background: dark.panel,
  borderBottom: `1px solid ${dark.hairline}`,
};

const closeBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: tokens.controlHeight,
  height: tokens.controlHeight,
  background: "transparent",
  border: `1px solid ${dark.border}`,
  borderRadius: tokens.radius.md,
  cursor: "pointer",
  color: dark.textMuted,
  transition,
};

const sectionStyle: React.CSSProperties = {
  borderTop: `1px solid ${dark.hairline}`,
  paddingTop: tokens.space.xl,
  marginTop: tokens.space.xl,
  display: "grid",
  gap: tokens.space.md,
  maxWidth: 760,
};

const h3Style: React.CSSProperties = {
  margin: 0,
  fontSize: tokens.text.md.size,
  color: dark.text,
  fontWeight: tokens.weight.semibold,
  letterSpacing: tokens.tracking.tight,
};


const btnRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

const btnStyle: React.CSSProperties = {
  height: tokens.controlHeight,
  padding: `0 ${tokens.space.lg}px`,
  borderRadius: tokens.radius.md,
  border: `1px solid ${dark.borderStrong}`,
  background: "transparent",
  color: dark.text,
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
  fontWeight: tokens.weight.medium,
  cursor: "pointer",
  transition,
  boxSizing: "border-box",
};

/** 每屏只有一个主按钮用实心强调色。 */
const primaryBtnStyle: React.CSSProperties = {
  ...btnStyle,
  border: "1px solid transparent",
  background: dark.accent,
  color: dark.onAccent,
  fontWeight: tokens.weight.semibold,
};

const statusStyle: React.CSSProperties = {
  fontSize: tokens.text.sm.size,
  padding: `${tokens.space.sm}px ${tokens.space.md}px`,
  background: dark.card,
  border: `1px solid ${dark.insetBorder}`,
  borderRadius: tokens.radius.md,
  lineHeight: tokens.text.sm.lh,
};

const hintStyle: React.CSSProperties = {
  fontSize: tokens.text.sm.size,
  color: dark.textMuted,
  margin: 0,
  lineHeight: tokens.text.sm.lh,
};
