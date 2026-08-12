// @vitest-environment jsdom

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ChatTurnTimeoutSetting,
  buildChatTurnTimeoutGetMessage,
  buildChatTurnTimeoutSetMessage,
} from "./SettingsPanel";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel, ConnectionState } from "../ws/ControlChannel";

class FakeControlChannel {
  state: ConnectionState = "connected";
  sent: ControlMessage[] = [];
  private listeners = new Set<(message: IncomingMessage) => void>();

  send(message: ControlMessage) {
    this.sent.push(message);
    return true;
  }

  onMessage(listener: (message: IncomingMessage) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStateChange() {
    return () => undefined;
  }

  emit(message: IncomingMessage) {
    for (const listener of this.listeners) listener(message);
  }
}

describe("ChatTurnTimeoutSetting", () => {
  it("does not render the default as persisted and ignores stale replies", async () => {
    const channel = new FakeControlChannel();
    render(
      <ChatTurnTimeoutSetting
        getChannel={() => channel as unknown as ControlChannel}
      />,
    );

    const input = screen.getByTestId("chat-turn-timeout-minutes") as HTMLInputElement;
    expect(input.disabled).toBe(true);
    expect(input.value).toBe("");

    await waitFor(() => expect(channel.sent).toHaveLength(1));
    const requestId = String(channel.sent[0].request_id);
    expect(channel.sent[0]).toEqual(buildChatTurnTimeoutGetMessage(requestId));

    act(() => {
      channel.emit({
        type: "chat_turn_timeout_response",
        request_id: "stale-request",
        payload: { minutes: 60 },
      });
    });
    expect(input.disabled).toBe(true);
    expect(input.value).toBe("");

    act(() => {
      channel.emit({
        type: "chat_turn_timeout_response",
        request_id: requestId,
        payload: { minutes: 16 },
      });
    });
    expect(input.disabled).toBe(false);
    expect(input.value).toBe("16");
    expect(screen.getByTestId("chat-turn-timeout-status").textContent).toBe(
      "已与后端同步",
    );

    fireEvent.change(input, { target: { value: "17" } });
    const setMessage = channel.sent.at(-1)!;
    expect(setMessage).toEqual(
      buildChatTurnTimeoutSetMessage(17, String(setMessage.request_id)),
    );
    expect(screen.getByTestId("chat-turn-timeout-status").textContent).toBe(
      "保存中…",
    );

    act(() => {
      channel.emit({
        type: "chat_turn_timeout_response",
        request_id: setMessage.request_id,
        payload: { minutes: 17 },
      });
    });
    expect(input.value).toBe("17");
    expect(screen.getByTestId("chat-turn-timeout-status").textContent).toBe(
      "已与后端同步",
    );
  });
});
