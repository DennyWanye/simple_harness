/**
 * 对话里的后台任务卡片（2026-09-29：编排的最终使用者是主 Agent）。
 *
 * 主 Agent 用 mission_start 发起任务后，对话里出现这张卡片：进度、等谁、发布了什么。
 * 需要人拿主意的事（确认完成要求、批准/拒绝发布）由人在卡片上亲手点，发的是和任务编排页
 * 完全相同的消息、同一条连接、同一个人的身份——模型只能读进度（mission_status），不能代批。
 */
import React, { useCallback, useContext, useEffect, useRef, useState } from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import {
  asList as list,
  asRecord as record,
  asText as text,
  newRequestKey,
} from "../stores/missionsStore";
import { ActionApprovalSummary } from "./ActionApprovalSummary";
import { MissionsChannelContext } from "./chatMission";
import { OperationWorkspace } from "./OperationWorkspace";
import { PlanningQuestions } from "./PlanningQuestions";

type Json = Record<string, unknown>;

const STATUS_LABEL: Record<string, string> = {
  CREATED: "已创建，等你确认完成要求",
  ACTIVE: "进行中",
  COMPLETED: "已完成",
  FAILED: "未完成（已停止）",
  CANCELLED: "已取消",
};
const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const DONE_TASKS = new Set(["COMPLETED", "SUCCEEDED", "ACCEPTED"]);
const REFRESH_MS = 5000;

const box: React.CSSProperties = {
  border: `1px solid ${tokens.color.accent.border}`,
  borderRadius: tokens.radius.md,
  padding: tokens.space.sm,
  background: dark.inset,
  color: dark.text,
  display: "grid",
  gap: tokens.space.xs,
  minWidth: 0,
  overflowWrap: "anywhere",
};
const button: React.CSSProperties = {
  padding: `${tokens.space.xs}px ${tokens.space.sm}px`,
  borderRadius: tokens.radius.sm,
  border: `1px solid ${tokens.color.surface.hairline}`,
  background: "transparent",
  color: dark.text,
  cursor: "pointer",
};
const muted: React.CSSProperties = { color: dark.textMuted, fontSize: tokens.text.xs.size };

export function ChatMissionCard({ missionId }: { missionId: string }): React.JSX.Element {
  const channel = useContext(MissionsChannelContext);
  const [detail, setDetail] = useState<Json | null>(null);
  const [error, setError] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [deciding, setDeciding] = useState<Record<string, boolean>>({});
  const getRequest = useRef<string | null>(null);
  const decisions = useRef(new Map<string, string>());

  const refresh = useCallback(() => {
    if (!channel || getRequest.current) return;
    const requestId = newRequestKey();
    getRequest.current = requestId;
    if (!channel.send({ type: "mission_get", request_id: requestId, payload: { mission_id: missionId } })) {
      getRequest.current = null;
    }
  }, [channel, missionId]);

  const status = text(record(detail?.mission).status);
  const terminal = TERMINAL.has(status);

  useEffect(() => {
    if (!channel) return;
    const off = channel.onMessage((incoming) => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const type = text(message.type);
      const payload = record(message.payload);
      if (type === "mission_changed" && text(payload.mission_id) === missionId) {
        refresh();
        return;
      }
      if (type === "mission_get_response" && payload.request_id === getRequest.current) {
        getRequest.current = null;
        if (payload.ok === true) {
          setDetail(record(payload.data));
          setError("");
        } else {
          setError(text(payload.error) || "读取任务失败");
        }
        return;
      }
      if (type === "mission_approval_decide_response") {
        const approvalId = decisions.current.get(text(payload.request_id));
        if (approvalId === undefined) return;
        decisions.current.delete(text(payload.request_id));
        setDeciding((state) => ({ ...state, [approvalId]: false }));
        if (payload.ok !== true) setError(text(payload.error) || "审批没有完成，请重试");
        refresh();
      }
    });
    refresh();
    return off;
  }, [channel, missionId, refresh]);

  // 任务没结束时与任务页一样每 5 秒补拉一次（推送丢了也不会停在旧状态）
  useEffect(() => {
    if (!channel || terminal) return;
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
  }, [channel, terminal, refresh]);

  const decide = (approvalId: string, decision: "approve" | "reject") => {
    if (!channel) return;
    const requestId = newRequestKey();
    const payload: Json = { approval_id: approvalId, decision };
    if (decision === "reject") payload.reason = (reasons[approvalId] ?? "").trim();
    decisions.current.set(requestId, approvalId);
    setDeciding((state) => ({ ...state, [approvalId]: true }));
    if (!channel.send({ type: "mission_approval_decide", request_id: requestId, payload })) {
      decisions.current.delete(requestId);
      setDeciding((state) => ({ ...state, [approvalId]: false }));
      setError("连接不可用，请稍后重试");
    }
  };

  const mission = record(detail?.mission);
  // 根任务（desktop-root-…）是整体汇总，不是步骤
  const work = list(detail?.tasks).filter((task) => [undefined, null, "", "work"].includes(record(task).kind as string)
    && !text(record(task).id).startsWith("desktop-root-"));
  const done = work.filter((task) => DONE_TASKS.has(text(record(task).status))).length;
  const approvals = list(detail?.approvals).map(record).filter((a) => text(a.state) === "PENDING");
  const questions = list(detail?.planning_questions).map(record).filter((q) => text(q.state) === "PENDING");
  const published = list(detail?.actions).map(record)
    .filter((a) => text(a.state) === "SUCCEEDED" && text(a.published_path));
  const workspace = record(detail?.operation_workspace);
  const requirementsRevision = Number(record(workspace.requirements_ref).revision) || 0;

  return (
    <section aria-label="后台任务" style={box} data-testid={`chat-mission-${missionId}`}>
      <div style={{ fontWeight: tokens.weight.semibold }}>
        后台任务：{status ? STATUS_LABEL[status] ?? status : channel ? "读取中…" : "连接未就绪"}
      </div>
      {text(mission.goal) ? <div>{text(mission.goal)}</div> : null}
      {requirementsRevision > 1 ? <div style={muted} data-testid="chat-mission-requirements-revision">要求第 {requirementsRevision} 版</div> : null}
      {work.length ? <div style={muted}>步骤：已完成 {done} / {work.length}</div> : null}
      {text(workspace.state) === "CONFIRMATION_REQUIRED" ? (
        <OperationWorkspace value={detail?.operation_workspace} channel={channel} onChanged={refresh} />
      ) : null}
      {/* 规划器问用户的问题：与任务页同一个组件、同一条消息，在对话里就能答（2026-10-05） */}
      {questions.length ? <PlanningQuestions questions={questions} channel={channel} onAnswered={refresh} /> : null}
      {approvals.map((approval) => {
        const approvalId = text(approval.request_id);
        const busy = deciding[approvalId] === true;
        const reason = reasons[approvalId] ?? "";
        return (
          <div key={approvalId} style={{ ...box, borderColor: tokens.color.surface.hairline }} data-testid={`chat-approval-${approvalId}`}>
            {text(approval.kind) === "action" ? (
              <ActionApprovalSummary summary={approval.summary} action={approval.action} />
            ) : text(approval.kind) === "source_change" ? (
              <div>
                {text(record(approval.source_change).operation) === "revoke" ? "撤销资料" : "把资料换成新版本"}：
                {text(record(approval.source_change).path)}。批准后，用到旧版的步骤会重新规划。
              </div>
            ) : (
              <div>有一项审批等你处理（{text(approval.kind) || "未知"}），请到任务编排页处理。</div>
            )}
            {text(approval.kind) === "action" || text(approval.kind) === "source_change" ? (
              <>
                <input aria-label="拒绝理由" placeholder="拒绝时写一句理由" value={reason}
                  onChange={(event) => setReasons({ ...reasons, [approvalId]: event.target.value })}
                  style={{ ...button, cursor: "text" }} />
                <div style={{ display: "flex", gap: tokens.space.sm }}>
                  <button type="button" style={button} disabled={busy} onClick={() => decide(approvalId, "approve")}>批准</button>
                  <button type="button" style={button} disabled={busy || !reason.trim()} onClick={() => decide(approvalId, "reject")}>拒绝</button>
                </div>
              </>
            ) : null}
          </div>
        );
      })}
      {published.map((action) => (
        <div key={text(action.action_key)}>已发布：{text(action.target)} → {text(action.published_path)}</div>
      ))}
      {status === "FAILED" && text(mission.stop_reason) ? <div style={muted}>停止原因：{text(mission.stop_reason)}</div> : null}
      {error ? <div role="alert" style={{ color: tokens.color.danger.fg }}>{error}</div> : null}
      <div style={muted}>完整过程在「任务编排」页。</div>
    </section>
  );
}
