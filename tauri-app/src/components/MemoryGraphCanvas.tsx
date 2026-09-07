import { useEffect, useRef } from "react";
import cytoscape, { type Core } from "cytoscape";
import { graphElements } from "../primary/graphElements";
import { graphStyle } from "../primary/graphStyle";
import type { GraphEdge, GraphNode } from "../primary/graphRequests";

export type GraphSelection = { kind: "node" | "edge"; id: string } | null;
export function MemoryGraphCanvas({ nodes, edges, selected, onSelect, claimInitialReveal }: {
  nodes: GraphNode[]; edges: GraphEdge[]; selected: GraphSelection; onSelect: (selection: GraphSelection) => void;
  claimInitialReveal: () => boolean;
}) {
  const container = useRef<HTMLDivElement>(null), core = useRef<Core | null>(null);
  const select = useRef(onSelect);
  const reveal = () => container.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (!claimInitialReveal()) return;
      // Filtering and background refresh must not steal the user's scroll position.
      if (!document.activeElement?.matches("input, textarea, select, [contenteditable=true]")) {
        container.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [claimInitialReveal]);
  useEffect(() => { select.current = onSelect; }, [onSelect]);
  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({ container: container.current, elements: graphElements(nodes, edges), style: graphStyle(nodes.length),
      // Disconnected memories have no hierarchy: one breadth-first root row
      // crowds labels and makes fit shrink every word. Include real label bounds.
      layout: edges.length === 0
        ? { name: "grid", cols: Math.max(1, Math.ceil(Math.sqrt(nodes.length))),
            animate: false, padding: 32, avoidOverlap: true, nodeDimensionsIncludeLabels: true }
        : { name: "breadthfirst", directed: true, animate: false, padding: 32,
            spacingFactor: 1.3, nodeDimensionsIncludeLabels: true },
      // Ordinary wheel scroll belongs to the enclosing memory pane; buttons zoom.
      minZoom: 0.12, maxZoom: 3, userZoomingEnabled: false, boxSelectionEnabled: false, autounselectify: false });
    core.current = cy;
    cy.on("tap", "node, edge", (event) => {
      select.current({ kind: event.target.data("kind"), id: event.target.data("source_id") });
    });
    cy.on("tap", (event) => { if (event.target === cy) select.current(null); });
    const resize = () => { if (!cy.destroyed()) { cy.resize(); cy.fit(undefined, 32); } };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(container.current);
    window.addEventListener("resize", resize);
    return () => { observer?.disconnect(); window.removeEventListener("resize", resize); cy.destroy(); core.current = null; };
  }, [nodes, edges]);
  useEffect(() => {
    const cy = core.current;
    if (!cy) return;
    cy.$(":selected").unselect();
    if (selected) cy.getElementById(`${selected.kind}:${selected.id}`).select();
  }, [selected, nodes, edges]);
  return <div>
    <div style={{ display: "flex", gap: 8, margin: "8px 0" }}>
      <button onClick={() => { core.current?.zoom(Math.min(3, core.current.zoom() * 1.3)); reveal(); }}>放大</button>
      <button onClick={() => { core.current?.zoom(Math.max(0.12, core.current.zoom() / 1.3)); reveal(); }}>缩小</button>
      <button onClick={() => { core.current?.fit(undefined, 32); reveal(); }}>显示全图</button>
    </div>
    <div ref={container} role="img" aria-label={`记忆关系图：${nodes.length}条记忆，${edges.length}条关系。可使用下方文字列表选择。`}
      style={{ width: "100%", height: "min(340px, 40vh)", minWidth: 0, background: "#111827", borderRadius: 10, overflow: "hidden" }} />
  </div>;
}
