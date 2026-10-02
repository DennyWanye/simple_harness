// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/** Reference material panel (2026-10-02, strict citation option A): source versions
 * and their register / supersede / revoke requests; the document citation review
 * panel was removed with the document domain.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ConnectionState } from "../ws/ControlChannel";
import { MissionSources, SourceDrafts } from "./MissionSources";

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
  last(type: string) { return this.sent.filter((m) => m.type === type).at(-1)!; }
  reply(request: ControlMessage, data: unknown, ok = true, error_code?: string) {
    act(() => { for (const listener of this.listeners) listener({
      type: `${request.type}_response`, payload: { request_id: request.request_id, ok, data, error_code },
    } as unknown as IncomingMessage); });
  }
}
const SOURCE = { path: "sources/a.md", version_hash: "old-version-hash", kind: "markdown", revoked: false, superseded_by: null, revision: 1 };
function setup(sources: Record<string, unknown>[] = [SOURCE]) {
  const channel = new Channel();
  const onChanged = vi.fn();
  const view = render(<MissionSources missionId="m1" sources={sources} channel={channel} onChanged={onChanged} />);
  return { channel, onChanged, ...view };
}
afterEach(cleanup);

describe("mission reference material", () => {
  it("supersede/revoke bind the selected expected version and only refresh on acknowledgment", () => {
    const { channel, onChanged } = setup();
    fireEvent.click(screen.getByRole("button", { name: "替换 sources/a.md" }));
    fireEvent.change(screen.getByLabelText("来源正文 1"), { target: { value: "new content" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    expect(channel.last("mission_source_supersede").payload).toEqual({ mission_id: "m1", path: "sources/a.md", content: "new content", kind: "markdown", expected_version_hash: "old-version-hash", idempotency_key: expect.any(String) });
    expect(onChanged).not.toHaveBeenCalled();
    channel.reply(channel.last("mission_source_supersede"), { approval_required: true });
    expect(onChanged).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "撤销 sources/a.md" }));
    fireEvent.change(screen.getByLabelText("撤销理由"), { target: { value: "outdated" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    expect(channel.last("mission_source_revoke").payload).toEqual({ mission_id: "m1", path: "sources/a.md", expected_version_hash: "old-version-hash", reason: "outdated", idempotency_key: expect.any(String) });
  });

  it("registers one source through its explicit command and retries with the same key", () => {
    const { channel, onChanged } = setup();
    fireEvent.click(screen.getByRole("button", { name: "登记新资料" }));
    fireEvent.change(screen.getByLabelText("来源路径 1"), { target: { value: "sources/new.md" } });
    fireEvent.change(screen.getByLabelText("来源正文 1"), { target: { value: "new evidence" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    const request = channel.last("mission_source_register");
    expect(request.payload).toEqual({ mission_id: "m1", path: "sources/new.md", content: "new evidence", kind: "markdown", idempotency_key: expect.any(String) });
    channel.reply(request, {}, false, "conflict");
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("conflict");
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    expect(channel.last("mission_source_register").payload).toEqual(request.payload);
  });

  it("offers no change on revoked or superseded versions", () => {
    setup([{ ...SOURCE, revoked: true }, { ...SOURCE, version_hash: "v2", superseded_by: "v3" }]);
    expect(screen.queryByRole("button", { name: /^撤销 / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^替换 / })).toBeNull();
    expect(screen.getByText(/已撤销/)).toBeTruthy();
    expect(screen.getByText(/已被新版本替代/)).toBeTruthy();
  });

  it("a reconnect turns an in-flight request into a visible unknown outcome", () => {
    const { channel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "登记新资料" }));
    fireEvent.change(screen.getByLabelText("来源路径 1"), { target: { value: "sources/new.md" } });
    fireEvent.change(screen.getByLabelText("来源正文 1"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    channel.changeState("disconnected" as ConnectionState);
    expect(screen.getByRole("alert").textContent).toContain("结果未知");
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
