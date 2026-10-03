/**
 * 动作审批卡片的标题与理由（2026-09-29 第六局真机点击）。
 *
 * 以前直接把审批摘要字典原样显示成一长串，系统按已批准效果写的理由也被标成「模型生成，未核实」。
 * 现在：发布写成「把 A 发布为 B」；理由按来源标注——系统生成的标「系统生成」，模型写的仍标「模型生成，未核实」。
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { asRecord as record, asText as text } from "../stores/missionsStore";
import { actionHeadline } from "./actionHeadline";

/** 阶段 B 裁决第 1 类：上一次为什么没生效（系统按原内容重交时卡片上写明）。 */
const PREVIOUS_OUTCOME: Record<string, string> = {
  service_refused: "发布服务拒绝了",
  not_delivered: "请求没有送到发布服务",
  human_ruled_not_applied: "你裁定它没有生效",
};

const note: React.CSSProperties = { marginLeft: tokens.space.xs, color: dark.textMuted, fontSize: tokens.text.xs.size };

export const ActionApprovalSummary: React.FC<{ summary: unknown; action: unknown }> = ({ summary, action }) => {
  const written = record(summary);
  if (typeof written.text === "string") {
    // 旧格式：摘要本身是一段 `{text, source}` 文字，照原样显示并按来源标注
    const reason = record(record(action).reason);
    return (
      <>
        <div style={{ fontWeight: tokens.weight.semibold }}>
          动作审批：{written.text}
          <span style={note}>{text(written.source) === "system" ? "（系统生成）" : "（模型生成，未核实）"}</span>
        </div>
        {text(reason.text) ? <div>理由：{text(reason.text)}<span style={note}>{
          text(reason.source) === "system" ? "（系统生成）" : "（模型生成，未核实）"}</span></div> : null}
      </>
    );
  }
  const item = { ...record(action), ...written };
  const reason = record(item.reason);
  const reasonText = text(reason.text ?? item.reason);
  const system = text(reason.source ?? item.reason_source) === "system";
  const previous = record(written.previous_attempt);
  const previousOutcome = PREVIOUS_OUTCOME[text(previous.outcome)];
  return (
    <>
      <div style={{ fontWeight: tokens.weight.semibold }}>动作审批：{actionHeadline(summary, action)}</div>
      {reasonText ? (
        <div>
          理由：{reasonText}
          <span style={note}>{system ? "（系统生成）" : "（模型生成，未核实）"}</span>
        </div>
      ) : null}
      {previousOutcome ? (
        <div data-testid="previous-attempt">
          上次没有生效：{previousOutcome}{text(previous.reason) ? `（${text(previous.reason)}）` : ""}，这是重新提交的申请。
        </div>
      ) : null}
    </>
  );
};
