import { beforeEach, describe, expect, it } from "vitest";

import type { ContextUsageSnapshot } from "../types/messages";
import { useSessionsStore } from "./sessionsStore";

function snapshot(
  version: number,
  source: ContextUsageSnapshot["source"] = "measured",
  overrides: Partial<ContextUsageSnapshot> = {},
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
    ...overrides,
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

  it("accepts only an exact replay at the same version", () => {
    const store = useSessionsStore.getState();
    const original = snapshot(5);
    expect(store.upsert_context_usage(original)).toBe(true);
    expect(store.upsert_context_usage({ ...original })).toBe(true);
    expect(store.upsert_context_usage({
      ...original,
      prompt_tokens: original.prompt_tokens + 1,
    })).toBe(false);
    expect(useSessionsStore.getState().sessions["context-store-session"]
      .context_usage?.prompt_tokens).toBe(original.prompt_tokens);
  });

  it("fails closed for a missing Session identity or invalid version", () => {
    const store = useSessionsStore.getState();
    expect(store.upsert_context_usage({ ...snapshot(1), session_id: "" })).toBe(false);
    expect(store.upsert_context_usage({ ...snapshot(1), version: undefined })).toBe(false);
    expect(useSessionsStore.getState().sessions[""]).toBeUndefined();
  });

  it("keeps model and measurement authority isolated per Session", () => {
    const store = useSessionsStore.getState();
    expect(store.upsert_context_usage(snapshot(2, "measured", {
      session_id: "session-a", model: "model-a", context_window: 100_000,
    }))).toBe(true);
    expect(store.upsert_context_usage(snapshot(1, "measured", {
      session_id: "session-b", model: "model-b", context_window: 200_000,
    }))).toBe(true);
    expect(useSessionsStore.getState().sessions["session-a"].context_usage?.model).toBe("model-a");
    expect(useSessionsStore.getState().sessions["session-b"].context_usage?.model).toBe("model-b");
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
