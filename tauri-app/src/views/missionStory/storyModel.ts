// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 任务过程（2026-09-27 用户：执行图看不出模型一步一步做了什么）的数据与中文。
 *
 * 2026-09-30 改走 SDK 正式只读接口（用户决定：不下架，补过滤与脱敏）：卡片来自
 * `taskgraph.execution_snapshot` 的执行过程节点（规划、执行、检查、审阅是模型回合；计划版本、
 * 修补请求、操作是节点事件），卡片明细来自 `taskgraph.execution_detail`——SDK 只读模型回复与
 * 工具结果、去掉思考内容、不读系统提示/上下文包/系统提醒，并对全部文字做密钥脱敏。
 * 这里只挑选和翻译，不推导状态；未知字段忽略，未知取值原样显示。
 */
import { DECISION_TYPE, stepTitle, type ExecNode, type ExecutionView, type LiveNode } from "../liveGraph/model";

export type StoryItem =
  | { t: "say"; text: string }
  | { t: "submit"; outcome: string; text: string }
  | { t: "decision"; decision_type: string; text: string }
  | { t: "method"; steps: string[]; text: string }
  | { t: "verdict"; verdict: string; reasons: { verdict: string; text: string }[] }
  | { t: "tool"; tool: string; ok: boolean; count?: number; path?: string; error?: string; bytes?: number;
      chars?: number; files?: string[]; file_count?: number; passed?: boolean; returncode?: number | null;
      tail?: string; found?: number };

export type TurnKind = "plan" | "work" | "review" | "final" | "other";
export type TurnCard = {
  id: string; kind: TurnKind; at: number; task_id: string | null;
  attempt: number | null; attempt_status: string | null; model: string | null;
  state: "waiting" | "running" | "done" | "failed";
  /** 检查/审阅节点自带的结论（明细没读到时徽标用它）。 */
  verdict: string | null;
  /** SDK 给的一句话摘要（已脱敏）：折叠时显示，明细还没读到时也用它。 */
  summary: string | null;
  /** 明细（`execution_detail`）读到了才有；null = 还没读。 */
  items: StoryItem[] | null; hidden_items: number;
};
export type EventCard = {
  id: string; kind: "event"; at: number; task_id: string | null; code: string;
  count?: number; reasons?: { text: string; model: boolean }[]; reason?: string | null; text?: string;
};
export type Card = TurnCard | EventCard;
export type StoryStep = {
  task_id: string; step: LiveNode["step"]; index: number | null; current: boolean; done: boolean;
};
export type Story = { mission_id: string; execution_hash: string; steps: StoryStep[]; cards: Card[] };
export type TurnDetail = { items: StoryItem[]; hidden_items: number };

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function parseItem(raw: unknown): StoryItem | null {
  if (!isObj(raw) || typeof raw.t !== "string") return null;
  switch (raw.t) {
    case "say": return { t: "say", text: str(raw.text) };
    case "submit": return { t: "submit", outcome: str(raw.outcome), text: str(raw.text) };
    case "decision": return { t: "decision", decision_type: str(raw.decision_type), text: str(raw.text) };
    case "method": return { t: "method", steps: Array.isArray(raw.steps) ? raw.steps.map(str).filter(Boolean) : [], text: str(raw.text) };
    case "verdict": return { t: "verdict", verdict: str(raw.verdict), reasons: (Array.isArray(raw.reasons) ? raw.reasons : [])
      .filter(isObj).map((r) => ({ verdict: str(r.verdict), text: str(r.text) })) };
    case "tool": return { ...(raw as Obj), t: "tool", tool: str(raw.tool), ok: raw.ok === true } as StoryItem;
    default: return null;
  }
}

/** `taskgraph.execution_detail` 的回复 → 这一回合的明细（未知行忽略）。 */
export function parseTurnDetail(data: unknown, missionId: string, nodeId: string): TurnDetail {
  if (!isObj(data) || data.mission_id !== missionId || !isObj(data.node) || data.node.node_id !== nodeId) {
    throw new Error("回合明细返回的数据不完整或格式不符");
  }
  return { items: (Array.isArray(data.items) ? data.items : []).map(parseItem).filter((i): i is StoryItem => i !== null),
    hidden_items: num(data.hidden_items) ?? 0 };
}

/** 模型回合的状态：已结算 = 结束，失败 = 回合失败，已交给模型 = 进行中，其余 = 排队。 */
function turnState(node: ExecNode): TurnCard["state"] {
  switch (node.turn?.state) {
    case "SETTLED": return "done";
    case "FAILED": return "failed";
    case "SUBMITTED": case "AGENT_CREATED": return "running";
    default: return node.turn ? "waiting" : "done";
  }
}

const COMPLETED_PHASES = new Set(["COMPLETED", "resolution_committed"]);

/** 执行过程视图 → 按时间排列的卡片（模型回合 + 节点事件）与步骤表。 */
export function storyFromExecution(view: ExecutionView, details: ReadonlyMap<string, TurnDetail>): Story {
  const taskOf = new Map(view.structure.map((n) => [n.occurrence_id, n.task_id]));
  const byId = new Map(view.nodes.map((n) => [n.node_id, n]));
  const target = (node: ExecNode, kind: string): string | null =>
    view.edges.find((e) => e.source === node.node_id && e.kind === kind)?.target ?? null;
  const structureTask = (node: ExecNode, kind: string): string | null => {
    const occurrence = target(node, kind);
    return occurrence ? taskOf.get(occurrence) ?? null : null;
  };
  const cards: Card[] = [];
  for (const node of [...view.nodes].sort((a, b) => (a.at_ms ?? 0) - (b.at_ms ?? 0) || a.node_id.localeCompare(b.node_id))) {
    const raw = node.raw;
    const at = (node.at_ms ?? 0) / 1000;
    const summary = node.summary?.text ?? null;
    const turn = (kind: TurnKind, taskId: string | null, extra: Partial<TurnCard> = {}): TurnCard => {
      const detail = details.get(node.node_id);
      return { id: node.node_id, kind, at, task_id: taskId, attempt: null, attempt_status: null,
        model: node.turn?.model ?? null, state: turnState(node), verdict: str(raw.verdict) || null, summary,
        items: detail?.items ?? null, hidden_items: detail?.hidden_items ?? 0, ...extra };
    };
    switch (node.kind) {
      case "planning":
        cards.push(turn("plan", structureTask(node, "decision_for")));
        break;
      case "attempt":
        cards.push(turn("work", str(raw.task_id) || null, {
          attempt: num(raw.ordinal), attempt_status: typeof raw.status === "string" ? raw.status : null }));
        break;
      case "check": {
        const attempt = byId.get(target(node, "review_of") ?? "");
        const taskId = attempt ? str(attempt.raw.task_id) || null : null;
        if (node.turn) cards.push(turn("review", taskId));
        else if (str(raw.verdict) && str(raw.verdict).toUpperCase() !== "PASS") {
          cards.push({ id: node.node_id, kind: "event", at, task_id: taskId, code: "review_failed",
            reasons: summary ? [{ text: summary, model: false }] : [] });
        }
        break;
      }
      case "review": {
        const purpose = str(raw.purpose);
        const final = purpose === "MISSION_FINAL" || purpose === "MISSION_JUDGE";
        cards.push(turn(final ? "final" : "review", structureTask(node, "reviews")));
        break;
      }
      case "plan_revision":
        cards.push({ id: node.node_id, kind: "event", at, task_id: null, code: "plan_committed",
          count: num(raw.plan_revision) ?? undefined, ...(summary ? { text: summary } : {}) });
        break;
      case "repair_request":
        cards.push({ id: node.node_id, kind: "event", at, task_id: null, code: "repair_requested",
          reasons: summary ? [{ text: summary, model: false }] : [] });
        break;
      case "operation":
        cards.push({ id: node.node_id, kind: "event", at, task_id: null, code: "operation",
          reason: str(raw.state) || null, ...(summary ? { text: summary } : {}) });
        break;
    }
  }
  const steps = view.structure.filter((n) => n.form === "primitive").map((n): StoryStep => ({
    task_id: n.task_id, step: n.step, index: n.step_index, current: true, done: COMPLETED_PHASES.has(n.phase) }));
  return { mission_id: view.mission_id, execution_hash: view.execution_hash, steps, cards };
}

/** 新结果里内容没变的卡片沿用旧对象：React.memo 的卡片就不会重画（运行中每几秒刷新一次）。 */
export function reuseCards(previous: Card[] | undefined, next: Card[]): Card[] {
  if (!previous?.length) return next;
  const old = new Map(previous.map((c) => [c.id, c]));
  return next.map((card) => {
    const kept = old.get(card.id);
    return kept && JSON.stringify(kept) === JSON.stringify(card) ? kept : card;
  });
}

// ---------- 中文 ----------

const FILE = /[\w\u4e00-\u9fa5.-]+\.(?:md|txt|py|ts|tsx|js|json|csv|xlsx|docx|pptx|pdf|html|yaml|yml|toml|sql)\b/i;

export type StepNames = Map<string, { number: number | null; title: string; key?: string; current: boolean }>;

/** task_id → "第 N 步" 与步骤名（方法步骤的职责）。 */
export function stepNames(steps: StoryStep[]): StepNames {
  const out: StepNames = new Map();
  steps.forEach((s, i) => {
    // 步骤名是模型起的英文，文件名更好懂："产出 01-用户画像.md"。每条要求只取它的第一个文件
    // （2026-09-27 真机："一句话概括 hello.md"让第 2 步显示成"产出 summary.md、hello.md"）
    const files = [...new Set((s.step?.evidence ?? []).map((e) => e.match(FILE)?.[0]).filter((f): f is string => !!f))];
    const full = s.step ? stepTitle(s.step, "") : "";
    const title = files.length ? "产出 " + files.join("、") : full;
    out.set(s.task_id, { number: (s.index ?? i) + 1, title, key: s.step?.key, current: s.current });
  });
  return out;
}

export function stepLabel(names: StepNames, taskId: string | null): string {
  if (!taskId) return "";
  const named = names.get(taskId);
  if (named) return `${named.current ? "" : "旧计划"}第 ${named.number} 步${named.title ? "「" + named.title + "」" : ""}`;
  return taskId.startsWith("desktop-root-") ? "整体汇总" : "某个步骤";
}

const VERDICT: Record<string, string> = {
  ACCEPT: "通过", PASS: "通过", REWORK: "要求返工", REJECT: "不通过", FAIL: "不通过", INCONCLUSIVE: "无法判断",
  UNKNOWN: "未知", NEEDS_EVIDENCE: "需要更多证据",
};
const REASON_VERDICT: Record<string, string> = {
  PASS: "满足", FAIL: "不满足", INFO: "提示", WARN: "注意", WARNING: "注意", BLOCKER: "严重", MAJOR: "较重", MINOR: "轻微",
  UNKNOWN: "未判定", NOT_APPLICABLE: "不适用",
};
export const verdictLabel = (v: string): string => VERDICT[v.toUpperCase()] ?? v;
export const reasonVerdictLabel = (v: string): string => REASON_VERDICT[v.toUpperCase()] ?? v;
export const verdictGood = (v: string): boolean => ["ACCEPT", "PASS"].includes(v.toUpperCase());
/** 审阅理由的倾向：满足 true、不满足 false、提示类 null。 */
export function reasonGood(v: string): boolean | null {
  const up = v.toUpperCase();
  if (up === "PASS" || up === "ACCEPT") return true;
  return ["FAIL", "REJECT", "BLOCKER", "MAJOR", "REWORK"].includes(up) ? false : null;
}

const STOP_REASON: Record<string, string> = {
  verification_passed: "验证通过", verification_failed: "验证未通过", budget_exhausted: "预算用完", cancelled: "已取消",
  user_cancelled: "已取消", planning_failed: "规划失败", max_attempts: "尝试次数用完", timeout: "超时",
  human_override: "人工接管后停止", no_dispatchable_work: "没有可继续执行的工作", store_fault: "任务数据读写反复出错，已停止", insufficient_evidence: "证据不足",
  artifact_conflict: "产物冲突", turn_failed: "模型这一回合失败", upstream_artifact_missing: "上游文件缺失",
  no_new_knowledge: "连续多轮没有新知识，已停止", result_duplication: "结果重复率过高，已停止",
  deadlock: "步骤之间互相等待成环（死锁），已停止", runtime_unavailable: "运行环境或模型服务不可用，已停止",
};
export const reasonLabel = (code: string | null | undefined): string => (code ? STOP_REASON[code] ?? code : "");

function size(bytes: number): string {
  return bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${bytes} 字节`;
}

function toolError(error: string | undefined): string {
  if (!error) return "失败";
  const missing = /^no such file:\s*(.+)$/i.exec(error);
  if (missing) return `找不到 ${missing[1]}`;
  if (/^path escapes/i.test(error)) return "路径超出工作区";
  return error;
}

export type Line = { icon: string; text: string; tone: "plain" | "model" | "good" | "bad" | "muted"; list?: { tag: string; good: boolean | null; text: string }[] };

/** 方法步骤名（模型起的英文）→ 步骤职责（"产出 01-用户画像.md"）；找不到就用原名。 */
function keyTitle(names: StepNames | undefined, key: string): string {
  for (const named of names?.values() ?? []) if (named.key === key && named.title) return named.title;
  return key;
}

/** 系统写的（英文）原因翻成大白话；认不出的原样显示。 */
const SYSTEM_REASON: [RegExp, (m: RegExpExecArray) => string][] = [
  [/failed verification identically (\d+) times/i, (m) => `同一步连续 ${m[1]} 次没通过检查，再重试也不会变，需要换一种拆法`],
  [/review is INCONCLUSIVE/i, () => "审阅员没能给出明确结论"],
  [/AssuranceReviewFormatExhausted/, () => "审阅员的回复格式多次不合规，没法读出结论"],
  [/turn_failed|provider_protocol_error/i, () => "模型这一回合失败"],
];
export function systemReason(text: string): string {
  for (const [pattern, say] of SYSTEM_REASON) {
    const match = pattern.exec(text);
    if (match) return say(match);
  }
  return text;
}

/** 卡片里一行：模型做的一件事。 */
export function itemLine(item: StoryItem, names?: StepNames): Line {
  switch (item.t) {
    case "say": return { icon: "💬", text: item.text, tone: "model" };
    case "submit": return { icon: "📤", text: "提交结果：" + (item.text || "（没有摘要）"), tone: "model" };
    case "decision": return { icon: "🧭", text: `决定${DECISION_TYPE[item.decision_type] ? "「" + DECISION_TYPE[item.decision_type] + "」" : "：" + item.decision_type}${item.text ? "——" + item.text : ""}`, tone: "model" };
    case "method": return { icon: "🧭", text: `拆成 ${item.steps.length} 步：${item.steps.map((k) => keyTitle(names, k)).join(" → ")}${item.text ? "。理由：" + item.text : ""}`, tone: "model" };
    case "verdict": return {
      icon: verdictGood(item.verdict) ? "✅" : "⚠️", text: "结论：" + verdictLabel(item.verdict), tone: verdictGood(item.verdict) ? "good" : "bad",
      list: item.reasons.map((r) => ({ tag: reasonVerdictLabel(r.verdict), good: reasonGood(r.verdict), text: r.text })),
    };
    case "tool": return toolLine(item);
  }
}

function toolLine(item: Extract<StoryItem, { t: "tool" }>): Line {
  const times = item.count && item.count > 1 ? ` ×${item.count}` : "";
  const bad = (text: string): Line => ({ icon: "✗", text: text + "：" + toolError(item.error) + times, tone: "bad" });
  switch (item.tool) {
    case "workspace_list":
      if (!item.ok) return bad("查看工作区");
      return { icon: "📂", text: item.file_count ? `查看工作区：${item.file_count} 个文件（${(item.files ?? []).join("、")}${(item.file_count ?? 0) > (item.files?.length ?? 0) ? "…" : ""}）${times}` : "查看工作区：空的" + times, tone: "plain" };
    case "workspace_read_file":
      if (!item.ok) return bad("读取文件");
      return { icon: "📖", text: `读取 ${item.path ?? "文件"}${item.chars ? `（${item.chars} 字）` : ""}${times}`, tone: "plain" };
    case "workspace_write_file":
      if (!item.ok) return bad("写入文件");
      return { icon: "✍️", text: `写入 ${item.path ?? "文件"}${item.bytes != null ? `（${size(item.bytes)}）` : ""}${times}`, tone: "good" };
    case "run_tests":
      if (!item.ok) return bad("运行检查");
      return { icon: item.passed ? "✅" : "🧪", text: `运行检查：${item.passed ? "通过" : "未通过"}${item.tail ? `（${item.tail}）` : ""}${times}`, tone: item.passed ? "good" : "bad" };
    case "knowledge_list":
      return item.ok ? { icon: "📚", text: "查看资料库" + times, tone: "plain" } : bad("查看资料库");
    case "knowledge_read":
      return item.ok ? { icon: "📚", text: `阅读资料${item.path ? " " + item.path : ""}${times}`, tone: "plain" } : bad("阅读资料");
    case "assurance_find_evidence":
      return item.ok ? { icon: "🔎", text: `查找证据${item.found != null ? `：找到 ${item.found} 份` : ""}${times}`, tone: "plain" } : bad("查找证据");
    case "assurance_read_evidence":
      return item.ok ? { icon: "🔎", text: "阅读证据" + times, tone: "plain" } : bad("阅读证据");
    default:
      return item.ok ? { icon: "🔧", text: `调用工具 ${item.tool}${times}`, tone: "plain" } : bad(`调用工具 ${item.tool}`);
  }
}

/** 卡片标题：谁、在做哪一步。 */
export function turnTitle(card: TurnCard, names: StepNames): string {
  const step = stepLabel(names, card.task_id);
  switch (card.kind) {
    case "plan": {
      const items = card.items ?? [];
      const decision = items.find((i) => i.t === "decision") as Extract<StoryItem, { t: "decision" }> | undefined;
      if (items.some((i) => i.t === "method")) return "规划器 · 把任务拆成步骤";
      if (decision) return "规划器 · " + (decision.decision_type === "REPAIR" ? "修补计划" : DECISION_TYPE[decision.decision_type] ?? "规划");
      return "规划器 · 规划";
    }
    case "work": return `执行者 · ${step || "一个步骤"}${card.attempt && card.attempt > 1 ? ` · 第 ${card.attempt} 次尝试` : ""}`;
    case "review": return `审阅员 · 检查${step || "一个步骤"}`;
    case "final": return "最终验收 · 检查全部交付";
    default: return "模型回合";
  }
}

export type Badge = { text: string; tone: "running" | "good" | "bad" | "muted" };

/** 卡片右上角的结果：进行中 / 通过 / 返工 / 失败…；任务已结束还没收尾的回合显示"已停止"。 */
export function turnBadge(card: TurnCard, missionEnded: boolean): Badge {
  if (card.state === "waiting" || card.state === "running") {
    if (missionEnded) return { text: "已停止", tone: "muted" };
    return card.state === "waiting" ? { text: "排队中", tone: "muted" } : { text: "进行中…", tone: "running" };
  }
  if (card.state === "failed") return { text: "回合失败", tone: "bad" };
  const said = [...(card.items ?? [])].reverse().find((i) => i.t === "verdict") as Extract<StoryItem, { t: "verdict" }> | undefined;
  const verdict = said?.verdict ?? card.verdict;
  if (verdict) return { text: verdictLabel(verdict), tone: verdictGood(verdict) ? "good" : "bad" };
  if (card.kind === "review" || card.kind === "final") return { text: "没给出结论", tone: "bad" };
  if (card.kind === "work") {
    if (card.attempt_status === "FAILED" || card.attempt_status === "LOST") return { text: "失败", tone: "bad" };
    return card.attempt_status === "SUCCEEDED" || card.attempt_status === "COMPLETED" || (card.items ?? []).some((i) => i.t === "submit")
      ? { text: "已提交", tone: "good" } : { text: "结束", tone: "muted" };
  }
  return { text: "完成", tone: "good" };
}

/** 折叠时的一行摘要。 */
export const GIST_LIMIT = 90;
export function turnGist(card: TurnCard, names?: StepNames): string {
  if (card.items === null) {
    const text = card.summary ?? "";
    return text.length > GIST_LIMIT ? text.slice(0, GIST_LIMIT - 1) + "…" : text;
  }
  const tools = card.items.filter((i): i is Extract<StoryItem, { t: "tool" }> => i.t === "tool");
  const count = (pred: (i: Extract<StoryItem, { t: "tool" }>) => boolean) => tools.filter(pred).reduce((n, i) => n + (i.count ?? 1), 0);
  const written = [...new Set(tools.filter((i) => i.tool === "workspace_write_file" && i.ok && i.path).map((i) => i.path!))];
  const failed = count((i) => !i.ok);
  const parts: string[] = [];
  if (written.length) parts.push("写入 " + written.join("、"));
  const reads = count((i) => i.ok && ["workspace_read_file", "knowledge_read", "assurance_read_evidence"].includes(i.tool));
  if (reads) parts.push(`读取 ${reads} 次`);
  if (failed) parts.push(`${failed} 次操作失败`);
  const last = [...card.items].reverse().find((i) => i.t === "verdict" || i.t === "submit" || i.t === "decision" || i.t === "method");
  if (last && last.t === "verdict") {
    const firstBad = last.reasons.find((r) => reasonGood(r.verdict) === false);
    parts.push((firstBad ?? last.reasons[0])?.text ?? "");
  } else if (last && last.t === "method") parts.push(`拆成 ${last.steps.length} 步：${last.steps.map((k) => keyTitle(names, k)).join(" → ")}`);
  else if (last && "text" in last && last.text) parts.push(last.text);
  const gist = parts.filter(Boolean).join(" · ");
  return gist.length > GIST_LIMIT ? gist.slice(0, GIST_LIMIT - 1) + "…" : gist;
}

const OPERATION_STATE: Record<string, string> = {
  SUCCEEDED: "已完成", FAILED: "失败", PENDING: "等待中", RUNNING: "进行中", UNKNOWN: "结果待核实",
};

/** 节点事件的一行。 */
export function eventLine(card: EventCard, names: StepNames): { icon: string; text: string; tone: Line["tone"] } {
  const step = stepLabel(names, card.task_id);
  switch (card.code) {
    case "mission_created": return { icon: "🚩", text: "任务开始", tone: "muted" };
    case "plan_committed": return { icon: "📋", text: `计划第 ${card.count ?? "?"} 版生效${card.text ? "：" + card.text : ""}`, tone: "plain" };
    case "review_failed": return { icon: "⚠️", text: `${step || "这一步"}没通过检查`, tone: "bad" };
    case "plan_rejected": return { icon: "⚠️", text: "拆分方案被系统退回，要求重拆", tone: "bad" };
    case "repair_requested": return { icon: "🔁", text: "请规划器想办法补救", tone: "plain" };
    case "operation": return { icon: "📦", text: `操作${card.text ? "：" + card.text : ""}${card.reason ? "（" + (OPERATION_STATE[card.reason] ?? card.reason) + "）" : ""}`, tone: card.reason === "SUCCEEDED" ? "good" : card.reason === "FAILED" ? "bad" : "plain" };
    case "result_rejected": return { icon: "⚠️", text: `${step || "这一步"}的结果没被收下（${reasonLabel(card.reason) || "原因未知"}）`, tone: "bad" };
    case "attempt_lost": return { icon: "⚠️", text: `${step || "这一步"}这次尝试中断（${reasonLabel(card.reason) || "原因未知"}）`, tone: "bad" };
    case "step_done": return { icon: "✔", text: `${step || "一个步骤"}完成`, tone: "good" };
    case "step_failed": return { icon: "✗", text: `${step || "一个步骤"}失败${card.reason ? "（" + reasonLabel(card.reason) + "）" : ""}`, tone: "bad" };
    case "comment": return { icon: "🗨", text: "评论：" + (card.text ?? ""), tone: "plain" };
    case "mission_completed": return { icon: "🏁", text: "任务完成", tone: "good" };
    case "mission_failed": return { icon: "🛑", text: `任务失败${card.reason ? "：" + reasonLabel(card.reason) : ""}`, tone: "bad" };
    case "mission_cancelled": return { icon: "⏹", text: "任务已取消", tone: "muted" };
    default: return { icon: "•", text: card.code, tone: "muted" };
  }
}

/** 顶部一句话：现在在干什么、做到第几步。 */
export function headline(story: Story, names: StepNames, missionStatus: string): string {
  const total = story.steps.length;
  const done = story.steps.filter((s) => s.done).length;
  const progress = total ? `已完成 ${done}/${total} 步` : "";
  const ending = MISSION_END[missionStatus];
  if (ending) return [ending, progress].filter(Boolean).join(" · ");
  const live = [...story.cards].reverse().find((c): c is TurnCard => c.kind !== "event" && (c.state === "running" || c.state === "waiting"));
  if (live) return [`正在进行：${turnTitle(live, names)}`, progress].filter(Boolean).join(" · ");
  return progress || (story.cards.length ? "等待下一步" : "还没有开始");
}

const MISSION_END: Record<string, string> = { COMPLETED: "任务完成", FAILED: "任务失败", CANCELLED: "任务已取消" };
