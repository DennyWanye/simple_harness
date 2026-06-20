// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * subagent-concurrency-driver WI-3.4 — 子代理并发进度面板。
 *
 * 订阅 subagentStore，渲染当前并发子代理的实时进度（queued/running/
 * completed/failed + kind 徽章）。runs 为空时不渲染（零侵入）。
 */
import { useSubagentStore } from "./subagentStore";

const STATUS_ICON: Record<string, string> = {
  queued: "⏳",
  running: "🔧",
  completed: "✅",
  failed: "❌",
};

const KIND_LABEL: Record<string, string> = {
  research: "调研",
  code: "编码",
  doc: "文档",
  web: "联网",
  fileops: "文件",
  general: "通用",
};

export function SubagentProgressPanel() {
  const runs = useSubagentStore((s) => s.runs);
  const clearTerminal = useSubagentStore((s) => s.clearTerminal);

  const list = Object.values(runs).sort((a, b) => a.ts - b.ts);
  if (list.length === 0) return null;

  const active = list.filter(
    (r) => r.status === "queued" || r.status === "running",
  ).length;

  return (
    <div
      data-testid="subagent-progress-panel"
      style={{
        margin: "6px 8px",
        padding: "8px 10px",
        borderRadius: 8,
        background: "rgba(120,140,200,0.10)",
        border: "1px solid rgba(120,140,200,0.25)",
        fontSize: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontWeight: 600,
          marginBottom: 4,
          color: "#445",
        }}
      >
        <span>
          🤖 子代理并发{" "}
          {active > 0 ? `· 运行中 ${active}/${list.length}` : `· 全部完成 (${list.length})`}
        </span>
        {active === 0 && (
          <button
            onClick={clearTerminal}
            style={{
              fontSize: 11,
              border: "none",
              background: "transparent",
              cursor: "pointer",
              color: "#778",
            }}
          >
            清除
          </button>
        )}
      </div>
      {list.map((r) => (
        <div
          key={r.run_id}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "2px 0",
            opacity: r.status === "completed" || r.status === "failed" ? 0.7 : 1,
          }}
        >
          <span>{STATUS_ICON[r.status] || "•"}</span>
          <span
            style={{
              fontSize: 10,
              padding: "1px 6px",
              borderRadius: 6,
              background: "rgba(90,110,170,0.18)",
              color: "#445",
              fontWeight: 600,
            }}
          >
            {KIND_LABEL[r.kind] || r.kind || "?"}
          </span>
          <span style={{ color: "#556" }}>{r.task_id || r.run_id}</span>
          <span style={{ marginLeft: "auto", color: "#889", fontSize: 11 }}>
            {r.status}
          </span>
        </div>
      ))}
    </div>
  );
}
