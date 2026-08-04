// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

import {
  VOICE_INPUT_ENABLED,
  VOICE_UNAVAILABLE_MESSAGE,
} from "./voiceAvailability";

describe("voice availability", () => {
  it("keeps the legacy path off and explains the Realtime replacement", () => {
    expect(VOICE_INPUT_ENABLED).toBe(false);
    expect(VOICE_UNAVAILABLE_MESSAGE).toContain("Realtime");
  });

  it.each(["./App.tsx", "./message-panel/MessagePanelRoot.tsx"])(
    "keeps %s wired to the shared fail-closed switch",
    (path) => {
      const source = readFileSync(new URL(path, import.meta.url), "utf8");
      expect(source).toContain(
        "useAudioChannel(BACKEND_PORT, secret, VOICE_INPUT_ENABLED)",
      );
      expect(source).toContain("!VOICE_INPUT_ENABLED");
      expect(source).toContain("VOICE_UNAVAILABLE_MESSAGE");
      expect(source).toContain('"mic-off"');
    },
  );
});
