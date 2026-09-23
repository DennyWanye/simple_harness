// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useRef, useState } from "react";
import { asList, asRecord, asText, newRequestKey, type MissionsChannel } from "../stores/missionsStore";
import { dark } from "../theme/components";
import { tokens } from "../theme/tokens";

type Json = Record<string, unknown>;

export function PlanningQuestions({ questions, channel, onAnswered }: {
  questions: Json[]; channel: MissionsChannel | null; onAnswered: () => void;
}) {
  return <section aria-label="规划待确认问题">
    {questions.map(question => <PlanningQuestion key={asText(question.decision_id)}
      question={question} channel={channel} onAnswered={onAnswered} />)}
  </section>;
}

function PlanningQuestion({ question, channel, onAnswered }: {
  question: Json; channel: MissionsChannel | null; onAnswered: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const flight = useRef<string | null>(null);
  // Keep the original nonce if a connection is lost after the server committed.
  const submission = useRef<{ answer: string; nonce: string } | null>(null);
  useEffect(() => {
    flight.current = null;
    setPending(false);
    if (!channel) return;
    return channel.onMessage(incoming => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const payload = asRecord(message.payload);
      if (message.type !== "mission_planning_answer_response" || payload.request_id !== flight.current) return;
      flight.current = null;
      setPending(false);
      if (payload.ok === true) { setError(""); onAnswered(); }
      else setError(asText(payload.error) || "回答未提交，请重试");
    });
  }, [channel, onAnswered]);
  const state = asText(question.state);
  const options = asList(question.options);
  const submit = () => {
    if (!answer.trim() || pending || !channel) return;
    if (submission.current?.answer !== answer) submission.current = { answer, nonce: newRequestKey() };
    const requestId = newRequestKey();
    flight.current = requestId;
    setPending(true);
    setError("");
    try {
      if (channel.send({ type: "mission_planning_answer", request_id: requestId, payload: {
        decision_id: question.decision_id, answer, expected_version: question.version,
        nonce: submission.current.nonce,
      } })) return;
    } catch { /* Preserve the answer and nonce for an explicit retry. */ }
    flight.current = null;
    setPending(false);
    setError("连接不可用，回答尚未确认；请重试");
  };
  return <div style={{ padding: tokens.space.md, marginBottom: tokens.space.sm, border: `1px solid ${dark.border}` }}>
    <div>模型请求确认</div>
    <p>{asText(question.question)}</p>
    {state === "PENDING" ? <>
      {options.length ? <select aria-label="选择规划问题回答" disabled={pending} value={answer} onChange={e => setAnswer(e.target.value)}>
        <option value="">请选择</option>
        {options.map(option => <option key={asText(option.key)} value={asText(option.key)}>{asText(option.label)}</option>)}
      </select> : <textarea aria-label="规划问题回答" maxLength={12000} disabled={pending} value={answer} onChange={e => setAnswer(e.target.value)} />}
      <button type="button" disabled={pending || !answer.trim() || !channel} onClick={submit}>{pending ? "正在提交…" : "提交回答"}</button>
    </> : <p>{state === "ANSWERED" ? `已回答：${asText(question.answer)}` : "计划已更新，此问题已失效"}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
