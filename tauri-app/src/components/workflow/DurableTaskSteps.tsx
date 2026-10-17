import { useState, type CSSProperties } from "react";

import type {
  WorkflowTaskStep,
  WorkflowTaskTool,
  WorkflowTaskTrace,
} from "../AgentActivityMessage";
import { requestHarnessPublicToolDetails } from "../../stores/harnessPublicSnapshotStore";
import "./DurableTaskSteps.styles.css";

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
  const status =
    tool.status === "cancelled"
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
  const duration =
    typeof tool.durationMs === "number"
      ? tool.durationMs < 1000
        ? `${Math.round(tool.durationMs)}ms`
        : `${(tool.durationMs / 1000).toFixed(1)}s`
      : null;

  return (
    <div data-testid={`compact-tool-${tool.id}`} className="sh-DurableTaskSteps-1">
      <div className="sh-DurableTaskSteps-2">
        <span aria-hidden="true" style={{ color }}>
          {["cancelled", "rejected"].includes(tool.status)
            ? "–"
            : tool.ok === false
              ? "×"
              : tool.ok === true
                ? "✓"
                : "●"}
        </span>
        <span title={toolSummary(tool)} className="sh-DurableTaskSteps-3">
          {toolSummary(tool)}
        </span>
        {duration ? <span className="sh-DurableTaskSteps-4">{duration}</span> : null}
        <span style={{ color, fontSize: 10 }}>{status}</span>
        <button
          type="button"
          aria-expanded={inputExpanded}
          aria-controls={detailId}
          aria-label={`${inputExpanded ? "收起" : "查看"}${toolSummary(tool)}输入`}
          onClick={() => {
            if (!inputExpanded && tool.args === undefined)
              requestHarnessPublicToolDetails(tool.detailRef);
            setInputExpanded((value) => !value);
          }}
          className="sh-DurableTaskSteps-5"
        >
          输入 {inputExpanded ? "▴" : "▾"}
        </button>
        {tool.resultAvailable ? (
          <button
            type="button"
            aria-expanded={resultExpanded}
            aria-controls={resultId}
            aria-label={`${resultExpanded ? "收起" : "查看"}${toolSummary(tool)}结果`}
            onClick={() => {
              if (!resultExpanded && tool.result === undefined)
                requestHarnessPublicToolDetails(tool.detailRef);
              setResultExpanded((value) => !value);
            }}
            className="sh-DurableTaskSteps-5"
          >
            结果 {resultExpanded ? "▴" : "▾"}
          </button>
        ) : null}
      </div>
      {inputExpanded ? (
        <div id={detailId} className="sh-DurableTaskSteps-6">
          <strong>输入</strong>
          {tool.args !== undefined ? (
            <pre className="sh-DurableTaskSteps-7">{compactValue(tool.args)}</pre>
          ) : (
            <span>{tool.unavailableReason ?? "正在读取可公开的工具输入…"}</span>
          )}
        </div>
      ) : null}
      {resultExpanded ? (
        <div id={resultId} className="sh-DurableTaskSteps-6">
          <strong>结果</strong>
          {tool.result !== undefined ? (
            <pre className="sh-DurableTaskSteps-7">{compactValue(tool.result)}</pre>
          ) : (
            <span>{tool.unavailableReason ?? "正在读取可公开的工具结果…"}</span>
          )}
          {tool.truncated ? <small>内容较长，已安全截断</small> : null}
        </div>
      ) : null}
    </div>
  );
}

export function DurableTaskSteps({ trace }: { trace: WorkflowTaskTrace }) {
  const [stepOverrides, setStepOverrides] = useState<Record<string, boolean>>({});

  const completed = trace.steps.filter((step) =>
    ["completed", "completed_with_recovery", "succeeded"].includes(step.status),
  ).length;
  const current = trace.steps.find((step) => step.current);

  return (
    <div
      data-testid={`durable-task-steps-${trace.runId}`}
      aria-label={`执行步骤，共 ${trace.steps.length} 步`}
      className="sh-DurableTaskSteps-8"
    >
      <div className="sh-DurableTaskSteps-9">
        <span>
          {completed}/{trace.steps.length} 步
        </span>
        <span title={current?.title}>
          {current
            ? `当前：${current.title}`
            : trace.projectionComplete
              ? "步骤记录"
              : "正在加载步骤记录"}
        </span>
      </div>
      <div className="sh-DurableTaskSteps-10">
        {trace.steps.map((step) => {
          const expanded = stepOverrides[step.id] ?? step.current;
          const tone = toneFor(step);
          const detailsId = `task-step-${step.id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
          return (
            <div
              key={step.id}
              data-testid={`durable-task-step-${step.id}`}
              className="sh-DurableTaskSteps-11"
            >
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={detailsId}
                aria-label={`第 ${step.index + 1} 步，${step.title}，${tone.label}`}
                onClick={() =>
                  setStepOverrides((current) => ({
                    ...current,
                    [step.id]: !(current[step.id] ?? step.current),
                  }))
                }
                className="sh-DurableTaskSteps-12"
              >
                <span aria-hidden="true" style={{ ...stepIconStyle, color: tone.color }}>
                  {tone.icon}
                </span>
                <span className="sh-DurableTaskSteps-13">
                  {step.index + 1}. {step.title}
                </span>
                {step.tools.length > 0 ? (
                  <span className="sh-DurableTaskSteps-4">{step.tools.length} 个工具</span>
                ) : null}
                <span style={{ ...metaStyle, color: tone.color }}>{tone.label}</span>
                <span aria-hidden="true" className="sh-DurableTaskSteps-14">
                  {expanded ? "▴" : "▾"}
                </span>
              </button>
              {expanded ? (
                <div id={detailsId} className="sh-DurableTaskSteps-15">
                  {step.messages.map((message, index) => (
                    <p key={`${step.id}:message:${index}`} className="sh-DurableTaskSteps-16">
                      {message}
                    </p>
                  ))}
                  {step.substeps.length > 0 ? (
                    <div
                      className="sh-DurableTaskSteps-17"
                      aria-label={`${step.title}的工作流步骤`}
                    >
                      {step.substeps.map((substep) => {
                        const substepTone =
                          STEP_TONE[substep.current ? "running" : substep.status] ??
                          STEP_TONE.pending;
                        return (
                          <div
                            key={substep.id}
                            className="sh-DurableTaskSteps-18"
                            aria-current={substep.current ? "step" : undefined}
                          >
                            <span style={{ color: substepTone.color }}>{substepTone.icon}</span>
                            <span>
                              {substep.index + 1}. {substep.label}
                            </span>
                            <small style={{ color: substepTone.color }}>{substepTone.label}</small>
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                  {step.tools.map((tool) => (
                    <CompactTool key={tool.id} tool={tool} />
                  ))}
                  {step.messages.length === 0 &&
                  step.tools.length === 0 &&
                  step.substeps.length === 0 ? (
                    <span className="sh-DurableTaskSteps-19">这一步还没有执行记录。</span>
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

const stepIconStyle: CSSProperties = {
  flex: "0 0 14px",
  width: 14,
  textAlign: "center",
  fontWeight: 700,
  fontSize: 11,
};
const metaStyle: CSSProperties = {
  flexShrink: 0,
  color: "#8492a6",
  fontSize: 10,
};
