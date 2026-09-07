import type { ElementDefinition } from "cytoscape";
import type { GraphEdge, GraphNode } from "./graphRequests";
import { FULL_LABEL_LIMIT, canvasLabel, labelBudget } from "./graphStyle";

export const memoryTypeLabels: Record<string, string> = {
  episode: "经历", semantic: "事实与偏好", procedure: "程序与方法", prospective: "未来意图",
};
export function graphElements(nodes: GraphNode[], edges: GraphEdge[]): ElementDefinition[] {
  const ids = new Set(nodes.map((n) => n.node_id));
  const budget = labelBudget(nodes.length);
  return [
    ...nodes.map((n) => ({ data: {
      id: `node:${n.node_id}`, source_id: n.node_id, kind: "node", memory_type: n.memory_type,
      label: canvasLabel(n.label, budget), full_label: Array.from(n.label).slice(0, FULL_LABEL_LIMIT).join(""),
      tentative: n.epistemic_status === "llm_inference" || ["draft", "candidate"].includes(n.lifecycle_state) ? "yes" : "no",
      contested: n.conflict_status === "contested" ? "yes" : "no",
    } })),
    ...edges.filter((e) => ids.has(e.source_node_id) && ids.has(e.target_node_id)).map((e) => ({ data: {
      id: `edge:${e.edge_id}`, source_id: e.edge_id, kind: "edge",
      source: `node:${e.source_node_id}`, target: `node:${e.target_node_id}`,
      label: e.relation_kind === "applies_to" ? "适用于" : e.label,
    } })),
  ];
}
