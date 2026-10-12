// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { VOICE_INPUT_ENABLED } from "./voiceAvailability";

describe("voice availability", () => {
  it("keeps the Realtime product entry temporarily disabled", () => {
    expect(VOICE_INPUT_ENABLED).toBe(false);
  });

  it("does not connect voice from App mount", () => {
    const source = readFileSync(resolve("src", "App.tsx"), "utf8");
    expect(source).not.toContain("useAudioChannel(");
    expect(source).not.toContain("useRealtimeVoice(");
  });

  it("guards the phone-style call control behind product availability", () => {
    const source = readFileSync(resolve("src", "views", "ChatView.tsx"), "utf8");
    expect(source.match(/data-testid="realtime-call-button"/g)).toHaveLength(1);
    // Prettier 会给多行 JSX 加括号、换行（2026-10-12 代码质量整治阶段 1），只认"按钮受开关控制"
    expect(source).toMatch(/VOICE_INPUT_ENABLED\s*&&\s*\(?\s*<button/);
    expect(source).not.toContain("chat-mic-disabled");
    expect(source).not.toContain("开始说话");
  });
});
