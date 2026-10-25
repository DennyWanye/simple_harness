// SPDX-License-Identifier: BUSL-1.1
/**
 * 改计划进度。
 *
 * 只在一次改计划卡在半路时出现：读 SDK 只读接口 `taskgraph.convergence`（收敛作业 + 被挡通知），
 * 每个卡住原因配一个出口：
 *
 * | 卡住原因 | 出口 |
 * |---|---|
 * | 旧尝试还在停下来 | 等它停下（或在下面的任务里取消它） |
 * | 旧尝试已停、计划结构没变，新计划没提交上 | 「放弃这次改计划」（写理由） |
 * | 推进通知连续失败被挡住 | 「重新发送」 |
 *
 * 外部动作结果不明的出口在「等待原因」里的"已生效 / 没生效"，这里不重复。
 * 两个按钮只能由人在这里点：Host 以本机用户身份执行，SDK 安全检查不满足时如实显示原因。
 * 读失败时保留上次画面并标"已过期"；回复不合公开合同（控制通道换成的协议错）时说"收到的数据格式不对"。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  asList as list,
  asRecord as record,
  asText as text,
  newRequestKey,
  type MissionsChannel,
} from "../stores/missionsStore";
import { PROTOCOL_ERROR } from "../ws/orchestrationContracts";
import { sendWhenConnected } from "../ws/sendWhenConnected";
import "./PlanChangePanel.styles.css";

type Job = { job_id: string; state: string; row_version: number };
type Blocked = {
  message_id: string;
  row_version: number;
  subject_key: string;
  error_code: string;
  attempts: number;
};
type View = { jobs: Job[]; blocked: Blocked[] };

const READ = "taskgraph.convergence";
const LIVE = new Set(["FENCED", "WAITING", "READY"]);

function parse(data: unknown): View {
  const body = record(data);
  const jobs = list(body.jobs)
    .map((row) => ({
      job_id: text(row.job_id),
      state: text(row.state),
      row_version: Number(row.row_version),
    }))
    .filter((job) => LIVE.has(job.state));
  // a job that ended takes its blocked notifications back to delivery (SDK), so every one listed is live
  const blocked = list(body.blocked_notifications).map((row) => ({
    message_id: text(row.message_id),
    row_version: Number(row.row_version),
    subject_key: text(row.subject_key),
    error_code: text(row.error_code),
    attempts: Number(row.attempts),
  }));
  return { jobs, blocked };
}

function reasonOf(job: Job): string {
  return job.state === "READY"
    ? "旧尝试已停、计划结构没变，新计划还没提交上"
    : "旧尝试还在停下来，等它停下（或在下面的任务里取消它）";
}

export function PlanChangePanel({
  missionId,
  channel,
}: {
  missionId: string;
  channel: MissionsChannel | null;
}): React.JSX.Element | null {
  const [view, setView] = useState<View | null>(null);
  /** 空串 = 不过期；否则是过期原因（一句大白话） */
  const [stale, setStale] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const reading = useRef<string | null>(null);
  const acting = useRef<string | null>(null);

  // 只发读取请求不写状态；已有读取在途算已发。返回是否发出。
  const sendRead = useCallback(() => {
    if (!channel) return false;
    if (reading.current) return true;
    const id = newRequestKey();
    reading.current = id;
    if (!channel.send({ type: READ, request_id: id, payload: { mission_id: missionId } })) {
      reading.current = null;
      return false;
    }
    return true;
  }, [channel, missionId]);
  // 消息回调里重读：发不出去就标已过期。
  const refresh = useCallback(() => {
    if (channel && !sendRead()) setStale("连接不可用");
  }, [channel, sendRead]);

  useEffect(() => {
    if (!channel) return undefined;
    const off = channel.onMessage((incoming) => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const type = text(message.type);
      const payload = record(message.payload);
      if (type === "session_event") {
        if (text(payload.mission_id) === missionId) refresh();
        return;
      }
      if (type === READ + "_response" && payload.request_id === reading.current) {
        reading.current = null;
        if (payload.ok === true) {
          setView(parse(payload.data));
          setStale("");
        }
        // keep the last view, marked as possibly out of date
        else setStale(payload.error_code === PROTOCOL_ERROR ? "收到的数据格式不对" : "读取失败");
        return;
      }
      if (
        (type === "taskgraph.abandon_convergence_response" ||
          type === "taskgraph.retry_notification_response") &&
        payload.request_id === acting.current
      ) {
        acting.current = null;
        setBusy(false);
        if (payload.ok === true) {
          setError("");
          setReason("");
        } else setError(text(payload.error) || "没有做成，请重试");
        refresh();
      }
    });
    // 首次读取：没连上就等连上再读（结果走上面的消息回调）。
    const cancelRead = sendWhenConnected(channel, sendRead);
    return () => {
      off();
      cancelRead();
    };
  }, [channel, missionId, refresh, sendRead]);

  const act = (type: string, body: Record<string, unknown>) => {
    if (!channel) return;
    const id = newRequestKey();
    acting.current = id;
    setBusy(true);
    if (!channel.send({ type, request_id: id, payload: { mission_id: missionId, ...body } })) {
      acting.current = null;
      setBusy(false);
      setError("连接不可用，请稍后重试");
    }
  };

  if (!view || (!view.jobs.length && !view.blocked.length)) return null;
  return (
    <section
      aria-label="改计划进度"
      className="sh-PlanChangePanel-1"
      data-testid="plan-change-panel"
    >
      <strong>
        改计划进度
        {stale ? (
          <span className="sh-PlanChangePanel-2">（已过期：{stale}，显示的是上次的情况）</span>
        ) : null}
      </strong>
      {view.jobs.map((job) => (
        <div key={job.job_id} className="sh-PlanChangePanel-3">
          <div>这次改计划卡住了：{reasonOf(job)}</div>
          <textarea
            aria-label="放弃理由"
            placeholder="写一句为什么放弃（会告诉规划器，避免它再提同样的改法）"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="sh-PlanChangePanel-4"
          />
          <div>
            <button
              type="button"
              className="sh-PlanChangePanel-5"
              disabled={busy || !reason.trim()}
              onClick={() =>
                act("taskgraph.abandon_convergence", {
                  job_id: job.job_id,
                  expected_version: job.row_version,
                  reason,
                })
              }
            >
              放弃这次改计划
            </button>
          </div>
        </div>
      ))}
      {view.blocked.map((row) => (
        <div key={row.message_id} className="sh-PlanChangePanel-6">
          <span>
            推进通知连续失败 {row.attempts} 次，被挡住了
            <span className="sh-PlanChangePanel-2">（{row.error_code}）</span>
          </span>
          <button
            type="button"
            className="sh-PlanChangePanel-5"
            disabled={busy}
            onClick={() =>
              act("taskgraph.retry_notification", {
                message_id: row.message_id,
                expected_version: row.row_version,
                reason: "人在任务页点了重新发送",
              })
            }
          >
            重新发送
          </button>
        </div>
      ))}
      {error ? (
        <div role="alert" className="sh-PlanChangePanel-7">
          {error}
        </div>
      ) : null}
    </section>
  );
}
