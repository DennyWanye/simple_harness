// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useState, useRef, useCallback, useEffect } from "react";

const TARGET_SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 512; // 32ms at 16kHz — Silero VAD requirement

/**
 * AudioWorklet processor: passes raw native-SR audio to the main thread.
 * Resampling and 16kHz framing happen on the main thread so we can emit
 * exact 512-sample @ 16kHz frames that Silero VAD requires.
 *
 * Inlined as Blob URL to avoid WebView2 file-path issues with
 * audioWorklet.addModule().
 */
const WORKLET_SRC = `
class RawPassthrough extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch && ch.length) {
      // Copy — underlying buffer is recycled next call.
      this.port.postMessage(new Float32Array(ch));
    }
    return true;
  }
}
registerProcessor('raw-passthrough', RawPassthrough);
`;

/**
 * Microphone recording hook.
 * Captures audio via AudioWorklet, resamples to 16kHz, emits 512-sample
 * PCM16 frames (32ms each) matching the Silero VAD frame contract.
 */
export function useAudioRecorder(onFrame: (pcm: ArrayBuffer) => void) {
  const [isRecording, setIsRecording] = useState(false);
  const contextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const recordingRef = useRef(false);

  const releaseResources = useCallback(() => {
    workletRef.current?.disconnect();
    workletRef.current = null;
    sourceRef.current?.disconnect();
    sourceRef.current = null;
    void contextRef.current?.close();
    contextRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recordingRef.current = false;
  }, []);

  const startRecording = useCallback(async () => {
    if (recordingRef.current) return;
    recordingRef.current = true;

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: { ideal: TARGET_SAMPLE_RATE },
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch {
      recordingRef.current = false;
      throw new Error("microphone_unavailable");
    }
    streamRef.current = stream;
    let blobUrl: string | null = null;
    try {
      const nativeSR =
        stream.getAudioTracks()[0].getSettings().sampleRate || 48000;
      const ctx = new AudioContext({ sampleRate: nativeSR });
      contextRef.current = ctx;

      // Load worklet via Blob URL — avoids WebView2 module path issues.
      const blob = new Blob([WORKLET_SRC], { type: "application/javascript" });
      blobUrl = URL.createObjectURL(blob);
      await ctx.audioWorklet.addModule(blobUrl);

      const source = ctx.createMediaStreamSource(stream);
      sourceRef.current = source;
      const worklet = new AudioWorkletNode(ctx, "raw-passthrough");

      const ratio = nativeSR / TARGET_SAMPLE_RATE;
      // Accumulator for 16kHz samples across worklet messages, sliced into
      // exact 512-sample frames before sending to backend.
      let resampleBuffer = new Float32Array(0);
      worklet.port.onmessage = (e: MessageEvent<Float32Array>) => {
        const raw = e.data;

        // Resample native-SR chunk → 16kHz (linear interpolation).
        const outLen = Math.floor(raw.length / ratio);
        const resampled = new Float32Array(outLen);
        for (let i = 0; i < outLen; i++) {
          const srcIdx = i * ratio;
          const idx = Math.floor(srcIdx);
          const frac = srcIdx - idx;
          const a = raw[idx] ?? 0;
          const b = raw[Math.min(idx + 1, raw.length - 1)] ?? 0;
          resampled[i] = a + frac * (b - a);
        }

        const combined = new Float32Array(resampleBuffer.length + resampled.length);
        combined.set(resampleBuffer);
        combined.set(resampled, resampleBuffer.length);

        let off = 0;
        while (off + FRAME_SAMPLES <= combined.length) {
          const pcm16 = new Int16Array(FRAME_SAMPLES);
          for (let j = 0; j < FRAME_SAMPLES; j++) {
            const s = Math.max(-1, Math.min(1, combined[off + j]));
            pcm16[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
          onFrame(pcm16.buffer);
          off += FRAME_SAMPLES;
        }
        resampleBuffer = combined.subarray(off);
      };

      source.connect(worklet);
      workletRef.current = worklet;
      setIsRecording(true);
    } catch {
      releaseResources();
      throw new Error("microphone_unavailable");
    } finally {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    }
  }, [onFrame, releaseResources]);

  const stopRecording = useCallback(() => {
    releaseResources();
    setIsRecording(false);
  }, [releaseResources]);

  useEffect(() => () => releaseResources(), [releaseResources]);

  return { isRecording, startRecording, stopRecording };
}
