// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 执行图 → elk 输入（纯函数，jsdom 可测）与 elk 输出 → 扁平坐标。
 *
 * - 计划结构：复合步骤是框，框里是它拆出的子步骤；先后顺序是紫色箭头，数据依赖默认隐藏。
 * - 执行过程：每个步骤框里是它自己的"执行 → 审阅 → 修补请求 → 规划 → 再执行"链；规划与计划版本、
 *   终审放在它们所属的复合步骤框里。箭头一律从原因指向结果（执行→审阅、审阅不通过→修补请求→规划→
 *   再执行），画出来是一个回路，但每条都是两次不同执行之间的因果边，不是调度图里的回边。
 * - 折叠的复合步骤不带子节点；连到被折叠节点的结构线改连到折叠框，执行过程线直接隐藏。
 */
import type { ExecEdge, ExecNode, ExecutionView, StructureNode } from "./model";

export const LEAF_W = 220;
export const LEAF_H = 64;
export const EXEC_W = 200;
export const EXEC_H = 56;
export const HEADER_H = 64; // group title row + status row (2026-09-26 真机：40 时子步骤压住标题)

export type ElkInput = {
  id: string; width?: number; height?: number; layoutOptions?: Record<string, string>;
  children?: ElkInput[]; edges?: { id: string; sources: string[]; targets: string[] }[];
};
export type ElkOutput = { id: string; x?: number; y?: number; width?: number; height?: number; children?: ElkOutput[] };
export type Placed = { id: string; parent: string | null; x: number; y: number; width: number; height: number; group: boolean };
export type DrawEdge = { kind: "order" | "data" | "process" | "rework"; source: string; target: string };

const ROOT_OPTIONS = {
  "elk.algorithm": "layered", "elk.direction": "DOWN", "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.spacing.nodeNode": "28", "elk.layered.spacing.nodeNodeBetweenLayers": "44",
};
const GROUP_OPTIONS = { "elk.padding": `[top=${HEADER_H + 8},left=14,bottom=14,right=14]` };

export type LayoutPlan = { elk: ElkInput; edges: DrawEdge[]; visible: Set<string>; groups: Set<string> };

/** 每个执行过程节点放在哪个结构框里（按记录下来的因果边，不按时间或名字猜）。 */
export function homesOf(view: ExecutionView): Map<string, string> {
  const structure = new Set(view.structure.map((n) => n.occurrence_id));
  const root = view.structure.find((n) => !n.parent)?.occurrence_id ?? view.structure[0]?.occurrence_id ?? "";
  const out = (kind: string, source: string) => view.edges.find((e) => e.kind === kind && e.source === source);
  const into = (kind: string, target: string) => view.edges.find((e) => e.kind === kind && e.target === target);
  const homes = new Map<string, string>();
  const byKind = (kind: ExecNode["kind"]) => view.nodes.filter((n) => n.kind === kind);
  for (const node of byKind("attempt")) {
    const occurrence = String(node.raw.occurrence_id ?? "");
    homes.set(node.node_id, structure.has(occurrence) ? occurrence : root);
  }
  for (const node of [...byKind("check"), ...byKind("operation")]) {
    const attempt = out(node.kind === "check" ? "review_of" : "operation_of", node.node_id)?.target;
    homes.set(node.node_id, (attempt && homes.get(attempt)) || root);
  }
  for (const node of byKind("repair_request")) {
    const attempt = into("repair_requested", node.node_id)?.source;
    homes.set(node.node_id, (attempt && homes.get(attempt)) || root);
  }
  for (const node of byKind("planning")) {
    const repair = view.edges.find((e) => e.kind === "decision_for" && e.source === node.node_id && e.target_layer === "execution");
    const goal = view.edges.find((e) => e.kind === "decision_for" && e.source === node.node_id && e.target_layer === "structure");
    homes.set(node.node_id, (repair && homes.get(repair.target)) || (goal && structure.has(goal.target) ? goal.target : root));
  }
  for (const node of byKind("plan_revision")) {
    const planner = into("committed_as", node.node_id)?.source;
    homes.set(node.node_id, (planner && homes.get(planner)) || root);
  }
  for (const node of byKind("review")) {
    const subject = out("reviews", node.node_id)?.target;
    homes.set(node.node_id, subject && structure.has(subject) ? subject : root);
  }
  return homes;
}

/** 执行过程边改成"原因 → 结果"的画法。 */
export function processEdges(nodes: ExecNode[], edges: ExecEdge[]): DrawEdge[] {
  const checkOf = new Map<string, string>();
  for (const e of edges) if (e.kind === "review_of") checkOf.set(e.target, e.source); // attempt → its (latest) check
  const retried = new Set(edges.filter((e) => e.kind === "retry_authorized").map((e) => e.target));
  const known = new Set(nodes.map((n) => n.node_id));
  const out: DrawEdge[] = [];
  const add = (kind: DrawEdge["kind"], source: string | undefined, target: string | undefined) => {
    if (source && target && source !== target && known.has(source) && known.has(target)) out.push({ kind, source, target });
  };
  for (const e of edges) {
    if (e.target_layer === "structure") continue;
    switch (e.kind) {
      case "review_of": add("process", e.target, e.source); break;
      case "operation_of": add("process", checkOf.get(e.target) ?? e.target, e.source); break;
      case "repair_requested": add("process", checkOf.get(e.source) ?? e.source, e.target); break;
      case "decision_for": add("process", e.target, e.source); break;
      case "retry_authorized": add("rework", e.source, e.target); break;
      case "rework_of": if (!retried.has(e.source)) add("rework", checkOf.get(e.target) ?? e.target, e.source); break;
      case "committed_as": add("process", e.source, e.target); break;
      case "supersedes": add("process", e.target, e.source); break;
    }
  }
  return out;
}

export function buildElkGraph(view: ExecutionView, collapsed: ReadonlySet<string>, showData: boolean,
                              showProcess: boolean): LayoutPlan {
  const known = new Set(view.structure.map((n) => n.occurrence_id));
  const parentOf = new Map<string, string | null>(view.structure.map((n) => [n.occurrence_id, n.parent && known.has(n.parent) ? n.parent : null]));
  const homes = showProcess ? homesOf(view) : new Map<string, string>();
  const children = new Map<string | null, string[]>();
  for (const node of view.structure) {
    const parent = parentOf.get(node.occurrence_id) ?? null;
    children.set(parent, [...(children.get(parent) ?? []), node.occurrence_id]);
  }
  const execIn = new Map<string, string[]>();
  for (const node of view.nodes) {
    const home = homes.get(node.node_id);
    if (home) execIn.set(home, [...(execIn.get(home) ?? []), node.node_id]);
  }
  const representative = (id: string): string => {
    let shown = id;
    for (let at = parentOf.get(id) ?? null; at; at = parentOf.get(at) ?? null) if (collapsed.has(at)) shown = at;
    return shown;
  };
  const visible = new Set<string>();
  const groups = new Set<string>();
  const nodes = new Map<string, ElkInput>();
  const build = (id: string): ElkInput => {
    visible.add(id);
    const kids = collapsed.has(id) ? [] : [...(children.get(id) ?? []).map(build),
      ...(execIn.get(id) ?? []).map((exec): ElkInput => {
        visible.add(exec);
        const leaf = { id: exec, width: EXEC_W, height: EXEC_H };
        nodes.set(exec, leaf);
        return leaf;
      })];
    const node: ElkInput = kids.length
      ? { id, layoutOptions: GROUP_OPTIONS, children: kids, edges: [] }
      : { id, width: LEAF_W, height: LEAF_H };
    if (kids.length) groups.add(id);
    nodes.set(id, node);
    return node;
  };
  const root: ElkInput = { id: "__root__", layoutOptions: ROOT_OPTIONS, children: (children.get(null) ?? []).map(build), edges: [] };
  // an execution node's frame chain: its home, then the home's ancestors
  const chain = (id: string): string[] => {
    const out: string[] = [];
    const start = known.has(id) ? parentOf.get(id) ?? null : homes.get(id) ?? null;
    for (let at = start; at; at = parentOf.get(at) ?? null) out.push(at);
    return out;
  };
  const seen = new Set<string>();
  const edges: DrawEdge[] = [];
  const place = (kind: DrawEdge["kind"], source: string, target: string) => {
    const key = kind + ":" + source + ">" + target;
    if (source === target || seen.has(key)) return;
    const up = chain(source);
    if (up.includes(target) || chain(target).includes(source)) return; // a line into its own frame says nothing
    seen.add(key);
    const common = chain(target).find((id) => up.includes(id));
    const holder = common ? nodes.get(common) ?? root : root;
    holder.edges = [...(holder.edges ?? []), { id: key, sources: [source], targets: [target] }];
    edges.push({ kind, source, target });
  };
  for (const edge of view.structureEdges) {
    if (edge.kind === "data" && !showData) continue;
    if (!known.has(edge.source) || !known.has(edge.target)) continue;
    place(edge.kind, representative(edge.source), representative(edge.target));
  }
  if (showProcess) {
    for (const edge of processEdges(view.nodes, view.edges)) {
      if (visible.has(edge.source) && visible.has(edge.target)) place(edge.kind, edge.source, edge.target);
    }
  }
  return { elk: root, edges, visible, groups };
}

/** elk 输出的嵌套坐标（相对父节点）拍平，React Flow 的子节点也用相对坐标。 */
export function flatten(output: ElkOutput, groups: ReadonlySet<string>): Placed[] {
  const out: Placed[] = [];
  const walk = (node: ElkOutput, parent: string | null) => {
    for (const child of node.children ?? []) {
      out.push({ id: child.id, parent, x: child.x ?? 0, y: child.y ?? 0,
        width: child.width ?? LEAF_W, height: child.height ?? LEAF_H, group: groups.has(child.id) });
      walk(child, child.id);
    }
  };
  walk(output, null);
  return out;
}

/** 节点多时默认折叠：不含"执行中/验收中"节点的复合步骤都折起来。 */
export const AUTO_COLLAPSE_OVER = 200;

/** 结构签名：节点集合、边、折叠与开关都不变时复用上次坐标，不重新排版（状态变化只换颜色文字）。 */
export function structureKey(view: ExecutionView, collapsed: ReadonlySet<string>, showData: boolean, showProcess: boolean): string {
  return [view.plan_revision, view.structure.map((n) => n.occurrence_id).join(","),
    showProcess ? view.nodes.map((n) => n.node_id).join(",") : "", showProcess ? view.edges.length : 0,
    [...collapsed].sort().join(","), showData, showProcess].join("|");
}

export type { StructureNode };
