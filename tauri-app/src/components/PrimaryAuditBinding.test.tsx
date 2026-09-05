import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrimaryChatView } from "../views/PrimaryChatView";
import { ControlChannel } from "../ws/ControlChannel";
import type { PrimaryWireRequest } from "../primary/requests";

vi.mock("../primary/PrimaryRunPanel", () => ({ PrimaryRunPanel: () => null }));
vi.mock("../code-panel/controlWs", () => ({ controlWS: {
  send: vi.fn(), state: () => "connected", on_message: () => () => {},
} }));

/** Only the network transport is replaced. Actual channel cache, bound replay,
 * PrimaryController, parent, request correlation and audit panel are exercised. */
class Socket {
  static readonly OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  sent: PrimaryWireRequest[] = [];
  constructor() { Socket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  close() { this.readyState = 3; this.onclose?.(); }
  receive(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
  reply(request: PrimaryWireRequest, result: object) {
    this.receive({ type: "human_memory_response", request_id: request.request_id,
      payload: { ok: true, operation: request.operation, result } });
  }
  send(serialized: string) {
    const r = JSON.parse(serialized) as PrimaryWireRequest;
    if (r.type !== "human_memory_request") return;
    this.sent.push(r);
    if (r.operation.startsWith("primary.audit.")) return;
    const result = r.operation === "primary.messages.page" || r.operation === "primary.memory.list"
      ? { primary_ref: "primary", items: [], next_cursor: null, revision: "1" }
      : { primary_ref: "primary", current_run: null, queued_count: 0, queued_count_truncated: false, revision: "1" };
    queueMicrotask(() => this.reply(r, result));
  }
}
const channels: ControlChannel[] = [];
const bound = { type: "companion_profile_bound", payload: {
  profile_id: "owner", profile_generation: 1, owner_key: "local:owner", binding_epoch: "1", next_request_seq: "2",
} };
afterEach(() => {
  cleanup(); channels.splice(0).forEach(c => c.disconnect());
  Socket.instances = []; vi.unstubAllGlobals();
});
async function mounted() {
  vi.stubGlobal("WebSocket", Socket);
  const channel = new ControlChannel(1, "synthetic-test-secret"); channels.push(channel);
  channel.connect(); const socket = Socket.instances.at(-1)!;
  socket.open(); socket.receive(bound); // Actual cache predates React mount.
  render(<PrimaryChatView channel={channel} />);
  await waitFor(() => expect((screen.getByRole("button", { name: "记忆" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "记忆" }));
  fireEvent.click(await screen.findByRole("button", { name: "操作记录" }));
  await act(async () => { await Promise.resolve(); }); // deliver boundPort replay
  return { channel, socket };
}
async function displayed(socket: Socket) {
  fireEvent.click(screen.getByRole("button", { name: "查看我的记忆操作记录（仅元数据）" }));
  const opened = socket.sent.at(-1)!;
  const expires = Date.now() / 1000 + 300;
  await act(async () => socket.reply(opened, {
    primary_ref: "primary", audit_ref: "audit", open_action_id: opened.request.open_action_id,
    expires_at: expires, max_reads: 32, page_limit: 100, purpose: "operation_metadata",
  }));
  fireEvent.click(screen.getByRole("button", { name: "读取记录" }));
  const page = socket.sent.at(-1)!;
  await act(async () => socket.reply(page, {
    primary_ref: "primary", audit_ref: "audit", page_action_id: page.request.page_action_id,
    snapshot_hash: "a".repeat(64), page_hash: "b".repeat(64), access_event_hash: "c".repeat(64),
    expires_at: expires, max_reads: 32, reads_used: 1, all_operations_recorded: false,
    next_cursor_ref: null, enumeration_complete: true, coverage: [],
    items: [{ family: "suppression", event_kind: "directive", outcome: "committed", occurred_at: 0,
      cognitive_effect: "not_applicable", operation_ref_hash: "d".repeat(64), item_hash: "e".repeat(64) }],
  }));
  expect(screen.getByText("忘记与禁用 · 已提交")).toBeTruthy();
}

it("actual parent remains usable after cached bound replay, but a fresh same-owner bound revokes its grant", async () => {
  const { socket } = await mounted();
  expect(socket.sent.filter(r => r.operation.startsWith("primary.audit."))).toHaveLength(0);
  expect(screen.getByRole("button", { name: "查看我的记忆操作记录（仅元数据）" })).toBeTruthy();
  await displayed(socket);
  // JSON decode creates a new receipt object. Same owner/epoch is insufficient
  // to preserve a capability across a genuinely new bound frame.
  await act(async () => socket.receive(bound));
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  expect(screen.getByRole("button", { name: "查看我的记忆操作记录（仅元数据）" })).toBeTruthy();
  expect(socket.sent.filter(r => r.operation === "primary.audit.open")).toHaveLength(1);
});

it("actual rechallenge/global-ready cannot restore metadata or send an automatic grant", async () => {
  const { socket } = await mounted(); await displayed(socket);
  await act(async () => socket.receive({ type: "companion_control_rechallenge" }));
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  await act(async () => socket.receive({ type: "companion_identity_status", payload: { ready: true } }));
  expect(screen.queryByRole("button", { name: "查看我的记忆操作记录（仅元数据）" })).toBeNull();
  expect(socket.sent.filter(r => r.operation === "primary.audit.open")).toHaveLength(1);
});

it("a global owner change retracts content without treating the broadcast as a new socket bind", async () => {
  const { socket, channel } = await mounted(); await displayed(socket);
  await act(async () => socket.receive({ type: "companion_identity_status", payload: {
    ready: true, profile_id: "other-owner", profile_generation: 2,
  } }));
  expect(channel.getLatestMessage("companion_profile_bound")).toBeNull();
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  expect(screen.queryByRole("button", { name: "查看我的记忆操作记录（仅元数据）" })).toBeNull();
  expect(socket.sent.filter(r => r.operation === "primary.audit.open")).toHaveLength(1);
});

it.each(["owner", "new-owner"])("channel reconnect as %s clears cached binding and requires a fresh explicit audit action", async owner => {
  const { channel, socket } = await mounted(); await displayed(socket);
  await act(async () => channel.disconnect());
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  expect(channel.getLatestMessage("companion_profile_bound")).toBeNull();
  await act(async () => { channel.connect(); Socket.instances.at(-1)!.open(); });
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  const replacement = Socket.instances.at(-1)!;
  await act(async () => replacement.receive({ ...bound, payload: {
    ...bound.payload, profile_id: owner, owner_key: `local:${owner}`, binding_epoch: "2",
  } }));
  fireEvent.click(await screen.findByRole("button", { name: "操作记录" }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  expect(replacement.sent.filter(r => r.operation.startsWith("primary.audit."))).toHaveLength(0);
  expect(screen.getByRole("button", { name: "查看我的记忆操作记录（仅元数据）" })).toBeTruthy();
});
