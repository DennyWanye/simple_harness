import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../Icon";
import type { WorkflowDecisionOption, WorkflowDecisionView } from "./types";
import "./DecisionPanel.styles.css";

type Props = {
  decision?: WorkflowDecisionView | null;
  submitting?: boolean;
  feedback?: string | null;
  error?: string | null;
  onResolve: (decision: WorkflowDecisionView, response: unknown) => void;
  onReopen: (decision: WorkflowDecisionView) => void;
  onCancel: (decision: WorkflowDecisionView) => void;
};

export function DecisionPanel({
  decision,
  submitting = false,
  feedback,
  error,
  onResolve,
  onReopen,
  onCancel,
}: Props) {
  const [draft, setDraft] = useState("");
  const locked = useRef(false);
  useEffect(() => {
    locked.current = submitting;
  }, [submitting]);
  // 换了决策（或版本）就清空草稿：渲染期按上一版比对，不在 effect 里 setState。
  const decisionKey = JSON.stringify([decision?.decision_id, decision?.version]);
  const [shownDecision, setShownDecision] = useState(decisionKey);
  if (shownDecision !== decisionKey) {
    setShownDecision(decisionKey);
    setDraft("");
  }
  useEffect(() => {
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
    <section aria-label="待处理决策" className="sh-DecisionPanel-1">
      <div className="sh-DecisionPanel-2">
        <span className="sh-DecisionPanel-3">
          <Icon name={expired ? "alert" : "hand"} size={14} />
          <strong className="sh-DecisionPanel-4">需要你的决定</strong>
          <span className="sh-DecisionPanel-5">{decision.kind}</span>
        </span>
        <span style={{ color: expired ? "#fca5a5" : "#fbbf24", fontSize: 10 }}>
          {expired ? "已过期" : "等待中"}
        </span>
      </div>
      <div className="sh-DecisionPanel-6">
        <strong className="sh-DecisionPanel-7">{prompt.title}</strong>
        {prompt.message && <p className="sh-DecisionPanel-8">{prompt.message}</p>}
        {prompt.detail && <pre className="sh-DecisionPanel-9">{prompt.detail}</pre>}
        {error && (
          <div role="alert" className="sh-DecisionPanel-10">
            {error}
          </div>
        )}
        {feedback && (
          <div role="status" className="sh-DecisionPanel-11">
            {feedback}
          </div>
        )}

        {expired ? (
          <div className="sh-DecisionPanel-12">
            <button
              type="button"
              disabled={submitting}
              onClick={() => runOnce(() => onReopen(decision))}
              style={buttonStyle("primary", submitting)}
            >
              <Icon name="refresh" size={13} />
              重新发起
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={() => runOnce(() => onCancel(decision))}
              style={buttonStyle("danger", submitting)}
            >
              <Icon name="stop" size={13} />
              取消运行
            </button>
          </div>
        ) : options.length > 0 ? (
          <div className="sh-DecisionPanel-13">
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
                <span className="sh-DecisionPanel-14">{option.label}</span>
              </button>
            ))}
          </div>
        ) : isApprovalKind(decision.kind) ? (
          <div className="sh-DecisionPanel-12">
            <button
              type="button"
              disabled={submitting}
              onClick={() => runOnce(() => onResolve(decision, { approved: true }))}
              style={buttonStyle("primary", submitting)}
            >
              <Icon name="check" size={13} />
              同意
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={() => runOnce(() => onResolve(decision, { approved: false }))}
              style={buttonStyle("danger", submitting)}
            >
              <Icon name="close" size={13} />
              拒绝
            </button>
          </div>
        ) : (
          <div className="sh-DecisionPanel-15">
            <textarea
              aria-label="决策回复"
              value={draft}
              disabled={submitting}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="输入回复"
              rows={3}
              className="sh-DecisionPanel-16"
            />
            <button
              type="button"
              disabled={submitting || !draft.trim()}
              onClick={submitDraft}
              style={buttonStyle("primary", submitting || !draft.trim())}
            >
              <Icon name="send" size={13} />
              提交
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

function describePrompt(value: unknown): {
  title: string;
  message: string;
  detail: string;
  options: WorkflowDecisionOption[];
} {
  if (typeof value === "string") return { title: value, message: "", detail: "", options: [] };
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { title: "工作流正在等待确认", message: "", detail: safeJson(value), options: [] };
  }
  const prompt = value as Record<string, unknown>;
  const title =
    firstString(prompt.title, prompt.question, prompt.summary, prompt.message) ??
    "工作流正在等待确认";
  const message = firstString(prompt.description, prompt.detail, prompt.reason) ?? "";
  const rawOptions = Array.isArray(prompt.options) ? prompt.options : [];
  const options = rawOptions.flatMap((item): WorkflowDecisionOption[] => {
    if (typeof item === "string") return [{ label: item, value: item }];
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const option = item as Record<string, unknown>;
    const label = firstString(option.label, option.title, option.name);
    if (!label) return [];
    return [
      {
        label,
        value: "value" in option ? option.value : option,
        description: firstString(option.description, option.detail),
        dangerous: option.dangerous === true,
      },
    ];
  });
  const known = new Set([
    "title",
    "question",
    "summary",
    "message",
    "description",
    "detail",
    "reason",
    "options",
  ]);
  const rest = Object.fromEntries(Object.entries(prompt).filter(([key]) => !known.has(key)));
  return { title, message, detail: Object.keys(rest).length ? safeJson(rest) : "", options };
}

function firstString(...values: unknown[]) {
  return values.find(
    (value): value is string => typeof value === "string" && value.trim().length > 0,
  );
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

function buttonStyle(tone: "primary" | "danger", disabled: boolean): React.CSSProperties {
  return {
    minHeight: 30,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    padding: "5px 10px",
    border: `1px solid ${tone === "danger" ? "rgba(248,113,113,0.35)" : "rgba(56,189,248,0.35)"}`,
    borderRadius: 6,
    background: tone === "danger" ? "rgba(127,29,29,0.28)" : "rgba(3,105,161,0.26)",
    color: tone === "danger" ? "#fca5a5" : "#bae6fd",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.55 : 1,
    fontSize: 11,
  };
}

function optionStyle(option: WorkflowDecisionOption, disabled: boolean): React.CSSProperties {
  return {
    ...buttonStyle(option.dangerous ? "danger" : "primary", disabled),
    width: "100%",
    textAlign: "left",
  };
}
