// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { VOICE_INPUT_ENABLED } from "./voiceAvailability";

describe("voice availability", () => {
  it("enables only the new Realtime product entry", () => {
    expect(VOICE_INPUT_ENABLED).toBe(true);
  });

  it("does not connect voice from App mount", () => {
    const source = readFileSync(resolve("src", "App.tsx"), "utf8");
    expect(source).not.toContain("useAudioChannel(");
    expect(source).not.toContain("useRealtimeVoice(");
  });

  it("keeps one phone-style call control in ChatView", () => {
    const source = readFileSync(resolve("src", "views", "ChatView.tsx"), "utf8");
    expect(source.match(/data-testid="realtime-call-button"/g)).toHaveLength(1);
    expect(source).not.toContain("chat-mic-disabled");
    expect(source).not.toContain("开始说话");
  });
});
