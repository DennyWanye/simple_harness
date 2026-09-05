import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryChatView } from "./PrimaryChatView";
import type { ControlChannel } from "../ws/ControlChannel";
vi.mock("../primary/PrimaryRunPanel", () => ({ PrimaryRunPanel: () => <div>运行权限面板</div> }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: { send: vi.fn(), state: () => "connected", on_message: () => () => {} } }));
afterEach(cleanup);
function fixture() {
  const listeners = new Set<(message: unknown) => void>();
  const bound = { type: "companion_profile_bound", payload: { profile_id: "p", profile_generation: 1 } };
  const sent: Array<Record<string, unknown>> = [];
  let pending: Record<string, unknown> | null = null;
  let acceptReads = true;
  const emit = (message: unknown) => listeners.forEach((fn) => fn(message));
  const channel = {
    state: "connected", getLatestMessage: () => bound,
    onMessage: (fn: (message: unknown) => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    onStateChange: () => () => {},
    send: (r: Record<string, unknown>) => {
      sent.push(r);
      if (r.operation === "queue.enqueue") { pending = r; return true; }
      if (!acceptReads) return true;
      const state = { primary_ref: "primary", revision: "r", current_run: null, queued_count: 2, queued_count_truncated: true };
      const item = { message_ref: "message", role: "assistant", text: "持久化的真实回复", has_more: false, total_chars: 8 };
      queueMicrotask(() => emit({ type: "human_memory_response", request_id: r.request_id, payload: {
        ok: true, operation: r.operation, result: r.operation === "primary.messages.page" ? { primary_ref: "primary", revision: "r", items: [item], next_cursor: null } : state,
      } }));
      return true;
    },
  } as unknown as ControlChannel;
  return { channel, sent, emit, stopReads: () => { acceptReads = false; }, ack: () => {
    const request = pending!.request as Record<string, unknown>;
    emit({ type: "human_memory_response", request_id: pending!.request_id, payload: { ok: true, operation: "queue.enqueue", result: {
      delivery_key: request.delivery_key, turn_ref: "turn", receipt_ref: "receipt", scope_ref: null, enqueue_sequence: 1, content_sha256: "a".repeat(64),
    } } });
  } };
}
describe("primary product view", () => {
  it("shows durable text, bounded count and clears draft only on real enqueue ACK", async () => {
    const h = fixture(), settings = vi.fn();
    render(<PrimaryChatView channel={h.channel} onOpenSettings={settings} />);
    await screen.findByText("持久化的真实回复");
    expect(screen.getAllByText(/排队 至少 2/).length).toBeGreaterThan(0);
    const input = screen.getByPlaceholderText("输入消息，Enter 发送…") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "下一条" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input.value).toBe("下一条");
    expect(h.sent.filter((r) => r.operation === "queue.enqueue")).toHaveLength(1);
    act(() => h.ack()); await waitFor(() => expect(input.value).toBe(""));
    fireEvent.click(screen.getByRole("button", { name: "模型与设置" })); expect(settings).toHaveBeenCalledTimes(1);
    expect(h.sent.some((r) => r.type === "chat_v2" || r.type === "session_set_model")).toBe(false);
  });
  it("removes displayed content immediately on a generic invalidation event", async () => {
    const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("持久化的真实回复"); h.stopReads();
    act(() => h.emit({ type: "human_memory_changed" }));
    expect(screen.queryByText("持久化的真实回复")).toBeNull();
  });
});
