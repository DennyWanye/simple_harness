// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { IncomingMessage } from "../types/messages";
import { useProtectedPathRequests } from "../hooks/useProtectedPathRequests";
import { ProtectedPathCard } from "./ProtectedPathCard";

class FakeChannel {
  sent: unknown[] = [];
  private listeners = new Set<(m: IncomingMessage) => void>();
  send = (m: unknown) => { this.sent.push(m); return true; };
  onMessage = (l: (m: IncomingMessage) => void) => { this.listeners.add(l); return () => { this.listeners.delete(l); }; };
  emit(m: unknown) { act(() => { for (const l of [...this.listeners]) l(m as IncomingMessage); }); }
}

function Host({ channel }: { channel: FakeChannel }) {
  const { queue, decide } = useProtectedPathRequests(channel as never);
  return <ProtectedPathCard queue={queue} onDecide={decide} />;
}

afterEach(cleanup);
const request = (id: string, path: string) => ({ type: "protected_path_request",
  payload: { request_id: id, session_id: "s", path, op: "read", tool: "read_file", reason: "凭证或密钥" } });

describe("ProtectedPathCard", () => {
  it("没有申请时不显示", () => {
    render(<Host channel={new FakeChannel()} />);
    expect(screen.queryByTestId("protected-path-card")).toBeNull();
  });

  it("显示路径和三个选择；点击后发出决定并显示下一张", () => {
    const channel = new FakeChannel();
    render(<Host channel={channel} />);
    channel.emit(request("r1", "/Users/me/.ssh/config"));
    channel.emit(request("r2", "/Users/me/.aws/credentials"));
    channel.emit(request("r1", "/Users/me/.ssh/config")); // duplicate ignored
    const card = screen.getByTestId("protected-path-card");
    expect(card.textContent).toContain("/Users/me/.ssh/config");
    expect(card.textContent).toContain("还有 1 个");
    fireEvent.click(screen.getByText("本会话都允许"));
    expect(channel.sent).toEqual([{ type: "protected_path_decision", payload: { request_id: "r1", decision: "allow_session" } }]);
    expect(screen.getByTestId("protected-path-card").textContent).toContain(".aws/credentials");
    fireEvent.click(screen.getByText("拒绝"));
    expect(channel.sent.at(-1)).toEqual({ type: "protected_path_decision", payload: { request_id: "r2", decision: "deny" } });
    expect(screen.queryByTestId("protected-path-card")).toBeNull();
  });
});
