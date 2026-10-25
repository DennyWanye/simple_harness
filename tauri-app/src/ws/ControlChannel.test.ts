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

  it("does not open or retry an identity socket before the secret exists", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const channel = new ControlChannel(8100, "");
    const states: string[] = [];
    channel.onStateChange((state) => states.push(state));

    channel.connect();

    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(channel.state).toBe("disconnected");
    expect(states).toEqual(["disconnected"]);
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

    expect(channel.getLatestMessage("companion_control_challenge")).toBeNull();
  });
});

// Same installed bridge/transport: these tests do not replace binding with the
// global companion_identity_status broadcast.
import { SessionClient } from "../primary/sessionClient";

describe("primary bound connection lifecycle", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeWebSocket.instances = [];
  });
  it("rehydrates a mount that missed bound, without adding a socket or signature flow", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const channel = new ControlChannel(8100, "secret");
    channel.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "companion_profile_bound",
      payload: { profile_id: "p", profile_generation: 1 },
    });
    const client = new SessionClient(channel);
    const pending = client.request("history");
    const request = JSON.parse(socket.sent.at(-1)!);
    socket.receive({
      type: "session_history_response",
      payload: { request_id: request.request_id, ok: true, data: [] },
    });
    await pending;
    expect(
      socket.sent.map((s) => JSON.parse(s)).filter((r) => r.type === "session_history"),
    ).toEqual([expect.objectContaining({ type: "session_history" })]);
    expect(FakeWebSocket.instances).toHaveLength(1);
    client.close();
    channel.disconnect();
    await Promise.resolve();
  });
  it("drops stale bound before connected listeners run and never buffers primary enqueue", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const channel = new ControlChannel(8100, "secret");
    channel.connect();
    FakeWebSocket.instances[0].open();
    FakeWebSocket.instances[0].receive({
      type: "companion_profile_bound",
      payload: { profile_id: "p", profile_generation: 1 },
    });
    channel.disconnect();
    expect(channel.getLatestMessage("companion_profile_bound")).toBeNull();
    expect(
      channel.send({
        type: "session_send",
        request_id: "r",
        payload: { text: "x", idempotency_key: "k" },
      }),
    ).toBe(false);
    const seen: unknown[] = [];
    channel.onStateChange((state) => {
      if (state === "connected") seen.push(channel.getLatestMessage("companion_profile_bound"));
    });
    channel.connect();
    FakeWebSocket.instances[1].open();
    expect(seen).toEqual([null]);
    expect(FakeWebSocket.instances[1].sent.some((s) => s.includes("session_send"))).toBe(false);
    channel.disconnect();
  });
  it("an unbind or rechallenge invalidates a cached bound on the same connection", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const channel = new ControlChannel(8100, "secret");
    channel.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "companion_profile_bound",
      payload: { profile_id: "p", profile_generation: 1 },
    });
    socket.receive({ type: "companion_control_rechallenge" });
    expect(channel.getLatestMessage("companion_profile_bound")).toBeNull();
    expect(socket.sent.some((s) => s.includes("human_memory_request"))).toBe(false);
    channel.disconnect();
  });
});

describe("ControlChannel checks orchestration replies against the public contracts (U02)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeWebSocket.instances = [];
  });

  it("hands listeners a protocol error instead of a reply that breaks its contract", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const channel = new ControlChannel(8100, "secret");
    channel.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const seen: unknown[] = [];
    channel.onMessage((message) => seen.push(message));
    socket.receive({
      type: "taskgraph.convergence_response",
      payload: {
        request_id: "r1",
        ok: true,
        data: { schema_version: 2, mission_id: "m1", debug: true },
      },
    });
    expect(seen).toEqual([
      {
        type: "taskgraph.convergence_response",
        payload: {
          request_id: "r1",
          ok: false,
          error_code: "protocol_error",
          error: "收到的数据格式不对，没有显示，请稍后重新读取。",
        },
      },
    ]);
    expect(
      JSON.stringify(channel.getLatestMessage("taskgraph.convergence_response")),
    ).not.toContain("debug");
    channel.disconnect();
  });
});
