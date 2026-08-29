// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

export const REALTIME_PATH = "/ws/realtime-voice";
export const REALTIME_PROTOCOL_VERSION = "2026-08-27.1";

const PCM_MAGIC = [0x53, 0x48, 0x52, 0x54] as const; // SHRT
const PCM_VERSION = 1;
const PCM_HEADER_BYTES = 24;
const INPUT_DIRECTION = 1;
const OUTPUT_DIRECTION = 2;
const MAX_INPUT_BYTES = 65_536;
const MAX_OUTPUT_BYTES = 262_144;
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

export type RealtimeConnectionState =
  | "disconnected"
  | "connecting"
  | "active"
  | "closing"
  | "error";

export type RealtimeDomainEvent = {
  kind: string;
  [key: string]: unknown;
};

export type RealtimeServerMessage = {
  type: string;
  generation: number;
  state?: string;
  correlation?: string;
  input_audio?: { codec: string; sample_rate: number; channels: number };
  output_audio?: { codec: string; sample_rate: number; channels: number };
  event?: RealtimeDomainEvent;
  code?: string;
  retryable?: boolean;
  reason?: string;
};

type StateListener = (state: RealtimeConnectionState) => void;
type MessageListener = (message: RealtimeServerMessage) => void;
type AudioListener = (pcm: ArrayBuffer) => void;

function newCorrelation(): string {
  const random = new Uint8Array(26);
  crypto.getRandomValues(random);
  return `corr_${Array.from(random, (value) => CROCKFORD[value % CROCKFORD.length]).join("")}`;
}

export function encodeInputPcm(
  generation: number,
  sequence: number,
  pcm: ArrayBuffer,
): ArrayBuffer {
  if (
    generation <= 0 ||
    !Number.isSafeInteger(generation) ||
    sequence <= 0 ||
    !Number.isSafeInteger(sequence) ||
    pcm.byteLength === 0 ||
    pcm.byteLength % 2 !== 0 ||
    pcm.byteLength > MAX_INPUT_BYTES
  ) {
    throw new Error("invalid_input_pcm");
  }
  const result = new ArrayBuffer(PCM_HEADER_BYTES + pcm.byteLength);
  const view = new DataView(result);
  PCM_MAGIC.forEach((value, index) => view.setUint8(index, value));
  view.setUint8(4, PCM_VERSION);
  view.setUint8(5, INPUT_DIRECTION);
  view.setUint16(6, 0, false);
  view.setUint32(8, generation, false);
  view.setBigUint64(12, BigInt(sequence), false);
  view.setUint32(20, pcm.byteLength, false);
  new Uint8Array(result, PCM_HEADER_BYTES).set(new Uint8Array(pcm));
  return result;
}

export function decodeOutputPcm(payload: ArrayBuffer): {
  generation: number;
  sequence: number;
  pcm: ArrayBuffer;
} {
  if (payload.byteLength < PCM_HEADER_BYTES) throw new Error("pcm_header_truncated");
  const view = new DataView(payload);
  if (
    PCM_MAGIC.some((value, index) => view.getUint8(index) !== value) ||
    view.getUint8(4) !== PCM_VERSION ||
    view.getUint8(5) !== OUTPUT_DIRECTION ||
    view.getUint16(6, false) !== 0
  ) {
    throw new Error("pcm_header_invalid");
  }
  const generation = view.getUint32(8, false);
  const rawSequence = view.getBigUint64(12, false);
  const size = view.getUint32(20, false);
  if (
    generation <= 0 ||
    rawSequence === 0n ||
    rawSequence > BigInt(Number.MAX_SAFE_INTEGER) ||
    size === 0 ||
    size % 2 !== 0 ||
    size > MAX_OUTPUT_BYTES ||
    PCM_HEADER_BYTES + size !== payload.byteLength
  ) {
    throw new Error("pcm_payload_invalid");
  }
  return {
    generation,
    sequence: Number(rawSequence),
    pcm: payload.slice(PCM_HEADER_BYTES),
  };
}

export class RealtimeChannel {
  private readonly url: string;
  private readonly secret: string;
  private socket: WebSocket | null = null;
  private generation = 1;
  private inputSequence = 0;
  private outputSequence = 0;
  private correlation = "";
  private state: RealtimeConnectionState = "disconnected";
  private stateListeners = new Set<StateListener>();
  private messageListeners = new Set<MessageListener>();
  private audioListeners = new Set<AudioListener>();

  constructor(port = 8100, secret = "") {
    if (!secret) throw new Error("realtime_secret_missing");
    this.url = `ws://127.0.0.1:${port}${REALTIME_PATH}`;
    this.secret = secret;
  }

  get connectionState(): RealtimeConnectionState {
    return this.state;
  }

  start(instructions: string): void {
    if (!instructions.trim()) throw new Error("realtime_instructions_missing");
    if (this.socket) return;
    this.setState("connecting");
    this.correlation = newCorrelation();
    const socket = new WebSocket(this.url);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.onopen = () => {
      this.sendJson({
        type: "local.auth",
        version: REALTIME_PROTOCOL_VERSION,
        secret: this.secret,
      });
      this.sendJson({
        type: "local.hello",
        version: REALTIME_PROTOCOL_VERSION,
        generation: this.generation,
        correlation: this.correlation,
      });
      this.sendJson({
        type: "call.start",
        generation: this.generation,
        instructions,
        required_features: [
          "server_turn_detection",
          "automatic_response",
          "interruption",
          "input_transcription",
          "audio_output",
        ],
      });
    };
    socket.onmessage = (event) => this.handleMessage(event.data);
    socket.onerror = () => this.fail();
    socket.onclose = () => {
      this.socket = null;
      if (this.state !== "error") this.setState("disconnected");
    };
  }

  sendAudio(pcm: ArrayBuffer): void {
    if (this.state !== "active" || this.socket?.readyState !== WebSocket.OPEN) return;
    this.inputSequence += 1;
    this.socket.send(encodeInputPcm(this.generation, this.inputSequence, pcm));
  }

  bargeIn(): void {
    if (this.state !== "active") return;
    this.sendJson({ type: "call.barge_in", generation: this.generation });
  }

  stop(reason: "client_hangup" | "app_shutdown" = "client_hangup"): void {
    if (!this.socket) return;
    if (this.socket.readyState === WebSocket.OPEN) {
      this.sendJson({ type: "call.stop", generation: this.generation, reason });
      this.setState("closing");
      return;
    }
    this.socket.close(1000, "");
  }

  closeNow(): void {
    this.socket?.close(1000, "");
    this.socket = null;
    this.setState("disconnected");
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  onMessage(listener: MessageListener): () => void {
    this.messageListeners.add(listener);
    return () => this.messageListeners.delete(listener);
  }

  onAudio(listener: AudioListener): () => void {
    this.audioListeners.add(listener);
    return () => this.audioListeners.delete(listener);
  }

  private handleMessage(payload: unknown): void {
    try {
      if (payload instanceof ArrayBuffer) {
        const frame = decodeOutputPcm(payload);
        if (
          frame.generation !== this.generation ||
          frame.sequence !== this.outputSequence + 1
        ) {
          throw new Error("pcm_sequence_invalid");
        }
        this.outputSequence = frame.sequence;
        this.audioListeners.forEach((listener) => listener(frame.pcm));
        this.sendJson({
          type: "call.audio_ack",
          generation: this.generation,
          direction: "output",
          highest_contiguous_sequence: this.outputSequence,
        });
        return;
      }
      if (typeof payload !== "string" || new TextEncoder().encode(payload).length > 1_048_576) {
        throw new Error("realtime_message_invalid");
      }
      const message = JSON.parse(payload) as RealtimeServerMessage;
      if (
        !message ||
        typeof message.type !== "string" ||
        message.generation !== this.generation
      ) {
        throw new Error("realtime_message_invalid");
      }
      if (message.type === "call.ready") {
        if (
          message.correlation !== this.correlation ||
          message.input_audio?.codec !== "pcm_s16le" ||
          message.input_audio.sample_rate !== 16_000 ||
          message.input_audio.channels !== 1 ||
          message.output_audio?.codec !== "pcm_s16le" ||
          message.output_audio.sample_rate !== 24_000 ||
          message.output_audio.channels !== 1
        ) {
          throw new Error("realtime_format_invalid");
        }
      } else if (message.type === "call.state" && message.state === "active") {
        this.setState("active");
      } else if (message.type === "call.error") {
        this.setState("error");
      } else if (message.type === "call.closed") {
        this.socket?.close(1000, "");
      }
      this.messageListeners.forEach((listener) => listener(message));
    } catch {
      this.fail();
    }
  }

  private sendJson(value: object): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(value));
    }
  }

  private fail(): void {
    this.setState("error");
    this.socket?.close(1002, "");
  }

  private setState(next: RealtimeConnectionState): void {
    if (next === this.state) return;
    this.state = next;
    this.stateListeners.forEach((listener) => listener(next));
  }
}
