// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 执行图数据（NEXT-TG-1.0 §8）：`taskgraph.execution_snapshot` 的解析与纯展示映射。
 *
 * 结构（计划里的步骤、拆分、先后与数据依赖）来自 SDK 严格执行图；执行过程（每次执行、审阅、
 * 修补请求、规划、计划版本、操作）来自同一次读取的执行过程投影。这里只挑选和翻译，不推导状态。
 */

type Obj = Record<string, unknown>;

export type StepDuty = { key: string; evidence: string[] };
/** 旧运行视图的节点形状里只剩"方法步骤职责"这一项还有人用（未提交的任务过程视图）。 */
export type LiveNode = { step?: StepDuty | null };

export type StructureNode = {
  occurrence_id: string; task_id: string; form: "compound" | "primitive"; phase: string; readiness: string;
  reason_codes: string[]; parent: string | null; step: StepDuty | null; step_index: number | null;
};
export type StructureEdge = { kind: "order" | "data"; source: string; target: string };
export type Summary = { text: string; source_kind: string; source_ref: string };
export type Turn = { intent_id: string; agent_id: string | null; state: string; profile_id: string | null; model: string | null };
export type ExecKind = "attempt" | "check" | "review" | "planning" | "repair_request" | "plan_revision" | "operation";
export type ExecNode = {
  node_id: string; kind: ExecKind; at_ms: number | null; summary: Summary | null; turn: Turn | null; raw: Obj;
};
export type ExecEdge = { kind: string; source: string; target: string; target_layer: "execution" | "structure" };
export type ExecutionView = {
  mission_id: string; plan_revision: number; through_seq: number; execution_hash: string;
  coverage: string; structure: StructureNode[]; structureEdges: StructureEdge[];
  nodes: ExecNode[]; edges: ExecEdge[];
};
/** 一页原始回复（第一页带结构，后续页只带执行节点）。 */
export type ExecutionPage = {
  first: Omit<ExecutionView, "nodes" | "edges"> | null; nodes: ExecNode[]; edges: ExecEdge[];
  next_cursor: string | null; complete: boolean;
};

function fail(): never { throw new Error("执行图返回的数据不完整或格式不符"); }
function maybeStr(value: unknown): string | null { return typeof value === "string" && value ? value : null; }

/**
 * 一页 `taskgraph.execution_snapshot` 回复 → 画面数据。
 *
 * 形状只核一次：控制通道收到回复时已按公开合同 `taskgraph-execution-view-v1` 核过（推后第 3 批 U09，
 * `ws/orchestrationContracts.ts`），不合合同的回复到不了这里。这里只核请求对应关系（是不是这个任务的），
 * 再挑选、翻译。
 */
export function parseExecutionPage(data: unknown, missionId: string): ExecutionPage {
  const raw = (data ?? {}) as Obj;
  if (raw.mission_id !== missionId) fail();
  const nodes = (raw.execution_nodes as Obj[]).map((n): ExecNode => ({
    node_id: n.node_id as string, kind: n.kind as ExecKind, at_ms: typeof n.at_ms === "number" ? n.at_ms : null,
    summary: (n.summary as Summary | null) ?? null, turn: (n.turn as Turn | null) ?? null, raw: n }));
  const edges = (raw.execution_edges as Obj[]).map((e): ExecEdge => ({
    kind: e.kind as string, source: e.source as string, target: e.target as string,
    target_layer: e.target_layer as ExecEdge["target_layer"] }));
  let first: ExecutionPage["first"] = null;
  if (raw.graph != null) {
    const graph = raw.graph as Obj;
    const token = raw.read_token as Obj;
    const cut = raw.execution_cut as Obj;
    const labels = new Map<string, { step: StepDuty; index: number | null }>();
    for (const l of (raw.occurrence_labels as Obj[] | null) ?? []) {
      const key = maybeStr(l.step_key);
      if (!key) continue;
      labels.set(l.occurrence_id as string, { step: { key, evidence: (l.duties as string[]).filter(Boolean) },
        index: (l.step_index as number | null) ?? null });
    }
    const parent = new Map<string, string>();
    const structureEdges: StructureEdge[] = [];
    for (const e of graph.edges as Obj[]) {
      if (e.kind === "refinement") parent.set(e.target as string, e.source as string);
      else structureEdges.push({ kind: e.kind as StructureEdge["kind"], source: e.source as string, target: e.target as string });
    }
    const structure = (graph.nodes as Obj[]).map((n): StructureNode => {
      const id = n.occurrence_id as string;
      const label = labels.get(id);
      return { occurrence_id: id, task_id: n.task_id as string, form: n.form as StructureNode["form"],
        phase: n.phase as string, readiness: n.readiness as string, reason_codes: n.reason_codes as string[],
        parent: parent.get(id) ?? null, step: label?.step ?? null, step_index: label?.index ?? null };
    });
    first = { mission_id: missionId, plan_revision: token.plan_revision as number, through_seq: token.through_seq as number,
      execution_hash: cut.execution_hash as string, coverage: cut.coverage as string, structure, structureEdges };
  }
  return { first, nodes, edges, next_cursor: maybeStr(raw.next_cursor), complete: raw.complete === true };
}

export function mergePages(pages: ExecutionPage[]): ExecutionView {
  const first = pages[0]?.first ?? fail();
  const nodes = pages.flatMap((p) => p.nodes);
  const ids = new Set(nodes.map((n) => n.node_id));
  const structure = new Set(first.structure.map((n) => n.occurrence_id));
  const edges = pages.flatMap((p) => p.edges).filter((e) =>
    ids.has(e.source) && (e.target_layer === "structure" ? structure.has(e.target) : ids.has(e.target)));
  return { ...first, nodes, edges };
}

// ------------------------------------------------------------------ 显示
export type Tone = "idle" | "person" | "ready" | "running" | "verifying" | "done" | "failed" | "cancelled" | "unknown";
export type Display = { label: string; tone: Tone };

export const TONE_COLOR: Record<Tone, string> = {
  idle: "#94a3b8", person: "#f59e0b", ready: "#38bdf8", running: "#3b82f6", verifying: "#8b5cf6",
  done: "#22c55e", failed: "#ef4444", cancelled: "#94a3b8", unknown: "#94a3b8",
};

const PHASE: Record<string, Display> = {
  BLOCKED: { label: "等待", tone: "idle" }, READY: { label: "可调度", tone: "ready" },
  ACTIVE: { label: "执行中", tone: "running" }, VERIFYING: { label: "验收中", tone: "verifying" },
  COMPLETED: { label: "完成", tone: "done" }, FAILED: { label: "失败", tone: "failed" },
  CANCELLED: { label: "取消", tone: "cancelled" },
  planning_ready: { label: "等待拆分", tone: "idle" }, refining: { label: "拆分中", tone: "running" },
  waiting_children: { label: "子步骤进行中", tone: "running" }, composition_review: { label: "汇总验收中", tone: "verifying" },
  resolution_committed: { label: "完成", tone: "done" }, evidence_or_authority_wait: { label: "等人处理", tone: "person" },
  AWAITING_INITIAL_PLAN: { label: "等待计划", tone: "idle" }, AWAITING_COMPLETION_MAPPING: { label: "等完成要求确认", tone: "person" },
  WAITING_FORMAL_CONTENT_REVIEW: { label: "等正式审阅", tone: "verifying" }, WAITING_EFFECT: { label: "等操作生效", tone: "person" },
  AWAITING_OUTCOME_REVIEW: { label: "操作结果核对中", tone: "verifying" },
  RECONCILIATION_REQUIRED: { label: "操作结果待核实", tone: "person" },
};
const UNKNOWN: Display = { label: "未知", tone: "unknown" };
const warned = new Set<string>();
function unknown(value: string): Display {
  if (!warned.has(value)) { warned.add(value); console.warn("执行图：未知状态值", value); }
  return UNKNOWN;
}
export function phaseDisplay(phase: string): Display { return phase ? PHASE[phase] ?? unknown(phase) : UNKNOWN; }

/** 就绪原因的中文（"为什么还没开始"）。 */
export const READINESS: Record<string, string> = {
  NOT_SELECTED: "未选入执行", NEEDS_REFINEMENT: "等待拆分", WAITING_ORDER: "等上一步完成",
  WAITING_DATA: "等上游交付", WAITING_EVIDENCE: "等证据", WAITING_APPROVAL: "等批准",
  STALE_BINDING: "计划已变，待重新绑定", READY_CANDIDATE: "可以开始",
  OBSERVER_UNAVAILABLE: "来源暂不可读", GRAPH_INTEGRITY: "结构待修复", VALIDITY_RECHECK_PENDING: "等有效性复核",
};
export function readinessLabel(reason: string | null): string | null {
  return reason ? READINESS[reason] ?? reason : null;
}

const ATTEMPT: Record<string, Display> = {
  PENDING: { label: "排队中", tone: "idle" }, CLAIMED: { label: "准备中", tone: "running" },
  RUNNING: { label: "执行中", tone: "running" }, SUBMITTED: { label: "已交稿", tone: "verifying" },
  VERIFYING: { label: "验收中", tone: "verifying" }, COMPLETED: { label: "通过", tone: "done" },
  RETRY_WAIT: { label: "未通过", tone: "failed" }, LOST: { label: "中断", tone: "failed" },
  TIMED_OUT: { label: "超时", tone: "failed" },
};
const VERDICT: Record<string, Display> = {
  PASS: { label: "通过", tone: "done" }, FAIL: { label: "不通过", tone: "failed" },
  ACCEPT: { label: "接受", tone: "done" }, REWORK: { label: "要求返工", tone: "failed" },
  REJECTED: { label: "拒绝", tone: "failed" }, INCONCLUSIVE: { label: "无法判定", tone: "person" },
};
const OPERATION: Record<string, Display> = {
  PROPOSED: { label: "待批准", tone: "person" }, APPROVED: { label: "已批准", tone: "ready" },
  HANDED_OFF: { label: "执行中", tone: "running" }, SUCCEEDED: { label: "已完成", tone: "done" },
  FAILED: { label: "失败", tone: "failed" }, UNKNOWN: { label: "结果待核实", tone: "person" },
  REJECTED: { label: "被拒绝", tone: "failed" }, CANCELLED: { label: "取消", tone: "cancelled" },
};
export const DECISION_TYPE: Record<string, string> = {
  REFINE: "拆分", PROPOSE_METHOD: "提出新方法", REQUEST_EVIDENCE: "要求补充证据", REPAIR: "修补计划",
  REQUEST_HUMAN: "请人处理", WAIT: "等待",
  NO_CHANGE: "不改动",
};
export const DECISION_STATUS: Record<string, string> = {
  UNREADABLE: "无法解析", DECODED: "已解析", REJECTED: "被拒绝", ADMITTED: "已接纳", COMPILED: "已编译",
  COMMIT_REJECTED: "提交被拒", COMMITTED: "已提交", NO_STATE_CHANGE: "无变化",
};
const REVIEW_PURPOSE: Record<string, string> = {
  MISSION_FINAL: "终审", ACTION_PROPOSAL: "操作审查", OPERATION_OUTCOME: "操作结果审查",
  COMPOSITION: "汇总审阅", METHOD_PLAN: "方法审阅", MISSION_JUDGE: "任务判定",
};

function running(turn: Turn | null): boolean { return !!turn && turn.state !== "SETTLED" && turn.state !== "FAILED"; }

/** 执行过程节点的标题与状态（一眼能看懂的中文）。 */
export function execDisplay(node: ExecNode): { title: string; status: Display } {
  const r = node.raw;
  switch (node.kind) {
    case "attempt":
      return { title: `第 ${Number(r.ordinal) || 1} 次执行`, status: ATTEMPT[String(r.status)] ?? unknown(String(r.status)) };
    case "check": {
      const verdict = String(r.verdict ?? "");
      return { title: "验收审阅", status: verdict ? VERDICT[verdict] ?? unknown(verdict) : { label: "审阅中", tone: "verifying" } };
    }
    case "review": {
      const verdict = String(r.verdict ?? "");
      const status = verdict ? VERDICT[verdict] ?? unknown(verdict)
        : running(node.turn) ? { label: "审阅中", tone: "verifying" as Tone } : { label: "已结束", tone: "idle" as Tone };
      return { title: REVIEW_PURPOSE[String(r.purpose)] ?? "审阅", status };
    }
    case "planning": {
      const decisions = Array.isArray(r.decisions) ? r.decisions as Obj[] : [];
      const last = decisions[decisions.length - 1];
      const role = "规划";
      if (!last) return { title: role, status: running(node.turn) ? { label: "思考中", tone: "running" } : { label: "已结束", tone: "idle" } };
      const type = DECISION_TYPE[String(last.decision_type)] ?? String(last.decision_type ?? "未解析");
      const ok = last.status === "COMMITTED" || last.status === "NO_STATE_CHANGE";
      return { title: `${role}：${type}`, status: { label: DECISION_STATUS[String(last.status)] ?? String(last.status),
        tone: ok ? "done" : last.status === "REJECTED" || last.status === "COMMIT_REJECTED" || last.status === "UNREADABLE" ? "failed" : "running" } };
    }
    case "repair_request":
      return { title: "修补请求", status: { label: "已提出", tone: "person" } };
    case "plan_revision":
      return { title: `计划第 ${Number(r.plan_revision)} 版`, status: r.state === "ACTIVE" ? { label: "当前", tone: "done" } : { label: "已被替换", tone: "idle" } };
    case "operation": {
      const verb = r.operation === "publish" ? "发布" : String(r.operation ?? "操作");
      return { title: `${verb}${r.target ? " " + String(r.target) : ""}`, status: OPERATION[String(r.state)] ?? unknown(String(r.state)) };
    }
  }
}

const STEP_LABEL: Record<string, string> = {
  prepare: "准备", deliver: "交付", continue: "接续", summary: "汇总", summarize: "汇总", review: "复核",
  verify: "验证", draft: "起草", write: "撰写", research: "调研", analyze: "分析", analysis: "分析",
  plan: "规划", design: "设计", implement: "实现", test: "测试", fix: "修复", finalize: "定稿", publish: "发布",
};
const FILE_NAME = /[\w一-龥.-]+\.(?:md|txt|py|ts|tsx|js|json|csv|xlsx|docx|pptx|pdf|html|yaml|yml|toml|sql)\b/gi;

/** 步骤的中文名：常见英文步骤名翻译，其余保留原名（模型起的名字）。 */
export function stepLabel(key: string): string {
  const base = key.toLowerCase().replace(/[-_]?\d+$/, "");
  return STEP_LABEL[base] ?? key;
}

/** 节点标题：有方法步骤信息时写"步骤名：产出哪些文件"，否则用任务目标（接续步骤去掉英文前缀）。 */
export function stepTitle(step: StepDuty | null | undefined, goal: string): string {
  if (step) {
    const files = [...new Set(step.evidence.join(" ").match(FILE_NAME) ?? [])];
    if (files.length) return `${stepLabel(step.key)}：产出 ${files.join("、")}`;
    const first = (step.evidence[0] ?? "").replace(new RegExp(`^${step.key}\\s*步骤\\s*`), "");
    return first ? `${stepLabel(step.key)}：${first}` : stepLabel(step.key);
  }
  return goal.replace(/^Continue from an accepted upstream delivery:\s*/, "接续上一步交付：");
}

/** 模型原文给人看：去掉证据编号和内部 id（一串哈希没人看得懂）。 */
/** 后台审阅层的英文原因 → 大白话（其余原样保留）。 */
const ENGLISH_REASONS: [RegExp, string][] = [
  [/critic verdict unusable:\s*Assurance review awaits original-call reconciliation\.?/gi, "审阅调用被打断，要等核对原调用结果，这次审阅作废"],
  [/critic verdict unusable:\s*/gi, "审阅结论无法使用："],
  [/The frozen applicability snapshot and deployment policy selected this method deterministically\.?/gi, "系统按适用条件和部署策略直接选定了方法"],
];

export function cleanText(value: string): string {
  return ENGLISH_REASONS.reduce((text, [pattern, plain]) => text.replace(pattern, plain), value)
    .replace(/[（(]\s*(?:ev-[0-9a-f]+…?[、，,\s]*)+[)）]/g, "")
    .replace(/\bev-[0-9a-f]{6,}…?/g, "证据")
    .replace(/\b(?:occ|task|mi|agent|result|acc|pd|synth|artifact|mission|intent|subject|event|pkg)-[0-9a-f]{12,}(?::attempt-\d+)?(?:\s+v\d+)?\b/g, "")
    .replace(/[（(]\s*[,，/、]?\s*[)）]/g, "")
    .replace(/[ \t]{2,}/g, " ").trim();
}

export const STALL_SECONDS = 10 * 60;

/** 一个步骤的"一句话进展"：最近一次执行交的说明。等待原因写在状态行里，这里不重复；
 *  就绪原因在步骤开始后不再更新，执行中/已结束的步骤写它会自相矛盾（真机见过"子步骤进行中 · 等待拆分"）。 */
export function stepProgress(_step: StructureNode, attempts: ExecNode[]): string {
  const latest = attempts[attempts.length - 1];
  return latest?.summary ? cleanText(latest.summary.text) : "";
}

/** 步骤状态：阶段是"可调度"但还在等（上一步、数据、批准……）时写"等待"，不写"可调度"。 */
export function stepDisplay(step: StructureNode): Display {
  const display = phaseDisplay(step.phase);
  return step.phase === "READY" && step.readiness && step.readiness !== "READY_CANDIDATE"
    ? { label: "等待", tone: "idle" } : display;
}
