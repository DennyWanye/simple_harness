// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import React from "react";
import { asList, asRecord, asText } from "../stores/missionsStore";

const STATES: Record<string, string> = {
  COLLECTING: "收集候选", DECIDED: "比较已完成", SYNTHESIZING: "综合并重新验证",
  COMPLETED: "已完成", DONE: "已完成", STOPPED: "已停止", FAILED: "未通过",
  READY: "待比较", INVALIDATED: "已失效", CANCELLED: "已取消",
  COMMITTED: "已正式接受", EXHAUSTED: "已停止",
  ACTIVE: "进行中", VERIFYING: "验证中",
};
const label = (state: unknown) => STATES[asText(state)] || asText(state) || "未知";

export const MissionSearch: React.FC<{ value: unknown }> = ({ value }) => {
  const data = asRecord(value);
  const rounds = asList(data.rounds);
  const fragments = asList(data.fragments);
  const changes = asList(data.graph_changes);
  if (!rounds.length && !fragments.length && !changes.length) return null;
  return <section aria-label="候选比较与片段验证" style={{ marginBlock: 16 }}>
    {rounds.length > 0 && <>
      <h3>候选比较</h3>
      <p>候选等待比较；综合结果需要重新验证后才能交付。</p>
      {rounds.map((round) => <div key={asText(round.round_id)}>
        <p>任务 {asText(round.task_id)} · {label(round.state)}</p>
        <ul>{asList(data.candidates).filter((candidate) => candidate.round_id === round.round_id).map((candidate) =>
          <li key={asText(candidate.result_id)}>尝试 {asText(candidate.attempt_id)} · {label(candidate.state)}
            {candidate.reason ? ` · ${asText(candidate.reason)}` : ""}</li>)}</ul>
        {round.synthesis_attempt_id ? <p>综合尝试：{asText(round.synthesis_attempt_id)}</p> : null}
        {asList(data.decisions).filter((decision) => decision.round_id === round.round_id).map((decision) =>
          <div key={asText(decision.receipt_id)}>
            <p>选择依据：{asText(decision.reason) || asText(decision.rule)}</p>
            <p>作为综合输入的结果：{Array.isArray(decision.input_results) ? decision.input_results.map(asText).join("、") || "无" : "无"}</p>
            <ul>{asList(decision.considered).filter((item) => item.eligible === false).map((item) =>
              <li key={asText(item.result_id)}>未采用 {asText(item.result_id)}：{asText(item.reason)}</li>)}</ul>
          </div>)}
      </div>)}
    </>}
    {fragments.length > 0 && <>
      <h3>片段独立验证</h3>
      <p>片段的验证仅覆盖所选条件；原任务的失败记录仍然保留。</p>
      <ul>{fragments.map((fragment) => <li key={asText(fragment.fragment_id)}>
        来自 {asText(fragment.origin_task_id)} · {asText(fragment.criteria_count)} 项条件 ·
        验证任务 {asText(fragment.validation_task_id)} · {label(fragment.validation_status)}
      </li>)}</ul>
    </>}
    {changes.length > 0 && <>
      <h3>任务图变更</h3>
      {changes.map((change) => <div key={asText(change.change_id)}>
        <p>版本 {asText(change.from_version)} → {asText(change.to_version)}</p>
        <p>提案理由（模型生成）：{asText(asRecord(change.rationale).text) || "未记录"}</p>
        <p>影响任务：{Array.isArray(change.affected_task_ids) ? change.affected_task_ids.map(asText).join("、") : "未记录"}</p>
        <p>停止路线：{[
          ...Object.keys(asRecord(change.superseded)),
          ...(Array.isArray(change.cancelled) ? change.cancelled.map(asText) : []),
        ].join("、") || "无"}</p>
      </div>)}
    </>}
  </section>;
};
