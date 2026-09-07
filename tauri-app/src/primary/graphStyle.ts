import type { StylesheetJson } from "cytoscape";

/** 画布标签预算：记忆越多，画布上的标签越短；完整标签始终在文字列表、详情栏与选中态可见。 */
export function labelBudget(nodeCount: number): number {
  if (nodeCount <= 12) return 70;
  if (nodeCount <= 40) return 32;
  return 18;
}
export function canvasLabel(label: string, budget: number): string {
  const chars = Array.from(label);
  return chars.length <= budget ? label : `${chars.slice(0, Math.max(1, budget - 1)).join("")}…`;
}
export const FULL_LABEL_LIMIT = 140;

export function graphStyle(nodeCount: number): StylesheetJson {
  const dense = nodeCount > 40, medium = nodeCount > 12;
  return [
    { selector: "node", style: { label: "data(label)", "background-color": "#8baaff", color: "#f1f5f9",
      width: dense ? 32 : 44, height: dense ? 32 : 44, "font-size": dense ? 10 : 12,
      "text-wrap": "wrap", "text-max-width": dense ? "96px" : medium ? "112px" : "130px", "text-overflow-wrap": "anywhere",
      "text-valign": "center", "text-halign": "right", "text-margin-x": dense ? 8 : 12,
      "text-background-color": "#111827", "text-background-opacity": dense ? 0.85 : 0, "text-background-padding": "2px",
      "border-width": 2, "border-color": "#cbd5e1" } },
    { selector: 'node[memory_type = "episode"]', style: { shape: "ellipse", "background-color": "#57bd9b" } },
    { selector: 'node[memory_type = "semantic"]', style: { shape: "round-rectangle" } },
    { selector: 'node[memory_type = "procedure"]', style: { shape: "hexagon", "background-color": "#dfb970" } },
    { selector: 'node[memory_type = "prospective"]', style: { shape: "diamond", "background-color": "#c3a0ee" } },
    { selector: 'node[tentative = "yes"]', style: { "border-style": "dashed", "background-opacity": 0.6 } },
    { selector: 'node[contested = "yes"]', style: { "border-color": "#fb923c", "border-width": 5 } },
    { selector: "edge", style: { label: "data(label)", width: 2, "line-color": "#94a3b8", "target-arrow-color": "#94a3b8",
      "target-arrow-shape": "triangle", "curve-style": "bezier", color: "#cbd5e1", "font-size": dense ? 9 : 11,
      "text-background-color": "#111827", "text-background-opacity": 1, "text-background-padding": "3px" } },
    // 选中的记忆总是显示完整标签并压在其它元素之上，密集图里也能读全。
    { selector: "node:selected", style: { label: "data(full_label)", "font-size": 12, "text-max-width": "200px",
      "text-background-opacity": 1, "z-index": 10, "border-color": "#ffffff", "border-width": 5 } },
    { selector: "edge:selected", style: { "line-color": "#ffffff", "target-arrow-color": "#ffffff", "z-index": 10 } },
  ];
}
