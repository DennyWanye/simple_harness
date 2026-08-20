import { useMemo, useState } from "react";

import {
  buildActivityTimeline,
  type ActivityTimelineItem,
} from "../AgentActivityMessage";
import {
  requestHarnessPublicToolDetails,
} from "../../stores/harnessPublicSnapshotStore";

function compact(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value ?? "");
  }
}

function statusTone(status: string): { icon: string; label: string; color: string } {
  if (["completed", "completed_with_recovery", "succeeded", "settled"].includes(status)) {
    return { icon: "✓", label: "已完成", color: "#34d399" };
  }
  if (["failed", "rejected"].includes(status)) return { icon: "×", label: "失败", color: "#f87171" };
  if (status === "cancelled") return { icon: "–", label: "已取消", color: "#94a3b8" };
  if (status === "waiting") return { icon: "…", label: "等待", color: "#fbbf24" };
  return { icon: "●", label: "进行中", color: "#38bdf8" };
}

function durationLabel(durationMs?: number): string | null {
  if (durationMs == null || !Number.isFinite(durationMs)) return null;
  return durationMs < 1000 ? `${Math.round(durationMs)}ms` : `${(durationMs / 1000).toFixed(1)}s`;
}

function TimelineRow({ item }: { item: ActivityTimelineItem }) {
  const [inputOpen, setInputOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);
  const tone = statusTone(item.status);
  const isTool = item.kind === "tool";
  const inputAvailable = item.input !== undefined || Boolean(item.detailRef);
  const resultAvailable = item.result !== undefined || Boolean(item.detailRef);
  const rowId = `activity-${item.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const duration = durationLabel(item.durationMs);
  return (
    <details className="agent-activity-timeline__item">
      <summary className="agent-activity-timeline__summary">
        <span aria-hidden="true" style={{ color: tone.color }}>{tone.icon}</span>
        <span className="agent-activity-timeline__title" title={item.target}>{item.title || "执行记录"}</span>
        {duration ? <span className="agent-activity-timeline__meta">{duration}</span> : null}
        <span className="agent-activity-timeline__status" style={{ color: tone.color }}>{tone.label}</span>
      </summary>
      <div id={rowId} className="agent-activity-timeline__detail">
          {item.action && item.action !== item.title ? <div className="agent-activity-timeline__line">动作：{item.action}</div> : null}
          {item.text ? <p className="agent-activity-timeline__text">{item.text}</p> : null}
          {isTool ? (
            <div className="agent-activity-timeline__tool-actions">
              {inputAvailable ? (
                <button
                  type="button"
                  aria-expanded={inputOpen}
                  onClick={() => {
                    if (!inputOpen && item.input === undefined && item.detailRef) requestHarnessPublicToolDetails(item.detailRef);
                    setInputOpen((value) => !value);
                  }}
                >{inputOpen ? "收起输入详情" : "输入详情"}</button>
              ) : null}
              {resultAvailable ? (
                <button
                  type="button"
                  aria-expanded={resultOpen}
                  onClick={() => {
                    if (!resultOpen && item.result === undefined && item.detailRef) requestHarnessPublicToolDetails(item.detailRef);
                    setResultOpen((value) => !value);
                  }}
                >{resultOpen ? "收起结果详情" : "结果详情"}</button>
              ) : null}
            </div>
          ) : null}
          {inputOpen ? <pre className="agent-activity-timeline__pre">{item.input !== undefined ? compact(item.input) : "正在读取可公开的工具输入…"}</pre> : null}
          {resultOpen ? <pre className="agent-activity-timeline__pre">{item.result !== undefined ? compact(item.result) : "正在读取可公开的工具结果…"}</pre> : null}
          {item.truncated ? <small className="agent-activity-timeline__truncated">内容较长，已安全截断</small> : null}
          {!item.text && !isTool && item.status === "unknown" ? <span className="agent-activity-timeline__muted">记录不完整，未推断成功。</span> : null}
      </div>
    </details>
  );
}

export function ActivityTimeline({ snapshot }: { snapshot: unknown }) {
  const items = useMemo(() => buildActivityTimeline(snapshot), [snapshot]);
  if (items.length === 0) {
    return <div className="harness-inspector__empty">Agent 还没有产生执行记录。</div>;
  }
  return (
    <div className="agent-activity-timeline" data-testid="agent-activity-timeline" aria-label="Agent 执行时间线">
      {items.map((item) => <TimelineRow key={item.id} item={item} />)}
    </div>
  );
}
