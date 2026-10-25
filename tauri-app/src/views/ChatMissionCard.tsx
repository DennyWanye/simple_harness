import { record, rows, text, type Json } from "../primary/sessionClient";
import "./ChatMissionCard.styles.css";
export function ChatMissionCard({
  task,
  cancel,
  inspect,
}: {
  task: Json;
  cancel: () => void;
  inspect: () => void;
}) {
  const detail = record(task.detail);
  const mission = record(detail.mission);
  const terminal = ["COMPLETED", "FAILED", "CANCELLED", "REJECTED"].includes(text(task.status));
  return (
    <section aria-label="后台任务" className="sh-ChatMissionCard-1">
      <strong>{text(mission.goal) || text(task.mission_id) || "任务创建中"}</strong>
      <p>{text(task.status)}</p>
      {rows(detail.tasks).map((step) => (
        <p key={text(step.id)}>
          {text(step.title) || text(step.goal)} · {text(step.status)}
        </p>
      ))}
      {rows(detail.artifacts).map((artifact) => (
        <p key={text(artifact.id)}>产物：{text(artifact.logical_path) || text(artifact.path)}</p>
      ))}
      <button onClick={inspect}>查看进度</button>
      {!terminal && <button onClick={cancel}>取消任务</button>}
    </section>
  );
}
