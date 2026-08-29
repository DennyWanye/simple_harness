// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  REALTIME_PATH,
  REALTIME_PROTOCOL_VERSION,
  RealtimeChannel,
  decodeOutputPcm,
  encodeInputPcm,
} from "./RealtimeChannel";

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  binaryType = "";
  sent: Array<string | ArrayBuffer> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  send(value: string | ArrayBuffer) {
    this.sent.push(value);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  receive(value: unknown) {
    this.onmessage?.({ data: value });
  }
}

function outputFrame(generation: number, sequence: number, pcm: Uint8Array) {
  const frame = encodeInputPcm(generation, sequence, new Uint8Array(pcm).buffer);
  new DataView(frame).setUint8(5, 2);
  return frame;
}

describe("RealtimeChannel", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("does not connect until the explicit start action", () => {
    const channel = new RealtimeChannel(8100, "local-secret");
    expect(MockWebSocket.instances).toHaveLength(0);
    channel.start("Fixture instructions.");
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toBe(
      `ws://127.0.0.1:8100${REALTIME_PATH}`,
    );
  });

  it("sends auth first, then hello and one call.start", () => {
    const channel = new RealtimeChannel(8100, "local-secret");
    channel.start("Fixture instructions.");
    const socket = MockWebSocket.instances[0];
    socket.open();
    const sent = socket.sent.map((value) => JSON.parse(value as string));
    expect(sent.map((value) => value.type)).toEqual([
      "local.auth",
      "local.hello",
      "call.start",
    ]);
    expect(sent[0]).toEqual({
      type: "local.auth",
      version: REALTIME_PROTOCOL_VERSION,
      secret: "local-secret",
    });
    expect(sent[1].correlation).toMatch(/^corr_[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(sent[2].required_features).toEqual([
      "server_turn_detection",
      "automatic_response",
      "interruption",
      "input_transcription",
      "audio_output",
    ]);
  });

  it("negotiates exact PCM, streams framed audio and ACKs output", () => {
    const channel = new RealtimeChannel(8100, "local-secret");
    const states: string[] = [];
    const received: ArrayBuffer[] = [];
    channel.onState((state) => states.push(state));
    channel.onAudio((pcm) => received.push(pcm));
    channel.start("Fixture instructions.");
    const socket = MockWebSocket.instances[0];
    socket.open();
    const hello = JSON.parse(socket.sent[1] as string);
    socket.receive(
      JSON.stringify({
        type: "call.ready",
        generation: 1,
        correlation: hello.correlation,
        input_audio: { codec: "pcm_s16le", sample_rate: 16_000, channels: 1 },
        output_audio: { codec: "pcm_s16le", sample_rate: 24_000, channels: 1 },
      }),
    );
    socket.receive(
      JSON.stringify({ type: "call.state", generation: 1, state: "active" }),
    );
    channel.sendAudio(new Uint8Array([1, 0, 2, 0]).buffer);
    const input = socket.sent.at(-1) as ArrayBuffer;
    const inputView = new DataView(input);
    expect(Array.from(new Uint8Array(input, 0, 4))).toEqual([0x53, 0x48, 0x52, 0x54]);
    expect(inputView.getUint8(5)).toBe(1);
    expect(inputView.getUint32(8, false)).toBe(1);
    expect(inputView.getBigUint64(12, false)).toBe(1n);

    socket.receive(outputFrame(1, 1, new Uint8Array([3, 0, 4, 0])));
    expect(states).toContain("active");
    expect(Array.from(new Uint8Array(received[0]))).toEqual([3, 0, 4, 0]);
    expect(JSON.parse(socket.sent.at(-1) as string)).toEqual({
      type: "call.audio_ack",
      generation: 1,
      direction: "output",
      highest_contiguous_sequence: 1,
    });
  });

  it("hangup uses the same primary channel and no second start control", () => {
    const channel = new RealtimeChannel(8100, "local-secret");
    channel.start("Fixture instructions.");
    const socket = MockWebSocket.instances[0];
    socket.open();
    channel.stop();
    const sent = socket.sent.map((value) => JSON.parse(value as string));
    expect(sent.filter((value) => value.type === "call.start")).toHaveLength(1);
    expect(sent.at(-1)).toEqual({
      type: "call.stop",
      generation: 1,
      reason: "client_hangup",
    });
  });
});

describe("Realtime PCM framing", () => {
  it("rejects a wrong output direction", () => {
    const input = encodeInputPcm(1, 1, new Uint8Array([0, 0]).buffer);
    expect(() => decodeOutputPcm(input)).toThrow("pcm_header_invalid");
  });
});
