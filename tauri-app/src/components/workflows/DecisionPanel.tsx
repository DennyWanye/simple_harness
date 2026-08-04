import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../Icon";
import type { WorkflowDecisionOption, WorkflowDecisionView } from "./types";

type Props = {
  decision?: WorkflowDecisionView | null;
  submitting?: boolean;
  feedback?: string | null;
  error?: string | null;
  onResolve: (decision: WorkflowDecisionView, response: unknown) => void;
  onReopen: (decision: WorkflowDecisionView) => void;
  onCancel: (decision: WorkflowDecisionView) => void;
};

export function DecisionPanel({ decision, submitting = false, feedback, error, onResolve, onReopen, onCancel }: Props) {
  const [draft, setDraft] = useState("");
  const locked = useRef(false);
  useEffect(() => {
    locked.current = submitting;
  }, [submitting]);
  useEffect(() => {
    setDraft("");
    locked.current = false;
  }, [decision?.decision_id, decision?.version]);

  const prompt = useMemo(() => describePrompt(decision?.prompt), [decision?.prompt]);
  if (!decision) return null;

  const expired = decision.status === "expired" || isExpired(decision.expires_at);
  const options = decision.options ?? prompt.options;
  const runOnce = (action: () => void) => {
    if (locked.current || submitting) return;
    locked.current = true;
    action();
  };
  const submitDraft = () => {
    const text = draft.trim();
    if (!text) return;
    let response: unknown = text;
    try {
      response = JSON.parse(text);
    } catch {
      // Plain text is a valid response for clarification decisions.
    }
    runOnce(() => onResolve(decision, response));
  };

  return (
    <section aria-label="待处理决策" style={sectionStyle}>
      <div style={sectionHeaderStyle}>
        <span style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
          <Icon name={expired ? "alert" : "hand"} size={14} />
          <strong style={{ fontSize: 12 }}>需要你的决定</strong>
          <span style={kindStyle}>{decision.kind}</span>
        </span>
        <span style={{ color: expired ? "#fca5a5" : "#fbbf24", fontSize: 10 }}>{expired ? "已过期" : "等待中"}</span>
      </div>
      <div style={bodyStyle}>
        <strong style={{ display: "block", overflowWrap: "anywhere", fontSize: 11 }}>{prompt.title}</strong>
        {prompt.message && <p style={messageStyle}>{prompt.message}</p>}
        {prompt.detail && <pre style={detailStyle}>{prompt.detail}</pre>}
        {error && <div role="alert" style={errorStyle}>{error}</div>}
        {feedback && <div role="status" style={successStyle}>{feedback}</div>}

        {expired ? (
          <div style={actionsStyle}>
            <button type="button" disabled={submitting} onClick={() => runOnce(() => onReopen(decision))} style={buttonStyle("primary", submitting)}>
              <Icon name="refresh" size={13} />重新发起
            </button>
            <button type="button" disabled={submitting} onClick={() => runOnce(() => onCancel(decision))} style={buttonStyle("danger", submitting)}>
              <Icon name="stop" size={13} />取消运行
            </button>
          </div>
        ) : options.length > 0 ? (
          <div style={optionGridStyle}>
            {options.map((option, index) => (
              <button
                key={`${option.label}-${index}`}
                type="button"
                disabled={submitting}
                title={option.description ?? option.label}
                onClick={() => runOnce(() => onResolve(decision, option.value))}
                style={optionStyle(option, submitting)}
              >
                <Icon name={option.dangerous ? "alert" : "check"} size={13} />
                <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{option.label}</span>
              </button>
            ))}
          </div>
        ) : isApprovalKind(decision.kind) ? (
          <div style={actionsStyle}>
            <button type="button" disabled={submitting} onClick={() => runOnce(() => onResolve(decision, { approved: true }))} style={buttonStyle("primary", submitting)}>
              <Icon name="check" size={13} />同意
            </button>
            <button type="button" disabled={submitting} onClick={() => runOnce(() => onResolve(decision, { approved: false }))} style={buttonStyle("danger", submitting)}>
              <Icon name="close" size={13} />拒绝
            </button>
          </div>
        ) : (
          <div style={{ display: "grid", gap: 7 }}>
            <textarea
              aria-label="决策回复"
              value={draft}
              disabled={submitting}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="输入回复"
              rows={3}
              style={textareaStyle}
            />
            <button type="button" disabled={submitting || !draft.trim()} onClick={submitDraft} style={buttonStyle("primary", submitting || !draft.trim())}>
              <Icon name="send" size={13} />提交
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

function describePrompt(value: unknown): { title: string; message: string; detail: string; options: WorkflowDecisionOption[] } {
  if (typeof value === "string") return { title: value, message: "", detail: "", options: [] };
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { title: "工作流正在等待确认", message: "", detail: safeJson(value), options: [] };
  }
  const prompt = value as Record<string, unknown>;
  const title = firstString(prompt.title, prompt.question, prompt.summary, prompt.message) ?? "工作流正在等待确认";
  const message = firstString(prompt.description, prompt.detail, prompt.reason) ?? "";
  const rawOptions = Array.isArray(prompt.options) ? prompt.options : [];
  const options = rawOptions.flatMap((item): WorkflowDecisionOption[] => {
    if (typeof item === "string") return [{ label: item, value: item }];
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const option = item as Record<string, unknown>;
    const label = firstString(option.label, option.title, option.name);
    if (!label) return [];
    return [{
      label,
      value: "value" in option ? option.value : option,
      description: firstString(option.description, option.detail),
      dangerous: option.dangerous === true,
    }];
  });
  const known = new Set(["title", "question", "summary", "message", "description", "detail", "reason", "options"]);
  const rest = Object.fromEntries(Object.entries(prompt).filter(([key]) => !known.has(key)));
  return { title, message, detail: Object.keys(rest).length ? safeJson(rest) : "", options };
}

function firstString(...values: unknown[]) {
  return values.find((value): value is string => typeof value === "string" && value.trim().length > 0);
}

function safeJson(value: unknown) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "[unavailable]";
  }
}

function isExpired(value?: number | null) {
  if (value == null) return false;
  const millis = value < 10_000_000_000 ? value * 1000 : value;
  return millis <= Date.now();
}

function isApprovalKind(kind: string) {
  return /approval|permission|confirm|review|plan|outline/i.test(kind);
}

const sectionStyle: React.CSSProperties = { borderTop: "1px solid rgba(148,163,184,0.14)" };
const sectionHeaderStyle: React.CSSProperties = { minHeight: 38, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "6px 10px", color: "#e5e7eb" };
const kindStyle: React.CSSProperties = { maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#94a3b8", fontSize: 10 };
const bodyStyle: React.CSSProperties = { display: "grid", gap: 8, padding: "0 10px 10px", color: "#e5e7eb" };
const messageStyle: React.CSSProperties = { margin: 0, color: "#cbd5e1", fontSize: 11, lineHeight: 1.5, overflowWrap: "anywhere" };
const detailStyle: React.CSSProperties = { maxHeight: 120, margin: 0, padding: 7, overflow: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere", border: "1px solid rgba(148,163,184,0.16)", borderRadius: 6, color: "#94a3b8", background: "rgba(15,23,42,0.54)", fontSize: 10 };
const actionsStyle: React.CSSProperties = { display: "flex", flexWrap: "wrap", gap: 7 };
const optionGridStyle: React.CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 7 };
const textareaStyle: React.CSSProperties = { width: "100%", minWidth: 0, resize: "vertical", padding: 7, border: "1px solid rgba(148,163,184,0.25)", borderRadius: 6, background: "#0f172a", color: "#e5e7eb", font: "inherit", fontSize: 11 };
const errorStyle: React.CSSProperties = { padding: 7, borderLeft: "3px solid #f87171", color: "#fca5a5", background: "rgba(127,29,29,0.14)", fontSize: 10, overflowWrap: "anywhere" };
const successStyle: React.CSSProperties = { padding: 7, borderLeft: "3px solid #34d399", color: "#86efac", background: "rgba(20,83,45,0.14)", fontSize: 10, overflowWrap: "anywhere" };

function buttonStyle(tone: "primary" | "danger", disabled: boolean): React.CSSProperties {
  return { minHeight: 30, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "5px 10px", border: `1px solid ${tone === "danger" ? "rgba(248,113,113,0.35)" : "rgba(56,189,248,0.35)"}`, borderRadius: 6, background: tone === "danger" ? "rgba(127,29,29,0.28)" : "rgba(3,105,161,0.26)", color: tone === "danger" ? "#fca5a5" : "#bae6fd", cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.55 : 1, fontSize: 11 };
}

function optionStyle(option: WorkflowDecisionOption, disabled: boolean): React.CSSProperties {
  return { ...buttonStyle(option.dangerous ? "danger" : "primary", disabled), width: "100%", textAlign: "left" };
}
