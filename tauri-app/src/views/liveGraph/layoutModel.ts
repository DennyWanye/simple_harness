// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 运行视图 → elk 输入（纯函数，jsdom 可测）与 elk 输出 → 扁平坐标。
 * 复合任务是父节点，子任务放进它的 children；折叠的复合任务不带 children，
 * 连到被折叠子任务的线改连到折叠框；线放在两端最近公共祖先里（elk 的要求）。
 */
import type { LiveEdge, LiveGraph } from "./model";

export const LEAF_W = 220;
export const LEAF_H = 64;
export const HEADER_H = 64; // group title row + status row (2026-09-26 真机：40 时子步骤压住标题)

export type ElkInput = {
  id: string; width?: number; height?: number; layoutOptions?: Record<string, string>;
  children?: ElkInput[]; edges?: { id: string; sources: string[]; targets: string[] }[];
};
export type ElkOutput = { id: string; x?: number; y?: number; width?: number; height?: number; children?: ElkOutput[] };
export type Placed = { id: string; parent: string | null; x: number; y: number; width: number; height: number; group: boolean };

const ROOT_OPTIONS = {
  "elk.algorithm": "layered", "elk.direction": "DOWN", "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.spacing.nodeNode": "28", "elk.layered.spacing.nodeNodeBetweenLayers": "44",
};
const GROUP_OPTIONS = { "elk.padding": `[top=${HEADER_H + 8},left=14,bottom=14,right=14]` };

export type LayoutPlan = { elk: ElkInput; edges: LiveEdge[]; visible: Set<string>; groups: Set<string> };

export function buildElkGraph(graph: LiveGraph, collapsed: ReadonlySet<string>, showData: boolean): LayoutPlan {
  const known = new Set(graph.nodes.map((n) => n.occurrence_id));
  const parentOf = new Map(graph.nodes.map((n) => [n.occurrence_id, n.parent && known.has(n.parent) ? n.parent : null]));
  const children = new Map<string | null, string[]>();
  for (const node of graph.nodes) {
    const parent = parentOf.get(node.occurrence_id) ?? null;
    children.set(parent, [...(children.get(parent) ?? []), node.occurrence_id]);
  }
  // the visible stand-in of a node: its outermost collapsed ancestor, else itself
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
    const kids = collapsed.has(id) ? [] : children.get(id) ?? [];
    const node: ElkInput = kids.length
      ? { id, layoutOptions: GROUP_OPTIONS, children: kids.map(build), edges: [] }
      : { id, width: LEAF_W, height: LEAF_H };
    if (kids.length) groups.add(id);
    nodes.set(id, node);
    return node;
  };
  const root: ElkInput = { id: "__root__", layoutOptions: ROOT_OPTIONS, children: (children.get(null) ?? []).map(build), edges: [] };
  const chain = (id: string): string[] => {
    const out: string[] = [];
    for (let at = parentOf.get(id) ?? null; at; at = parentOf.get(at) ?? null) out.push(at);
    return out;
  };
  const seen = new Set<string>();
  const edges: LiveEdge[] = [];
  for (const edge of graph.edges) {
    if (edge.kind === "data" && !showData) continue;
    if (!known.has(edge.source) || !known.has(edge.target)) continue;
    const source = representative(edge.source), target = representative(edge.target);
    const key = edge.kind + ":" + source + ">" + target;
    if (source === target || seen.has(key)) continue;
    const up = chain(source);
    if (up.includes(target) || chain(target).includes(source)) continue; // a line into its own frame says nothing
    seen.add(key);
    const common = chain(target).find((id) => up.includes(id));
    const holder = common ? nodes.get(common) ?? root : root;
    holder.edges = [...(holder.edges ?? []), { id: key, sources: [source], targets: [target] }];
    edges.push({ kind: edge.kind, source, target });
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

/** 节点多时默认折叠：不含"运行中/验证中"节点的复合任务都折起来。 */
export const AUTO_COLLAPSE_OVER = 200;

/** 结构签名：同一版本、同样的折叠与开关时复用上次坐标，不重新排版。 */
export function structureKey(graph: LiveGraph, collapsed: ReadonlySet<string>, showData: boolean): string {
  return [graph.plan_revision, graph.nodes.length, [...collapsed].sort().join(","), showData].join("|");
}
