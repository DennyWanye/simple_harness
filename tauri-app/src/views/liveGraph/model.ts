// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 运行视图（plans/2026-09-25-orchestration-live-view §3 决定一、六）：`mission_live_graph` 的解析
 * 和纯展示映射。状态由 SDK 写好（tasks.status / CompoundPhaseChanged），这里只挑选和翻译，不推导。
 */

export type LiveNode = {
  occurrence_id: string; task_id: string; form: "compound" | "primitive"; parent: string | null;
  method: string | null; task_status: string | null; phase: string | null; readiness_reason: string | null;
  attempt_count: number; last_event_at: number | null;
};
export type LiveEdge = { kind: "order" | "data"; source: string; target: string };
export type LiveGraph = {
  mission_id: string; source: "htn" | "planning"; plan_revision: number | null; through_seq: number;
  nodes: LiveNode[]; edges: LiveEdge[]; revisions: number[];
};

type Obj = Record<string, unknown>;
function fail(): never { throw new Error("执行图返回的数据不完整或格式不符"); }
function obj(value: unknown): Obj {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Obj : fail();
}
function str(value: unknown): string { return typeof value === "string" && value ? value : fail(); }
function maybeStr(value: unknown): string | null { return value == null ? null : str(value); }
function int(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : fail();
}

/** 必需字段缺失就报错；多出的字段忽略（后端加字段不应让前端崩）。 */
export function parseLiveGraph(data: unknown, missionId: string): LiveGraph {
  const raw = obj(data);
  if (raw.mission_id !== missionId) fail();
  const source = raw.source === "htn" || raw.source === "planning" ? raw.source : fail();
  const nodes = (Array.isArray(raw.nodes) ? raw.nodes : fail()).map((item): LiveNode => {
    const n = obj(item);
    return {
      occurrence_id: str(n.occurrence_id), task_id: str(n.task_id),
      form: n.form === "compound" ? "compound" : n.form === "primitive" ? "primitive" : fail(),
      parent: maybeStr(n.parent), method: maybeStr(n.method), task_status: maybeStr(n.task_status),
      phase: maybeStr(n.phase), readiness_reason: maybeStr(n.readiness_reason), attempt_count: int(n.attempt_count),
      last_event_at: typeof n.last_event_at === "number" ? n.last_event_at : null,
    };
  });
  const edges = (Array.isArray(raw.edges) ? raw.edges : fail()).map((item): LiveEdge => {
    const e = obj(item);
    return { kind: e.kind === "order" || e.kind === "data" ? e.kind : fail(), source: str(e.source), target: str(e.target) };
  });
  return {
    mission_id: missionId, source, plan_revision: raw.plan_revision == null ? null : int(raw.plan_revision),
    through_seq: int(raw.through_seq), nodes, edges,
    revisions: (Array.isArray(raw.revisions) ? raw.revisions : fail()).map(int),
  };
}

export type Tone = "idle" | "person" | "ready" | "running" | "verifying" | "done" | "failed" | "cancelled" | "unknown";
export type Display = { label: string; tone: Tone };

export const TONE_COLOR: Record<Tone, string> = {
  idle: "#94a3b8", person: "#f59e0b", ready: "#38bdf8", running: "#3b82f6", verifying: "#8b5cf6",
  done: "#22c55e", failed: "#ef4444", cancelled: "#94a3b8", unknown: "#94a3b8",
};

const TASK: Record<string, Display> = {
  BLOCKED: { label: "等待", tone: "idle" }, READY: { label: "可调度", tone: "ready" },
  ACTIVE: { label: "运行中", tone: "running" }, VERIFYING: { label: "验证中", tone: "verifying" },
  COMPLETED: { label: "完成", tone: "done" }, FAILED: { label: "失败", tone: "failed" },
  CANCELLED: { label: "取消", tone: "cancelled" },
};
const PHASE: Record<string, Display> = {
  planning_ready: { label: "等待拆分", tone: "idle" }, refining: { label: "拆分中", tone: "running" },
  waiting_children: { label: "子任务进行中", tone: "running" }, composition_review: { label: "汇总验收中", tone: "verifying" },
  resolution_committed: { label: "完成", tone: "done" }, evidence_or_authority_wait: { label: "等人处理", tone: "person" },
};
const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const UNKNOWN: Display = { label: "未知", tone: "unknown" };
const warned = new Set<string>();

function unknown(value: string): Display {
  if (!warned.has(value)) { warned.add(value); console.warn("执行图：未知状态值", value); }
  return UNKNOWN;
}

/** 终态任务以任务状态为准（取消后复合任务最后一条阶段事件可能还是"子任务进行中"）；
 * 否则复合任务看 SDK 算好的阶段；都没有时看任务状态。 */
export function displayOf(node: Pick<LiveNode, "form" | "task_status" | "phase">): Display {
  const status = node.task_status;
  if (status && TERMINAL.has(status)) return TASK[status];
  if (node.form === "compound" && node.phase) return PHASE[node.phase] ?? unknown(node.phase);
  if (status) return TASK[status] ?? unknown(status);
  return UNKNOWN;
}

/** 就绪原因（复合任务阶段事件里带的 readiness_reason）的中文。 */
export const READINESS: Record<string, string> = {
  NOT_SELECTED: "未选入执行", NEEDS_REFINEMENT: "等待细化", WAITING_ORDER: "等待前序完成",
  WAITING_DATA: "等待输入", WAITING_EVIDENCE: "等待证据", WAITING_APPROVAL: "等待授权",
  STALE_BINDING: "绑定已变化", READY_CANDIDATE: "可供调度", WAITING_OPERATION_UNKNOWN: "等待效果核对",
  OBSERVER_UNAVAILABLE: "来源不可用", GRAPH_INTEGRITY: "结构待修复", VALIDITY_RECHECK_PENDING: "等待有效性复核",
};
export function readinessLabel(reason: string | null): string | null {
  return reason ? READINESS[reason] ?? reason : null;
}

export const STALL_SECONDS = 10 * 60;
const ACTIVE_TONES = new Set<Tone>(["running", "verifying"]);

/** 运行中/验证中/拆分中，且该任务最后一条事件距今超过 10 分钟：只是提示，不改状态。 */
export function isStalled(node: LiveNode, nowSeconds: number, missionTerminal: boolean): boolean {
  if (missionTerminal || node.last_event_at == null) return false;
  return ACTIVE_TONES.has(displayOf(node).tone) && nowSeconds - node.last_event_at > STALL_SECONDS;
}

/** 规划决定的中文。 */
export const DECISION_TYPE: Record<string, string> = {
  REFINE: "拆分", PROPOSE_METHOD: "提出新方法", REQUEST_EVIDENCE: "要求补充证据", REPAIR: "修补计划",
  BIND_EXISTING_GOAL: "复用已有目标", DECLARE_BLOCKED: "声明受阻", REQUEST_HUMAN: "请人处理", WAIT: "等待",
  NO_CHANGE: "不改动",
};
export const DECISION_STATUS: Record<string, string> = {
  UNREADABLE: "无法解析", DECODED: "已解析", REJECTED: "被拒绝", ADMITTED: "已接纳", COMPILED: "已编译",
  COMMIT_REJECTED: "提交被拒", COMMITTED: "已提交", NO_STATE_CHANGE: "无变化",
};
