import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContextBreakdownModal } from "./ContextBreakdownModal";
import type { ContextUsageSnapshot } from "../types/messages";

afterEach(cleanup);

const measuredSnapshot: ContextUsageSnapshot = {
  schema_version: 2,
  session_id: "session-1",
  source: "measured",
  sample_id: "sample-7",
  snapshot_id: "snapshot-7",
  snapshot_version: 3,
  version: 7,
  availability: "available",
  has_measurement: true,
  model: "sf-glm-5.2",
  prompt_tokens: 3_200,
  completion_tokens: 100,
  cached_tokens: 0,
  context_window: 128_000,
  effective_ceiling: 115_200,
  compact_at: 102_400,
  recall_sweet: 16_000,
  updated_at: 20,
};

function fixture(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "session-1",
    correlation_id: "request-placeholder",
    snapshot_id: "snapshot-7",
    sample_id: "sample-7",
    snapshot_version: 3,
    usage_version: 7,
    snapshot_fingerprint: "sha256:public-7",
    availability: "available",
    model: "sf-glm-5.2",
    sections: [{
      kind: "system",
      label: "System",
      count: 1,
      tokens: 1_000,
      token_source: "estimated",
      public_preview: "1 条已脱敏系统指令",
      preview_truncated: false,
      preview: "Authorization: Bearer sk-secret-value-123456",
    }],
    total_estimated_tokens: 1_000,
    last_usage_prompt_tokens: 3_200,
    context_window: 128_000,
    effective_ceiling: 115_200,
    compact_at: 102_400,
    updated_at: 20,
    history: [],
    ts: 20,
    ...overrides,
  };
}

function setup(snapshot: ContextUsageSnapshot | null = measuredSnapshot) {
  let listener: ((message: unknown) => void) | undefined;
  const send = vi.fn();
  const view = render(
    <ContextBreakdownModal
      open
      onClose={vi.fn()}
      sessionId="session-1"
      projectName="deskpet"
      projectRoot={null}
      snapshot={snapshot}
      send={send}
      onMessage={(next) => {
        listener = next;
        return () => { listener = undefined; };
      }}
    />,
  );
  return {
    ...view,
    send,
    emit: (payload: Record<string, unknown>) => {
      act(() => listener?.({ type: "context_breakdown_response", payload }));
    },
  };
}

describe("ContextBreakdownModal authority fence", () => {
  it("requests and accepts only the current Session/correlation/snapshot/sample/version", async () => {
    const { send, emit } = setup();
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const request = send.mock.calls[0][0];
    expect(request).toEqual({
      type: "context_breakdown_request",
      payload: {
        session_id: "session-1",
        request_id: expect.any(String),
        expected_snapshot_id: "snapshot-7",
        expected_sample_id: "sample-7",
        expected_usage_version: 7,
        expected_snapshot_version: 3,
      },
    });
    const requestId = request.payload.request_id;

    emit(fixture({ correlation_id: "old-request" }));
    emit(fixture({ correlation_id: requestId, session_id: "session-2" }));
    emit(fixture({ correlation_id: requestId, snapshot_version: 2 }));
    emit(fixture({ correlation_id: requestId, snapshot_id: "snapshot-old" }));
    emit(fixture({ correlation_id: requestId, sample_id: "sample-old" }));
    expect(screen.queryByText("System")).toBeNull();

    emit(fixture({ correlation_id: requestId }));
    expect(await screen.findByText("System")).toBeTruthy();
    fireEvent.click(screen.getByText("System"));
    expect(screen.getByText("1 条已脱敏系统指令")).toBeTruthy();
    expect(screen.queryByText(/sk-secret-value/)).toBeNull();
  });

  it("rejects an equal-version response with a different public payload", async () => {
    const { send, emit } = setup();
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const requestId = send.mock.calls[0][0].payload.request_id;
    emit(fixture({ correlation_id: requestId }));
    expect(await screen.findByText("System")).toBeTruthy();
    emit(fixture({
      correlation_id: requestId,
      sections: [{
        kind: "memory", label: "Memory", count: 1, tokens: 10,
        token_source: "estimated", public_preview: "conflict",
      }],
    }));
    expect(screen.queryByText("Memory")).toBeNull();
    expect(screen.getByText("System")).toBeTruthy();
  });

  it("shows binding-only and missing measurement as unavailable, never numeric zero", async () => {
    const { send, emit } = setup({
      ...measuredSnapshot,
      source: "binding_only",
      availability: "unavailable",
      has_measurement: false,
      sample_id: null,
      snapshot_id: null,
      snapshot_version: null,
      prompt_tokens: 0,
      effective_ceiling: 0,
    });
    expect(screen.getByText(/用量不可用/)).toBeTruthy();
    expect(screen.queryByText(/0 \/ 0 tokens/)).toBeNull();
    expect(screen.queryByText(/no model yet/i)).toBeNull();
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    emit(fixture({
      correlation_id: send.mock.calls[0][0].payload.request_id,
      snapshot_id: null,
      snapshot_version: null,
      sample_id: null,
      usage_version: 7,
      availability: "unavailable",
      sections: [],
      total_estimated_tokens: null,
      last_usage_prompt_tokens: null,
    }));
    expect(await screen.findByText("冻结请求构成不可用")).toBeTruthy();
    expect(screen.queryByText(/估算合计 0/)).toBeNull();
  });

  it("creates a new request after close/reopen and ignores the retired response", async () => {
    let listener: ((message: unknown) => void) | undefined;
    const send = vi.fn();
    const props = {
      onClose: vi.fn(), sessionId: "session-1", snapshot: measuredSnapshot,
      send, onMessage: (next: (message: unknown) => void) => {
        listener = next; return () => { listener = undefined; };
      },
    };
    const view = render(<ContextBreakdownModal open {...props} />);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const oldId = send.mock.calls[0][0].payload.request_id;
    view.rerender(<ContextBreakdownModal open={false} {...props} />);
    view.rerender(<ContextBreakdownModal open {...props} />);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    const newId = send.mock.calls[1][0].payload.request_id;
    expect(newId).not.toBe(oldId);
    act(() => listener?.({
      type: "context_breakdown_response",
      payload: fixture({ correlation_id: oldId }),
    }));
    expect(screen.queryByText("System")).toBeNull();
  });
});
