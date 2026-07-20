import { describe, expect, it } from "vitest";

import {
  EMPTY_DURABLE_RUN_STATE,
  EMPTY_LIVE_RUN_STATE,
  reduceDurableRunEvent,
  reduceLiveStreamEvent,
  type RunEventEnvelope,
} from "./runEventReducer";

function durable(event_id: string, durable_seq: number, kind = "progress"): RunEventEnvelope {
  return { event_id, run_id: "run-1", durable_seq, kind, status: "running" };
}

function live(event_id: string, stream_epoch: string, live_seq: number): RunEventEnvelope {
  return {
    event_id,
    run_id: "run-1",
    kind: "assistant_delta",
    status: "running",
    live_cursor: { stream_epoch, live_seq },
  };
}

describe("run event ordering domains", () => {
  it("keeps hydrated durable progress independent from live token ordering", () => {
    let durableState = reduceDurableRunEvent(EMPTY_DURABLE_RUN_STATE, durable("accepted", 1, "accepted"));
    let liveState = reduceLiveStreamEvent(EMPTY_LIVE_RUN_STATE, live("a-1", "epoch-a", 1));
    liveState = reduceLiveStreamEvent(liveState, live("a-2", "epoch-a", 2));
    durableState = reduceDurableRunEvent(durableState, durable("progress", 2));
    durableState = reduceDurableRunEvent(durableState, durable("final", 3, "final"));

    expect(durableState.events.map((event) => event.event_id)).toEqual(["accepted", "progress", "final"]);
    expect(liveState.events.map((event) => event.event_id)).toEqual(["a-1", "a-2"]);
  });

  it("retires an old live epoch after reconnect and drops its late frames", () => {
    let state = reduceLiveStreamEvent(EMPTY_LIVE_RUN_STATE, live("a-1", "epoch-a", 1));
    state = reduceLiveStreamEvent(state, live("b-1", "epoch-b", 1));
    state = reduceLiveStreamEvent(state, live("a-late", "epoch-a", 2));

    expect(state.currentEpoch).toBe("epoch-b");
    expect(state.retiredEpochs.has("epoch-a")).toBe(true);
    expect(state.events.map((event) => event.event_id)).toEqual(["a-1", "b-1"]);
  });

  it("deduplicates hydration/live repeats and rejects cross-domain envelopes", () => {
    const accepted = durable("accepted", 1, "accepted");
    let durableState = reduceDurableRunEvent(EMPTY_DURABLE_RUN_STATE, accepted);
    durableState = reduceDurableRunEvent(durableState, accepted);
    durableState = reduceDurableRunEvent(durableState, live("wrong-domain", "epoch-a", 1));

    const token = live("a-1", "epoch-a", 1);
    let liveState = reduceLiveStreamEvent(EMPTY_LIVE_RUN_STATE, token);
    liveState = reduceLiveStreamEvent(liveState, token);
    liveState = reduceLiveStreamEvent(liveState, durable("wrong-domain", 2));

    expect(durableState.events).toHaveLength(1);
    expect(liveState.events).toHaveLength(1);
  });
});
