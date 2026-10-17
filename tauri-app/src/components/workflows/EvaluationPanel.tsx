import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../Icon";
import type { EvaluationView } from "./types";
import "./EvaluationPanel.styles.css";

export type EvaluationInput = {
  score: number;
  labels: string[];
  comment: string;
  experimentVersion?: string;
};

type Props = {
  evaluations: EvaluationView[];
  submitting?: boolean;
  error?: string | null;
  feedback?: string | null;
  onSubmit: (input: EvaluationInput) => void;
};

export function EvaluationPanel({
  evaluations,
  submitting = false,
  error,
  feedback,
  onSubmit,
}: Props) {
  const [score, setScore] = useState(0.8);
  const [labels, setLabels] = useState("");
  const [comment, setComment] = useState("");
  const [pickedA, setVersionA] = useState("");
  const [pickedB, setVersionB] = useState("");
  const locked = useRef(false);
  const versions = useMemo(
    () =>
      Array.from(
        new Set(
          evaluations
            .map((item) => item.experiment_version)
            .filter((item): item is string => Boolean(item)),
        ),
      ),
    [evaluations],
  );
  useEffect(() => {
    locked.current = submitting;
  }, [submitting]);
  // 用户没选时默认前两个版本（渲染期派生，不在 effect 里补写状态）。
  const versionA = pickedA || versions[0] || "";
  const versionB = pickedB || versions[1] || "";

  const submit = () => {
    if (locked.current || submitting) return;
    locked.current = true;
    onSubmit({
      score,
      labels: labels
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      comment: comment.trim(),
      experimentVersion: versionA || undefined,
    });
  };
  const comparison = versionA && versionB ? compareVersions(evaluations, versionA, versionB) : null;

  return (
    <div data-testid="evaluation-panel">
      <section aria-label="人工评分" className="sh-EvaluationPanel-1">
        <div className="sh-EvaluationPanel-2">
          <span className="sh-EvaluationPanel-3">
            <Icon name="user" size={14} />
            <strong>人工评分</strong>
          </span>
          <output>{score.toFixed(2)}</output>
        </div>
        <div className="sh-EvaluationPanel-4">
          <label className="sh-EvaluationPanel-5">
            <span>分数</span>
            <input
              aria-label="评分"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={score}
              disabled={submitting}
              onChange={(event) => setScore(Number(event.target.value))}
              className="sh-EvaluationPanel-6"
            />
          </label>
          <label className="sh-EvaluationPanel-5">
            <span>标签</span>
            <input
              aria-label="评分标签"
              value={labels}
              disabled={submitting}
              onChange={(event) => setLabels(event.target.value)}
              placeholder="准确, 清晰"
              className="sh-EvaluationPanel-7"
            />
          </label>
          <label className="sh-EvaluationPanel-5">
            <span>说明</span>
            <textarea
              aria-label="评分说明"
              value={comment}
              disabled={submitting}
              onChange={(event) => setComment(event.target.value)}
              rows={3}
              className="sh-EvaluationPanel-8"
            />
          </label>
          {error && (
            <div role="alert" className="sh-EvaluationPanel-9">
              {error}
            </div>
          )}
          {feedback && (
            <div role="status" className="sh-EvaluationPanel-10">
              {feedback}
            </div>
          )}
          <button
            type="button"
            disabled={submitting}
            onClick={submit}
            style={submitStyle(submitting)}
          >
            <Icon name={submitting ? "loader" : "check"} size={13} />
            提交评分
          </button>
        </div>
      </section>

      {versions.length > 1 && (
        <section aria-label="版本比较" className="sh-EvaluationPanel-1">
          <div className="sh-EvaluationPanel-2">
            <span className="sh-EvaluationPanel-3">
              <Icon name="grid" size={14} />
              <strong>实验版本比较</strong>
            </span>
          </div>
          <div className="sh-EvaluationPanel-11">
            <select
              aria-label="版本 A"
              value={versionA}
              onChange={(event) => setVersionA(event.target.value)}
              className="sh-EvaluationPanel-7"
            >
              {versions.map((version) => (
                <option key={version}>{version}</option>
              ))}
            </select>
            <span className="sh-EvaluationPanel-12">vs</span>
            <select
              aria-label="版本 B"
              value={versionB}
              onChange={(event) => setVersionB(event.target.value)}
              className="sh-EvaluationPanel-7"
            >
              {versions.map((version) => (
                <option key={version}>{version}</option>
              ))}
            </select>
            {comparison && (
              <span
                style={{
                  gridColumn: "1 / -1",
                  color: comparison.delta >= 0 ? "#34d399" : "#f87171",
                  overflowWrap: "anywhere",
                  fontSize: 11,
                }}
              >
                {comparison.a.toFixed(2)} / {comparison.b.toFixed(2)} (
                {comparison.delta >= 0 ? "+" : ""}
                {comparison.delta.toFixed(2)})
              </span>
            )}
          </div>
        </section>
      )}

      <section aria-label="评测记录" className="sh-EvaluationPanel-1">
        <div className="sh-EvaluationPanel-2">
          <strong>评测记录</strong>
          <span className="sh-EvaluationPanel-13">{evaluations.length}</span>
        </div>
        {evaluations.length === 0 ? (
          <div className="sh-EvaluationPanel-14">暂无 Evaluation</div>
        ) : (
          evaluations.map((item) => (
            <div key={item.evaluation_id} className="sh-EvaluationPanel-15">
              <span className="sh-EvaluationPanel-16">
                <strong className="sh-EvaluationPanel-17">{item.evaluator_name}</strong>
                <span className="sh-EvaluationPanel-18">
                  v{item.evaluator_version}
                  {item.experiment_version ? ` · ${item.experiment_version}` : ""}
                </span>
                {(item.comment || item.explanation) && (
                  <span className="sh-EvaluationPanel-19">{item.comment ?? item.explanation}</span>
                )}
              </span>
              <span
                style={{ color: item.verdict === "pass" ? "#34d399" : "#fbbf24", fontSize: 11 }}
              >
                {item.score == null ? item.verdict : item.score.toFixed(2)}
              </span>
            </div>
          ))
        )}
      </section>
    </div>
  );
}

function compareVersions(items: EvaluationView[], a: string, b: string) {
  const average = (version: string) => {
    const scores = items
      .filter((item) => item.experiment_version === version && item.score != null)
      .map((item) => item.score as number);
    return scores.length ? scores.reduce((total, value) => total + value, 0) / scores.length : 0;
  };
  const scoreA = average(a);
  const scoreB = average(b);
  return { a: scoreA, b: scoreB, delta: scoreA - scoreB };
}

function submitStyle(disabled: boolean): React.CSSProperties {
  return {
    minHeight: 31,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    padding: "5px 10px",
    border: "1px solid rgba(56,189,248,0.35)",
    borderRadius: 6,
    background: "rgba(3,105,161,0.28)",
    color: "#bae6fd",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.55 : 1,
    fontSize: 11,
  };
}
