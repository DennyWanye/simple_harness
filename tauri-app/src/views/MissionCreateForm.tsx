// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React from "react";
import type { MissionRow, OrchestrationStatus } from "../stores/missionsStore";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { SourceDrafts, type SourceDraft } from "./MissionSources";
import { PublishCriterionHelper } from "./PublishCriterionHelper";
import { type MissionEvent } from "../stores/missionsStore";
import { button, field, muted } from "./missionsViewShared";

export interface MissionCreateFormProps {
  createPending: boolean;
  goal: string;
  setGoal: React.Dispatch<React.SetStateAction<string>>;
  criteria: string;
  setCriteria: React.Dispatch<React.SetStateAction<string>>;
  store: {
    selectedId: string | null;
    detail: Record<string, unknown> | null;
    status: OrchestrationStatus | null;
    policy: Record<string, unknown> | null;
    missions: MissionRow[];
    error: string | null;
    listError: string | null;
    selectedEvents: MissionEvent[] | undefined;
    selectedHasMore: boolean;
    selectedLoading: boolean;
    select: (missionId: string | null) => void;
    setError: (error: string | null) => void;
  };
  badPytestLines: string[];
  canAttachSources: boolean;
  sources: SourceDraft[];
  setSources: React.Dispatch<React.SetStateAction<SourceDraft[]>>;
  setSourceImporting: React.Dispatch<React.SetStateAction<boolean>>;
  strictQuotes: boolean;
  setStrictQuotes: React.Dispatch<React.SetStateAction<boolean>>;
  status: OrchestrationStatus | null;
  selectedContextId: string;
  setContextProfile: React.Dispatch<React.SetStateAction<string | null>>;
  selectedContext:
    | {
        profile_id: string;
        max_input_tokens: number;
        max_total_tokens?: number;
        output_reserve?: number;
        safety_margin?: number;
        default_max_output_tokens: number;
        max_output_tokens_ceiling: number;
        mission_max_tokens: number;
      }
    | undefined;
  defaultTokenCap: number | undefined;
  maxTokens: string;
  setMaxTokens: React.Dispatch<React.SetStateAction<string>>;
  maxAttempts: string;
  setMaxAttempts: React.Dispatch<React.SetStateAction<string>>;
  validCaps: boolean;
  submittable: boolean;
  submit: () => void;
  submitBlocker:
    | ""
    | "还没连上后台，请稍候"
    | "请填写任务目标"
    | "请至少写一条成功条件"
    | "正在导入来源资料…"
    | "严格引用需要至少一份资料：在「参考资料」里点「添加来源」粘贴正文，或「导入来源文件」；不需要就在高级设置里取消勾选"
    | "每份来源资料都要填写路径（如 sources/笔记.md）和正文"
    | "请检查上面标红的设置";
  missingMaterialHint:
    | ""
    | "目标里提到了参考资料，但还没有附上任何资料；需要的话请在上面「参考资料」里添加";
}

/** 新建任务表单（目标、成功条件、来源资料、高级设置与提交）。9H 从 MissionsView 的渲染里原样挪出，只读属性、不持有状态。 */
export function MissionCreateForm({
  createPending,
  goal,
  setGoal,
  criteria,
  setCriteria,
  store,
  badPytestLines,
  canAttachSources,
  sources,
  setSources,
  setSourceImporting,
  strictQuotes,
  setStrictQuotes,
  status,
  selectedContextId,
  setContextProfile,
  selectedContext,
  defaultTokenCap,
  maxTokens,
  setMaxTokens,
  maxAttempts,
  setMaxAttempts,
  validCaps,
  submittable,
  submit,
  submitBlocker,
  missingMaterialHint,
}: MissionCreateFormProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: tokens.space.sm }}>
      <label htmlFor="mission-goal">任务目标</label>
      <textarea
        id="mission-goal"
        aria-label="任务目标"
        style={field}
        disabled={createPending}
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
      />
      <label htmlFor="mission-criteria">成功条件（每行一条）</label>
      <textarea
        id="mission-criteria"
        aria-label="成功条件"
        style={field}
        disabled={createPending}
        value={criteria}
        onChange={(e) => setCriteria(e.target.value)}
      />
      <PublishCriterionHelper
        criteria={criteria}
        onChange={setCriteria}
        disabled={createPending}
        publish={store.status?.publish}
      />
      <div style={muted}>
        普通文字写要求即可；以 pytest: 开头的行会当作测试命令运行，后面写测试路径，例如 pytest:
        tests/
      </div>
      {badPytestLines.length > 0 && (
        <div role="alert" style={{ color: dark.danger }}>
          「{badPytestLines[0]}」会被当作测试命令运行，但 pytest: 后面不是测试路径。要写说明就去掉
          pytest: 前缀。
        </div>
      )}
      {canAttachSources && (
        <div
          data-testid="create-sources"
          style={{ display: "flex", flexDirection: "column", gap: tokens.space.xs }}
        >
          <div style={{ fontWeight: tokens.weight.semibold }}>参考资料（可选）</div>
          <div style={muted}>
            给任务附上会议纪要、需求文档等，执行时可以读取。目标里提到的资料要在这里附上，系统不会自己去找。
          </div>
          <SourceDrafts
            sources={sources}
            onChange={setSources}
            disabled={createPending}
            onBusy={setSourceImporting}
          />
        </div>
      )}
      <details data-testid="create-advanced" open={strictQuotes || undefined}>
        <summary style={{ cursor: "pointer", color: dark.textMuted }}>
          高级设置（可选，一般不用改）
        </summary>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: tokens.space.sm,
            marginTop: tokens.space.sm,
          }}
        >
          {canAttachSources && (
            <label style={{ display: "flex", alignItems: "flex-start", gap: tokens.space.xs }}>
              <input
                aria-label="严格引用模式"
                type="checkbox"
                checked={strictQuotes}
                disabled={createPending}
                onChange={(e) => setStrictQuotes(e.target.checked)}
              />
              <span>
                严格引用：只根据我提供的资料作答，每个结论都要逐字引用原文并注明出处；审阅员会按这条要求检查（需要至少一份资料）
              </span>
            </label>
          )}
          {!!status?.context_profiles?.length && (
            <label style={muted}>
              输入上下文容量
              <select
                aria-label="输入上下文容量"
                style={field}
                value={selectedContextId}
                disabled={createPending}
                onChange={(e) => setContextProfile(e.target.value)}
              >
                {status.context_profiles.map((profile) => (
                  <option key={profile.profile_id} value={profile.profile_id}>
                    {profile.max_total_tokens
                      ? `${profile.max_total_tokens / 1024}K 总窗口`
                      : `${profile.max_input_tokens / 1024}K tokens`}
                  </option>
                ))}
              </select>
              <span>
                {selectedContext?.max_total_tokens
                  ? `输入与输出共享 ${selectedContext.max_total_tokens / 1024}K 总窗口；输入最多 ${selectedContext.max_input_tokens / 1024}K，已预留输出与安全余量。总预算按实际调用消耗。`
                  : "容量包含本轮材料与历史；单次输出另计，上限 32K。总预算按实际调用消耗。"}
              </span>
            </label>
          )}
          <div style={{ display: "flex", gap: tokens.space.sm }}>
            <input
              aria-label="Token 上限"
              inputMode="numeric"
              placeholder={
                defaultTokenCap ? `Token 上限（留空=${defaultTokenCap}）` : "Token 上限（可选）"
              }
              style={{ ...field, minHeight: 0, height: tokens.controlHeight }}
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
            />
            <input
              aria-label="尝试次数上限"
              inputMode="numeric"
              placeholder={
                status?.mission_budget_defaults
                  ? `尝试次数上限（留空=${status.mission_budget_defaults.max_attempts}）`
                  : "尝试次数上限（可选）"
              }
              style={{ ...field, minHeight: 0, height: tokens.controlHeight }}
              value={maxAttempts}
              onChange={(e) => setMaxAttempts(e.target.value)}
            />
          </div>
          {!validCaps && (
            <div role="alert" style={{ color: dark.danger }}>
              Token 上限和尝试次数上限要填正整数，或者留空用默认值。
            </div>
          )}
        </div>
      </details>
      <button type="button" style={button} disabled={!submittable} onClick={submit}>
        提交任务
      </button>
      {submitBlocker && (
        <div role="status" data-testid="submit-blocker" style={muted}>
          还不能提交：{submitBlocker}
        </div>
      )}
      {!submitBlocker && missingMaterialHint && (
        <div role="status" data-testid="material-hint" style={{ color: dark.warning }}>
          {missingMaterialHint}
        </div>
      )}
      {createPending && <div role="status">正在创建任务…</div>}
    </div>
  );
}
