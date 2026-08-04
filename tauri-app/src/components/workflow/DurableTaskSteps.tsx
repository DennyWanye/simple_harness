import { useState, type CSSProperties } from "react";

import type {
  WorkflowTaskStep,
  WorkflowTaskTool,
  WorkflowTaskTrace,
} from "../AgentActivityMessage";
import { requestHarnessPublicToolDetails } from "../../stores/harnessPublicSnapshotStore";

const STEP_TONE: Record<string, { icon: string; label: string; color: string }> = {
  completed: { icon: "✓", label: "已完成", color: "#34d399" },
  completed_with_recovery: { icon: "✓", label: "修复后完成", color: "#34d399" },
  succeeded: { icon: "✓", label: "已完成", color: "#34d399" },
  running: { icon: "●", label: "进行中", color: "#38bdf8" },
  waiting: { icon: "…", label: "等待", color: "#fbbf24" },
  failed: { icon: "×", label: "失败", color: "#f87171" },
  cancelled: { icon: "–", label: "已取消", color: "#94a3b8" },
  pending: { icon: "○", label: "待执行", color: "#8492a6" },
};

function toneFor(step: WorkflowTaskStep) {
  return STEP_TONE[step.current ? "running" : step.status] ?? STEP_TONE.pending;
}

function compactValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value ?? "");
  }
}

function toolSummary(tool: WorkflowTaskTool): string {
  return [tool.name, tool.action, tool.target].filter(Boolean).join(" · ");
}

function CompactTool({ tool }: { tool: WorkflowTaskTool }) {
  const [inputExpanded, setInputExpanded] = useState(false);
  const [resultExpanded, setResultExpanded] = useState(false);
  const detailId = `task-tool-${tool.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const resultId = `${detailId}-result`;
  const status = tool.status === "cancelled"
    ? "已取消"
    : tool.status === "rejected"
    ? "已拒绝"
    : tool.ok === false
    ? "失败"
    : tool.ok === true
    ? "完成"
    : "执行中";
  const color = ["cancelled", "rejected"].includes(tool.status)
    ? "#94a3b8"
    : tool.ok === false
    ? "#f87171"
    : tool.ok === true
    ? "#34d399"
    : "#38bdf8";
  const duration = typeof tool.durationMs === "number"
    ? tool.durationMs < 1000 ? `${Math.round(tool.durationMs)}ms` : `${(tool.durationMs / 1000).toFixed(1)}s`
    : null;

  return (
    <div data-testid={`compact-tool-${tool.id}`} style={toolRowStyle}>
      <div style={toolButtonStyle}>
        <span aria-hidden="true" style={{ color }}>
          {["cancelled", "rejected"].includes(tool.status)
            ? "–"
            : tool.ok === false
            ? "×"
            : tool.ok === true
            ? "✓"
            : "●"}
        </span>
        <span title={toolSummary(tool)} style={toolNameStyle}>{toolSummary(tool)}</span>
        {duration ? <span style={metaStyle}>{duration}</span> : null}
        <span style={{ color, fontSize: 10 }}>{status}</span>
        <button
          type="button"
          aria-expanded={inputExpanded}
          aria-controls={detailId}
          aria-label={`${inputExpanded ? "收起" : "查看"}${toolSummary(tool)}输入`}
          onClick={() => {
            if (!inputExpanded && tool.args === undefined) requestHarnessPublicToolDetails(tool.detailRef);
            setInputExpanded((value) => !value);
          }}
          style={toolMiniButtonStyle}
        >输入 {inputExpanded ? "▴" : "▾"}</button>
        {tool.resultAvailable ? (
          <button
            type="button"
            aria-expanded={resultExpanded}
            aria-controls={resultId}
            aria-label={`${resultExpanded ? "收起" : "查看"}${toolSummary(tool)}结果`}
            onClick={() => {
              if (!resultExpanded && tool.result === undefined) requestHarnessPublicToolDetails(tool.detailRef);
              setResultExpanded((value) => !value);
            }}
            style={toolMiniButtonStyle}
          >结果 {resultExpanded ? "▴" : "▾"}</button>
        ) : null}
      </div>
      {inputExpanded ? (
        <div id={detailId} style={toolDetailStyle}>
          <strong>输入</strong>
          {tool.args !== undefined
            ? <pre style={preStyle}>{compactValue(tool.args)}</pre>
            : <span>{tool.unavailableReason ?? "正在读取可公开的工具输入…"}</span>}
        </div>
      ) : null}
      {resultExpanded ? (
        <div id={resultId} style={toolDetailStyle}>
          <strong>结果</strong>
          {tool.result !== undefined
            ? <pre style={preStyle}>{compactValue(tool.result)}</pre>
            : <span>{tool.unavailableReason ?? "正在读取可公开的工具结果…"}</span>}
          {tool.truncated ? <small>内容较长，已安全截断</small> : null}
        </div>
      ) : null}
    </div>
  );
}

export function DurableTaskSteps({ trace }: { trace: WorkflowTaskTrace }) {
  const [stepOverrides, setStepOverrides] = useState<Record<string, boolean>>({});

  const completed = trace.steps.filter((step) =>
    ["completed", "completed_with_recovery", "succeeded"].includes(step.status)).length;
  const current = trace.steps.find((step) => step.current);

  return (
    <div
      data-testid={`durable-task-steps-${trace.runId}`}
      aria-label={`执行步骤，共 ${trace.steps.length} 步`}
      style={containerStyle}
    >
      <div style={overviewStyle}>
        <span>{completed}/{trace.steps.length} 步</span>
        <span title={current?.title}>
          {current ? `当前：${current.title}` : trace.projectionComplete ? "步骤记录" : "正在加载步骤记录"}
        </span>
      </div>
      <div style={stepsStyle}>
        {trace.steps.map((step) => {
          const expanded = stepOverrides[step.id] ?? step.current;
          const tone = toneFor(step);
          const detailsId = `task-step-${step.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
          return (
            <div key={step.id} data-testid={`durable-task-step-${step.id}`} style={stepStyle}>
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={detailsId}
                aria-label={`第 ${step.index + 1} 步，${step.title}，${tone.label}`}
                onClick={() => setStepOverrides((current) => ({
                  ...current,
                  [step.id]: !(current[step.id] ?? step.current),
                }))}
                style={stepButtonStyle}
              >
                <span aria-hidden="true" style={{ ...stepIconStyle, color: tone.color }}>
                  {tone.icon}
                </span>
                <span style={stepTitleStyle}>
                  {step.index + 1}. {step.title}
                </span>
                {step.tools.length > 0 ? (
                  <span style={metaStyle}>{step.tools.length} 个工具</span>
                ) : null}
                <span style={{ ...metaStyle, color: tone.color }}>{tone.label}</span>
                <span aria-hidden="true" style={chevronStyle}>{expanded ? "▴" : "▾"}</span>
              </button>
              {expanded ? (
                <div id={detailsId} style={stepDetailStyle}>
                  {step.messages.map((message, index) => (
                    <p key={`${step.id}:message:${index}`} style={messageStyle}>{message}</p>
                  ))}
                  {step.substeps.length > 0 ? (
                    <div style={substepsStyle} aria-label={`${step.title}的工作流步骤`}>
                      {step.substeps.map((substep) => {
                        const substepTone = STEP_TONE[substep.current ? "running" : substep.status] ?? STEP_TONE.pending;
                        return (
                          <div key={substep.id} style={substepStyle} aria-current={substep.current ? "step" : undefined}>
                            <span style={{ color: substepTone.color }}>{substepTone.icon}</span>
                            <span>{substep.index + 1}. {substep.label}</span>
                            <small style={{ color: substepTone.color }}>{substepTone.label}</small>
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                  {step.tools.map((tool) => <CompactTool key={tool.id} tool={tool} />)}
                  {step.messages.length === 0 && step.tools.length === 0 && step.substeps.length === 0 ? (
                    <span style={emptyStyle}>这一步还没有执行记录。</span>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const containerStyle: CSSProperties = {
  display: "grid",
  gap: 5,
  paddingTop: 4,
};
const overviewStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 10,
  color: "#91a1b5",
  fontSize: 10.5,
};
const stepsStyle: CSSProperties = {
  display: "grid",
  borderTop: "1px solid rgba(148,163,184,0.14)",
};
const stepStyle: CSSProperties = {
  display: "grid",
  borderBottom: "1px solid rgba(148,163,184,0.12)",
};
const stepButtonStyle: CSSProperties = {
  width: "100%",
  minHeight: 30,
  display: "flex",
  alignItems: "center",
  gap: 7,
  padding: "5px 0",
  border: 0,
  background: "transparent",
  color: "inherit",
  textAlign: "left",
  cursor: "pointer",
  font: "inherit",
};
const stepIconStyle: CSSProperties = {
  flex: "0 0 14px",
  width: 14,
  textAlign: "center",
  fontWeight: 700,
  fontSize: 11,
};
const stepTitleStyle: CSSProperties = {
  minWidth: 0,
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "#dce5f2",
  fontSize: 11.5,
};
const metaStyle: CSSProperties = {
  flexShrink: 0,
  color: "#8492a6",
  fontSize: 10,
};
const chevronStyle: CSSProperties = {
  flexShrink: 0,
  color: "#718096",
  fontSize: 10,
};
const stepDetailStyle: CSSProperties = {
  display: "grid",
  gap: 4,
  padding: "0 0 7px 21px",
};
const messageStyle: CSSProperties = {
  margin: 0,
  color: "#b9c2d0",
  fontSize: 11,
  lineHeight: 1.45,
  whiteSpace: "pre-wrap",
};
const toolRowStyle: CSSProperties = {
  display: "grid",
  borderRadius: 4,
  background: "rgba(15,23,42,0.42)",
};
const toolButtonStyle: CSSProperties = {
  minHeight: 26,
  width: "100%",
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "3px 7px",
  background: "transparent",
  color: "#cbd5e1",
  textAlign: "left",
  font: "inherit",
};
const substepsStyle: CSSProperties = {
  display: "grid",
  gap: 2,
  padding: "2px 0 4px",
};
const substepStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "14px minmax(0, 1fr) auto",
  alignItems: "center",
  gap: 5,
  minHeight: 22,
  color: "#b9c2d0",
  fontSize: 10.5,
};
const toolMiniButtonStyle: CSSProperties = {
  flexShrink: 0,
  border: "1px solid rgba(148,163,184,0.2)",
  borderRadius: 4,
  padding: "1px 4px",
  background: "transparent",
  color: "#91a1b5",
  cursor: "pointer",
  font: "inherit",
  fontSize: 9.5,
};
const toolNameStyle: CSSProperties = {
  minWidth: 0,
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontFamily: "Consolas, 'Courier New', monospace",
  fontSize: 10.5,
};
const toolDetailStyle: CSSProperties = {
  display: "grid",
  gap: 3,
  padding: "4px 7px 7px 21px",
  color: "#8fa0b6",
  fontSize: 10,
};
const preStyle: CSSProperties = {
  maxHeight: 150,
  overflow: "auto",
  margin: 0,
  padding: 6,
  borderRadius: 4,
  background: "rgba(2,6,23,0.52)",
  color: "#b9c2d0",
  fontFamily: "Consolas, 'Courier New', monospace",
  fontSize: 10,
  lineHeight: 1.35,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};
const emptyStyle: CSSProperties = {
  color: "#718096",
  fontSize: 10.5,
};
