import { beforeEach, describe, expect, it } from "vitest";

import type { ContextUsageSnapshot } from "../types/messages";
import { useSessionsStore } from "./sessionsStore";

function snapshot(
  version: number,
  source: ContextUsageSnapshot["source"] = "measured",
): ContextUsageSnapshot {
  return {
    schema_version: 2,
    session_id: "context-store-session",
    source,
    version,
    binding_epoch: 1,
    availability: "available",
    provider_id: "kimi",
    model: "kimi-k3",
    model_id: "kimi-k3",
    prompt_tokens: source === "binding_only" ? 0 : version * 10,
    completion_tokens: 0,
    cached_tokens: 0,
    context_window: source === "binding_only" ? 0 : 131_072,
    effective_ceiling: source === "binding_only" ? 0 : 124_518,
    compact_at: source === "binding_only" ? 0 : 91_750,
    recall_sweet: source === "binding_only" ? 0 : 16_000,
    has_measurement: source !== "binding_only",
    updated_at: version,
  };
}

describe("Context Usage store authority", () => {
  beforeEach(() => {
    useSessionsStore.setState({
      active_sid: "default",
      sessions: {},
    });
  });

  it("rejects an older live event after a newer recovery response", () => {
    const store = useSessionsStore.getState();
    expect(store.upsert_context_usage(snapshot(5))).toBe(true);
    expect(store.upsert_context_usage(snapshot(4))).toBe(false);
    expect(
      useSessionsStore.getState().sessions["context-store-session"]
        .context_usage?.version,
    ).toBe(5);
  });

  it("keeps binding-only as an explicit no-measurement state", () => {
    useSessionsStore.getState().upsert_context_usage(snapshot(1, "binding_only"));
    const value = useSessionsStore.getState().sessions[
      "context-store-session"
    ].context_usage;
    expect(value?.source).toBe("binding_only");
    expect(value?.has_measurement).toBe(false);
    expect(value?.context_window).toBe(0);
  });
});
