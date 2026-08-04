import type { WorkflowEdgeView, WorkflowNodeView } from "./types";

export function WorkflowGraph({ nodes, edges }: { nodes: WorkflowNodeView[]; edges: WorkflowEdgeView[] }) {
  if (nodes.length === 0) return <div style={emptyStyle}>暂无节点</div>;
  const incoming = new Map<string, WorkflowEdgeView[]>();
  edges.forEach((edge) => incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge]));
  return (
    <div data-testid="workflow-graph" style={graphStyle}>
      {nodes.map((node, index) => (
        <div key={node.id} style={{ display: "contents" }}>
          {index > 0 && <div aria-hidden="true" style={lineStyle}>↓</div>}
          <div style={{ ...nodeStyle, borderColor: nodeColor(node.status) }}>
            <span style={{ ...dotStyle, background: nodeColor(node.status) }} />
            <span style={{ minWidth: 0, flex: 1 }}>
              <strong style={{ display: "block", fontSize: 12 }}>{node.label}</strong>
              <span style={{ color: "#94a3b8", fontSize: 10 }}>
                {node.status}{node.attempt ? ` · #${node.attempt}` : ""}
              </span>
              {node.error && <span style={{ display: "block", marginTop: 3, color: "#fca5a5", overflowWrap: "anywhere", fontSize: 10 }}>{node.error}</span>}
              {node.recovery_action && <span style={{ display: "block", marginTop: 2, color: "#fbbf24", overflowWrap: "anywhere", fontSize: 9 }}>{node.recovery_action}</span>}
            </span>
            {incoming.get(node.id)?.[0]?.label && <span style={edgeLabelStyle}>{incoming.get(node.id)?.[0]?.label}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function nodeColor(status: string) {
  if (status === "succeeded" || status === "completed") return "#34d399";
  if (status === "running") return "#38bdf8";
  if (status === "waiting") return "#fbbf24";
  if (status === "failed" || status === "blocked") return "#f87171";
  if (status === "retryable" || status === "succeeded_pending") return "#fb923c";
  return "#64748b";
}

const graphStyle: React.CSSProperties = { minHeight: 120, display: "grid", justifyItems: "stretch", alignContent: "start", padding: 10 };
const nodeStyle: React.CSSProperties = { width: "100%", minHeight: 52, display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", border: "1px solid", borderRadius: 7, background: "rgba(15,23,42,0.72)", color: "#e5e7eb" };
const dotStyle: React.CSSProperties = { width: 8, height: 8, borderRadius: "50%", flex: "0 0 auto" };
const lineStyle: React.CSSProperties = { height: 24, display: "grid", placeItems: "center", color: "#475569", fontSize: 14 };
const edgeLabelStyle: React.CSSProperties = { maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#64748b", fontSize: 10 };
const emptyStyle: React.CSSProperties = { padding: 20, textAlign: "center", color: "#64748b", fontSize: 12 };
