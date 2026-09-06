import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrimaryChatView } from "./PrimaryChatView";
import type { ControlChannel } from "../ws/ControlChannel";
import type { PrimaryWireRequest } from "../primary/requests";

vi.mock("../code-panel/controlWs", () => ({ controlWS: {
  state: () => "connected", on_message: () => () => {}, send_command: () => true,
} }));
afterEach(cleanup);

const original = { primary_ref: "primary", run_ref: "host-run", sdk_run_ref: "sdk-run", generation: 1,
  effect_ref: "effect", challenge_ref: "challenge", challenge_hash: "a".repeat(64), scope_ref: "new-scope",
  proposal_hash: "b".repeat(64), root_path: "/original-root", root_identity_hash: "c".repeat(64),
  state: "pending", can_decide: true, expires_at_millis: 1900000000000, binding_receipt_ref: null };

// Real View/controller/boundPort/conditional card and request correlator.
// Only the transport/server snapshots are simulated; this is not native proof.
function fixture() {
  const messages = new Set<(message: unknown) => void>();
  const states = new Set<(state: "connected" | "disconnected" | "connecting") => void>();
  const sent: PrimaryWireRequest[] = [];
  let primary = "primary", committed = false;
  let bound = { type: "companion_profile_bound", payload: { profile_id: "owner", profile_generation: 1 } };
  const emit = (message: unknown) => messages.forEach(fn => fn(message));
  const respond = (wire: PrimaryWireRequest, result: Record<string, unknown>) => emit({
    type: "human_memory_response", request_id: wire.request_id,
    payload: { ok: true, operation: wire.operation, result },
  });
  const channel = {
    state: "connected", getLatestMessage: () => bound,
    onMessage: (fn: (message: unknown) => void) => { messages.add(fn); return () => { messages.delete(fn); }; },
    onStateChange: (fn: (state: "connected" | "disconnected" | "connecting") => void) => {
      states.add(fn); return () => { states.delete(fn); };
    },
    send: (wire: PrimaryWireRequest) => {
      sent.push(wire);
      if (wire.operation === "primary.bindings.decide") { committed = true; return true; } // ACK deliberately lost
      if (wire.operation === "primary.bindings.status") return true; // independently release exact response
      const result = wire.operation === "primary.bindings.pending"
        ? { primary_ref: primary, items: committed ? [] : [original], next_cursor: null, truncated: false }
        : wire.operation === "primary.messages.page"
          ? { primary_ref: primary, revision: "r1", items: [], next_cursor: null }
          : { primary_ref: primary, revision: "r1", current_run: null, queued_count: 0, queued_count_truncated: false };
      queueMicrotask(() => respond(wire, result));
      return true;
    },
  };
  return { channel: channel as unknown as ControlChannel, sent, respond,
    disconnect() { channel.state = "disconnected"; states.forEach(fn => fn("disconnected")); },
    reconnect(owner: string, primaryRef: string) {
      primary = primaryRef;
      bound = { type: "companion_profile_bound", payload: { profile_id: owner, profile_generation: 1 } };
      channel.state = "connected"; states.forEach(fn => fn("connected")); emit(bound);
    },
  };
}

it("recovers a lost bound ACK after actual parent unmount, isolating owner and primary", async () => {
  const h = fixture();
  render(<PrimaryChatView channel={h.channel} />);
  fireEvent.click(await screen.findByRole("button", { name: "允许本次绑定" }));
  const write = h.sent.find(wire => wire.operation === "primary.bindings.decide")!;
  expect(write.request).toMatchObject({ primary_ref: "primary", challenge_ref: "challenge", decision: "allow" });
  await act(async () => h.disconnect());
  // The real controller clears primaryRef, so this is a DOM unmount, not
  // merely the child receiving a port event with ready=true throughout.
  expect(screen.queryByRole("region", { name: "项目目录授权" })).toBeNull();
  await act(async () => h.respond(write, { ...write.request, state: "bound", binding_receipt_ref: "late-ack" }));
  expect(screen.queryByText("新任务已绑定此目录")).toBeNull();

  for (const [owner, primary] of [["different-owner", "primary"], ["owner", "different-primary"]]) {
    await act(async () => h.reconnect(owner, primary));
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新状态" }).hasAttribute("disabled")).toBe(false));
    await screen.findByRole("region", { name: "项目目录授权" });
    expect(h.sent.filter(wire => wire.operation === "primary.bindings.status")).toHaveLength(0);
    expect(screen.queryByText("目录：/original-root")).toBeNull();
    expect(screen.queryByText("新任务已绑定此目录")).toBeNull();
    await act(async () => h.disconnect());
    expect(screen.queryByRole("region", { name: "项目目录授权" })).toBeNull();
  }

  await act(async () => h.reconnect("owner", "primary"));
  await waitFor(() => expect(h.sent.filter(wire => wire.operation === "primary.bindings.status")).toHaveLength(1));
  const read = h.sent.find(wire => wire.operation === "primary.bindings.status")!;
  expect(read.request).toEqual({ primary_ref: "primary", challenge_ref: "challenge" });
  expect(screen.queryByText("新任务已绑定此目录")).toBeNull(); // pending=[] proved nothing
  await act(async () => h.respond(read, { primary_ref: "primary", next_cursor: null, truncated: false,
    items: [{ ...original, state: "bound", can_decide: false, binding_receipt_ref: "durable-ack" }] }));
  await screen.findByText("新任务已绑定此目录");
  expect(screen.getByText("新任务：new-scope")).toBeTruthy();
  expect(screen.getByText(/旧任务不会重新打开/)).toBeTruthy();
  expect(h.sent.filter(wire => wire.operation === "primary.bindings.decide")).toEqual([write]);
});
