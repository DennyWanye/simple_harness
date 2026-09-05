import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { PrimaryMemoryPanel } from "./PrimaryMemoryPanel";
import type { PrimaryPort } from "../primary/controller";
import type { PrimaryWireRequest } from "../primary/requests";

afterEach(cleanup);
it("the real memory parent requires explicit grant and page clicks, and hiding retracts audit metadata", async () => {
  const sent: PrimaryWireRequest[] = [], listeners = new Set<(v: unknown) => void>();
  const port: PrimaryPort = {
    state: () => "connected", on_state_change: () => () => {},
    send_command: r => { sent.push(r); return true; },
    on_message: fn => { listeners.add(fn); return () => { listeners.delete(fn); }; },
  };
  const reply = async (message: PrimaryWireRequest, result: object) => act(async () => {
    [...listeners].forEach(fn => fn({ type: "human_memory_response", request_id: message.request_id,
      payload: { ok: true, operation: message.operation, result } }));
  });
  const props = { port, primaryRef: "p", verifiedOwnerKey: "owner", ready: true };
  const ui = render(<PrimaryMemoryPanel {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "操作记录" }));
  expect(sent.filter(r => r.operation.startsWith("primary.audit"))).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "查看我的记忆操作记录（仅元数据）" }));
  const opening = sent.at(-1)!;
  expect(opening.operation).toBe("primary.audit.open");
  const expires = Date.now() / 1000 + 300;
  await reply(opening, { primary_ref: "p", audit_ref: "audit", open_action_id: opening.request.open_action_id,
    expires_at: expires, max_reads: 32, page_limit: 100, purpose: "operation_metadata" });
  expect(sent.filter(r => r.operation === "primary.audit.page")).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "读取记录" }));
  const reading = sent.at(-1)!;
  await reply(reading, { primary_ref: "p", audit_ref: "audit", page_action_id: reading.request.page_action_id,
    snapshot_hash: "a".repeat(64), page_hash: "b".repeat(64), access_event_hash: "c".repeat(64),
    expires_at: expires, max_reads: 32, reads_used: 1, all_operations_recorded: false,
    next_cursor_ref: null, enumeration_complete: true, coverage: [],
    items: [{ family: "suppression", event_kind: "directive", outcome: "committed", occurred_at: 0,
      cognitive_effect: "not_applicable", operation_ref_hash: "d".repeat(64), item_hash: "e".repeat(64) }],
  });
  expect(screen.getByText("忘记与禁用 · 已提交")).toBeTruthy();
  expect((screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "记忆列表" }));
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  expect(sent.at(-1)?.operation).toBe("primary.audit.close");
  ui.rerender(<PrimaryMemoryPanel {...props} primaryRef="other" verifiedOwnerKey="other-owner" />);
  fireEvent.click(screen.getByRole("button", { name: "操作记录" }));
  expect(screen.queryByText("忘记与禁用 · 已提交")).toBeNull();
  expect(screen.getByRole("button", { name: "查看我的记忆操作记录（仅元数据）" })).toBeTruthy();
});
