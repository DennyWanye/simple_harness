// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useSessionsStore } from "../stores/sessionsStore";
import type { CompanionDecision, CompanionEvent } from "../types/messages";
import type { SlashCommandResult } from "./controlWsTypes";

export const pendingRunStatusQueries = new Map<
  string,
  { sid: string; runId: string; timer: ReturnType<typeof setTimeout> }
>();

export function companionEventFrom(value: unknown): CompanionEvent | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const outer = value as Record<string, unknown>;
  const raw =
    outer.type === "companion_event" &&
    outer.payload &&
    typeof outer.payload === "object" &&
    !Array.isArray(outer.payload)
      ? (outer.payload as Record<string, unknown>)
      : outer;
  const notificationSource = (
    raw.notification && typeof raw.notification === "object" ? raw.notification : raw
  ) as Record<string, unknown>;
  const event: CompanionEvent = {
    event_id: String(raw.event_id || raw.projection_event_id || ""),
    profile_id: String(raw.profile_id || ""),
    profile_generation: Number(raw.profile_generation || 0),
    session_id: String(raw.session_id || ""),
    seq: Number(raw.seq ?? 0),
    route_version: raw.route_version === undefined ? undefined : Number(raw.route_version),
    redaction_version:
      raw.redaction_version === undefined ? undefined : Number(raw.redaction_version),
    importance: raw.importance === "important" ? "important" : "normal",
    occurred_at: typeof raw.occurred_at === "string" ? raw.occurred_at : undefined,
    received_at: typeof raw.received_at === "number" ? raw.received_at : Date.now(),
    notification: {
      notification_id: String(notificationSource.notification_id || ""),
      kind: String(notificationSource.kind || "growth_notice"),
      summary: String(notificationSource.summary || ""),
      detail_ref: String(notificationSource.detail_ref || ""),
      detail_version: String(notificationSource.detail_version || ""),
      available_actions: Array.isArray(notificationSource.available_actions)
        ? notificationSource.available_actions.map(String)
        : [],
    },
    decision:
      raw.decision && typeof raw.decision === "object"
        ? (raw.decision as CompanionDecision)
        : undefined,
    tombstone: raw.tombstone === true,
  };
  if (
    !event.event_id ||
    !event.profile_id ||
    !Number.isInteger(event.profile_generation) ||
    event.profile_generation < 1 ||
    !event.session_id ||
    !Number.isInteger(event.seq) ||
    event.seq < 0 ||
    !event.notification.notification_id
  ) {
    return null;
  }
  return event;
}

export function persistedWorkflowFileArtifact(
  workflowEvent: unknown,
): Record<string, unknown> | null {
  if (!workflowEvent || typeof workflowEvent !== "object" || Array.isArray(workflowEvent)) {
    return null;
  }
  const payload = (workflowEvent as Record<string, unknown>).payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const payloadRecord = payload as Record<string, unknown>;
  const nestedPayload = payloadRecord.payload;
  const nestedPayloadRecord =
    nestedPayload && typeof nestedPayload === "object" && !Array.isArray(nestedPayload)
      ? (nestedPayload as Record<string, unknown>)
      : null;

  // Older workflow rows wrapped the intent payload once more than newer rows.
  // Accept both persisted shapes, but never recursively scan arbitrary payloads.
  const candidates = [nestedPayloadRecord?.artifact, payloadRecord.artifact];
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) continue;
    const artifact = candidate as Record<string, unknown>;
    if (artifact.kind !== "file") continue;
    const path = typeof artifact.path === "string" ? artifact.path.trim() : "";
    if (!path) continue;
    return { ...artifact, kind: "file", path };
  }
  return null;
}

export function publicToolErrorText(value: unknown): string | undefined {
  if (typeof value === "string") return value.trim() || undefined;
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  try {
    return JSON.stringify(value);
  } catch {
    return undefined;
  }
}

export function persistedToolOutcome(text: unknown): {
  ok: boolean;
  error?: string;
  toolName?: string;
} {
  let parsed: Record<string, unknown> | null = null;
  try {
    const value = JSON.parse(String(text || ""));
    if (value && typeof value === "object" && !Array.isArray(value)) {
      parsed = value as Record<string, unknown>;
    }
  } catch {
    parsed = null;
  }
  if (!parsed) return { ok: true };

  const durableMeta = parsed._deskpet_tool_outcome_v1;
  const meta =
    durableMeta && typeof durableMeta === "object" && !Array.isArray(durableMeta)
      ? (durableMeta as Record<string, unknown>)
      : null;
  const outcomeValue = parsed.outcome;
  const outcome = typeof outcomeValue === "string" ? outcomeValue.trim().toLowerCase() : "";
  const explicitOk = typeof parsed.ok === "boolean" ? parsed.ok : undefined;
  const status = typeof parsed.status === "string" ? parsed.status.trim().toLowerCase() : "";
  const successfulOutcomes = ["succeeded"];
  const failedOutcomes = ["partial", "rejected", "failed", "unknown"];
  const successfulStatuses = ["succeeded", "completed", "ok"];
  const failedStatuses = ["partial", "rejected", "failed", "unknown", "cancelled", "denied"];
  const legacyOutcome = typeof meta?.outcome === "string" ? meta.outcome.trim().toLowerCase() : "";
  const knownOutcome = successfulOutcomes.includes(outcome)
    ? true
    : failedOutcomes.includes(outcome)
      ? false
      : undefined;
  const knownStatus = successfulStatuses.includes(status)
    ? true
    : failedStatuses.includes(status)
      ? false
      : undefined;
  const legacyKnownOutcome = successfulOutcomes.includes(legacyOutcome)
    ? true
    : failedOutcomes.includes(legacyOutcome)
      ? false
      : undefined;
  const ok = knownOutcome ?? explicitOk ?? knownStatus ?? legacyKnownOutcome ?? false;
  const structuredError =
    parsed.error ??
    (parsed.error_code || parsed.public_message
      ? {
          code: parsed.error_code,
          message: parsed.public_message,
        }
      : undefined) ??
    meta?.error;
  return {
    ok,
    error: ok ? undefined : publicToolErrorText(structuredError),
    toolName: typeof parsed.tool === "string" ? parsed.tool : undefined,
  };
}

/**
 * FEAT-A2 — 把 slash_command_result 的 result 对象格式化成一条可读文本。
 * 8 种 result.type 全覆盖（help / goal_set / goal_status / goal_cleared /
 * skill_result / prefs_list / prefs_cleared / error）+ default 兜底（raw JSON），
 * 禁止任何 type 落空被静默丢弃。
 */
export function format_slash_result(result: SlashCommandResult | null | undefined): string {
  const r = result || {};
  switch (r.type) {
    case "help": {
      const builtins = (r.builtins || [])
        .map((b) => `  /${b.name} — ${b.description || ""}`)
        .join("\n");
      const skills = (r.skills || [])
        .map((s) => `  /${s.name} — ${s.description || ""}`)
        .join("\n");
      const parts = ["可用命令:"];
      if (builtins) parts.push(builtins);
      if (skills) parts.push("可用 skill:", skills);
      return parts.join("\n");
    }
    case "goal_set":
      return `已设置目标: ${r.text || ""}（上限 ${r.max_iterations ?? "?"} 轮）`;
    case "goal_status":
      return r.active
        ? `当前目标: ${r.text || ""}（已用 ${r.iterations_used ?? 0}/${r.max_iterations ?? "?"} 轮${r.done ? "，已完成" : ""}）`
        : "当前无活动目标";
    case "goal_cleared":
      return r.ok ? "已清除当前目标" : "无目标可清除";
    case "skill_result":
      return `skill /${r.skill || ""} 输出:\n${
        typeof r.output === "string" ? r.output : JSON.stringify(r.output, null, 2)
      }`;
    case "prefs_list": {
      const entries = r.entries || [];
      if (!entries.length) return "偏好记忆为空（暂无意图/计划记录）";
      const lines = entries.map((e) => `  ${e.kind}/${e.label}: ${e.text}`);
      return [`偏好记忆（共 ${r.count ?? entries.length} 条）:`, ...lines].join("\n");
    }
    case "prefs_cleared":
      return `已清除 ${r.removed ?? 0} 条偏好记忆${r.kind ? `（kind=${r.kind}）` : ""}`;
    case "error":
      return `命令出错: ${r.message || "unknown"}${r.hint ? `\n提示: ${r.hint}` : ""}`;
    default:
      // 兜底：未知 type 也要展示，禁止静默丢。
      return `[slash 结果] ${JSON.stringify(r)}`;
  }
}

/**
 * Route a backend WS message into the zustand store. Most events
 * carry `payload.session_id`; otherwise use the currently active session.
 */
export function selectNewestInflightRunAfterTerminal(sid: string, terminalRunId: string): void {
  const state = useSessionsStore.getState();
  const session = state.sessions[sid];
  if (!session || session.selected_run_id !== terminalRunId) return;
  const next = Object.values(session.run_projections)
    .filter(
      (projection) =>
        projection.run_id !== terminalRunId &&
        projection.inflight &&
        projection.ui_state !== "closed",
    )
    .sort((left, right) => right.last_activity - left.last_activity)[0];
  if (next) state.select_run_projection(sid, next.run_id);
}
