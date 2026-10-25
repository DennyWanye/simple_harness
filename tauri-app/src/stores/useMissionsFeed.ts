// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * useMissionsFeed — 任务编排的常驻订阅。
 *
 * 在 App 层挂一次，始终挂载，与编排视图是否打开无关：
 * - 连上控制通道时发 `orchestration_status`、`mission_list`；
 * - 处理 `orchestration_status_response`、`mission_list_response`；
 * - 收到 `session_event` 推送 → `applyChange`，并节流（≤1 次/秒）重拉 `mission_list`，
 *   让侧栏「任务编排」角标（待审批总数）和列表里的 ui_state 实时更新。
 *
 * MissionsView 不再做这些，只处理详情、事件、各命令的应答和选中 Mission 的刷新。
 */
import { useEffect } from "react";

import type { IncomingMessage } from "../types/messages";
import { PROTOCOL_ERROR, PROTOCOL_ERROR_TEXT } from "../ws/orchestrationContracts";
import {
  asList,
  asRecord,
  asText,
  newRequestKey,
  useMissionsStore,
  type OrchestrationStatus,
  type MissionsChannel,
} from "./missionsStore";

/** 推送触发的列表重拉最短间隔。 */
const LIST_REFRESH_INTERVAL_MS = 1000;
/** 推送没改变任何任务状态时，列表重拉的最短间隔。 */
const QUIET_LIST_REFRESH_MS = 5000;

export function useMissionsFeed(channel: MissionsChannel | null): void {
  useEffect(() => {
    if (!channel) return undefined;
    let statusRequest: string | null = null;
    const request = (type: string) => {
      const requestId = newRequestKey();
      if (type === "mission_list") useMissionsStore.getState().setListRequest(requestId);
      else statusRequest = requestId;
      channel.send({ type, request_id: requestId, payload: {} });
    };

    // throttle: send now, then at most once per interval; pushes inside the interval
    // collapse into one trailing refresh so the last change is never missed
    let cooling: ReturnType<typeof setTimeout> | null = null;
    let pending = false;
    let lastSent = 0;
    const sendList = () => {
      pending = false;
      lastSent = Date.now();
      request("mission_list");
      cooling = setTimeout(() => {
        cooling = null;
        if (pending) sendList();
      }, LIST_REFRESH_INTERVAL_MS);
    };
    // NEXT-TG-1.0 §9 (2026-09-28): a running Mission pushes every second or two and each
    // list costs the backend a pass over every row.  A push that moved no status (or named
    // a Mission the list does not have) refreshes within 1 s; otherwise at most every 5 s.
    let quietWait = false; // the pending timer is the slow 5 s one
    const refreshList = (urgent: boolean) => {
      if (cooling && urgent && quietWait) {
        // a status change must not wait out the slow timer: fall back to the 1 s pace
        clearTimeout(cooling);
        cooling = null;
        quietWait = false;
        const wait = LIST_REFRESH_INTERVAL_MS - (Date.now() - lastSent);
        if (wait <= 0) {
          sendList();
          return;
        }
        pending = true;
        cooling = setTimeout(() => {
          cooling = null;
          if (pending) sendList();
        }, wait);
        return;
      }
      if (cooling) {
        pending = true;
        return;
      }
      if (urgent || Date.now() - lastSent >= QUIET_LIST_REFRESH_MS) sendList();
      else {
        pending = true;
        quietWait = true;
        cooling = setTimeout(
          () => {
            cooling = null;
            quietWait = false;
            if (pending) sendList();
          },
          QUIET_LIST_REFRESH_MS - (Date.now() - lastSent),
        );
      }
    };

    const off = channel.onMessage((incoming: IncomingMessage) => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const payload = asRecord(message.payload);
      const state = useMissionsStore.getState();
      switch (message.type) {
        case "orchestration_status_response": {
          if (payload.request_id !== statusRequest) break;
          const data = asRecord(payload.data);
          if (payload.ok !== false && typeof data.available === "boolean") {
            state.setStatus(data as unknown as OrchestrationStatus);
          }
          break;
        }
        case "mission_list_response":
          if (payload.request_id !== state.listRequestId) break;
          if (payload.ok === true) state.setMissions(asList(asRecord(payload.data).missions));
          // 坏消息（通道已按公开合同换成协议错）不动上次的列表，只记下这句话给列表上方显示
          else if (payload.error_code === PROTOCOL_ERROR)
            state.setListError(asText(payload.error) || PROTOCOL_ERROR_TEXT);
          break;
        case "session_event": {
          if (
            ["delegated", "task_progress", "needs_response", "result_ready"].includes(
              asText(payload.kind),
            )
          ) {
            const eventType = asText(asRecord(payload.payload).type);
            refreshList(
              payload.kind !== "task_progress" ||
                ["MissionCompleted", "MissionFailed", "MissionCancelled"].includes(eventType),
            );
          }
          break;
        }
        default:
          break;
      }
    });

    request("orchestration_status");
    sendList();
    // 2026-09-25 UI 全量点击：编排后台连续出错时任务停在原地、界面仍显示"正在自动进行"。
    // 状态以前只在打开/重连时取一次；定时重取，出错能在 15 秒内显示出来。
    const statusTimer = setInterval(() => {
      request("orchestration_status");
      refreshList(false);
    }, 15000);
    const offState = channel.onStateChange?.((connection) => {
      statusRequest = null;
      useMissionsStore.getState().setListRequest(null);
      if (cooling) clearTimeout(cooling);
      cooling = null;
      pending = false;
      if (connection === "connected") {
        request("orchestration_status");
        sendList();
      }
    });
    return () => {
      clearInterval(statusTimer);
      off();
      offState?.();
      if (cooling) clearTimeout(cooling);
      cooling = null;
      pending = false;
    };
  }, [channel]);
}
