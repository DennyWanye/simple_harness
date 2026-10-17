// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { DailyBudgetStatus, IncomingMessage } from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";

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

export function buildChatTurnTimeoutSetMessage(minutes: number, requestId?: string) {
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

// ----------------------------------------------------------------------
// Auto mode toggle.
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

export function readAutoModePolicy(message: IncomingMessage): AuthorizationPolicyView | null {
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
    provenance: typeof payload.provenance === "string" ? payload.provenance : "unknown",
  };
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

export function isDataDirPreferenceNoop(current: DataDirSetting, target: string): boolean {
  return current.effective === target && current.preference === target;
}

export function buildStorageUsageMessage(requestId: string, refresh = false) {
  return {
    type: "orchestration_storage_get",
    request_id: requestId,
    payload: refresh ? { refresh: true } : {},
  };
}

export function formatGb(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return formatMb(bytes);
}

/** "2026-10" → "2026 年 10 月"；认不出的原样返回。 */
export function formatMonth(month: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  return m ? `${m[1]} 年 ${Number(m[2])} 月` : month;
}

/** token 数按中文习惯：≥1 亿写"亿"，≥1 万写"万"，最多两位小数，整数不带小数。 */
export function formatTokens(value: number): string {
  const fixed = (n: number) =>
    Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, "");
  if (value >= 100_000_000) return `${fixed(Math.round(value / 1_000_000) / 100)} 亿`;
  if (value >= 10_000) return `${fixed(Math.round(value / 100) / 100)} 万`;
  return String(value);
}

export function formatMb(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}
