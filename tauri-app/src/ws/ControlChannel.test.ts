import { afterEach, describe, expect, it, vi } from "vitest";

import { ControlChannel } from "./ControlChannel";


class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(value: unknown) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }

  send(value: string) {
    this.sent.push(value);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
}


describe("ControlChannel connection-scoped message cache", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeWebSocket.instances = [];
  });

  it("retains an early identity challenge until the React bridge subscribes", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const channel = new ControlChannel(8100, "secret");

    channel.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "companion_control_challenge",
      payload: {
        connection_id: "connection-1",
        control_epoch: "1",
        challenge: "challenge-1",
        request_seq: "1",
        binding_epoch: "1",
      },
    });

    expect(channel.getLatestMessage("companion_control_challenge")).toEqual({
      type: "companion_control_challenge",
      payload: {
        connection_id: "connection-1",
        control_epoch: "1",
        challenge: "challenge-1",
        request_seq: "1",
        binding_epoch: "1",
      },
    });
  });

  it("clears connection credentials before a reconnect can expose them", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const channel = new ControlChannel(8100, "secret");

    channel.connect();
    FakeWebSocket.instances[0].open();
    FakeWebSocket.instances[0].receive({
      type: "companion_control_challenge",
      payload: { challenge: "old" },
    });
    channel.disconnect();
    channel.connect();
    FakeWebSocket.instances[1].open();

    expect(
      channel.getLatestMessage("companion_control_challenge"),
    ).toBeNull();
  });
});
