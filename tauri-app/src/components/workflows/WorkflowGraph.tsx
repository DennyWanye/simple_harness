import type { WorkflowEdgeView, WorkflowNodeView } from "./types";
import "./WorkflowGraph.styles.css";

export function WorkflowGraph({
  nodes,
  edges,
}: {
  nodes: WorkflowNodeView[];
  edges: WorkflowEdgeView[];
}) {
  if (nodes.length === 0) return <div className="sh-WorkflowGraph-1">暂无节点</div>;
  const incoming = new Map<string, WorkflowEdgeView[]>();
  edges.forEach((edge) => incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge]));
  return (
    <div data-testid="workflow-graph" className="sh-WorkflowGraph-2">
      {nodes.map((node, index) => (
        <div key={node.id} className="sh-WorkflowGraph-3">
          {index > 0 && (
            <div aria-hidden="true" className="sh-WorkflowGraph-4">
              ↓
            </div>
          )}
          <div style={{ ...nodeStyle, borderColor: nodeColor(node.status) }}>
            <span style={{ ...dotStyle, background: nodeColor(node.status) }} />
            <span className="sh-WorkflowGraph-5">
              <strong className="sh-WorkflowGraph-6">{node.label}</strong>
              <span className="sh-WorkflowGraph-7">
                {node.status}
                {node.attempt ? ` · #${node.attempt}` : ""}
              </span>
              {node.error && <span className="sh-WorkflowGraph-8">{node.error}</span>}
              {node.recovery_action && (
                <span className="sh-WorkflowGraph-9">{node.recovery_action}</span>
              )}
            </span>
            {incoming.get(node.id)?.[0]?.label && (
              <span className="sh-WorkflowGraph-10">{incoming.get(node.id)?.[0]?.label}</span>
            )}
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

const nodeStyle: React.CSSProperties = {
  width: "100%",
  minHeight: 52,
  display: "flex",
  alignItems: "center",
  gap: 9,
  padding: "8px 10px",
  border: "1px solid",
  borderRadius: 7,
  background: "rgba(15,23,42,0.72)",
  color: "#e5e7eb",
};
const dotStyle: React.CSSProperties = {
  width: 8,
  height: 8,
  borderRadius: "50%",
  flex: "0 0 auto",
};
