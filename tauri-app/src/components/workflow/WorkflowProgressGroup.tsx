import { useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";

import type { Message, WorkflowProgressStatus } from "../../stores/sessionsStore";

export interface WorkflowProgressGroupProps {
  runId: string;
  summary?: Message;
  stages: Message[];
}

const STATUS_TONES: Record<WorkflowProgressStatus, {
  label: string;
  accent: string;
  soft: string;
}> = {
  running: { label: "进行中", accent: "#38bdf8", soft: "rgba(56,189,248,0.14)" },
  waiting: { label: "等待操作", accent: "#fbbf24", soft: "rgba(251,191,36,0.14)" },
  completed: { label: "已完成", accent: "#34d399", soft: "rgba(52,211,153,0.14)" },
  failed: { label: "失败", accent: "#f87171", soft: "rgba(248,113,113,0.14)" },
  cancelled: { label: "已取消", accent: "#94a3b8", soft: "rgba(148,163,184,0.14)" },
};

const METRIC_LABELS: Record<string, string> = {
  mode: "模式",
  question_count: "问题",
  active_branch_count: "分支",
  query_count: "查询",
  providers: "来源",
  candidates: "候选",
  kept: "保留",
  direct_sources: "一手来源",
  attempted: "尝试",
  succeeded: "成功",
  dropped: "丢弃",
  passages: "段落",
  iteration: "轮次",
  followup_count: "补充查询",
  new_evidence: "新证据",
  domains: "域名",
  sections: "章节",
  claim_count: "论断",
  citations: "引用",
  supported: "有支撑",
  unsupported: "无支撑",
  support_rate: "支撑率",
  artifact_count: "产物",
  report_bytes: "报告字节",
  status: "结果",
};

function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function safeDomId(runId: string): string {
  return `workflow-stages-${runId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function sortStages(stages: Message[]): Message[] {
  return [...stages].sort((a, b) =>
    (a.workflow_seq ?? Number.MAX_SAFE_INTEGER) -
      (b.workflow_seq ?? Number.MAX_SAFE_INTEGER) ||
    String(a.workflow_event_id || a.id).localeCompare(String(b.workflow_event_id || b.id)),
  );
}

export function WorkflowProgressGroup({
  runId,
  summary,
  stages,
}: WorkflowProgressGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const orderedStages = useMemo(() => sortStages(stages), [stages]);
  const fallback = orderedStages.at(-1);
  const status = summary?.workflow_status ??
    (fallback?.workflow_stage_id === "finalize" ? "completed" : "running");
  const tone = STATUS_TONES[status];
  const total = Math.max(
    1,
    summary?.workflow_total ?? fallback?.workflow_total ?? (stages.length > 0 ? 13 : 1),
  );
  const distinctCompleted = new Set(
    orderedStages.map((stage) => stage.workflow_stage_id).filter(Boolean),
  ).size;
  const legacyOrdinal = Math.max(
    summary?.workflow_ordinal ?? 0,
    summary?.workflow_display_ordinal ?? 0,
  );
  const completed = Math.min(
    total,
    Math.max(
      summary?.workflow_completed_count ?? 0,
      distinctCompleted,
      stages.length === 0 ? legacyOrdinal : 0,
    ),
  );
  const percent = status === "completed"
    ? 100
    : Math.min(99, Math.round((completed / total) * 100));
  const warningCount = Math.max(
    summary?.workflow_warning_count ?? 0,
    orderedStages.filter((stage) => stage.workflow_degraded).length,
  );
  const timestamps = [summary, ...orderedStages]
    .map((message) => message?.ts)
    .filter((ts): ts is number => typeof ts === "number" && Number.isFinite(ts));
  const startedAt = timestamps.length > 0 ? Math.min(...timestamps) : 0;
  const endedAt = Math.max(...timestamps, startedAt);
  const elapsedMs = summary?.workflow_elapsed_ms ?? Math.max(0, endedAt - startedAt);
  const name = summary?.workflow_name || fallback?.workflow_name || "深度调研";
  const currentStage = summary?.workflow_error || summary?.workflow_stage ||
    fallback?.workflow_stage || "等待总体进度";
  const detailsId = safeDomId(runId);

  const toggleFromKeyboard = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setExpanded((value) => !value);
  };

  return (
    <section
      data-testid={`workflow-progress-${runId}`}
      data-role="workflow_group"
      data-status={status}
      aria-label={`${name}总体进度`}
      style={{
        alignSelf: "stretch",
        height: stages.length === 0 ? 104 : undefined,
        minHeight: 104,
        maxHeight: stages.length === 0 ? 104 : undefined,
        boxSizing: "border-box",
        overflow: stages.length === 0 ? "hidden" : "visible",
        borderRadius: 8,
        border: `1px solid ${tone.accent}55`,
        background: "rgba(18, 24, 35, 0.92)",
      }}
    >
      <div style={{ padding: "11px 13px", display: "grid", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <strong style={titleStyle}>{name}</strong>
          <span style={{ ...statusStyle, color: tone.accent, background: tone.soft }}>
            {tone.label}
          </span>
        </div>
        <div
          title={currentStage}
          aria-live={status === "waiting" || status === "failed" ? "assertive" : "polite"}
          style={{
            ...detailStyle,
            color: summary?.workflow_error ? tone.accent : "#b9c2d0",
          }}
        >
          {currentStage}
        </div>
        <div
          role="progressbar"
          aria-label={`${name}进度`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-valuetext={`${tone.label}，${currentStage}，${completed}/${total}，已用时 ${formatDuration(elapsedMs)}`}
          style={progressTrackStyle}
        >
          <div style={{
            width: `${percent}%`,
            height: "100%",
            borderRadius: 3,
            background: tone.accent,
            transition: status === "running" ? "width 220ms ease" : "none",
          }} />
        </div>
        <div style={footerStyle}>
          <span>{completed}/{total} · 已用时 {formatDuration(elapsedMs)}</span>
          <span>{warningCount > 0 ? `⚠ ${warningCount}` : `${percent}%`}</span>
          {orderedStages.length > 0 ? (
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={detailsId}
              onClick={() => setExpanded((value) => !value)}
              onKeyDown={toggleFromKeyboard}
              style={toggleStyle}
            >
              {expanded ? "收起阶段" : `查看阶段 (${orderedStages.length})`}
            </button>
          ) : null}
        </div>
      </div>

      {orderedStages.length > 0 ? (
        <div id={detailsId} hidden={!expanded} style={stageListStyle}>
          {orderedStages.map((stage) => (
            <article
              key={stage.workflow_event_id || stage.id}
              data-testid={`workflow-stage-${stage.workflow_event_id || stage.id}`}
              data-degraded={stage.workflow_degraded ? "true" : "false"}
              style={stageBubbleStyle}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong style={{ color: "#dce8f5", fontSize: 11.5 }}>
                  {stage.workflow_stage || stage.workflow_stage_id || "已完成阶段"}
                </strong>
                <span style={{ color: "#8492a6", fontSize: 10 }}>
                  {stage.workflow_duration_ms === undefined
                    ? "已完成"
                    : formatDuration(stage.workflow_duration_ms)}
                </span>
              </div>
              {stage.text ? <div style={stageSummaryStyle}>{stage.text}</div> : null}
              {stage.workflow_metrics ? (
                <div style={metricsStyle}>
                  {Object.entries(stage.workflow_metrics)
                    .filter(([key]) => key in METRIC_LABELS)
                    .map(([key, value]) => (
                    <span key={key}>{METRIC_LABELS[key] || key}: {String(value)}</span>
                    ))}
                </div>
              ) : null}
              <div style={stageFooterStyle}>
                {stage.workflow_degraded ? <span>已自动降级</span> : <span>正常完成</span>}
                {stage.workflow_next_stage ? <span>下一步：{stage.workflow_next_stage}</span> : null}
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

const titleStyle: CSSProperties = {
  minWidth: 0,
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "#e8edf6",
  fontSize: 12.5,
};

const statusStyle: CSSProperties = {
  flexShrink: 0,
  padding: "2px 7px",
  borderRadius: 4,
  fontSize: 10.5,
  fontWeight: 600,
  whiteSpace: "nowrap",
};

const detailStyle: CSSProperties = {
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: 11.5,
  lineHeight: "18px",
};

const progressTrackStyle: CSSProperties = {
  height: 6,
  alignSelf: "center",
  borderRadius: 3,
  overflow: "hidden",
  background: "rgba(148,163,184,0.18)",
};

const footerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  color: "#7f8a99",
  fontSize: 10.5,
  lineHeight: "18px",
};

const toggleStyle: CSSProperties = {
  marginLeft: "auto",
  padding: 0,
  border: 0,
  background: "transparent",
  color: "#8bcdf4",
  font: "inherit",
  cursor: "pointer",
};

const stageListStyle: CSSProperties = {
  display: "grid",
  gap: 7,
  padding: "0 10px 10px",
};

const stageBubbleStyle: CSSProperties = {
  display: "grid",
  gap: 5,
  padding: "9px 10px",
  borderRadius: 7,
  border: "1px solid rgba(148, 163, 184, 0.2)",
  background: "rgba(255, 255, 255, 0.04)",
};

const stageSummaryStyle: CSSProperties = {
  color: "#b9c2d0",
  fontSize: 11,
  lineHeight: 1.45,
};

const metricsStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "3px 9px",
  color: "#91a1b5",
  fontSize: 10,
};

const stageFooterStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 8,
  color: "#8492a6",
  fontSize: 10,
};
