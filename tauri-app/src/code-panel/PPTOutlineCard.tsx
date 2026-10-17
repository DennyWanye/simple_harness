// SPDX-License-Identifier: BUSL-1.1

import { useEffect, useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";

import type { PPTOutlineDecision, PPTOutlineHistoryItem } from "../types/skillPlatform";
import { controlWS } from "./controlWs";
import "./PPTOutlineCard.styles.css";

export interface PPTOutlineCardProps {
  outlineId: string;
  topic: string;
  outlineMd: string;
  sourcesCount: number;
  noResearch: boolean;
  history: PPTOutlineHistoryItem[];
  awaiting?: boolean;
  decisionStatus?: string;
  sessionId?: string;
  runId?: string;
  decisionId?: string;
  nonce?: string;
  version?: number;
}

export function PPTOutlineCard({
  outlineId,
  topic,
  outlineMd,
  sourcesCount,
  noResearch,
  history,
  awaiting = false,
  decisionStatus: persistedDecisionStatus,
  sessionId,
  runId,
  decisionId,
  nonce,
  version,
}: PPTOutlineCardProps) {
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const effectiveAwaiting = awaiting;
  const canSubmitFeedback = feedback.trim().length > 0;
  const sessionFence = sessionId ? { session_id: sessionId } : {};
  const durableFence =
    runId && decisionId && nonce && version !== undefined
      ? { ...sessionFence, run_id: runId, decision_id: decisionId, nonce, version }
      : sessionFence;

  useEffect(() => {
    if (!submitting || !awaiting) return;
    const timer = globalThis.setTimeout(() => setSubmitting(false), 5000);
    return () => globalThis.clearTimeout(timer);
  }, [awaiting, submitting]);

  const sendDecision = (payload: PPTOutlineDecision["payload"]) => {
    const sent = controlWS.send({
      type: "ppt_outline_decision",
      payload,
    });
    setSubmitting(sent);
  };

  const decide = (action: PPTOutlineDecision["payload"]["action"]) => {
    sendDecision({ outline_id: outlineId, action, ...durableFence });
  };

  const submitModify = () => {
    const trimmed = feedback.trim();
    if (!trimmed) return;
    sendDecision({
      outline_id: outlineId,
      action: "modify",
      feedback: trimmed,
      ...durableFence,
    });
  };

  const reuse = (reuseId: string) => {
    sendDecision({
      outline_id: outlineId,
      action: "reuse",
      reuse_id: reuseId,
      ...durableFence,
    });
  };

  return (
    <div data-testid="ppt-outline-card" className="sh-PPTOutlineCard-1">
      <div className="sh-PPTOutlineCard-2">PPT 大纲确认 · {topic || "未命名主题"}</div>
      <div className="sh-PPTOutlineCard-3">📚 {sourcesCount} 个调研来源</div>
      {noResearch && <div className="sh-PPTOutlineCard-4">⚠️ 本次未取得调研来源，基于通用知识</div>}

      <div className="sh-PPTOutlineCard-5">
        <ReactMarkdown>{outlineMd || "（暂无大纲内容）"}</ReactMarkdown>
      </div>

      {history.length > 0 && (
        <div className="sh-PPTOutlineCard-6">
          <button
            type="button"
            onClick={() => setHistoryOpen((v) => !v)}
            className="sh-PPTOutlineCard-7"
          >
            📜 历史大纲
          </button>
          {historyOpen && (
            <div data-testid="ppt-outline-history" className="sh-PPTOutlineCard-8">
              {history.map((item) => (
                <button
                  key={item.outline_id}
                  type="button"
                  disabled={!effectiveAwaiting}
                  onClick={() => reuse(item.outline_id)}
                  className="sh-PPTOutlineCard-9"
                >
                  <span className="sh-PPTOutlineCard-10">{item.topic || "未命名大纲"}</span>
                  <span className="sh-PPTOutlineCard-11">
                    {formatTime(item.created_at)} · {item.sources_count} 个来源
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {effectiveAwaiting ? (
        <>
          {editing && (
            <div className="sh-PPTOutlineCard-12">
              <label htmlFor={`ppt-outline-feedback-${outlineId}`} className="sh-PPTOutlineCard-13">
                说说想改哪里
              </label>
              <textarea
                id={`ppt-outline-feedback-${outlineId}`}
                aria-label="说说想改哪里"
                value={feedback}
                onChange={(event) => setFeedback(event.currentTarget.value)}
                className="sh-PPTOutlineCard-14"
                rows={4}
              />
              <button
                type="button"
                disabled={!canSubmitFeedback}
                onClick={submitModify}
                style={{
                  ...primaryButtonStyle,
                  opacity: canSubmitFeedback ? 1 : 0.5,
                  cursor: canSubmitFeedback ? "pointer" : "not-allowed",
                }}
              >
                提交修改
              </button>
            </div>
          )}
          <div className="sh-PPTOutlineCard-15">
            <button
              type="button"
              disabled={submitting}
              onClick={() => decide("accept")}
              className="sh-PPTOutlineCard-16"
            >
              ✅ 确认生成
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={() => setEditing(true)}
              className="sh-PPTOutlineCard-7"
            >
              ✏️ 修改
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={() => decide("cancel")}
              className="sh-PPTOutlineCard-17"
            >
              ✖ 取消
            </button>
          </div>
        </>
      ) : (
        <div data-testid="ppt-outline-resolved" className="sh-PPTOutlineCard-18">
          {persistedDecisionStatus ?? "已提交决定"}
        </div>
      )}
    </div>
  );
}

function formatTime(value: string) {
  if (!value) return "未知时间";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const primaryButtonStyle: CSSProperties = {
  background: "#0d9488",
  color: "#ffffff",
  border: "none",
  borderRadius: 6,
  padding: "6px 12px",
  fontSize: 12.5,
  fontWeight: 700,
  cursor: "pointer",
};

export default PPTOutlineCard;
