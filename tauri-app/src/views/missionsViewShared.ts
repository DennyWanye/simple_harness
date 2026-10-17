// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React from "react";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import {
  asList as list,
  asRecord as record,
  asText as text,
  type MissionEvent,
} from "../stores/missionsStore";

export type Json = Record<string, unknown>;

export const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

/** 重启恢复把"库与自己历史对不上"的任务单独隔离（后端 recovery_isolated）；列表与详情都用这一句。 */
export const RECOVERY_ISOLATED_TEXT = "重启核对没通过，已隔离，不再推进；可以取消";

/** P3.1 §3.4 界面状态词汇；ui_state 由后端投影给出，界面不自己推断，缺失时回退原始 status。 */
const UI_STATE_LABEL: Record<string, string> = {
  received: "请求已接收",
  queued: "排队",
  running: "运行",
  verifying: "待验证",
  waiting_person: "等你处理",
  unknown: "UNKNOWN（结果未知）",
  delivered: "正式交付",
  failed: "失败",
  cancelled: "已取消",
};

export const LAYER_LABEL: Record<string, string> = {
  PASS: "通过",
  FAIL: "未通过",
  ERROR: "错误",
  NOT_REQUIRED: "不需要",
  SKIPPED: "跳过",
  NEEDS_HUMAN: "待人工",
  SUSPENDED: "待人工",
  // 产物的验证状态（2026-09-26 真机：列表里直接显示英文 VERIFIED）
  VERIFIED: "已验证",
  UNVERIFIED: "未验证",
  REJECTED: "未通过",
};

export const REJECTION_LABEL: Record<string, string> = {
  stale_source: "来源已失效",
  source_unavailable: "来源不可用",
  used_knowledge_stale: "引用的知识已失效",
  revoked: "已撤销",
  superseded: "已替代",
  not_current: "非当前版本",
};

export const WAIT_LABEL: Record<string, string> = {
  source_change: "来源变更审批",
  review: "人工复核",
  action: "动作审批",
  arbitration: "仲裁",
};

export const ERROR_TEXT: Record<string, string> = {
  local_tests_disabled: "本机执行测试代码已关闭：成功条件不能使用 pytest:",
  action_criteria_disabled: "这个部署没有启用真实动作：成功条件不能使用 action:",
  secret_rejected: "内容里有像密钥的文本，未接受",
  orchestration_unavailable: "编排服务不可用",
  not_found: "找不到这个对象",
  conflict: "同一个请求键已经对应另一个不同的请求",
  integrity_error: "产物内容与记录的哈希不一致",
};

/** 视图自己发、自己处理应答的消息类型；其余应答属于常驻订阅或别的视图。 */
export const OWN_RESPONSES = new Set([
  "mission_get_response",
  "mission_events_response",
  "orchestration_policy_status_response",
  "mission_create_response",
  "mission_create_with_sources_response",
  "mission_cancel_response",
  "mission_approval_decide_response",
  "mission_takeover_response",
  "mission_action_resolve_response",
  "mission_comment_response",
  "mission_artifact_read_response",
]);

export const EVENT_PAGE_LIMIT = 200;

// 严格引用开关打开时追加的成功条件（2026-10-02 用户决定：交给审阅员按这条判断，程序不再逐条核对）。
export const STRICT_QUOTES_CRITERION =
  "每个结论都逐字引用所附资料里的原文，并注明出自哪份资料、哪一处；资料里找不到依据的内容不写成结论，而是明确说明资料未提及";

/** 只说明"后台还活着"、不改变详情内容的事件：推送只带这些时不重拉详情（2026-09-27 性能：
 *  运行中每几秒一次心跳，每次都重拉整份详情并重画整页）。等人操作的变化由 5 秒兜底刷新接住。 */
export const NOISE_EVENTS = new Set([
  "HeartbeatReceived",
  "AssuranceEvidenceChanged",
  "AssuranceUseValidityChecked",
]);

export const MAX_AUTO_PAGES = 20;

export const TIMELINE_SIZE = 50;

/** 一个 Mission 的请求在途状态（只在回调里读写）。 */
export interface Flight {
  get: boolean;
  getAgain: boolean;
  events: boolean;
  pages: number;
  gap: boolean;
}

/** 推送里只带原始 status 时的中文（2026-09-25 真机点击：列表曾直接显示 PLANNING）。 */
const STATUS_LABEL: Record<string, string> = {
  CREATED: "请求已接收",
  PLANNING: "规划中",
  ACTIVE: "运行",
  VERIFYING: "待验证",
  COMPLETED: "正式交付",
  FAILED: "失败",
  CANCELLED: "已取消",
  STOPPED: "已停止",
};

/** 停止原因与 Task 状态的中文（2026-09-25 真机点击：界面直接显示 verification_passed / BLOCKED）。 */
export const STOP_REASON_LABEL: Record<string, string> = {
  verification_passed: "验证通过",
  verification_failed: "验证未通过",
  budget_exhausted: "预算用完",
  cancelled: "已取消",
  user_cancelled: "已取消",
  planning_failed: "规划失败",
  max_attempts: "尝试次数用完",
  timeout: "超时",
  human_override: "人工接管后停止",
  no_dispatchable_work: "没有可继续执行的工作",
  store_fault: "任务数据读写反复出错，已停止",
  no_new_knowledge: "连续多轮没有新知识，已停止",
  result_duplication: "结果重复率过高，已停止",
  deadlock: "步骤之间互相等待成环（死锁），已停止",
  runtime_unavailable: "运行环境或模型服务不可用，已停止",
};

export const TASK_STATUS_LABEL: Record<string, string> = {
  PENDING: "等待",
  READY: "就绪",
  ACTIVE: "执行中",
  RUNNING: "执行中",
  BLOCKED: "等待前置步骤",
  VERIFYING: "检查中",
  DONE: "完成",
  COMPLETED: "完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  SKIPPED: "跳过",
};

export function stateLabel(uiState: unknown, status: unknown): string {
  const ui = text(uiState);
  const raw = text(status);
  return ui ? (UI_STATE_LABEL[ui] ?? ui) : (STATUS_LABEL[raw] ?? raw);
}

/** 时间戳（秒 / 毫秒 / ISO 字符串）→ 本地 HH:MM:SS；认不出就返回 null。 */
function clock(value: unknown): string | null {
  let ms: number;
  if (typeof value === "number") ms = value < 1e12 ? value * 1000 : value;
  else if (typeof value === "string" && value.trim()) {
    const n = Number(value);
    ms = Number.isFinite(n) ? (n < 1e12 ? n * 1000 : n) : Date.parse(value);
  } else return null;
  if (!Number.isFinite(ms)) return null;
  const d = new Date(ms);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

export function formatBytes(value: unknown): string {
  const n = Number(value);
  if (value == null || !Number.isFinite(n) || n < 0) return "大小未知";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function shortHash(value: unknown): string {
  return text(value)
    .replace(/^[a-z0-9]+:/i, "")
    .slice(0, 12);
}

export function show(value: unknown): string {
  return value != null && typeof value === "object" ? JSON.stringify(value) : text(value);
}

/** `{text, source}` 或旧的纯字符串 → 文字。 */
export function plain(value: unknown): string {
  return text(record(value).text ?? value);
}

export function waitLine(item: unknown): string {
  if (typeof item === "string") return `等待：${item}`;
  const wait = record(item);
  const kind = text(wait.kind);
  const since = clock(wait.since);
  return `等待：${WAIT_LABEL[kind] ?? (kind || "未知")}${since ? `（自 ${since}）` : ""}`;
}

export function eventLine(event: MissionEvent): string {
  const head = `#${event.seq} ${event.type}`;
  if (event.type === "HumanCommentAdded" && event.summary != null)
    return `${head}：${plain(event.summary)}`;
  return head;
}

export const box: React.CSSProperties = {
  background: dark.inset,
  border: `1px solid ${tokens.color.surface.hairline}`,
  borderRadius: tokens.radius.md,
  padding: tokens.space.md,
};

export const button: React.CSSProperties = {
  height: tokens.controlHeight,
  padding: `0 ${tokens.space.md}px`,
  borderRadius: tokens.radius.md,
  border: `1px solid ${tokens.color.surface.hairline}`,
  background: dark.panel,
  color: dark.text,
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
  cursor: "pointer",
};

export const field: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  minHeight: 64,
  padding: tokens.space.sm,
  borderRadius: tokens.radius.md,
  border: `1px solid ${tokens.color.surface.hairline}`,
  background: dark.inset,
  color: dark.text,
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
};

export const muted: React.CSSProperties = { color: dark.textMuted, fontSize: tokens.text.xs.size };

export const BUDGET_ROWS_SHOWN = 8;

/** 预算去向：这件事（含下级、含被换掉的做法）到现在花了多少、试了几次。默认收起；只列花过或试过的。 */
/** 一行花费：已用 token、尝试次数（失败次数）、用量未知的次数。义务行与步骤行同一个写法。 */
export function spendLine(row: Json, label: string): string {
  const unknown = Number(row.unknown_usage_attempts) || 0;
  return (
    `${label} · 已用 ${Number(row.settled_tokens) || 0} token · 尝试 ${Number(row.attempts) || 0} 次（失败 ${Number(row.failed_attempts) || 0} 次）` +
    (unknown ? `，另有 ${unknown} 次用量未知` : "")
  );
}

export const heading: React.CSSProperties = { fontWeight: tokens.weight.semibold };

/** 顶部"下一步"提示（2026-09-25 真机点击：要人操作的按钮埋在长页面中间，用户找不到）。
 *  只读投影：从详情推出当前最该做的一件事，按钮跳到对应区域；不代替那一步本身。 */
export function nextStep(
  detail: Json,
  approvals: number,
): { text: string; target?: string; action?: string } {
  const mission = record(detail.mission);
  const status = text(mission.status);
  const ws = record(detail.operation_workspace);
  // 已结束的任务：留下的提问、授权请求都不再需要人处理（2026-09-29 取消后仍提示"去回答"）。
  const ended = ["COMPLETED", "FAILED", "CANCELLED"].includes(status);
  // 被隔离的任务不会再推进：别的"下一步"都不成立，先说这一句
  if (!ended && detail.recovery_isolated) return { text: RECOVERY_ISOLATED_TEXT };
  if (
    !ended &&
    text(ws.mission_id) &&
    text(ws.state) &&
    text(ws.state) !== "APPROVED" &&
    ws.editable === true
  )
    return {
      text: "下一步：在「完成要求」里勾选要交付的内容，再点「确认上述完成要求」。",
      target: "mission-step-requirements",
      action: "去确认",
    };
  if (!ended && list(detail.planning_authorization_requests).length)
    return { text: "下一步：授权本轮规划，任务才会开始执行。" };
  if (!ended && list(detail.planning_questions).some((q) => text(record(q).state) === "PENDING"))
    return {
      text: "下一步：回答任务提出的问题。",
      target: "mission-step-questions",
      action: "去回答",
    };
  if (approvals > 0)
    return {
      text: `下一步：有 ${approvals} 项等你审批或复核。`,
      target: "mission-step-approvals",
      action: "去处理",
    };
  if (status === "COMPLETED")
    return {
      text: "任务已完成，结果在下方「产物」里。",
      target: "mission-step-artifacts",
      action: "查看产物",
    };
  if (status === "FAILED" && text(mission.stop_reason).toLowerCase() === "human_override")
    return { text: "任务已按你的接管操作停止。" };
  if (status === "FAILED") return { text: "任务失败了，原因见上方状态和下方「Task 与验证」。" };
  if (status === "CANCELLED") return { text: "任务已取消。" };
  return { text: "任务正在自动进行，需要你操作时这里会提示。" };
}

/** 完成要求还没确认时，提示条先让人去确认；授权仍留在原位置，不会同时出现两份。 */
export function nextStepBlocksAuthorization(detail: Json): boolean {
  const ws = record(detail.operation_workspace);
  return !!(
    text(ws.mission_id) &&
    text(ws.state) &&
    text(ws.state) !== "APPROVED" &&
    ws.editable === true
  );
}

export function jumpTo(id: string): void {
  const target = typeof document === "undefined" ? null : document.getElementById(id);
  target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
}

/** Task 已经结束的状态：这些 Task 不再提供接管。 */
export const TASK_ENDED = new Set(["DONE", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"]);
