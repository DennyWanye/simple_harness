// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ControlWireMessage } from "./controlWsTypes";
import type { SessionsStore } from "../stores/sessionTypes";
import { companionEventFrom } from "./controlWsShared";

/** 控制通道消息处理：陪伴事件。由 controlWs 的处理函数表按消息类型分发（9G 从 dispatch 的 switch 拆出，分支体原样）。 */
export function onCompanionIdentityUnready(
  _msg: ControlWireMessage,
  store: SessionsStore,
  _sid: string,
): void {
  store.set_companion_owner(null);
}

export function onCompanionEvent(
  msg: ControlWireMessage,
  store: SessionsStore,
  _sid: string,
): void {
  const event = companionEventFrom(msg.payload);
  if (event) {
    store.reduce_companion_event(event);
    if (event.decision?.kind === "action_confirmation") {
      store.clear_companion_provisional(event.decision.run_id);
    }
  }
}

export function onCompanionProjectionRetracted(
  msg: ControlWireMessage,
  store: SessionsStore,
  _sid: string,
): void {
  const p = msg.payload || {};
  store.retract_companion_projection({
    event_id: String(p.event_id || p.projection_event_id || ""),
    notification_id: String(p.notification_id || ""),
    profile_id: String(p.profile_id || ""),
    profile_generation: Number(p.profile_generation || 0),
    session_id: String(p.session_id || store.active_sid),
    seq: Number(p.seq ?? 0),
    route_version: p.route_version === undefined ? undefined : Number(p.route_version),
    redaction_version: Number(p.redaction_version || 0),
    occurred_at: typeof p.occurred_at === "string" ? p.occurred_at : undefined,
    received_at: Date.now(),
  });
}

export function onCompanionProvisionalDelta(
  msg: ControlWireMessage,
  store: SessionsStore,
  _sid: string,
): void {
  const p = msg.payload || {};
  const runId = String(p.run_id || "");
  const invocationId = String(p.invocation_id || "");
  const streamEpoch = String(p.stream_epoch || "");
  if (p.retract === true || p.cancelled === true || p.unknown === true) {
    store.clear_companion_provisional(runId, invocationId, streamEpoch);
  } else {
    store.upsert_companion_provisional(runId, invocationId, streamEpoch, String(p.content || ""));
  }
}
