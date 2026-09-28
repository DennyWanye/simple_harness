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
  return (
    <>
      <div style={{ fontWeight: tokens.weight.semibold }}>动作审批：{actionHeadline(summary, action)}</div>
      {reasonText ? (
        <div>
          理由：{reasonText}
          <span style={note}>{system ? "（系统生成）" : "（模型生成，未核实）"}</span>
        </div>
      ) : null}
    </>
  );
};
