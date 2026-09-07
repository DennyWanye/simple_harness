import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrimaryMemoryPanel } from "./PrimaryMemoryPanel";
import { CognitiveRequests } from "../primary/cognitiveRequests";
import type { PrimaryPort } from "../primary/controller";
import type { PrimaryWireRequest } from "../primary/requests";

afterEach(cleanup);
it("displays the memory label, keeps canonical identity in the request, and forgets only on explicit click", async () => {
  const listeners = new Set<(v: unknown) => void>(), sent: PrimaryWireRequest[] = [];
  const port: PrimaryPort = { state: () => "connected", on_state_change: () => () => {},
    send_command: (r) => { sent.push(r); return true; }, on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; } };
  const ui = render(<PrimaryMemoryPanel port={port} primaryRef="p" verifiedOwnerKey="owner" ready />);
  await act(async () => { listeners.forEach((fn) => fn({ type: "human_memory_response", request_id: sent[0].request_id,
    payload: { ok: true, operation: "primary.memory.list", result: { primary_ref: "p", next_cursor: null, items: [
      { memory_id: "canonical-42", revision: 3, label: "偏好简洁", status: "active", can_forget: true, content_hash: "a".repeat(64) },
    ] } } })); });
  expect(screen.getByText("偏好简洁")).toBeTruthy();
  expect(screen.queryByText(/canonical-42|修订 3/)).toBeNull();
  expect(sent).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "忘记这条记忆" }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent[1].operation).toBe("primary.memory.forget");
  expect(sent[1].request).toMatchObject({ primary_ref: "p", memory_id: "canonical-42", expected_revision: 3, action_id: expect.any(String) });
  expect(screen.queryByText("偏好简洁")).toBeNull();
  expect(screen.queryByRole("button", { name: /撤销/ })).toBeNull();
  ui.rerender(<PrimaryMemoryPanel port={port} primaryRef="p" verifiedOwnerKey={null} ready={false} />);
  expect(screen.getByText("等待当前连接身份确认")).toBeTruthy();
});

it("parent-owned requests survive unmount/disconnect, replay the original action, and notify only a matched ACK", async () => {
  const listeners = new Set<(v: unknown) => void>(), sent: PrimaryWireRequest[] = [];
  const port: PrimaryPort = { state: () => "connected", on_state_change: () => () => {},
    send_command: (r) => { sent.push(r); return true; }, on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; } };
  const emit = (raw: unknown) => listeners.forEach((fn) => fn(raw));
  const reply = (i: number, result: object) => emit({ type: "human_memory_response", request_id: sent[i].request_id,
    payload: { ok: true, operation: sent[i].operation, result } });
  const requests = new CognitiveRequests(), onForgotten = vi.fn();
  const props = { port, primaryRef: "p", verifiedOwnerKey: "owner:1", ready: true, requests, onForgotten };
  const first = render(<PrimaryMemoryPanel {...props} />);
  await act(async () => reply(0, { primary_ref: "p", next_cursor: null, items: [
    { memory_id: "canonical-42", revision: 3, label: "私密偏好", status: "active", can_forget: true, content_hash: "a".repeat(64) },
  ] }));
  fireEvent.click(screen.getByRole("button", { name: "忘记这条记忆" }));
  const original = sent[1];
  await act(async () => emit({ type: "companion_control_rechallenge" }));
  first.unmount();
  expect(onForgotten).not.toHaveBeenCalled();
  const second = render(<PrimaryMemoryPanel {...props} ready={false} verifiedOwnerKey={null} />);
  expect(screen.queryByText("私密偏好")).toBeNull();
  expect(sent).toHaveLength(2);
  second.rerender(<PrimaryMemoryPanel {...props} />);
  await act(async () => reply(2, { primary_ref: "p", next_cursor: null, items: [] }));
  expect(sent.filter((r) => r.operation === "primary.memory.forget")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "重试同一忘记操作" }));
  expect(sent[3].request).toEqual(original.request);
  expect(sent[3].request_id).not.toBe(original.request_id);
  await act(async () => reply(3, { ...original.request, status: "applied", directive_ref: "sdk-directive",
    evidence_ref: "host-action", decision_hash: "b".repeat(64) }));
  expect(onForgotten).toHaveBeenCalledTimes(1); // fresh list still has no reply
  expect(screen.queryByText("私密偏好")).toBeNull();
  await act(async () => reply(4, { primary_ref: "p", next_cursor: null, items: [] }));
  expect(screen.queryByRole("button", { name: "重试同一忘记操作" })).toBeNull();
  second.unmount();
});
