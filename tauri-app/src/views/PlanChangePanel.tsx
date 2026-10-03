// SPDX-License-Identifier: BUSL-1.1
/**
 * 改计划进度（HTN 补齐阶段 B 第 2 条，2026-10-03）。
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
 * 读失败时保留上次画面并标"已过期"。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { asList as list, asRecord as record, asText as text, newRequestKey, type MissionsChannel } from "../stores/missionsStore";

type Job = { job_id: string; state: string; row_version: number };
type Blocked = { message_id: string; row_version: number; subject_key: string; error_code: string; attempts: number };
type View = { jobs: Job[]; blocked: Blocked[] };

const READ = "taskgraph.convergence";
const LIVE = new Set(["FENCED", "WAITING", "READY"]);
const box: React.CSSProperties = {
  border: `1px solid ${tokens.color.accent.border}`, borderRadius: tokens.radius.md, padding: tokens.space.sm,
  background: dark.inset, color: dark.text, display: "grid", gap: tokens.space.xs, marginTop: tokens.space.sm,
};
const button: React.CSSProperties = {
  padding: `${tokens.space.xs}px ${tokens.space.sm}px`, borderRadius: tokens.radius.sm,
  border: `1px solid ${tokens.color.surface.hairline}`, background: "transparent", color: dark.text, cursor: "pointer",
};
const muted: React.CSSProperties = { color: dark.textMuted, fontSize: tokens.text.xs.size };

function parse(data: unknown): View {
  const body = record(data);
  const jobs = list(body.jobs).map((row) => ({ job_id: text(row.job_id), state: text(row.state), row_version: Number(row.row_version) }))
    .filter((job) => LIVE.has(job.state));
  // a job that ended takes its blocked notifications back to delivery (SDK), so every one listed is live
  const blocked = list(body.blocked_notifications).map((row) => ({
    message_id: text(row.message_id), row_version: Number(row.row_version), subject_key: text(row.subject_key),
    error_code: text(row.error_code), attempts: Number(row.attempts),
  }));
  return { jobs, blocked };
}

function reasonOf(job: Job): string {
  return job.state === "READY" ? "旧尝试已停、计划结构没变，新计划还没提交上" : "旧尝试还在停下来，等它停下（或在下面的任务里取消它）";
}

export function PlanChangePanel({ missionId, channel }: { missionId: string; channel: MissionsChannel | null }): React.JSX.Element | null {
  const [view, setView] = useState<View | null>(null);
  const [stale, setStale] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const reading = useRef<string | null>(null);
  const acting = useRef<string | null>(null);

  const refresh = useCallback(() => {
    if (!channel || reading.current) return;
    const id = newRequestKey();
    reading.current = id;
    if (!channel.send({ type: READ, request_id: id, payload: { mission_id: missionId } })) {
      reading.current = null;
      setStale(true);
    }
  }, [channel, missionId]);

  useEffect(() => {
    if (!channel) return undefined;
    const off = channel.onMessage((incoming) => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const type = text(message.type);
      const payload = record(message.payload);
      if (type === "mission_changed") {
        if (text(payload.mission_id) === missionId) refresh();
        return;
      }
      if (type === READ + "_response" && payload.request_id === reading.current) {
        reading.current = null;
        if (payload.ok === true) { setView(parse(payload.data)); setStale(false); }
        else setStale(true);  // keep the last view, marked as possibly out of date
        return;
      }
      if ((type === "taskgraph.abandon_convergence_response" || type === "taskgraph.retry_notification_response")
          && payload.request_id === acting.current) {
        acting.current = null;
        setBusy(false);
        if (payload.ok === true) { setError(""); setReason(""); }
        else setError(text(payload.error) || "没有做成，请重试");
        refresh();
      }
    });
    refresh();
    return off;
  }, [channel, missionId, refresh]);

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
    <section aria-label="改计划进度" style={box} data-testid="plan-change-panel">
      <strong>改计划进度{stale ? <span style={muted}>（已过期：读取失败，显示的是上次的情况）</span> : null}</strong>
      {view.jobs.map((job) => (
        <div key={job.job_id} style={{ display: "grid", gap: tokens.space.xs }}>
          <div>这次改计划卡住了：{reasonOf(job)}</div>
          <textarea aria-label="放弃理由" placeholder="写一句为什么放弃（会告诉规划器，避免它再提同样的改法）"
            value={reason} onChange={(event) => setReason(event.target.value)}
            style={{ width: "100%", boxSizing: "border-box", minHeight: 48, background: dark.inset, color: dark.text }} />
          <div>
            <button type="button" style={button} disabled={busy || !reason.trim()}
              onClick={() => act("taskgraph.abandon_convergence", { job_id: job.job_id, expected_version: job.row_version, reason })}>
              放弃这次改计划
            </button>
          </div>
        </div>
      ))}
      {view.blocked.map((row) => (
        <div key={row.message_id} style={{ display: "flex", alignItems: "center", gap: tokens.space.sm }}>
          <span>推进通知连续失败 {row.attempts} 次，被挡住了<span style={muted}>（{row.error_code}）</span></span>
          <button type="button" style={button} disabled={busy}
            onClick={() => act("taskgraph.retry_notification", { message_id: row.message_id, expected_version: row.row_version, reason: "人在任务页点了重新发送" })}>
            重新发送
          </button>
        </div>
      ))}
      {error ? <div role="alert" style={{ color: tokens.color.danger.fg }}>{error}</div> : null}
    </section>
  );
}
