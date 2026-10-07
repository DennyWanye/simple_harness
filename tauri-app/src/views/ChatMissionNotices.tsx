/**
 * 主对话里的后台任务结束通知（HTN 补齐阶段 B 第 1 条，2026-10-03）。
 *
 * 保证通道在任务结束时通知 Host；Host 记下通知，直到人在这里点"已收到"。主 Agent 下一轮会在
 * 上下文里看到还没收到的通知，但"已收到"只能由人亲手点——模型不能代点。
 */
import React, { useCallback, useContext, useEffect, useRef, useState } from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { asList as list, asRecord as record, asText as text, newRequestKey } from "../stores/missionsStore";
import { MissionsChannelContext } from "./chatMission";
import { PROTOCOL_ERROR, STALE_NOTE } from "../ws/orchestrationContracts";

type Settled = { operation: string; target: string; applied: boolean };
type Notice = {
  notice_id: string; mission_id: string; status: string; status_zh: string; goal: string; stop_reason: string;
  /** 停下时还有几个对外操作结果不明（系统会继续核对）。 */
  unresolved_actions: number;
  /** 这条通知说的是停下后某个对外操作的核对结果。 */
  action_settled: Settled | null;
};

const box: React.CSSProperties = {
  border: `1px solid ${tokens.color.accent.border}`,
  borderRadius: tokens.radius.md,
  padding: tokens.space.sm,
  background: dark.inset,
  color: dark.text,
  display: "flex",
  alignItems: "center",
  gap: tokens.space.sm,
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
  flexShrink: 0,
};
const oneLine: React.CSSProperties = { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
const muted: React.CSSProperties = { color: dark.textMuted, fontSize: tokens.text.xs.size };
/** 只摆最新几条；旧库里攒下的一批通知由人点一次"全部已收到"清掉。 */
const SHOWN = 3;
const ALL = "__all__";

function asNotice(value: unknown): Notice | null {
  const row = record(value);
  const noticeId = text(row.notice_id);
  if (!noticeId) return null;
  return {
    notice_id: noticeId,
    mission_id: text(row.mission_id),
    status: text(row.status),
    status_zh: text(row.status_zh) || text(row.status),
    goal: text(row.goal),
    stop_reason: text(row.stop_reason),
    unresolved_actions: Number(row.unresolved_actions) || 0,
    action_settled: row.action_settled
      ? { operation: text(record(row.action_settled).operation), target: text(record(row.action_settled).target),
        applied: record(row.action_settled).applied === true }
      : null,
  };
}

export function ChatMissionNotices(): React.JSX.Element | null {
  const channel = useContext(MissionsChannelContext);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  /** 最近一次通知列表读到坏消息（推后第 3 批 U09）：卡片保留上次的，下面显示这句话；好列表到了清掉。 */
  const [listError, setListError] = useState("");
  const shown = useRef(false);
  const listRequest = useRef<string | null>(null);
  const acks = useRef(new Map<string, string>());

  const refresh = useCallback(() => {
    if (!channel || listRequest.current) return;
    const requestId = newRequestKey();
    listRequest.current = requestId;
    if (!channel.send({ type: "mission_notices", request_id: requestId, payload: {} })) listRequest.current = null;
  }, [channel]);

  useEffect(() => {
    if (!channel) return;
    const off = channel.onMessage((incoming) => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const type = text(message.type);
      const payload = record(message.payload);
      if (type === "mission_changed") {
        refresh();
        return;
      }
      if (type === "mission_notices_response" && payload.request_id === listRequest.current) {
        listRequest.current = null;
        if (payload.ok === true) {
          shown.current = true;
          setNotices(list(payload.data).map(asNotice).filter((item): item is Notice => item !== null));
          setListError("");
        } else if (payload.error_code === PROTOCOL_ERROR) {
          setListError(text(payload.error) + (shown.current ? STALE_NOTE : ""));
        }
        return;
      }
      if (type === "mission_notice_ack_response") {
        const noticeId = acks.current.get(text(payload.request_id));
        if (noticeId === undefined) return;
        acks.current.delete(text(payload.request_id));
        setBusy((state) => ({ ...state, [noticeId]: false }));
        if (payload.ok === true) {
          setNotices((items) => (noticeId === ALL ? [] : items.filter((item) => item.notice_id !== noticeId)));
          setError("");
        } else {
          setError(text(payload.error) || "没有记上，请重试");
        }
      }
    });
    // 第 2 批 U04：通道断开再连上就重新拉一次（Host 会从已读序号续读）；断线前没应答的请求作废，
    // 不然 listRequest 一直占着，重连后永远不再拉。
    const offState = channel.onStateChange?.((state) => {
      if (state !== "connected") return;
      listRequest.current = null;
      refresh();
    });
    refresh();
    return () => { off(); offState?.(); };
  }, [channel, refresh]);

  const acknowledge = (noticeId: string) => {
    if (!channel) return;
    const requestId = newRequestKey();
    acks.current.set(requestId, noticeId);
    setBusy((state) => ({ ...state, [noticeId]: true }));
    const payload = noticeId === ALL ? { all: true } : { notice_id: noticeId };
    if (!channel.send({ type: "mission_notice_ack", request_id: requestId, payload })) {
      acks.current.delete(requestId);
      setBusy((state) => ({ ...state, [noticeId]: false }));
      setError("连接不可用，请稍后重试");
    }
  };

  if (!notices.length && !error && !listError) return null;
  return (
    <div aria-label="后台任务通知" style={{ display: "grid", gap: tokens.space.xs, margin: `${tokens.space.xs}px 0` }}>
      {notices.slice(-SHOWN).map((notice) => (
        <div key={notice.notice_id} style={box} data-testid={`chat-notice-${notice.notice_id}`}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {/* 目标原文可能很长：只摆一行，悬停看全文。 */}
            {notice.action_settled ? (
              <>
                <div style={oneLine} title={notice.goal || notice.mission_id}>后台任务的对外操作有结果了：{notice.goal || notice.mission_id}</div>
                <div style={muted}>{`${notice.action_settled.operation} ${notice.action_settled.target} 核对结果：${notice.action_settled.applied ? "已生效" : "未生效"}`}</div>
              </>
            ) : (
              <>
                <div style={oneLine} title={notice.goal || notice.mission_id}>后台任务{notice.status_zh}：{notice.goal || notice.mission_id}</div>
                {notice.status === "FAILED" && notice.stop_reason ? <div style={muted}>停止原因：{notice.stop_reason}</div> : null}
                {notice.unresolved_actions > 0 ? <div style={muted}>{`还有 ${notice.unresolved_actions} 个对外操作结果不明，系统会继续核对`}</div> : null}
              </>
            )}
          </div>
          <button type="button" style={button} disabled={busy[notice.notice_id] === true}
            onClick={() => acknowledge(notice.notice_id)}>已收到</button>
        </div>
      ))}
      {notices.length > SHOWN ? (
        <div style={{ display: "flex", alignItems: "center", gap: tokens.space.sm }}>
          <span style={muted}>还有 {notices.length - SHOWN} 条较早的通知</span>
          <button type="button" style={button} disabled={busy[ALL] === true}
            onClick={() => acknowledge(ALL)}>全部已收到</button>
        </div>
      ) : null}
      {listError ? <div role="alert" style={{ color: tokens.color.danger.fg }}>{listError}</div> : null}
      {error ? <div role="alert" style={{ color: tokens.color.danger.fg }}>{error}</div> : null}
    </div>
  );
}
