// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React, { useState } from "react";
import { asList as list, asRecord as record, asText as text } from "../stores/missionsStore";
import { formatBytes, shortHash, plain, BUDGET_ROWS_SHOWN, spendLine } from "./missionsViewShared";
import type { Json } from "./missionsViewShared";
import "./MissionsViewParts.styles.css";

/** 已通过验收、但在现行要求下不再算数的步骤（多是按旧版要求通过的）。没有就不显示。 */
export function StepsNoLongerCounting({ rows }: { rows: unknown[] }) {
  const steps = rows.map(record);
  if (!steps.length) return null;
  return (
    <div className="sh-MissionsViewParts-1" data-testid="mission-steps-no-longer-counting">
      {`不再算数：${steps.map((step) => `${text(step.label)}（按第 ${text(step.requirements_revision)} 版要求通过）`).join("；")}——要求改过，规划器会决定重做还是沿用`}
    </div>
  );
}

export function BudgetByDuty({ rows }: { rows: unknown[] }) {
  const spent = rows
    .map(record)
    .filter((row) => Number(row.attempts) > 0 || Number(row.settled_tokens) > 0);
  if (!spent.length) return null;
  const shown = spent.slice(0, BUDGET_ROWS_SHOWN);
  return (
    <details data-testid="mission-budget-by-duty" className="sh-MissionsViewParts-1">
      <summary>预算去向</summary>
      {shown.map((row) => {
        const steps = list(row.steps)
          .map(record)
          .filter((step) => Number(step.attempts) > 0 || Number(step.settled_tokens) > 0);
        const line = spendLine(row, text(row.label) || text(row.obligation_id));
        return (
          <div key={text(row.obligation_id)} style={{ paddingLeft: (Number(row.depth) || 0) * 12 }}>
            {steps.length ? (
              <details data-testid="mission-budget-steps">
                <summary>{line}</summary>
                {steps.slice(0, BUDGET_ROWS_SHOWN).map((step) => (
                  <div key={text(step.task_id)} className="sh-MissionsViewParts-2">
                    {spendLine(step, text(step.label) || text(step.task_id))}
                    {Number(step.branches) > 1 ? `（${Number(step.branches)} 个分支共用）` : ""}
                  </div>
                ))}
                {steps.length > BUDGET_ROWS_SHOWN ? (
                  <div className="sh-MissionsViewParts-2">{`其余 ${steps.length - BUDGET_ROWS_SHOWN} 步`}</div>
                ) : null}
              </details>
            ) : (
              line
            )}
          </div>
        );
      })}
      {spent.length > shown.length ? <div>{`其余 ${spent.length - shown.length} 项`}</div> : null}
    </details>
  );
}

export const ModelText: React.FC<{ value: unknown }> = ({ value }) => (
  <span>
    {plain(value)}
    <span className="sh-MissionsViewParts-3">（模型生成，未核实）</span>
  </span>
);

/** 验证层 summary：只有 source=model 才标注；source=system 或旧的纯字符串只显示文字。 */
export const LayerSummary: React.FC<{ value: unknown }> = ({ value }) =>
  text(record(value).source) === "model" ? (
    <ModelText value={value} />
  ) : (
    <span>{plain(value)}</span>
  );

export const ArtifactPanel: React.FC<{ artifact: Json; verified?: boolean }> = ({
  artifact,
  verified = false,
}) => {
  const encoding = text(artifact.encoding);
  const content = typeof artifact.content === "string" ? artifact.content : null;
  const readable = encoding !== "binary" && content !== null;
  return (
    <section aria-label="产物内容" className="sh-MissionsViewParts-4">
      <div className="sh-MissionsViewParts-5">产物：{text(artifact.path)}</div>
      <div className="sh-MissionsViewParts-6">
        {`hash：${text(artifact.content_hash)} · 大小：${formatBytes(artifact.size_bytes)}`}
      </div>
      {readable ? (
        <>
          <div className="sh-MissionsViewParts-1">
            {/* 2026-09-26 真机：列表写"已验证"，预览却一律写"未核实"。 */}
            {verified ? "（已通过核验）" : "（模型生成，未核实）"}
            {artifact.truncated === true ? " · 内容过长，已截断" : ""}
          </div>
          <pre className="sh-MissionsViewParts-7">{content}</pre>
        </>
      ) : (
        <div className="sh-MissionsViewParts-8">
          {encoding === "binary" ? "二进制内容，未显示" : "没有返回内容"}
        </div>
      )}
    </section>
  );
};

/** P3.2 P32-15：一个动作在世界上到底发生了什么。已发布时显示实际路径与回读的内容
 * hash；UNKNOWN 说成“核对中”，因为系统自己也还不知道，绝不写成失败或成功。 */
export function ActionOutcome({
  action,
}: {
  action: Record<string, unknown>;
}): React.JSX.Element | null {
  const state = text(action.state);
  const path = text(action.published_path);
  const hash = text(action.published_hash);
  if (!state) return null;
  const label =
    state === "SUCCEEDED" ? "已发布" : state === "UNKNOWN" ? "核对中" : "已生成，未发布";
  return (
    <div className="sh-MissionsViewParts-6" data-testid="action-outcome" data-action-state={state}>
      {label}
      {path ? ` · ${path}` : ""}
      {hash ? ` · 内容 ${shortHash(hash)}` : ""}
    </div>
  );
}

/**
 * 阶段 B 裁决第 3 类：一次对外动作结果不明（或失败了却证明不了没生效），系统判不了，由人裁定。
 * 依据必填；"没生效"之后系统会按原内容重新出一张审批卡。只能由人亲手点。
 */
export const ResolveOutcomeBox: React.FC<{
  actionKey: string;
  failed: boolean;
  send: (type: string, payload?: Json) => unknown;
}> = ({ actionKey, failed, send }) => {
  const [basis, setBasis] = useState("");
  return (
    <div className="sh-MissionsViewParts-9" data-testid={`resolve-${actionKey}`}>
      <div className="sh-MissionsViewParts-10">
        系统判断不了这次发布有没有生效，请你查看后裁定：
      </div>
      <textarea
        aria-label="裁定依据"
        placeholder="写一句你依据什么判断（例如：查了发布目录）"
        className="sh-MissionsViewParts-11"
        value={basis}
        onChange={(e) => setBasis(e.target.value)}
      />
      <div className="sh-MissionsViewParts-12">
        {failed ? null : (
          <button
            type="button"
            className="sh-MissionsViewParts-13"
            disabled={!basis.trim()}
            onClick={() =>
              send("mission_action_resolve", { action_key: actionKey, outcome: "succeeded", basis })
            }
          >
            已生效
          </button>
        )}
        <button
          type="button"
          className="sh-MissionsViewParts-13"
          disabled={!basis.trim()}
          onClick={() =>
            send("mission_action_resolve", { action_key: actionKey, outcome: "failed", basis })
          }
        >
          没生效
        </button>
      </div>
    </div>
  );
};

/**
 * 卡住的 Task 不一定被判为 blocked（例如一直在跑、在排队），
 * 人也要能接管。依据必填，与「回合结果未知」卡片用同一套命令与可访问名称。
 */
export const TakeoverBox: React.FC<{
  taskId: string;
  send: (type: string, payload?: Json) => unknown;
}> = ({ taskId, send }) => {
  const [open, setOpen] = useState(false);
  const [basis, setBasis] = useState("");
  if (!open) {
    return (
      <button type="button" className="sh-MissionsViewParts-14" onClick={() => setOpen(true)}>
        接管这个 Task
      </button>
    );
  }
  return (
    <div className="sh-MissionsViewParts-9">
      <textarea
        aria-label="接管依据"
        className="sh-MissionsViewParts-11"
        value={basis}
        onChange={(e) => setBasis(e.target.value)}
      />
      <div className="sh-MissionsViewParts-12">
        <button
          type="button"
          className="sh-MissionsViewParts-13"
          disabled={!basis.trim()}
          onClick={() =>
            send("mission_takeover", { task_id: taskId, action: "retry_with_note", basis })
          }
        >
          接管：重试
        </button>
        <button
          type="button"
          className="sh-MissionsViewParts-13"
          disabled={!basis.trim()}
          onClick={() => send("mission_takeover", { task_id: taskId, action: "stop", basis })}
        >
          接管：停止
        </button>
      </div>
    </div>
  );
};
