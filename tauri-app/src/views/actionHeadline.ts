import { asRecord as record, asText as text } from "../stores/missionsStore";

/** 审批摘要 → 一句人话。 */
export function actionHeadline(summary: unknown, action: unknown): string {
  const item = { ...record(action), ...record(summary) };
  const params = record(item.params);
  const target = text(item.target);
  const source = text(params.artifact_path);
  if (text(item.connector) === "file_publish" && text(item.operation) === "publish") {
    return source && source !== target ? `把 ${source} 发布为 ${target}` : `发布 ${target || source}`;
  }
  const name = [text(item.connector), text(item.operation)].filter(Boolean).join(".");
  return `${name || "动作"}${target ? ` → ${target}` : ""}`;
}
