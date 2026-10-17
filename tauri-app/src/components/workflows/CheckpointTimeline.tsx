import type { CheckpointView } from "./types";
import { Icon } from "../Icon";
import "./CheckpointTimeline.styles.css";

export function CheckpointTimeline({
  checkpoints,
  onFork,
}: {
  checkpoints: CheckpointView[];
  onFork?: (id: string) => void;
}) {
  if (checkpoints.length === 0)
    return <div className="sh-CheckpointTimeline-1">暂无 Checkpoint</div>;
  return (
    <ol className="sh-CheckpointTimeline-2">
      {checkpoints.map((checkpoint) => (
        <li key={checkpoint.checkpoint_id} className="sh-CheckpointTimeline-3">
          <span aria-hidden="true" className="sh-CheckpointTimeline-4" />
          <span className="sh-CheckpointTimeline-5">
            <strong className="sh-CheckpointTimeline-6">
              {checkpoint.node_id ?? "checkpoint"}
            </strong>
            <span className="sh-CheckpointTimeline-7">
              {checkpoint.checkpoint_id.slice(0, 14)} · {checkpoint.status}
            </span>
            {checkpoint.can_fork === false && checkpoint.fork_reason && (
              <span className="sh-CheckpointTimeline-8">{checkpoint.fork_reason}</span>
            )}
          </span>
          {onFork && (
            <button
              type="button"
              title={checkpoint.fork_reason ?? "从这里创建分支"}
              aria-label="从这里创建分支"
              disabled={checkpoint.can_fork === false}
              onClick={() => onFork(checkpoint.checkpoint_id)}
              style={{
                ...forkStyle,
                opacity: checkpoint.can_fork === false ? 0.45 : 1,
                cursor: checkpoint.can_fork === false ? "not-allowed" : "pointer",
              }}
            >
              <Icon name="plus" size={13} />
            </button>
          )}
        </li>
      ))}
    </ol>
  );
}

const forkStyle: React.CSSProperties = {
  width: 29,
  height: 29,
  display: "grid",
  placeItems: "center",
  padding: 0,
  borderRadius: 5,
  border: "1px solid rgba(148,163,184,0.24)",
  background: "rgba(15,23,42,0.8)",
  color: "#cbd5e1",
};
