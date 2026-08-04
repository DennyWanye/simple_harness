import type { CheckpointView } from "./types";
import { Icon } from "../Icon";

export function CheckpointTimeline({ checkpoints, onFork }: { checkpoints: CheckpointView[]; onFork?: (id: string) => void }) {
  if (checkpoints.length === 0) return <div style={emptyStyle}>暂无 Checkpoint</div>;
  return (
    <ol style={{ listStyle: "none", padding: 8, margin: 0 }}>
      {checkpoints.map((checkpoint) => (
        <li key={checkpoint.checkpoint_id} style={rowStyle}>
          <span aria-hidden="true" style={markerStyle} />
          <span style={{ minWidth: 0, flex: 1, overflowWrap: "anywhere" }}>
            <strong style={{ display: "block", fontSize: 11 }}>{checkpoint.node_id ?? "checkpoint"}</strong>
            <span style={{ color: "#94a3b8", fontSize: 10 }}>{checkpoint.checkpoint_id.slice(0, 14)} · {checkpoint.status}</span>
            {checkpoint.can_fork === false && checkpoint.fork_reason && <span style={{ display: "block", marginTop: 2, color: "#64748b", fontSize: 9 }}>{checkpoint.fork_reason}</span>}
          </span>
          {onFork && <button type="button" title={checkpoint.fork_reason ?? "从这里创建分支"} aria-label="从这里创建分支" disabled={checkpoint.can_fork === false} onClick={() => onFork(checkpoint.checkpoint_id)} style={{ ...forkStyle, opacity: checkpoint.can_fork === false ? 0.45 : 1, cursor: checkpoint.can_fork === false ? "not-allowed" : "pointer" }}><Icon name="plus" size={13} /></button>}
        </li>
      ))}
    </ol>
  );
}

const rowStyle: React.CSSProperties = { minHeight: 48, display: "flex", alignItems: "center", gap: 9, borderBottom: "1px solid rgba(148,163,184,0.1)", color: "#e5e7eb" };
const markerStyle: React.CSSProperties = { width: 9, height: 9, borderRadius: "50%", background: "#38bdf8", boxShadow: "0 0 0 3px rgba(56,189,248,0.12)" };
const forkStyle: React.CSSProperties = { width: 29, height: 29, display: "grid", placeItems: "center", padding: 0, borderRadius: 5, border: "1px solid rgba(148,163,184,0.24)", background: "rgba(15,23,42,0.8)", color: "#cbd5e1" };
const emptyStyle: React.CSSProperties = { padding: 20, textAlign: "center", color: "#64748b", fontSize: 12 };
