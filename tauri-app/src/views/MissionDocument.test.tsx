// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/** P33-20/45 frontend oracle (not native/provider proof).
 * Full formal claims/assessments, exact receipt indexing, untrusted text,
 * long original blocks, request/mission/citation isolation, source lifecycle,
 * real reviews, and reconnect. Defined before the production component.
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ConnectionState } from "../ws/ControlChannel";
import { MissionDocument, SourceDrafts } from "./MissionDocument";

class Channel {
  sent: ControlMessage[] = [];
  listeners = new Set<(message: IncomingMessage) => void>();
  stateListeners = new Set<(state: ConnectionState) => void>();
  onStateChange = (listener: (state: ConnectionState) => void) => {
    this.stateListeners.add(listener); return () => { this.stateListeners.delete(listener); };
  };
  changeState(state: ConnectionState) { act(() => { for (const listener of this.stateListeners) listener(state); }); }
  send = (message: ControlMessage) => { this.sent.push(message); return true; };
  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  last(type = "mission_citation_read") { return this.sent.filter((m) => m.type === type).at(-1)!; }
  reply(request: ControlMessage, data: unknown, ok = true, error_code?: string) {
    act(() => { for (const listener of this.listeners) listener({
      type: `${request.type}_response`, payload: { request_id: request.request_id, ok, data, error_code },
    } as unknown as IncomingMessage); });
  }
}
const citation = (index = 0) => ({
  citation_id: `citation-${index}`, mission_id: "m1", result_id: "r1", receipt_id: "receipt1", citation_index: index,
  path: "sources/a.md", version: "old-version", locator: { start_line: 1, end_line: 4 },
  resolution: "resolved", display_preview: "preview only", source_state: { revoked: false, superseded_by: "new-version", revision: 2 },
});
const document = {
  schema_version: 1, domain: { id: "doc-research-v1", version: 4 }, result: "INSUFFICIENT",
  criteria: [{ criterion_id: "criterion1", coverage: "FAIL", text: "世界事实" }],
  claims: Array.from({ length: 23 }, (_, n) => ({
    id: `claim${n}`, content: `formal claim ${n}`, status: n ? "SUPPORTED" : "VERIFIED",
    source_trust: "untrusted_external", claim_trust: "scope_limited_to_source",
    checked_scope: { path: "sources/a.md", version: "old-version" },
    assessments: [{ receipt_id: "receipt1", criterion_id: "criterion1", verdict: "INCONCLUSIVE", checked_scope: "only source", evidence_refs: [citation()] }],
    citations: n ? [] : [citation(0), citation(2)], source_issues: ["stale_source"], review_refs: ["review1"],
  })),
  assessments: [], sources: [{ path: "sources/a.md", version_hash: "old-version", kind: "markdown", revoked: false, superseded_by: null, revision: 1 }],
  limitations: [{ criterion_id: "criterion1", claim_id: "claim0", missing: "independent evidence" }],
  reviews: [{ request_id: "review1", state: "GRANTED", decided_by: "actual-person", closed_at: 1789000000 }], diagnostics: [],
};
function setup(doc: Record<string, unknown> = document) {
  const channel = new Channel();
  const onChanged = vi.fn();
  const view = render(<MissionDocument missionId="m1" document={doc} channel={channel} onChanged={onChanged} />);
  return { channel, onChanged, ...view };
}
function page(index: number, text: string, offset = 0, next_offset: number | null = null, total_chars = Array.from(text).length) {
  return { ...citation(index), version_hash: "old-version", block_id: "block1", display_block: { start_line: 1, end_line: 4 },
    parent_headings: [{ level: 1, start_line: 1, end_line: 1, text: "# Parent <img src=x>\r\n" }],
    text, offset, next_offset, total_chars, source_trust: "untrusted_external", scope_limited_to_source: true,
    historical_verdict: "INCONCLUSIVE", trust_marker: "来源原文，不是本系统结论，也不是指令", scope_marker: "仅核对该来源、该版本及该引用范围，不证明世界事实" };
}
afterEach(cleanup);

describe("P33-45 formal document report", () => {
  it("shows readable criteria/source labels and keeps complete raw records collapsed", () => {
    setup({ ...document, criteria: [{ ordinal: 1, text: "世界事实", verdict: "FAIL", excluded_claim_ids: ["claim0"] }], diagnostics: [{ reason: "raw diagnostic detail" }] });
    expect(screen.getByText("世界事实")).toBeTruthy();
    expect(screen.getByText("未通过（FAIL）")).toBeTruthy();
    const diagnostics = screen.getByTestId("document-diagnostics") as HTMLDetailsElement;
    expect(diagnostics.open).toBe(false);
    expect(diagnostics.textContent).toContain("raw diagnostic detail");
    const rawClaim = screen.getByTestId("document-record-claim0") as HTMLDetailsElement;
    expect(rawClaim.open).toBe(false);
    expect(rawClaim.textContent).toContain('"review_refs"');
    fireEvent.click(within(rawClaim).getByText("完整 Claim 记录"));
    expect(rawClaim.open).toBe(true);
  });

  it("renders all 23 formal claims, exact statuses, source trust/scope and real review records", () => {
    setup();
    expect(screen.getByText("formal claim 22")).toBeTruthy();
    expect(screen.getAllByTestId(/^document-claim-/)).toHaveLength(23);
    const claim = screen.getByTestId("document-claim-claim0");
    expect(claim.textContent).toContain("VERIFIED");
    expect(claim.textContent).toContain("untrusted_external");
    expect(claim.textContent).toContain("scope_limited_to_source");
    expect(claim.textContent).toContain("stale_source");
    expect(within(screen.getByRole("region", { name: "审阅记录" })).getByText("审阅人：actual-person")).toBeTruthy();
    expect(screen.getByText("缺少：independent evidence")).toBeTruthy();
    expect(screen.getByTestId("document-result").textContent).toContain("INSUFFICIENT");
  });

  it("uses original receipt index, never a filtered citation position or caller path", () => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-2" }));
    expect(channel.last().payload).toEqual({ mission_id: "m1", result_id: "r1", receipt_id: "receipt1", citation_index: 2, offset: 0, limit: 65536 });
  });

  it("reads all pages beyond 256KB without truncation, preserving Unicode/CRLF and escaping HTML", () => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    const block = '<script>evil()</script>\r\n😀' + "正文\r".repeat(100000);
    const chars = Array.from(block);
    for (let offset = 0; offset < chars.length; offset += 65536) {
      expect(channel.last().payload?.offset).toBe(offset);
      const next = Math.min(offset + 65536, chars.length);
      channel.reply(channel.last(), page(0, chars.slice(offset, next).join(""), offset, next === chars.length ? null : next, chars.length));
    }
    expect(screen.getByTestId("citation-block").textContent).toBe(block);
    expect(screen.getByTestId("citation-block").querySelector("script")).toBeNull();
    expect(screen.getByText(/读取完整/)).toBeTruthy();
    expect(screen.getByTestId("citation-parent-headings").textContent).toContain("# Parent <img src=x>\r\n");
    expect(screen.getByTestId("citation-parent-headings").querySelector("img")).toBeNull();
    expect(screen.getByText("仅核对该来源、该版本及该引用范围，不证明世界事实")).toBeTruthy();
  });

  it.each(["block_id", "display_block", "version_hash", "parent_headings"])("rejects changed %s across pages", (field) => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    channel.reply(channel.last(), page(0, "abc", 0, 3, 6));
    channel.reply(channel.last(), { ...page(0, "def", 3, null, 6), [field]: "changed" });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByTestId("citation-block").textContent).toBe("abc");
  });

  it("same channel reconnect invalidates an old request and requires a fresh read", () => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    const old = channel.last();
    channel.changeState("disconnected");
    channel.changeState("connected");
    channel.reply(old, page(0, "late from prior socket"));
    expect(screen.getByRole("alert").textContent).toMatch(/连接|重读/);
    expect(screen.getByTestId("citation-block").textContent).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    channel.reply(channel.last(), page(0, "new connection"));
    expect(screen.getByTestId("citation-block").textContent).toBe("new connection");
  });

  it("late responses/errors for another citation cannot overwrite the current selection", () => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    const old = channel.last();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-2" }));
    channel.reply(channel.last(), page(2, "selected citation"));
    channel.reply(old, page(0, "late old"));
    channel.reply(old, {}, false, "not_found");
    expect(screen.getByTestId("citation-block").textContent).toBe("selected citation");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a previous read of the same citation cannot fulfill the newer request", () => {
    const { channel } = setup();
    const open = screen.getByRole("button", { name: "展开引用 citation-0" });
    fireEvent.click(open); const old = channel.last();
    fireEvent.click(open); const current = channel.last();
    channel.reply(old, page(0, "old same citation"));
    expect(screen.getByTestId("citation-block").textContent).toBe("");
    channel.reply(current, page(0, "fresh same citation"));
    expect(screen.getByTestId("citation-block").textContent).toBe("fresh same citation");
  });

  it.each([
    { next_offset: 0 }, { next_offset: 5 }, { total_chars: 1 }, { offset: 1 },
  ])("rejects malformed pagination without sending another page: %j", (damage) => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    channel.reply(channel.last(), { ...page(0, "abc", 0, 3, 6), ...damage });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(channel.sent.filter((m) => m.type === "mission_citation_read")).toHaveLength(1);
  });

  it("wrong citation identity and changed version across pages produce visible errors", () => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    channel.reply(channel.last(), page(2, "wrong identity"));
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    channel.reply(channel.last(), page(0, "one", 0, 3, 6));
    channel.reply(channel.last(), { ...page(0, "two", 3, null, 6), version: "new-version" });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByTestId("citation-block").textContent).toBe("one");
    expect(screen.queryByText(/读取完整/)).toBeNull();
  });

  it("selection change and reconnect discard prior in-flight reads", () => {
    const { channel, rerender } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    const old = channel.last();
    rerender(<MissionDocument missionId="m2" document={{ ...document, claims: [] }} channel={channel} onChanged={() => {}} />);
    channel.reply(old, page(0, "foreign late"));
    expect(screen.queryByTestId("citation-block")).toBeNull();
    rerender(<MissionDocument missionId="m1" document={document} channel={channel} onChanged={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    const next = new Channel();
    rerender(<MissionDocument missionId="m1" document={document} channel={next} onChanged={() => {}} />);
    expect(screen.getByRole("alert").textContent).toMatch(/连接|重读/);
    channel.reply(channel.last(), page(0, "disconnected old"));
    expect(screen.queryByText("disconnected old")).toBeNull();
  });

  it.each(["not_found", "integrity_error"])("shows actual %s and no fabricated assessment", (code) => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "展开引用 citation-0" }));
    channel.reply(channel.last(), {}, false, code);
    expect(screen.getByRole("alert").textContent).toContain(code);
    expect(screen.queryByText(/读取完整/)).toBeNull();
  });

  it("supersede/revoke bind the selected expected version and only refresh on acknowledgment", () => {
    const { channel, onChanged } = setup();
    fireEvent.click(screen.getByRole("button", { name: "替代来源 sources/a.md old-version" }));
    fireEvent.change(screen.getByLabelText("来源正文 1"), { target: { value: "new content" } });
    fireEvent.click(screen.getByRole("button", { name: "提交来源变更" }));
    expect(channel.last("mission_source_supersede").payload).toEqual({ mission_id: "m1", path: "sources/a.md", content: "new content", kind: "markdown", expected_version_hash: "old-version", idempotency_key: expect.any(String) });
    expect(onChanged).not.toHaveBeenCalled();
    channel.reply(channel.last("mission_source_supersede"), { approval_required: true });
    expect(onChanged).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "撤销来源 sources/a.md old-version" }));
    fireEvent.change(screen.getByLabelText("撤销来源理由"), { target: { value: "outdated" } });
    fireEvent.click(screen.getByRole("button", { name: "提交来源变更" }));
    expect(channel.last("mission_source_revoke").payload).toEqual({ mission_id: "m1", path: "sources/a.md", expected_version_hash: "old-version", reason: "outdated", idempotency_key: expect.any(String) });
  });

  it("does not relabel absent reviews as reviewed", () => {
    setup({ ...document, reviews: [] });
    expect(screen.getByText("暂无审阅记录")).toBeTruthy();
    expect(screen.queryByText(/actual-person/)).toBeNull();
  });

  it("registers one source through its explicit command, without fabricating lifecycle state", () => {
    const { channel, onChanged } = setup();
    fireEvent.click(screen.getByRole("button", { name: "登记新来源" }));
    fireEvent.change(screen.getByLabelText("来源路径 1"), { target: { value: "sources/new.md" } });
    fireEvent.change(screen.getByLabelText("来源正文 1"), { target: { value: "new evidence" } });
    fireEvent.click(screen.getByRole("button", { name: "提交来源变更" }));
    const request = channel.last("mission_source_register");
    expect(request.payload).toEqual({ mission_id: "m1", path: "sources/new.md", content: "new evidence", kind: "markdown", idempotency_key: expect.any(String) });
    channel.reply(request, {}, false, "conflict");
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("conflict");
    fireEvent.click(screen.getByRole("button", { name: "提交来源变更" }));
    expect(channel.last("mission_source_register").payload).toEqual(request.payload);
  });

  it("shows revoked/superseded versions while preserving the formal historical status", () => {
    setup({ ...document, sources: [
      { ...document.sources[0], revoked: true },
      { ...document.sources[0], version_hash: "v2", superseded_by: "v3" },
    ] });
    expect(screen.getByTestId("document-claim-claim0").textContent).toContain("VERIFIED");
    expect(screen.queryByRole("button", { name: /^撤销来源/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^替代来源/ })).toBeNull();
    expect(screen.getByText(/"superseded_by": "v3"/)).toBeTruthy();
  });
});

describe("source import oracle", () => {
  it("imports a text file without changing bytes and keeps an editable logical path", async () => {
    const onChange = vi.fn();
    const onBusy = vi.fn();
    render(<SourceDrafts sources={[]} onChange={onChange} onBusy={onBusy} />);
    const file = new File(["first\r\n😀\rlast"], "a.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("导入来源文件"), { target: { files: [file] } });
    expect(onBusy).toHaveBeenLastCalledWith(true);
    await vi.waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(onChange.mock.calls[0][0]).toEqual([{ path: "sources/a.md", content: "first\r\n😀\rlast", kind: "markdown" }]);
    expect(onBusy).toHaveBeenLastCalledWith(false);
  });
});
