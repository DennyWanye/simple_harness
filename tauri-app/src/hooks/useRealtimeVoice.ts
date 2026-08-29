// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useRef, useState } from "react";

import { useAudioPlayer } from "./useAudioPlayer";
import { useAudioRecorder } from "./useAudioRecorder";
import {
  RealtimeChannel,
  type RealtimeDomainEvent,
  type RealtimeServerMessage,
} from "../ws/RealtimeChannel";

const REALTIME_INSTRUCTIONS =
  "你是 Simple Harness 的实时语音助手。使用简洁、自然的中文回答，允许用户随时打断。";

export type RealtimeVoiceState =
  | "idle"
  | "connecting"
  | "listening"
  | "speaking"
  | "closing"
  | "error";

export function useRealtimeVoice(port: number, secret: string) {
  const [state, setState] = useState<RealtimeVoiceState>("idle");
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [transcript, setTranscript] = useState("");
  const [responseText, setResponseText] = useState("");
  const stateRef = useRef<RealtimeVoiceState>("idle");
  const channelRef = useRef<RealtimeChannel | null>(null);
  const subscriptionsRef = useRef<Array<() => void>>([]);
  const {
    isPlaying,
    bargeIn,
    primeContext,
    playPcm,
    completeOutput,
  } = useAudioPlayer();

  const updateState = useCallback((next: RealtimeVoiceState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const sendAudio = useCallback((pcm: ArrayBuffer) => {
    channelRef.current?.sendAudio(pcm);
  }, []);
  const { isRecording, startRecording, stopRecording } = useAudioRecorder(sendAudio);

  const clearSubscriptions = useCallback(() => {
    subscriptionsRef.current.forEach((unsubscribe) => unsubscribe());
    subscriptionsRef.current = [];
  }, []);

  const fail = useCallback(
    (code: string) => {
      setErrorCode(code);
      stopRecording();
      bargeIn();
      updateState("error");
    },
    [bargeIn, stopRecording, updateState],
  );

  const handleDomainEvent = useCallback(
    (event: RealtimeDomainEvent) => {
      if (event.kind === "SpeechStarted") {
        if (stateRef.current === "speaking" || isPlaying) {
          bargeIn();
          channelRef.current?.bargeIn();
        }
        updateState("listening");
      } else if (event.kind === "TranscriptCompleted" && typeof event.text === "string") {
        setTranscript(event.text);
      } else if (event.kind === "OutputText" && typeof event.text === "string") {
        const text = event.text;
        setResponseText((current) =>
          event.is_delta === true ? `${current}${text}` : text,
        );
      } else if (event.kind === "OutputAudioStarted") {
        updateState("speaking");
      } else if (event.kind === "OutputAudioCompleted") {
        completeOutput();
      } else if (event.kind === "ResponseFinished") {
        completeOutput();
        if (stateRef.current !== "closing") updateState("listening");
      }
    },
    [bargeIn, completeOutput, isPlaying, updateState],
  );

  const handleMessage = useCallback(
    (message: RealtimeServerMessage) => {
      if (message.type === "call.event" && message.event) {
        handleDomainEvent(message.event);
      } else if (message.type === "call.error") {
        fail(message.code ?? "internal");
      } else if (message.type === "call.closed") {
        stopRecording();
        bargeIn();
        clearSubscriptions();
        channelRef.current = null;
        if (stateRef.current !== "error") updateState("idle");
      }
    },
    [bargeIn, clearSubscriptions, fail, handleDomainEvent, stopRecording, updateState],
  );

  const start = useCallback(async () => {
    if (!secret) {
      fail("backend_unavailable");
      return;
    }
    clearSubscriptions();
    channelRef.current?.closeNow();
    channelRef.current = null;
    setErrorCode(null);
    setTranscript("");
    setResponseText("");
    updateState("connecting");
    try {
      await primeContext();
      await startRecording();
      const channel = new RealtimeChannel(port, secret);
      channelRef.current = channel;
      subscriptionsRef.current = [
        channel.onState((next) => {
          if (next === "active") updateState("listening");
          else if (next === "closing") updateState("closing");
          else if (next === "error" && stateRef.current !== "error") {
            fail("transport_error");
          }
        }),
        channel.onMessage(handleMessage),
        channel.onAudio(playPcm),
      ];
      channel.start(REALTIME_INSTRUCTIONS);
    } catch (error) {
      const code = error instanceof Error ? error.message : "internal";
      fail(code);
      channelRef.current?.closeNow();
      channelRef.current = null;
      clearSubscriptions();
    }
  }, [
    clearSubscriptions,
    fail,
    handleMessage,
    playPcm,
    port,
    primeContext,
    secret,
    startRecording,
    updateState,
  ]);

  const hangUp = useCallback(() => {
    if (!channelRef.current) return;
    stopRecording();
    bargeIn();
    updateState("closing");
    channelRef.current.stop("client_hangup");
  }, [bargeIn, stopRecording, updateState]);

  useEffect(
    () => () => {
      stopRecording();
      bargeIn();
      channelRef.current?.stop("app_shutdown");
      clearSubscriptions();
      channelRef.current = null;
    },
    [bargeIn, clearSubscriptions, stopRecording],
  );

  return {
    state,
    errorCode,
    transcript,
    responseText,
    isRecording,
    isPlaying,
    start,
    hangUp,
  };
}
