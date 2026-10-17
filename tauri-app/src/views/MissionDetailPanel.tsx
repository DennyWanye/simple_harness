// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React, { Suspense, lazy } from "react";
import type { ConfirmRequest } from "../components/useConfirm";
import type { OrchestrationStatus } from "../stores/missionsStore";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { MissionSources } from "./MissionSources";
import { PlanningQuestions } from "./PlanningQuestions";
import { PlanningAuthorization } from "./PlanningAuthorization";
import { OperationWorkspace } from "./OperationWorkspace";
import { MissionDiagnostics } from "./MissionDiagnostics";
import { MissionStory } from "./missionStory/MissionStory";
import { MissionAssurance } from "./MissionAssurance";
import { ActionApprovalSummary } from "./ActionApprovalSummary";
import { PlanChangePanel } from "./PlanChangePanel";
import {
  asList as list,
  asRecord as record,
  asText as text,
  newRequestKey as newKey,
  type MissionEvent,
  type MissionsChannel,
} from "../stores/missionsStore";
import {
  TERMINAL,
  LAYER_LABEL,
  REJECTION_LABEL,
  WAIT_LABEL,
  TIMELINE_SIZE,
  STOP_REASON_LABEL,
  TASK_STATUS_LABEL,
  stateLabel,
  formatBytes,
  shortHash,
  plain,
  waitLine,
  eventLine,
  box,
  button,
  field,
  muted,
  heading,
  nextStep,
  nextStepBlocksAuthorization,
  jumpTo,
  TASK_ENDED,
} from "./missionsViewShared";
import type { Json } from "./missionsViewShared";
import {
  StepsNoLongerCounting,
  BudgetByDuty,
  ModelText,
  LayerSummary,
  ArtifactPanel,
  ActionOutcome,
  ResolveOutcomeBox,
  TakeoverBox,
} from "./MissionsViewParts";

const LiveGraph = lazy(() =>
  import("./liveGraph/LiveGraph").then((m) => ({ default: m.LiveGraph })),
);

export interface MissionDetailPanelProps {
  mission: Json;
  detail: Record<string, unknown>;
  waiting: unknown[];
  send: (type: string, payload?: Json) => string;
  selectedId: string;
  channel: MissionsChannel | null;
  ask: (request: ConfirmRequest) => Promise<boolean>;
  pendingApprovals: Json[];
  refreshPlanningQuestions: () => void;
  bases: Record<string, string>;
  setBases: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  reasons: Record<string, string>;
  setReasons: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  reviewPending: Record<string, boolean>;
  submitReviewDecision: (
    approvalId: string,
    decision: "review_pass" | "review_fail",
    note: string,
  ) => void;
  graphTab: "story" | "graph";
  setGraphTab: React.Dispatch<React.SetStateAction<"story" | "graph">>;
  onStalled: (count: number) => void;
  fetchEvents: (missionId: string, fresh: boolean) => void;
  refreshSelected: (missionId: string) => void;
  status: OrchestrationStatus | null;
  artifacts: Json[];
  internals: Json[];
  setShowInternal: React.Dispatch<React.SetStateAction<boolean>>;
  showInternal: boolean;
  deliverables: Json[];
  setArtifact: React.Dispatch<React.SetStateAction<Json | null>>;
  artifactRequest: React.RefObject<{
    requestId: string;
    missionId: string | null;
    artifactId: string;
  } | null>;
  artifact: Json | null;
  comment: string;
  setComment: React.Dispatch<React.SetStateAction<string>>;
  events: MissionEvent[];
  allEventsShown: boolean;
  setAllEventsShown: React.Dispatch<React.SetStateAction<boolean>>;
  recentEvents: MissionEvent[];
  showMore: boolean;
}

/** 选中任务的详情（预算、等待、审批与审阅、执行图、产物、评论、事件时间线）。9H 从 MissionsView 的渲染里原样挪出，只读属性、不持有状态。 */
export function MissionDetailPanel({
  mission,
  detail,
  waiting,
  send,
  selectedId,
  channel,
  ask,
  pendingApprovals,
  refreshPlanningQuestions,
  bases,
  setBases,
  reasons,
  setReasons,
  reviewPending,
  submitReviewDecision,
  graphTab,
  setGraphTab,
  onStalled,
  fetchEvents,
  refreshSelected,
  status,
  artifacts,
  internals,
  setShowInternal,
  showInternal,
  deliverables,
  setArtifact,
  artifactRequest,
  artifact,
  comment,
  setComment,
  events,
  allEventsShown,
  setAllEventsShown,
  recentEvents,
  showMore,
}: MissionDetailPanelProps) {
  return (
    <>
      <div style={box}>
        <div style={{ fontSize: tokens.text.lg.size, fontWeight: tokens.weight.semibold }}>
          {text(mission.goal)}
        </div>
        <div
          data-testid="mission-state"
          data-status={text(mission.status)}
          data-ui-state={text(mission.ui_state) || undefined}
          style={{ color: dark.textMuted }}
        >
          {`状态：${stateLabel(mission.ui_state, mission.status)}`}
          {mission.stop_reason
            ? ` · 停止原因：${STOP_REASON_LABEL[text(mission.stop_reason).toLowerCase()] ?? text(mission.stop_reason)}`
            : ""}
          {` · 策略版本：${text(record(detail.mission_policy).version_id) || "—"}`}
        </div>
        <div style={muted}>
          Token 已结算 {text(record(detail.usage).settled_tokens) || "未知"} · 当前预留{" "}
          {text(record(detail.usage).reserved_tokens) || "未知"}
          {Number(record(detail.usage).overrun_calls) > 0
            ? ` · 超出上限的调用 ${text(record(detail.usage).overrun_calls)} 次（超出总上限 ${text(record(detail.usage).overrun_tokens) || "0"}，已照实入账）`
            : ""}
        </div>
        <div style={muted} data-testid="mission-budget">
          {`预算：Token 上限 ${text(record(mission.budget).max_tokens) || "—"} · 尝试次数上限 ${text(record(mission.budget).max_attempts) || "—"}`}
        </div>
        {!!record(detail.runtime_context).max_input_tokens && (
          <div style={muted} data-testid="mission-context">
            {record(detail.runtime_context).max_total_tokens
              ? `本任务总上下文 ${Number(record(detail.runtime_context).max_total_tokens) / 1024}K，输入最多 ${Number(record(detail.runtime_context).max_input_tokens) / 1024}K tokens`
              : `本任务输入上下文 ${Number(record(detail.runtime_context).max_input_tokens) / 1024}K tokens`}
          </div>
        )}
        <BudgetByDuty rows={list(detail.budget_by_duty)} />
        <StepsNoLongerCounting rows={list(detail.steps_no_longer_counting)} />
        {list(detail.unrefined_goals).length ? (
          <div style={muted} data-testid="mission-unrefined-goals">
            {`还没细化：${list(detail.unrefined_goals)
              .map((goal) => text(record(goal).label))
              .join("；")}（规划器还没给它们定做法）`}
          </div>
        ) : null}
        {waiting.length ? (
          <div aria-label="等待原因" style={{ marginTop: tokens.space.sm }}>
            {waiting.map((item, index) => (
              <div key={`${index}-${text(record(item).request_id)}`}>
                {waitLine(item)}
                {text(record(item).kind) === "reconciliation" &&
                record(item).needs_human === true ? (
                  <ResolveOutcomeBox
                    actionKey={text(record(item).action_key)}
                    failed={text(record(item).state) === "FAILED"}
                    send={send}
                  />
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
        {!TERMINAL.has(text(mission.status)) && (
          <PlanChangePanel
            key={selectedId + ":plan-change"}
            missionId={selectedId}
            channel={channel}
          />
        )}
        {!TERMINAL.has(text(mission.status)) && (
          <button
            type="button"
            style={{ ...button, marginTop: tokens.space.sm }}
            onClick={async () => {
              // 2026-09-25 UI 全量点击：取消不可撤销，以前一点就取消。
              if (
                await ask({
                  title: "取消这个任务？",
                  message: "取消后任务停止，不能恢复。已生成的产物会保留。",
                  confirm_label: "取消任务",
                })
              )
                send("mission_cancel", { mission_id: selectedId });
            }}
          >
            取消任务
          </button>
        )}
      </div>

      {(() => {
        const step = nextStep(detail, pendingApprovals.length);
        return (
          <div
            data-testid="mission-next-step"
            role="status"
            style={{
              ...box,
              borderColor: step.target ? tokens.color.accent.border : tokens.color.surface.hairline,
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: tokens.space.sm,
            }}
          >
            <span style={{ flex: "1 1 240px", minWidth: 0 }}>{step.text}</span>
            {step.target && (
              <button type="button" style={button} onClick={() => jumpTo(step.target!)}>
                {step.action}
              </button>
            )}
            {/* 授权就在提示条里一键完成（2026-09-25 真机点击：跳转后还要再找按钮） */}
            {list(detail.planning_authorization_requests).length > 0 &&
              !nextStepBlocksAuthorization(detail) && (
                <div style={{ flexBasis: "100%" }}>
                  <PlanningAuthorization
                    requests={list(detail.planning_authorization_requests)}
                    channel={channel}
                    onChanged={refreshPlanningQuestions}
                  />
                </div>
              )}
          </div>
        );
      })()}

      {list(detail.blocked).map((item) => {
        const taskId = text(item.task_id);
        const basis = bases[taskId] ?? "";
        return (
          <div key={taskId} role="alert" style={{ ...box, borderColor: tokens.color.warning.bg }}>
            回合结果未知（进程在模型调用中途退出）：{taskId}
            <textarea
              aria-label="接管依据"
              style={field}
              value={basis}
              onChange={(e) => setBases({ ...bases, [taskId]: e.target.value })}
            />
            <div style={{ display: "flex", gap: tokens.space.sm }}>
              <button
                type="button"
                style={button}
                disabled={!basis.trim()}
                onClick={() =>
                  send("mission_takeover", {
                    task_id: taskId,
                    action: "retry_with_note",
                    basis,
                  })
                }
              >
                接管：重试
              </button>
              <button
                type="button"
                style={button}
                disabled={!basis.trim()}
                onClick={() => send("mission_takeover", { task_id: taskId, action: "stop", basis })}
              >
                接管：停止
              </button>
            </div>
          </div>
        );
      })}

      <div id="mission-step-requirements">
        <OperationWorkspace
          value={detail.operation_workspace}
          channel={channel}
          onChanged={refreshPlanningQuestions}
        />
      </div>
      {nextStepBlocksAuthorization(detail) && (
        <PlanningAuthorization
          requests={list(detail.planning_authorization_requests)}
          channel={channel}
          onChanged={refreshPlanningQuestions}
        />
      )}
      <div id="mission-step-questions">
        <PlanningQuestions
          questions={list(detail.planning_questions)}
          channel={channel}
          onAnswered={refreshPlanningQuestions}
        />
      </div>
      <div id="mission-step-approvals" />
      {pendingApprovals.map((approval) => {
        const requestId = text(approval.request_id);
        const reason = reasons[requestId] ?? "";
        const kind = text(approval.kind);
        const action = record(approval.action);
        return (
          <div
            key={requestId}
            style={{
              ...box,
              minWidth: 0,
              overflowWrap: "anywhere",
              borderColor: tokens.color.accent.border,
            }}
            data-testid={`approval-${requestId}`}
          >
            {kind === "action" ? (
              <ActionApprovalSummary summary={approval.summary} action={action} />
            ) : (
              <div style={heading}>
                {WAIT_LABEL[kind] ?? `未知审批类型（${kind || "未提供"}）`}：
                <ModelText value={approval.summary} />
              </div>
            )}
            {kind === "action" ? <ActionOutcome action={action} /> : null}
            {kind === "source_change" ? (
              <pre
                style={{
                  minWidth: 0,
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  wordBreak: "break-word",
                }}
              >
                {JSON.stringify(record(approval.source_change), null, 2)}
              </pre>
            ) : null}
            {kind === "action" || kind === "source_change" ? (
              <>
                <textarea
                  aria-label="拒绝理由"
                  style={field}
                  value={reason}
                  onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })}
                />
                <div style={{ display: "flex", gap: tokens.space.sm }}>
                  <button
                    type="button"
                    style={button}
                    onClick={() =>
                      send("mission_approval_decide", {
                        approval_id: requestId,
                        decision: "approve",
                      })
                    }
                  >
                    批准
                  </button>
                  <button
                    type="button"
                    style={button}
                    disabled={!reason.trim()}
                    onClick={() =>
                      send("mission_approval_decide", {
                        approval_id: requestId,
                        decision: "reject",
                        reason,
                      })
                    }
                  >
                    拒绝
                  </button>
                </div>
              </>
            ) : kind === "review" ? (
              <>
                <textarea
                  aria-label="复核理由"
                  placeholder="通过可选；未通过请说明原因"
                  style={field}
                  value={reason}
                  onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })}
                />
                <div style={{ display: "flex", gap: tokens.space.sm }}>
                  <button
                    type="button"
                    style={button}
                    disabled={reviewPending[requestId] === true}
                    onClick={() => {
                      const note = reason.trim();
                      submitReviewDecision(requestId, "review_pass", note);
                    }}
                  >
                    复核通过
                  </button>
                  <button
                    type="button"
                    style={button}
                    disabled={!reason.trim() || reviewPending[requestId] === true}
                    onClick={() => {
                      submitReviewDecision(requestId, "review_fail", reason.trim());
                    }}
                  >
                    复核不通过
                  </button>
                </div>
              </>
            ) : kind === "arbitration" ? (
              <>
                <textarea
                  aria-label="仲裁依据"
                  style={field}
                  value={reason}
                  onChange={(e) => setReasons({ ...reasons, [requestId]: e.target.value })}
                />
                <div style={{ display: "flex", gap: tokens.space.sm, flexWrap: "wrap" }}>
                  {(Array.isArray(approval.options) ? approval.options : [])
                    .filter((option) => option === "met" || option === "unmet")
                    .map((option) => (
                      <button
                        key={text(option)}
                        type="button"
                        style={button}
                        disabled={!reason.trim()}
                        onClick={() =>
                          send("mission_approval_decide", {
                            approval_id: requestId,
                            decision: "arbitrate",
                            ruling: text(option),
                            basis: reason,
                          })
                        }
                      >
                        裁决：{text(option)}
                      </button>
                    ))}
                </div>
              </>
            ) : null}
            {list(approval.comments).map((item, index) => (
              <div key={index} style={{ ...muted, marginTop: tokens.space.xs }}>
                {`评论（${text(item.principal_id) || "未知"}）：${text(item.text)}`}
              </div>
            ))}
          </div>
        );
      })}

      {/* 2026-09-27：默认看「任务过程」（模型每一步做了什么）；结构图（步骤之间的先后关系）按需打开 */}
      <div role="tablist" aria-label="任务视图" style={{ display: "flex", gap: tokens.space.xs }}>
        {(
          [
            ["graph", "执行图"],
            ["story", "任务过程"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={graphTab === key}
            onClick={() => setGraphTab(key)}
            style={{
              ...button,
              borderColor:
                graphTab === key ? tokens.color.accent.border : tokens.color.surface.hairline,
              fontWeight: graphTab === key ? tokens.weight.semibold : undefined,
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {graphTab === "story" ? (
        <MissionStory
          key={selectedId + ":story"}
          missionId={selectedId}
          channel={channel}
          missionStatus={text(mission.status)}
          onStalled={onStalled}
        />
      ) : (
        <Suspense fallback={<div style={muted}>正在加载执行图…</div>}>
          <LiveGraph
            key={selectedId + ":live-graph"}
            missionId={selectedId}
            channel={channel}
            detail={detail}
            onLoadMoreEvents={() => fetchEvents(selectedId, true)}
            onStalled={onStalled}
          />
        </Suspense>
      )}

      {list(detail.sources).length > 0 && (
        <MissionSources
          key={selectedId + ":sources"}
          missionId={selectedId}
          sources={list(detail.sources)}
          channel={channel}
          onChanged={() => refreshSelected(selectedId)}
        />
      )}

      <details data-testid="mission-diagnostics-group">
        <summary style={{ cursor: "pointer", color: dark.textMuted }}>
          诊断信息（保证状态等，排查问题时用）
        </summary>

        {status?.assurance_available === true && (
          <MissionAssurance
            key={selectedId + ":assurance"}
            missionId={selectedId}
            channel={channel}
          />
        )}

        {status?.diagnostics_available === true && (
          <MissionDiagnostics
            key={selectedId + ":diagnostics"}
            missionId={selectedId}
            channel={channel}
          />
        )}
      </details>

      <div style={box}>
        <div style={heading}>Task 与验证</div>
        {list(detail.tasks).map((task) => {
          const taskId = text(task.id);
          const blockedIds = new Set(list(detail.blocked).map((item) => text(item.task_id)));
          const canTakeOver =
            !TERMINAL.has(text(mission.status)) &&
            !TASK_ENDED.has(text(task.status)) &&
            !blockedIds.has(taskId);
          return (
            <div key={taskId} data-testid={`task-${taskId}`} style={{ marginTop: tokens.space.sm }}>
              <ModelText value={task.goal} /> ·{" "}
              {TASK_STATUS_LABEL[text(task.status)] ?? text(task.status)}
              {canTakeOver ? <TakeoverBox taskId={taskId} send={send} /> : null}
            </div>
          );
        })}
        {list(detail.results).map((result) => {
          const attemptId = text(result.attempt_id);
          const layers = list(result.verification_layers);
          const verdict = text(result.verdict);
          const verificationState = text(result.verification_state);
          const failed = verificationState === "DONE" && verdict === "FAIL";
          const rejection = failed ? record(result.final_rejection) : {};
          const reason = text(rejection.reason);
          return (
            <div key={text(result.result_id) || attemptId} style={{ marginTop: tokens.space.sm }}>
              {(verdict || verificationState) && (
                <div
                  data-testid={`result-final-${text(result.result_id) || attemptId}`}
                  data-verdict={verdict}
                  data-verification-state={verificationState}
                  style={{ color: failed ? tokens.color.danger.fg : dark.textMuted }}
                >
                  <strong>
                    {failed ? "结果最终未接受" : "结果判定"}（{verdict || "尚未判定"} /{" "}
                    {verificationState || "未知"}）
                  </strong>
                  {reason && (
                    <div>
                      拒绝原因：{REJECTION_LABEL[reason] ?? reason}（{reason}）
                    </div>
                  )}
                  {list(rejection.source_issues).map((issue, index) => (
                    <div key={index} style={{ overflowWrap: "anywhere" }}>
                      {text(issue.path) || "来源"} ·{" "}
                      {REJECTION_LABEL[text(issue.reason)] ?? text(issue.reason)}（
                      {text(issue.reason) || text(issue.code)}）
                      {issue.version ? ` · 版本：${text(issue.version)}` : ""}
                    </div>
                  ))}
                </div>
              )}
              <div>
                结果摘要：
                <ModelText value={result.summary} />
              </div>
              <div style={{ display: "flex", gap: tokens.space.xs, flexWrap: "wrap" }}>
                {layers.map((layer) => {
                  const layerStatus = text(layer.status);
                  const color =
                    layerStatus === "PASS"
                      ? tokens.color.success.fg
                      : layerStatus === "FAIL" || layerStatus === "ERROR"
                        ? tokens.color.danger.fg
                        : dark.textMuted;
                  return (
                    <span
                      key={text(layer.layer)}
                      data-testid={`layer-${attemptId}-${text(layer.layer)}`}
                      data-status={layerStatus}
                      style={{ ...box, padding: `2px ${tokens.space.sm}px`, color }}
                    >
                      {text(layer.layer)}：{LAYER_LABEL[layerStatus] ?? layerStatus}
                    </span>
                  );
                })}
              </div>
              {layers
                .filter((layer) => plain(layer.summary).length > 0)
                .map((layer) => (
                  <div
                    key={text(layer.layer)}
                    data-testid={`layer-summary-${attemptId}-${text(layer.layer)}`}
                    style={{ ...muted, marginTop: tokens.space.xs }}
                  >
                    {text(layer.layer)}：<LayerSummary value={layer.summary} />
                  </div>
                ))}
            </div>
          );
        })}
      </div>

      {artifacts.length ? (
        <div id="mission-step-artifacts" style={box}>
          <div style={heading}>产物</div>
          {internals.length > 0 && (
            <button
              type="button"
              style={{
                ...button,
                height: "auto",
                padding: `2px ${tokens.space.sm}px`,
                marginTop: tokens.space.xs,
                color: dark.textMuted,
              }}
              onClick={() => setShowInternal((v) => !v)}
            >
              {showInternal
                ? "收起系统检查记录"
                : `显示系统检查记录（${internals.length} 个，排查问题时看）`}
            </button>
          )}
          {[...deliverables, ...internals].map((item, index) => {
            const artifactId = text(item.id);
            const folded = index >= deliverables.length;
            const verification = text(item.verification_status);
            return (
              <div
                key={artifactId}
                data-testid={`artifact-${artifactId}`}
                data-internal={folded ? "true" : undefined}
                hidden={folded && !showInternal}
                style={{
                  display: folded && !showInternal ? "none" : "flex",
                  alignItems: "center",
                  gap: tokens.space.sm,
                  marginTop: tokens.space.sm,
                  minWidth: 0,
                }}
              >
                <span
                  style={{
                    flex: 1,
                    minWidth: 0,
                    overflowWrap: "anywhere",
                    wordBreak: "break-word",
                  }}
                >
                  {`${text(item.path)} · ${formatBytes(item.size_bytes)} · 验证：${LAYER_LABEL[verification] ?? (verification || "—")} · ${shortHash(item.content_hash)}`}
                </span>
                <button
                  type="button"
                  style={button}
                  onClick={() => {
                    setArtifact(null);
                    const requestId = newKey();
                    artifactRequest.current = {
                      requestId,
                      missionId: selectedId,
                      artifactId,
                    };
                    channel?.send({
                      type: "mission_artifact_read",
                      request_id: requestId,
                      payload: { artifact_id: artifactId },
                    });
                  }}
                >
                  查看产物
                </button>
              </div>
            );
          })}
        </div>
      ) : null}

      {artifact ? (
        <div style={{ minWidth: 0 }}>
          <ArtifactPanel
            artifact={artifact}
            verified={[...deliverables, ...internals].some(
              (item) =>
                text(item.id) === text(artifact.artifact_id) &&
                text(item.verification_status).toUpperCase() === "VERIFIED",
            )}
          />
        </div>
      ) : null}

      <div style={box}>
        <div style={heading}>评论</div>
        <textarea
          aria-label="评论"
          style={{ ...field, marginTop: tokens.space.sm }}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <button
          type="button"
          style={{ ...button, marginTop: tokens.space.sm }}
          disabled={!comment.trim()}
          onClick={() => send("mission_comment", { target_id: selectedId, text: comment.trim() })}
        >
          发表评论
        </button>
      </div>

      <div style={box}>
        <div style={heading}>原始事件记录</div>
        <div style={muted}>
          系统内部记录，排查问题时看；想了解模型做了什么，看上面的「任务过程」。
        </div>
        {events.length > TIMELINE_SIZE ? (
          <div style={muted}>
            {allEventsShown
              ? `共 ${events.length} 条`
              : `共 ${events.length} 条，显示最近 ${TIMELINE_SIZE} 条`}
            <button
              type="button"
              style={{ ...button, marginLeft: tokens.space.sm }}
              onClick={() => setAllEventsShown((v) => !v)}
            >
              {allEventsShown ? "只看最近" : "显示全部"}
            </button>
          </div>
        ) : null}
        {recentEvents.map((event) => (
          <div key={event.seq} data-testid={`event-${event.seq}`} style={muted}>
            {eventLine(event)}
          </div>
        ))}
        {showMore ? (
          <button
            type="button"
            style={{ ...button, marginTop: tokens.space.sm }}
            onClick={() => fetchEvents(selectedId, true)}
          >
            加载更多事件
          </button>
        ) : null}
      </div>
    </>
  );
}
