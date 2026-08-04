// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  VOICE_INPUT_ENABLED,
  VOICE_UNAVAILABLE_MESSAGE,
} from "./voiceAvailability";

describe("voice availability", () => {
  it("keeps the legacy path off and explains the Realtime replacement", () => {
    expect(VOICE_INPUT_ENABLED).toBe(false);
    expect(VOICE_UNAVAILABLE_MESSAGE).toContain("Realtime");
  });

  it("keeps App.tsx wired to the shared fail-closed switch", () => {
    // T8（workbench-ui）：App 的底部输入条/mic 按钮退役，音频通道基础
    // 设施保留 —— 通道开关必须仍走 voiceAvailability 单一源。
    const source = readFileSync(resolve("src", "App.tsx"), "utf8");
    expect(source).toContain(
      "useAudioChannel(BACKEND_PORT, secret, VOICE_INPUT_ENABLED)",
    );
  });

  it("keeps the ChatView mic placeholder disabled with the shared message (B9)", () => {
    const source = readFileSync(resolve("src", "views", "ChatView.tsx"), "utf8");
    expect(source).toContain("VOICE_UNAVAILABLE_MESSAGE");
    expect(source).toContain('"mic-off"');
    // 占位按钮必须是无条件禁用（真语音待中转站 Realtime 接入）。
    expect(source).toMatch(/data-testid="chat-mic-disabled"\s+disabled/);
  });
});
