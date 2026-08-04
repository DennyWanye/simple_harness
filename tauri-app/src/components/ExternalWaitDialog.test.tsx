// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useExternalWaitRequests } from "../hooks/useExternalWaitRequests";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";
import { ExternalWaitDialog } from "./ExternalWaitDialog";

class FakeChannel {
  readonly sent: ControlMessage[] = [];
  private listener: ((message: IncomingMessage) => void) | null = null;

  send = (message: ControlMessage) => {
    this.sent.push(message);
    return true;
  };

  onMessage = (listener: (message: IncomingMessage) => void) => {
    this.listener = listener;
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  };

  emit(message: IncomingMessage) {
    this.listener?.(message);
  }
}

function Harness({ channel }: { channel: FakeChannel }) {
  const { current, complete } = useExternalWaitRequests(
    channel as unknown as ControlChannel,
  );
  return <ExternalWaitDialog request={current} onComplete={complete} />;
}

afterEach(cleanup);

describe("ExternalWaitDialog", () => {
  it("returns the complete durable fence and never treats UAC as auto auth", () => {
    const channel = new FakeChannel();
    render(<Harness channel={channel} />);

    act(() => {
      channel.emit({
        type: "external_wait_request",
        payload: {
          session_id: "session-1",
          run_id: "run-1",
          request_id: "decision-1",
          decision_id: "decision-1",
          nonce: "nonce-1",
          version: 0,
          title: "请完成 Windows 系统确认",
          required_action: "在 UAC 窗口中点击允许。",
          wait_kind: "uac",
          wait_ref: "external-wait:1",
          evidence_refs: ["evidence:uac-visible"],
        },
      });
    });

    expect(screen.getByTestId("external-wait-dialog")).toBeTruthy();
    expect(screen.getByText("在 UAC 窗口中点击允许。")).toBeTruthy();
    expect(screen.getByText(/重新探测真实结果/)).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", {
        name: "外部操作已处理，检查结果",
      }),
    );

    expect(channel.sent).toEqual([
      {
        type: "external_wait_response",
        payload: {
          session_id: "session-1",
          run_id: "run-1",
          request_id: "decision-1",
          decision_id: "decision-1",
          nonce: "nonce-1",
          version: 0,
          wait_ref: "external-wait:1",
        },
      },
    ]);
    expect(screen.queryByTestId("external-wait-dialog")).toBeNull();
  });
});
