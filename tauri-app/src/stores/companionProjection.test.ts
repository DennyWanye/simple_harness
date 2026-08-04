// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { beforeEach, describe, expect, it } from "vitest";

import type { CompanionEvent } from "../types/messages";
import { useSessionsStore } from "./sessionsStore";

function event(overrides: Partial<CompanionEvent> = {}): CompanionEvent {
  return {
    event_id: "event-1",
    profile_id: "profile-a",
    profile_generation: 3,
    session_id: "default",
    seq: 1,
    route_version: 1,
    occurred_at: "2026-07-25T01:00:00Z",
    notification: {
      notification_id: "notice-1",
      kind: "growth_notice",
      summary: "学会了一个更稳定的做法",
      detail_ref: "detail-1",
      detail_version: "v1",
      available_actions: ["rollback", "forget"],
    },
    ...overrides,
  };
}

beforeEach(() => {
  const base = useSessionsStore.getState().sessions.default;
  useSessionsStore.setState({
    active_sid: "default",
    sessions: {
      default: {
        ...base,
        messages: [],
        companion_events: [],
        companion_detail_cache: {},
      },
    },
    companion_owner: null,
    companion_provisional_streams: {},
  });
});

describe("Companion projection reducer", () => {
  it("rejects events until the backend-confirmed owner fence matches", () => {
    const store = useSessionsStore.getState();
    expect(store.reduce_companion_event(event())).toBe(false);

    store.set_companion_owner({
      profile_id: "profile-a",
      profile_generation: 3,
    });
    expect(useSessionsStore.getState().reduce_companion_event(event())).toBe(true);
    expect(
      useSessionsStore.getState().sessions.default.companion_events,
    ).toHaveLength(1);
    expect(
      useSessionsStore.getState().reduce_companion_event(
        event({ profile_generation: 2, event_id: "old-event" }),
      ),
    ).toBe(false);
  });

  it("merges history/live by event id, seq and monotonic route version", () => {
    const store = useSessionsStore.getState();
    store.set_companion_owner({
      profile_id: "profile-a",
      profile_generation: 3,
    });
    expect(useSessionsStore.getState().reduce_companion_event(event())).toBe(true);
    expect(useSessionsStore.getState().reduce_companion_event(event())).toBe(true);
    expect(
      useSessionsStore.getState().reduce_companion_event(
        event({ seq: 2, route_version: 0 }),
      ),
    ).toBe(false);
    expect(
      useSessionsStore.getState().reduce_companion_event(
        event({
          seq: 2,
          route_version: 2,
          notification: {
            ...event().notification,
            summary: "新的单调投影",
          },
        }),
      ),
    ).toBe(true);
    expect(
      (useSessionsStore.getState().sessions.default.companion_events ?? [])[0]
        .notification.summary,
    ).toBe("新的单调投影");
  });

  it("refreshes only a same-sequence detail fence and clears stale detail cache", () => {
    const store = useSessionsStore.getState();
    store.set_companion_owner({
      profile_id: "profile-a",
      profile_generation: 3,
    });
    expect(useSessionsStore.getState().reduce_companion_event(event())).toBe(true);
    useSessionsStore.getState().set_companion_detail_cache(
      "notice-1",
      { overview: ["stale"] },
    );

    expect(useSessionsStore.getState().reduce_companion_event(event({
      received_at: Date.now(),
      notification: {
        ...event().notification,
        detail_version: "v2",
      },
    }))).toBe(true);
    let session = useSessionsStore.getState().sessions.default;
    expect(session.companion_events?.[0].notification.detail_version).toBe("v2");
    expect(session.companion_detail_cache).toEqual({});

    expect(useSessionsStore.getState().reduce_companion_event(event({
      notification: {
        ...event().notification,
        detail_version: "v3",
        summary: "same-sequence content rewrite must fail",
      },
    }))).toBe(false);
    session = useSessionsStore.getState().sessions.default;
    expect(session.companion_events?.[0].notification.detail_version).toBe("v2");
  });

  it("retracts through the dedicated tombstone transition and clears detail/actions", () => {
    let store = useSessionsStore.getState();
    store.set_companion_owner({
      profile_id: "profile-a",
      profile_generation: 3,
    });
    store = useSessionsStore.getState();
    store.reduce_companion_event(event());
    store.set_companion_detail_cache("notice-1", { secret: "must disappear" });

    expect(useSessionsStore.getState().retract_companion_projection({
      event_id: "event-1",
      notification_id: "notice-1",
      profile_id: "profile-a",
      profile_generation: 3,
      session_id: "default",
      seq: 1,
      redaction_version: 1,
    })).toBe(true);

    const session = useSessionsStore.getState().sessions.default;
    expect((session.companion_events ?? [])[0]).toMatchObject({
      event_id: "event-1",
      tombstone: true,
      redaction_version: 1,
      notification: {
        notification_id: "notice-1",
        detail_ref: "",
        available_actions: [],
      },
    });
    expect(session.companion_detail_cache).toEqual({});
  });

  it("moves one event across session routes and retracts without leaving an old copy", () => {
    let store = useSessionsStore.getState();
    store.set_companion_owner({
      profile_id: "profile-a",
      profile_generation: 3,
    });
    store = useSessionsStore.getState();
    store.reduce_companion_event(event());
    store.ensure("main-next");
    expect(useSessionsStore.getState().reduce_companion_event(event({
      session_id: "main-next",
      seq: 2,
      route_version: 2,
      notification: {
        ...event().notification,
        summary: "已迁移",
      },
    }))).toBe(true);

    let state = useSessionsStore.getState();
    expect(state.sessions.default.companion_events ?? []).toEqual([]);
    expect(state.sessions["main-next"].companion_events).toHaveLength(1);

    state.ensure("main-latest");
    expect(useSessionsStore.getState().retract_companion_projection({
      event_id: "event-1",
      notification_id: "notice-1",
      profile_id: "profile-a",
      profile_generation: 3,
      session_id: "main-latest",
      seq: 2,
      route_version: 3,
      redaction_version: 1,
    })).toBe(true);

    state = useSessionsStore.getState();
    expect(state.sessions["main-next"].companion_events ?? []).toEqual([]);
    expect(state.sessions["main-latest"].companion_events).toEqual([
      expect.objectContaining({
        event_id: "event-1",
        tombstone: true,
        notification: expect.objectContaining({
          summary: "这条内容已被遗忘或撤回。",
          available_actions: [],
        }),
      }),
    ]);
  });

  it("clears only Companion state on owner switch and keeps normal chat", () => {
    useSessionsStore.getState().set_companion_owner({
      profile_id: "profile-a",
      profile_generation: 3,
    });
    useSessionsStore.getState().push_message("default", {
      role: "assistant",
      text: "普通聊天必须保留",
    });
    useSessionsStore.getState().reduce_companion_event(event());
    useSessionsStore.getState().set_companion_owner({
      profile_id: "profile-b",
      profile_generation: 1,
    });

    const session = useSessionsStore.getState().sessions.default;
    expect(session.messages.map((message) => message.text)).toContain(
      "普通聊天必须保留",
    );
    expect(session.companion_events).toEqual([]);
  });

  it("keeps provisional streams outside Message history and clears them exactly", () => {
    const store = useSessionsStore.getState();
    store.upsert_companion_provisional("run-1", "inv-1", "epoch-1", "半");
    useSessionsStore.getState().upsert_companion_provisional(
      "run-1",
      "inv-1",
      "epoch-1",
      "截",
    );
    useSessionsStore.getState().upsert_companion_provisional(
      "run-1",
      "inv-2",
      "epoch-1",
      "保留",
    );

    expect(useSessionsStore.getState().sessions.default.messages).toEqual([]);
    expect(Object.values(
      useSessionsStore.getState().companion_provisional_streams,
    )).toEqual(["半截", "保留"]);

    useSessionsStore.getState().clear_companion_provisional(
      "run-1",
      "inv-1",
      "epoch-1",
    );
    expect(Object.values(
      useSessionsStore.getState().companion_provisional_streams,
    )).toEqual(["保留"]);
    useSessionsStore.getState().clear_companion_provisional();
    expect(useSessionsStore.getState().companion_provisional_streams).toEqual({});
  });
});
