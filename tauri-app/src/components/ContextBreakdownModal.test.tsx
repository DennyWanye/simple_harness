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
    snapshot_id: "snapshot-7",
    sample_id: "sample-7",
    snapshot_version: 3,
    usage_version: 7,
    snapshot_fingerprint: "sha256:public-7",
    availability: "available",
    model: "sf-glm-5.2",
    sections: [
      {
        kind: "system",
        label: "System",
        count: 1,
        tokens: 1_000,
        token_source: "estimated",
        public_preview: "1 条已脱敏系统指令",
        preview_truncated: false,
        preview: "Authorization: Bearer sk-secret-value-123456",
      },
    ],
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

function deferred() {
  let resolve!: (value: unknown) => void;
  const promise = new Promise<unknown>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("ContextBreakdownModal", () => {
  it("打开时读一次明细，只显示脱敏后的预览", async () => {
    const query = vi.fn().mockResolvedValue(fixture());
    render(
      <ContextBreakdownModal open onClose={vi.fn()} snapshot={measuredSnapshot} query={query} />,
    );
    expect(await screen.findByText("System")).toBeTruthy();
    expect(query).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("System"));
    expect(screen.getByText("1 条已脱敏系统指令")).toBeTruthy();
    expect(screen.queryByText(/sk-secret-value/)).toBeNull();
  });

  it("没有运行时显示暂无数据", async () => {
    const query = vi.fn().mockResolvedValue(null);
    render(
      <ContextBreakdownModal open onClose={vi.fn()} snapshot={measuredSnapshot} query={query} />,
    );
    expect(await screen.findByText("暂无数据")).toBeTruthy();
  });

  it("shows binding-only and missing measurement as unavailable, never numeric zero", async () => {
    const query = vi.fn().mockResolvedValue(
      fixture({
        snapshot_id: null,
        snapshot_version: null,
        sample_id: null,
        usage_version: null,
        availability: "unavailable",
        sections: [],
        total_estimated_tokens: null,
        last_usage_prompt_tokens: null,
      }),
    );
    render(
      <ContextBreakdownModal
        open
        onClose={vi.fn()}
        query={query}
        snapshot={{
          ...measuredSnapshot,
          source: "binding_only",
          availability: "unavailable",
          has_measurement: false,
          sample_id: null,
          snapshot_id: null,
          snapshot_version: null,
          prompt_tokens: 0,
          effective_ceiling: 0,
        }}
      />,
    );
    expect(screen.getByText(/用量不可用/)).toBeTruthy();
    expect(screen.queryByText(/0 \/ 0 tokens/)).toBeNull();
    expect(await screen.findByText("冻结请求构成不可用")).toBeTruthy();
    expect(screen.queryByText(/估算合计 0/)).toBeNull();
  });

  it("关掉后才回来的旧读取结果不显示；重新打开再读一次", async () => {
    const first = deferred();
    const query = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(null);
    const props = { onClose: vi.fn(), snapshot: measuredSnapshot, query };
    const view = render(<ContextBreakdownModal open {...props} />);
    view.rerender(<ContextBreakdownModal open={false} {...props} />);
    await act(async () => first.resolve(fixture()));
    view.rerender(<ContextBreakdownModal open {...props} />);
    await waitFor(() => expect(query).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("暂无数据")).toBeTruthy();
    expect(screen.queryByText("System")).toBeNull();
  });

  it("打开期间用量换了样本：清掉旧内容并重读", async () => {
    const query = vi.fn().mockResolvedValueOnce(fixture()).mockReturnValueOnce(deferred().promise);
    const view = render(
      <ContextBreakdownModal open onClose={vi.fn()} snapshot={measuredSnapshot} query={query} />,
    );
    expect(await screen.findByText("System")).toBeTruthy();
    view.rerender(
      <ContextBreakdownModal
        open
        onClose={vi.fn()}
        snapshot={{ ...measuredSnapshot, sample_id: "sample-8", version: 8 }}
        query={query}
      />,
    );
    expect(screen.queryByText("System")).toBeNull();
    await waitFor(() => expect(query).toHaveBeenCalledTimes(2));
  });
});
