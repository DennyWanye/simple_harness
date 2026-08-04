import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../Icon";
import type { EvaluationView } from "./types";

export type EvaluationInput = { score: number; labels: string[]; comment: string; experimentVersion?: string };

type Props = {
  evaluations: EvaluationView[];
  submitting?: boolean;
  error?: string | null;
  feedback?: string | null;
  onSubmit: (input: EvaluationInput) => void;
};

export function EvaluationPanel({ evaluations, submitting = false, error, feedback, onSubmit }: Props) {
  const [score, setScore] = useState(0.8);
  const [labels, setLabels] = useState("");
  const [comment, setComment] = useState("");
  const [versionA, setVersionA] = useState("");
  const [versionB, setVersionB] = useState("");
  const locked = useRef(false);
  const versions = useMemo(() => Array.from(new Set(evaluations.map((item) => item.experiment_version).filter((item): item is string => Boolean(item)))), [evaluations]);
  useEffect(() => {
    locked.current = submitting;
  }, [submitting]);
  useEffect(() => {
    if (!versionA && versions[0]) setVersionA(versions[0]);
    if (!versionB && versions[1]) setVersionB(versions[1]);
  }, [versionA, versionB, versions]);

  const submit = () => {
    if (locked.current || submitting) return;
    locked.current = true;
    onSubmit({
      score,
      labels: labels.split(",").map((item) => item.trim()).filter(Boolean),
      comment: comment.trim(),
      experimentVersion: versionA || undefined,
    });
  };
  const comparison = versionA && versionB ? compareVersions(evaluations, versionA, versionB) : null;

  return (
    <div data-testid="evaluation-panel">
      <section aria-label="人工评分" style={sectionStyle}>
        <div style={headerStyle}><span style={{ display: "flex", alignItems: "center", gap: 7 }}><Icon name="user" size={14} /><strong>人工评分</strong></span><output>{score.toFixed(2)}</output></div>
        <div style={formStyle}>
          <label style={fieldStyle}>
            <span>分数</span>
            <input aria-label="评分" type="range" min={0} max={1} step={0.05} value={score} disabled={submitting} onChange={(event) => setScore(Number(event.target.value))} style={{ width: "100%" }} />
          </label>
          <label style={fieldStyle}><span>标签</span><input aria-label="评分标签" value={labels} disabled={submitting} onChange={(event) => setLabels(event.target.value)} placeholder="准确, 清晰" style={inputStyle} /></label>
          <label style={fieldStyle}><span>说明</span><textarea aria-label="评分说明" value={comment} disabled={submitting} onChange={(event) => setComment(event.target.value)} rows={3} style={textareaStyle} /></label>
          {error && <div role="alert" style={errorStyle}>{error}</div>}
          {feedback && <div role="status" style={successStyle}>{feedback}</div>}
          <button type="button" disabled={submitting} onClick={submit} style={submitStyle(submitting)}><Icon name={submitting ? "loader" : "check"} size={13} />提交评分</button>
        </div>
      </section>

      {versions.length > 1 && (
        <section aria-label="版本比较" style={sectionStyle}>
          <div style={headerStyle}><span style={{ display: "flex", alignItems: "center", gap: 7 }}><Icon name="grid" size={14} /><strong>实验版本比较</strong></span></div>
          <div style={compareControlsStyle}>
            <select aria-label="版本 A" value={versionA} onChange={(event) => setVersionA(event.target.value)} style={inputStyle}>{versions.map((version) => <option key={version}>{version}</option>)}</select>
            <span style={{ color: "#64748b" }}>vs</span>
            <select aria-label="版本 B" value={versionB} onChange={(event) => setVersionB(event.target.value)} style={inputStyle}>{versions.map((version) => <option key={version}>{version}</option>)}</select>
            {comparison && <span style={{ gridColumn: "1 / -1", color: comparison.delta >= 0 ? "#34d399" : "#f87171", overflowWrap: "anywhere", fontSize: 11 }}>{comparison.a.toFixed(2)} / {comparison.b.toFixed(2)} ({comparison.delta >= 0 ? "+" : ""}{comparison.delta.toFixed(2)})</span>}
          </div>
        </section>
      )}

      <section aria-label="评测记录" style={sectionStyle}>
        <div style={headerStyle}><strong>评测记录</strong><span style={{ color: "#64748b", fontSize: 10 }}>{evaluations.length}</span></div>
        {evaluations.length === 0 ? <div style={emptyStyle}>暂无 Evaluation</div> : evaluations.map((item) => (
          <div key={item.evaluation_id} style={rowStyle}>
            <span style={{ minWidth: 0, flex: 1 }}>
              <strong style={{ display: "block", overflowWrap: "anywhere", fontSize: 11 }}>{item.evaluator_name}</strong>
              <span style={{ display: "block", color: "#94a3b8", overflowWrap: "anywhere", fontSize: 10 }}>v{item.evaluator_version}{item.experiment_version ? ` · ${item.experiment_version}` : ""}</span>
              {(item.comment || item.explanation) && <span style={{ display: "block", marginTop: 3, color: "#cbd5e1", overflowWrap: "anywhere", fontSize: 10 }}>{item.comment ?? item.explanation}</span>}
            </span>
            <span style={{ color: item.verdict === "pass" ? "#34d399" : "#fbbf24", fontSize: 11 }}>{item.score == null ? item.verdict : item.score.toFixed(2)}</span>
          </div>
        ))}
      </section>
    </div>
  );
}

function compareVersions(items: EvaluationView[], a: string, b: string) {
  const average = (version: string) => {
    const scores = items.filter((item) => item.experiment_version === version && item.score != null).map((item) => item.score as number);
    return scores.length ? scores.reduce((total, value) => total + value, 0) / scores.length : 0;
  };
  const scoreA = average(a);
  const scoreB = average(b);
  return { a: scoreA, b: scoreB, delta: scoreA - scoreB };
}

const sectionStyle: React.CSSProperties = { borderBottom: "1px solid rgba(148,163,184,0.14)", color: "#e5e7eb" };
const headerStyle: React.CSSProperties = { minHeight: 39, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "6px 10px", fontSize: 12 };
const formStyle: React.CSSProperties = { display: "grid", gap: 8, padding: "0 10px 10px" };
const fieldStyle: React.CSSProperties = { display: "grid", gap: 5, color: "#94a3b8", fontSize: 10 };
const inputStyle: React.CSSProperties = { width: "100%", minWidth: 0, minHeight: 30, padding: "4px 7px", border: "1px solid rgba(148,163,184,0.25)", borderRadius: 6, background: "#0f172a", color: "#e5e7eb", font: "inherit", fontSize: 11 };
const textareaStyle: React.CSSProperties = { ...inputStyle, resize: "vertical", minHeight: 58 };
const compareControlsStyle: React.CSSProperties = { display: "grid", gridTemplateColumns: "minmax(0,1fr) auto minmax(0,1fr)", alignItems: "center", gap: 7, padding: "0 10px 10px" };
const rowStyle: React.CSSProperties = { minHeight: 48, display: "flex", alignItems: "flex-start", gap: 8, padding: "8px 10px", borderTop: "1px solid rgba(148,163,184,0.09)" };
const emptyStyle: React.CSSProperties = { padding: 20, textAlign: "center", color: "#64748b", fontSize: 12 };
const errorStyle: React.CSSProperties = { padding: 7, borderLeft: "3px solid #f87171", color: "#fca5a5", background: "rgba(127,29,29,0.14)", fontSize: 10 };
const successStyle: React.CSSProperties = { padding: 7, borderLeft: "3px solid #34d399", color: "#86efac", background: "rgba(20,83,45,0.14)", fontSize: 10 };
function submitStyle(disabled: boolean): React.CSSProperties { return { minHeight: 31, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "5px 10px", border: "1px solid rgba(56,189,248,0.35)", borderRadius: 6, background: "rgba(3,105,161,0.28)", color: "#bae6fd", cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.55 : 1, fontSize: 11 }; }
