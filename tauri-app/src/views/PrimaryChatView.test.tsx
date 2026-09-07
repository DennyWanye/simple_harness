import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrimaryChatView } from "./PrimaryChatView";
import { graph } from "../primary/testing/graphFixture";
import type { ControlChannel } from "../ws/ControlChannel";
vi.mock("../primary/PrimaryRunPanel", () => ({ PrimaryRunPanel: () => <div>运行权限面板</div> }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: { send: vi.fn(), state: () => "connected", on_message: () => () => {} } }));
afterEach(() => { cleanup(); vi.useRealTimers(); });
function fixture(messageItems?: Array<Record<string, unknown>>) {
  const listeners = new Set<(message: unknown) => void>();
  const bound = { type: "companion_profile_bound", payload: { profile_id: "p", profile_generation: 1 } };
  const sent: Array<Record<string, unknown>> = [];
  let pending: Record<string, unknown> | null = null;
  let memoryPending: Record<string, unknown> | null = null;
  let acceptReads = true;
  let forgotten = false;
  const stateListeners = new Set<(state: string) => void>();
  const emit = (message: unknown) => listeners.forEach((fn) => fn(message));
  const channel = {
    state: "connected", getLatestMessage: () => bound,
    onMessage: (fn: (message: unknown) => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    onStateChange: (fn: (state: string) => void) => { stateListeners.add(fn); return () => { stateListeners.delete(fn); }; },
    send: (r: Record<string, unknown>) => {
      sent.push(r);
      if (r.operation === "queue.enqueue") { pending = r; return true; }
      if (r.operation === "primary.memory.forget") { memoryPending = r; return true; }
      if (!acceptReads) return true;
      const state = { primary_ref: "primary", revision: "r", current_run: null, queued_count: 2, queued_count_truncated: true };
      const item = { message_ref: "message", role: "assistant", text: "持久化的真实回复", has_more: true, total_chars: 20 };
      const memories = { primary_ref: "primary", items: forgotten ? [] : [{ memory_id: "semantic-memory", revision: 1,
        label: "回答保持简洁", status: "active", can_forget: true, content_hash: "a".repeat(64) }], next_cursor: null };
      queueMicrotask(() => emit({ type: "human_memory_response", request_id: r.request_id, payload: {
        ok: true, operation: r.operation, result: r.operation === "primary.memory.graph" ? { ...graph, primary_ref: "primary", nodes: [], edges: [] } : r.operation === "primary.messages.detail" ? { message_ref: "message", offset: 0, text: "旧来源完整详情", total_chars: 7, next_offset: null } : r.operation === "primary.memory.list" ? memories : r.operation === "primary.messages.page" ? { primary_ref: "primary", revision: "r", items: messageItems ?? [item], next_cursor: null } : state,
      } }));
      return true;
    },
  } as unknown as ControlChannel;
  return { channel, sent, emit, disconnect: () => { stateListeners.forEach((fn) => fn("disconnected")); },
    resumeReads: () => { acceptReads = true; }, stopReads: () => { acceptReads = false; }, ackForget: (overrides: Record<string, unknown> = {}) => {
    const request = memoryPending!.request as Record<string, unknown>;
    forgotten = true;
    acceptReads = false;
    emit({ type: "human_memory_response", request_id: memoryPending!.request_id, payload: { ok: true,
      operation: "primary.memory.forget", result: { primary_ref: "primary", action_id: request.action_id,
        memory_id: request.memory_id, status: "applied", directive_ref: "directive", evidence_ref: "action-evidence", decision_hash: "b".repeat(64), ...overrides } } });
  }, ack: () => {
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
  it("uses the bound cognitive panel and retracts displayed history before post-forget reads finish", async () => {
    const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("持久化的真实回复");
    fireEvent.click(screen.getByRole("button", { name: "记忆" }));
    await screen.findByText("回答保持简洁");
    fireEvent.click(screen.getByRole("button", { name: "忘记这条记忆" }));
    expect(h.sent.filter((r) => r.operation === "primary.memory.forget")).toHaveLength(1);
    await act(async () => { h.ackForget(); await Promise.resolve(); });
    expect(screen.queryByText("持久化的真实回复")).toBeNull();
    expect(h.sent.some((r) => r.type === "memory_forget" || r.type === "memory_facts_list")).toBe(false);
  });
});


it("confirms a matching forget ACK across parent content invalidation with reads still pending", async () => {
  const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("持久化的真实回复");
  fireEvent.click(screen.getByRole("button", { name: /读取完整消息/ }));
  await screen.findByText("旧来源完整详情");
  fireEvent.click(screen.getByRole("button", { name: "记忆" }));
  await screen.findByText("回答保持简洁");
  fireEvent.click(screen.getByRole("button", { name: "忘记这条记忆" }));
  h.stopReads();
  await act(async () => { h.emit({ type: "human_memory_changed", payload: {} }); });
  expect(screen.queryByText("持久化的真实回复")).toBeNull();
  expect(screen.queryByText("回答保持简洁")).toBeNull();
  expect(screen.queryByText("旧来源完整详情")).toBeNull();
  await act(async () => { h.ackForget(); });
  expect(screen.getByText("已忘记该记忆；保留原始历史档案。")).toBeTruthy();
  expect(screen.queryByText("有一项忘记操作尚未确认。")).toBeNull();
  expect(h.sent.filter((r) => r.operation === "primary.memory.forget")).toHaveLength(1);
  h.resumeReads();
  fireEvent.click(screen.getByRole("button", { name: "关系图" }));
  await screen.findByText("当前没有符合条件的记忆关系可展示。");
  expect(h.sent.filter((r) => r.operation === "primary.memory.graph")).toHaveLength(1);
});

it.each(["disconnect", "companion_control_rechallenge", "owner-change"])(
  "%s cannot accept a late forget ACK as current authorization", async (event) => {
    const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
    await screen.findByText("持久化的真实回复");
    fireEvent.click(screen.getByRole("button", { name: "记忆" }));
    await screen.findByText("回答保持简洁");
    fireEvent.click(screen.getByRole("button", { name: "忘记这条记忆" }));
    h.stopReads();
    await act(async () => { h.emit({ type: "human_memory_changed" }); });
    await act(async () => {
      if (event === "disconnect") h.disconnect();
      else if (event === "owner-change") h.emit({ type: "companion_profile_bound", payload: { profile_id: "new-owner", profile_generation: 2 } });
      else h.emit({ type: event });
      h.ackForget();
    });
    expect(screen.queryByText("持久化的真实回复")).toBeNull();
    expect(screen.queryByText("回答保持简洁")).toBeNull();
    expect(screen.queryByText("已忘记该记忆；保留原始历史档案。")).toBeNull();
    expect(h.sent.filter((r) => r.operation === "primary.memory.forget")).toHaveLength(1);
  });


it("retries an earlier timeout with the same action and accepts ACK after another invalidation", async () => {
  const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("持久化的真实回复");
  fireEvent.click(screen.getByRole("button", { name: "记忆" }));
  await screen.findByText("回答保持简洁");
  vi.useFakeTimers(); h.stopReads();
  fireEvent.click(screen.getByRole("button", { name: "忘记这条记忆" }));
  const first = h.sent.find((r) => r.operation === "primary.memory.forget")!;
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(screen.getByText("有一项忘记操作尚未确认。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "重试同一忘记操作" }));
  const writes = h.sent.filter((r) => r.operation === "primary.memory.forget");
  expect(writes).toHaveLength(2);
  expect(writes[1].request).toEqual(first.request);
  expect(writes[1].request_id).not.toBe(first.request_id);
  await act(async () => { h.emit({ type: "human_memory_changed" }); });
  await act(async () => { h.ackForget(); });
  expect(screen.getByText("已忘记该记忆；保留原始历史档案。")).toBeTruthy();
  expect(screen.queryByText("有一项忘记操作尚未确认。")).toBeNull();
  expect(h.sent.filter((r) => r.operation === "primary.memory.forget")).toHaveLength(2);
});

it("a wrong-action ACK cannot clear unknown merely because a fresh list is empty", async () => {
  const h = fixture(); render(<PrimaryChatView channel={h.channel} />);
  await screen.findByText("持久化的真实回复");
  fireEvent.click(screen.getByRole("button", { name: "记忆" }));
  await screen.findByText("回答保持简洁");
  fireEvent.click(screen.getByRole("button", { name: "忘记这条记忆" }));
  h.stopReads();
  await act(async () => { h.emit({ type: "human_memory_changed" }); });
  await act(async () => { h.ackForget({ action_id: "wrong-action" }); });
  expect(screen.getByText("有一项忘记操作尚未确认。")).toBeTruthy();
  const freshList = h.sent.findLast((r) => r.operation === "primary.memory.list")!;
  await act(async () => { h.emit({ type: "human_memory_response", request_id: freshList.request_id,
    payload: { ok: true, operation: "primary.memory.list", result: { primary_ref: "primary", items: [], next_cursor: null } } }); });
  h.resumeReads();
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  await screen.findByText("当前页没有可展示的认知记忆。");
  expect(screen.getByText("有一项忘记操作尚未确认。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "关系图" }));
  await screen.findByText("有一项忘记操作尚未确认，请返回记忆列表处理。");
  expect(h.sent.filter((r) => r.operation === "primary.memory.graph")).toHaveLength(0);
});


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
