import type { StylesheetJson } from "cytoscape";

import { palette, type ThemeName } from "../theme/tokens";

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

/**
 * cytoscape 渲染在 <canvas> 上，无法解析 CSS 变量，所以这里是全项目
 * 唯一按主题取**字面值**的地方（取自 tokens.ts 的 `palette`）。
 * 记忆类型靠形状区分，颜色只用一档强调色深浅 + 争议状态色。
 */
export function graphStyle(nodeCount: number, theme: ThemeName = "dark"): StylesheetJson {
  const dense = nodeCount > 40, medium = nodeCount > 12;
  const p = palette[theme];
  return [
    { selector: "node", style: { label: "data(label)", "background-color": p.accent, color: p.text,
      width: dense ? 30 : 40, height: dense ? 30 : 40, "font-size": dense ? 10 : 12,
      "text-wrap": "wrap", "text-max-width": dense ? "96px" : medium ? "112px" : "130px", "text-overflow-wrap": "anywhere",
      "text-valign": "center", "text-halign": "right", "text-margin-x": dense ? 8 : 12,
      "text-background-color": p.card, "text-background-opacity": dense ? 0.9 : 0, "text-background-padding": "2px",
      "border-width": 1, "border-color": p.hairlineStrong } },
    { selector: 'node[memory_type = "episode"]', style: { shape: "ellipse" } },
    { selector: 'node[memory_type = "semantic"]', style: { shape: "round-rectangle" } },
    { selector: 'node[memory_type = "procedure"]', style: { shape: "hexagon" } },
    { selector: 'node[memory_type = "prospective"]', style: { shape: "diamond" } },
    { selector: 'node[tentative = "yes"]', style: { "border-style": "dashed", "background-opacity": 0.45 } },
    { selector: 'node[contested = "yes"]', style: { "border-color": p.warning, "border-width": 3 } },
    { selector: "edge", style: { label: "data(label)", width: 1, "line-color": p.text3, "target-arrow-color": p.text3,
      "target-arrow-shape": "triangle", "curve-style": "bezier", color: p.text2, "font-size": dense ? 9 : 11,
      "text-background-color": p.card, "text-background-opacity": 1, "text-background-padding": "3px" } },
    // 选中的记忆总是显示完整标签并压在其它元素之上，密集图里也能读全。
    { selector: "node:selected", style: { label: "data(full_label)", "font-size": 12, "text-max-width": "200px",
      "text-background-opacity": 1, "z-index": 10, "border-color": p.text, "border-width": 3 } },
    { selector: "edge:selected", style: { "line-color": p.accent, "target-arrow-color": p.accent, "z-index": 10 } },
  ];
}
