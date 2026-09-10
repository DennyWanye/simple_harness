import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryChatView } from "./PrimaryChatView";
import type { ControlChannel } from "../ws/ControlChannel";
vi.mock("../primary/PrimaryRunPanel", () => ({ PrimaryRunPanel: () => <div>运行权限面板</div> }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: { send: vi.fn(), state: () => "connected", on_message: () => () => {} } }));
afterEach(() => { cleanup(); vi.useRealTimers(); });
function fixture(messageItems?: Array<Record<string, unknown>>) {
  const listeners = new Set<(message: unknown) => void>();
  const bound = { type: "companion_profile_bound", payload: { profile_id: "p", profile_generation: 1 } };
  const sent: Array<Record<string, unknown>> = [];
  let pending: Record<string, unknown> | null = null;
  let acceptReads = true;
  const stateListeners = new Set<(state: string) => void>();
  const emit = (message: unknown) => listeners.forEach((fn) => fn(message));
  const channel = {
    state: "connected", getLatestMessage: () => bound,
    onMessage: (fn: (message: unknown) => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    onStateChange: (fn: (state: string) => void) => { stateListeners.add(fn); return () => { stateListeners.delete(fn); }; },
    send: (r: Record<string, unknown>) => {
      sent.push(r);
      if (r.operation === "queue.enqueue") { pending = r; return true; }
      if (!acceptReads) return true;
      const state = { primary_ref: "primary", revision: "r", current_run: null, queued_count: 2, queued_count_truncated: true };
      const item = { message_ref: "message", role: "assistant", text: "持久化的真实回复", has_more: true, total_chars: 20 };
      queueMicrotask(() => emit({ type: "human_memory_response", request_id: r.request_id, payload: {
        ok: true, operation: r.operation, result: r.operation === "primary.messages.detail" ? { message_ref: "message", offset: 0, text: "旧来源完整详情", total_chars: 7, next_offset: null } : r.operation === "primary.messages.page" ? { primary_ref: "primary", revision: "r", items: messageItems ?? [item], next_cursor: null } : state,
      } }));
      return true;
    },
  } as unknown as ControlChannel;
  return { channel, sent, emit, disconnect: () => { stateListeners.forEach((fn) => fn("disconnected")); },
    resumeReads: () => { acceptReads = true; }, stopReads: () => { acceptReads = false; }, ack: () => {
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


// 2026-09-10 removed with the Memory SDK: cognitive drawer / forget-ACK case

// 2026-09-10 removed with the Memory SDK: cognitive drawer / forget-ACK case

// 2026-09-10 removed with the Memory SDK: cognitive drawer / forget-ACK case

// 2026-09-10 removed with the Memory SDK: cognitive drawer / forget-ACK case


it("renders a typed Host reminder separately when the model only answers 47 and retracts it on invalidation", async () => {
  const h = fixture([
    { message_ref: "answer", role: "assistant", text: "47", has_more: false, total_chars: 2 },
    { message_ref: "notice", role: "reminder", notice_id: "a".repeat(64), text: "检查银杏测试清单", has_more: false, total_chars: 8 },
  ]);
  render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("检查银杏测试清单");
  expect(screen.getByText("提醒")).toBeTruthy();
  expect(screen.getByText("47")).toBeTruthy();
  // 口径：提醒卡片固定在同一轮助手正文之后，前端按读模型顺序渲染、不重排。
  expect(screen.getByText("47").compareDocumentPosition(screen.getByText("检查银杏测试清单")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  h.stopReads();
  act(() => h.emit({ type: "human_memory_changed" }));
  expect(screen.queryByText("检查银杏测试清单")).toBeNull();
  expect(h.sent.some((r) => r.operation === "prospective_ack")).toBe(false);
});

it("rejects reminder text without a typed notice identity", async () => {
  const h = fixture([{ message_ref: "unbound", role: "reminder", text: "不得显示", has_more: false, total_chars: 4 }]);
  render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("提醒来源格式无效");
  expect(screen.queryByText("不得显示")).toBeNull();
});

// UAT 2026-09-09：「我分不清哪句话是我说的，哪句话是 AI 回答的」——三种角色
// 必须有不同的对齐/底色，工具记录默认折叠但看得见"有工具调用"，点开可读。
it("renders user / assistant / tool with distinct roles and a collapsible tool record", async () => {
  const h = fixture([
    { message_ref: "u1", role: "user", text: "我的提问", has_more: false, total_chars: 4 },
    { message_ref: "a1", role: "assistant", text: "助手的回答", has_more: false, total_chars: 5 },
    { message_ref: "t1", role: "tool", text: '{"call_id":"c1","name":"read_file"}\n工具正文内容', has_more: false, total_chars: 40 },
  ]);
  render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("助手的回答");
  const user = screen.getByTestId("primary-message-user");
  const assistant = screen.getByTestId("primary-message-assistant");
  const tool = screen.getByTestId("primary-message-tool");
  expect(user.getAttribute("data-role")).toBe("user");
  expect(user.style.textAlign).toBe("right");
  expect(assistant.style.textAlign).toBe("left");
  expect(user.style.background).not.toBe(assistant.style.background);
  expect(tool.style.background).not.toBe(assistant.style.background);
  // 折叠时正文不渲染，但标题写明是哪个工具。
  const toggle = screen.getByRole("button", { name: /工具 · read_file/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("工具正文内容")).toBeNull();
  fireEvent.click(toggle);
  expect(screen.getByText("工具正文内容")).toBeTruthy();
  expect(screen.getByRole("button", { name: /工具 · read_file/ }).getAttribute("aria-expanded")).toBe("true");
});
