import type { WorkflowRunSummary } from "./types";

type Props = {
  runs: WorkflowRunSummary[];
  selectedRunId?: string | null;
  onSelect: (runId: string) => void;
};

const statusColor: Record<string, string> = {
  running: "#38bdf8",
  waiting: "#fbbf24",
  retryable: "#fb923c",
  cancel_requested: "#fb923c",
  cancelling: "#fb923c",
  blocked: "#f87171",
  completed: "#34d399",
  failed: "#f87171",
  cancelled: "#94a3b8",
  created: "#a78bfa",
};

export function RunList({ runs, selectedRunId, onSelect }: Props) {
  if (runs.length === 0) {
    return <div style={emptyStyle}>暂无运行记录</div>;
  }
  return (
    <div role="list" aria-label="Workflow runs" style={{ display: "grid", gap: 1 }}>
      {runs.map((run) => (
        <button
          key={run.run_id}
          type="button"
          role="listitem"
          aria-current={selectedRunId === run.run_id ? "true" : undefined}
          onClick={() => onSelect(run.run_id)}
          style={{
            ...rowStyle,
            background: selectedRunId === run.run_id ? "rgba(56,189,248,0.12)" : "transparent",
            borderColor: selectedRunId === run.run_id ? "rgba(56,189,248,0.32)" : "transparent",
          }}
        >
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: statusColor[run.status] ?? "#94a3b8" }} />
          <span style={{ minWidth: 0, flex: 1, textAlign: "left" }}>
            <strong style={{ display: "block", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {run.workflow_name}
            </strong>
            <span style={{ display: "block", fontSize: 10, color: "#94a3b8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {run.run_id.slice(0, 12)} · v{run.workflow_version}
            </span>
          </span>
          <span style={{ fontSize: 10, color: statusColor[run.status] ?? "#94a3b8" }}>{run.status}</span>
        </button>
      ))}
    </div>
  );
}

const rowStyle: React.CSSProperties = {
  width: "100%",
  minHeight: 48,
  display: "flex",
  alignItems: "center",
  gap: 9,
  padding: "7px 9px",
  color: "#e5e7eb",
  border: "1px solid transparent",
  borderRadius: 6,
  cursor: "pointer",
  background: "transparent",
};
const emptyStyle: React.CSSProperties = { padding: 20, textAlign: "center", color: "#64748b", fontSize: 12 };
