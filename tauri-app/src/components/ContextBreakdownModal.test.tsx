import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContextBreakdownModal } from "./ContextBreakdownModal";

afterEach(cleanup);

describe("ContextBreakdownModal", () => {
  it("shows project context history and raw details by default", async () => {
    let listener: ((message: unknown) => void) | undefined;
    const send = vi.fn();
    render(
      <ContextBreakdownModal
        open
        onClose={vi.fn()}
        sessionId="session-1"
        projectName="(untitled)"
        projectRoot={null}
        snapshot={{
          session_id: "session-1",
          model: "sf-glm-5.2",
          prompt_tokens: 3_200,
          completion_tokens: 100,
          cached_tokens: 0,
          context_window: 128_000,
          effective_ceiling: 115_200,
          compact_at: 102_400,
          recall_sweet: 16_000,
          updated_at: 20,
        }}
        send={send}
        onMessage={(next) => {
          listener = next;
          return () => {
            listener = undefined;
          };
        }}
      />,
    );

    await waitFor(() =>
      expect(send).toHaveBeenCalledWith({
        type: "context_breakdown_request",
        payload: { session_id: "session-1" },
      }),
    );
    act(() =>
      listener?.({
        type: "context_breakdown_response",
        payload: {
          session_id: "session-1",
          project_name: "deskpet",
          project_root: "F:\\projects\\deskpet",
          model: "sf-glm-5.2",
          sections: [
            {
              kind: "system",
              label: "System",
              tokens: 1_000,
              preview: "Authorization: Bearer sk-secret-value-123456",
            },
          ],
          total_estimated_tokens: 1_000,
          last_usage_prompt_tokens: 3_200,
          context_window: 128_000,
          effective_ceiling: 115_200,
          compact_at: 102_400,
          updated_at: 20,
          history: [
            {
              sample_id: "before",
              session_id: "session-1",
              event_type: "provider_attempt",
              tokens_after: 12_000,
              prompt_tokens: 12_000,
              context_window: 128_000,
              effective_ceiling: 115_200,
              estimate_method: "provider_usage",
              created_at: 10,
            },
            {
              sample_id: "compact",
              session_id: "session-1",
              event_type: "compaction",
              tokens_before: 12_000,
              tokens_after: 3_200,
              prompt_tokens: 3_200,
              context_window: 128_000,
              effective_ceiling: 115_200,
              estimate_method: "compressor_report",
              created_at: 20,
            },
          ],
          ts: 20,
        },
      }),
    );

    expect(await screen.findByText("deskpet · sf-glm-5.2")).toBeTruthy();
    expect(screen.getByText("F:\\projects\\deskpet")).toBeTruthy();
    expect(screen.getByRole("img")).toBeTruthy();

    fireEvent.click(screen.getByText("System"));
    expect(screen.getByText(/sk-secret-value-123456/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看原文" })).toBeNull();
  });
});
