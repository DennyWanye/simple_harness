// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CompanionEvent, CompanionOwnerFence } from "../types/messages";
import { stable_json } from "./stableJson";

export function same_companion_owner(
  owner: CompanionOwnerFence | null,
  event: Pick<CompanionEvent, "profile_id" | "profile_generation">,
): boolean {
  return (
    !!owner &&
    owner.profile_id === event.profile_id &&
    owner.profile_generation === event.profile_generation
  );
}

export function valid_companion_event(event: CompanionEvent): boolean {
  return (
    !!event.event_id &&
    !!event.profile_id &&
    Number.isInteger(event.profile_generation) &&
    event.profile_generation > 0 &&
    !!event.session_id &&
    Number.isInteger(event.seq) &&
    event.seq >= 0 &&
    !!event.notification?.notification_id &&
    Array.isArray(event.notification.available_actions)
  );
}

export function reduce_companion_events(
  current: CompanionEvent[],
  event: CompanionEvent,
  owner: CompanionOwnerFence | null,
): { events: CompanionEvent[]; handled: boolean } {
  if (!valid_companion_event(event) || !same_companion_owner(owner, event)) {
    return { events: current, handled: false };
  }
  const index = current.findIndex((item) => item.event_id === event.event_id);
  if (index < 0) {
    return {
      events: [...current, event].sort(
        (a, b) => a.seq - b.seq || a.event_id.localeCompare(b.event_id),
      ),
      handled: true,
    };
  }
  const previous = current[index];
  const previousRoute = previous.route_version ?? 0;
  const nextRoute = event.route_version ?? previousRoute;
  if (nextRoute < previousRoute || event.seq < previous.seq) {
    return { events: current, handled: false };
  }
  if (event.seq === previous.seq) {
    const { received_at: _nextReceived, ...nextDurable } = event;
    const { received_at: _previousReceived, ...previousDurable } = previous;
    if (stable_json(nextDurable) === stable_json(previousDurable)) {
      return { events: current, handled: true };
    }
    const nextWithoutDetailFence = {
      ...nextDurable,
      notification: {
        ...nextDurable.notification,
        detail_version: "",
      },
    };
    const previousWithoutDetailFence = {
      ...previousDurable,
      notification: {
        ...previousDurable.notification,
        detail_version: "",
      },
    };
    if (stable_json(nextWithoutDetailFence) !== stable_json(previousWithoutDetailFence)) {
      return { events: current, handled: false };
    }
    const events = [...current];
    events[index] = event;
    return { events, handled: true };
  }
  const events = [...current];
  events[index] = event;
  events.sort((a, b) => a.seq - b.seq || a.event_id.localeCompare(b.event_id));
  return { events, handled: true };
}

export const COMPANION_TOMBSTONE_SUMMARY = "这条内容已被遗忘或撤回。";

export function retract_companion_events(
  current: CompanionEvent[],
  event: Omit<CompanionEvent, "notification"> & {
    notification_id: string;
    redaction_version: number;
    notification?: CompanionEvent["notification"];
  },
  owner: CompanionOwnerFence | null,
): { events: CompanionEvent[]; handled: boolean } {
  if (
    !same_companion_owner(owner, event) ||
    !event.event_id ||
    !event.notification_id ||
    !Number.isInteger(event.redaction_version) ||
    event.redaction_version < 1
  ) {
    return { events: current, handled: false };
  }
  const index = current.findIndex((item) => item.event_id === event.event_id);
  const previous = index >= 0 ? current[index] : null;
  if (previous && event.redaction_version <= (previous.redaction_version ?? 0)) {
    return {
      events: current,
      handled:
        event.redaction_version === previous.redaction_version && previous.tombstone === true,
    };
  }
  if (previous && previous.notification.notification_id !== event.notification_id) {
    return { events: current, handled: false };
  }
  const tombstone: CompanionEvent = {
    event_id: event.event_id,
    profile_id: event.profile_id,
    profile_generation: event.profile_generation,
    session_id: event.session_id || previous?.session_id || "",
    seq: Math.max(event.seq ?? 0, previous?.seq ?? 0),
    route_version: event.route_version ?? previous?.route_version,
    redaction_version: event.redaction_version,
    importance: event.importance ?? previous?.importance,
    occurred_at: event.occurred_at ?? previous?.occurred_at,
    received_at: event.received_at ?? Date.now(),
    notification: {
      notification_id: event.notification_id,
      kind: "tombstone",
      summary: COMPANION_TOMBSTONE_SUMMARY,
      detail_ref: "",
      detail_version: event.notification?.detail_version ?? "",
      available_actions: [],
    },
    tombstone: true,
  };
  if (!valid_companion_event(tombstone)) {
    return { events: current, handled: false };
  }
  const events = [...current];
  if (index >= 0) events[index] = tombstone;
  else events.push(tombstone);
  events.sort((a, b) => a.seq - b.seq || a.event_id.localeCompare(b.event_id));
  return { events, handled: true };
}

export function companion_stream_key(
  runId: string,
  invocationId: string,
  streamEpoch: string,
): string {
  return `${runId}\u001f${invocationId}\u001f${streamEpoch}`;
}
