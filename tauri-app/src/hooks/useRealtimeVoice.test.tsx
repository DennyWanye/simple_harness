// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fakes = vi.hoisted(() => ({
  primeContext: vi.fn(async () => undefined),
  startRecording: vi.fn(async () => undefined),
  stopRecording: vi.fn(),
  bargeIn: vi.fn(),
  playPcm: vi.fn(),
  completeOutput: vi.fn(),
  channelStart: vi.fn(),
  channelStop: vi.fn(),
  channelBargeIn: vi.fn(),
  stateListener: null as null | ((state: string) => void),
  messageListener: null as null | ((message: unknown) => void),
}));

vi.mock("./useAudioRecorder", () => ({
  useAudioRecorder: () => ({
    isRecording: false,
    startRecording: fakes.startRecording,
    stopRecording: fakes.stopRecording,
  }),
}));

vi.mock("./useAudioPlayer", () => ({
  useAudioPlayer: () => ({
    isPlaying: false,
    stop: fakes.bargeIn,
    bargeIn: fakes.bargeIn,
    reset: vi.fn(),
    primeContext: fakes.primeContext,
    playPcm: fakes.playPcm,
    completeOutput: fakes.completeOutput,
  }),
}));

vi.mock("../ws/RealtimeChannel", () => ({
  RealtimeChannel: class {
    start = fakes.channelStart;
    stop = fakes.channelStop;
    bargeIn = fakes.channelBargeIn;
    closeNow = vi.fn();
    sendAudio = vi.fn();
    onState(listener: (state: string) => void) {
      fakes.stateListener = listener;
      return () => undefined;
    }
    onMessage(listener: (message: unknown) => void) {
      fakes.messageListener = listener;
      return () => undefined;
    }
    onAudio() {
      return () => undefined;
    }
  },
}));

import { useRealtimeVoice } from "./useRealtimeVoice";

describe("useRealtimeVoice", () => {
  beforeEach(() => {
    for (const value of Object.values(fakes)) {
      if (typeof value === "function" && "mockClear" in value) {
        (value as ReturnType<typeof vi.fn>).mockClear();
      }
    }
    fakes.stateListener = null;
    fakes.messageListener = null;
  });

  it("mount does not request microphone, create audio output or connect", () => {
    const { result } = renderHook(() => useRealtimeVoice(8100, "local-secret"));
    expect(result.current.state).toBe("idle");
    expect(fakes.primeContext).not.toHaveBeenCalled();
    expect(fakes.startRecording).not.toHaveBeenCalled();
    expect(fakes.channelStart).not.toHaveBeenCalled();
  });

  it("one start action primes audio, obtains mic and starts the call", async () => {
    const { result } = renderHook(() => useRealtimeVoice(8100, "local-secret"));
    await act(async () => result.current.start());
    expect(fakes.primeContext).toHaveBeenCalledTimes(1);
    expect(fakes.startRecording).toHaveBeenCalledTimes(1);
    expect(fakes.channelStart).toHaveBeenCalledTimes(1);
    act(() => fakes.stateListener?.("active"));
    expect(result.current.state).toBe("listening");
  });

  it("speech during AI output performs local stop and one barge-in", async () => {
    const { result } = renderHook(() => useRealtimeVoice(8100, "local-secret"));
    await act(async () => result.current.start());
    act(() => {
      fakes.messageListener?.({
        type: "call.event",
        generation: 1,
        event: { kind: "OutputAudioStarted" },
      });
    });
    expect(result.current.state).toBe("speaking");
    act(() => {
      fakes.messageListener?.({
        type: "call.event",
        generation: 1,
        event: { kind: "SpeechStarted" },
      });
    });
    expect(fakes.bargeIn).toHaveBeenCalledTimes(1);
    expect(fakes.channelBargeIn).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe("listening");
  });

  it("the same hook exposes hangup without a second speaking control", async () => {
    const { result } = renderHook(() => useRealtimeVoice(8100, "local-secret"));
    await act(async () => result.current.start());
    act(() => result.current.hangUp());
    expect(fakes.stopRecording).toHaveBeenCalledTimes(1);
    expect(fakes.channelStop).toHaveBeenCalledWith("client_hangup");
    expect(result.current.state).toBe("closing");
  });
});
