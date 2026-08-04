// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from "vitest";

import {
  backdropStyle,
  buttonStyle,
  cardStyle,
  inputStyle,
  surfaceModal,
  tabStyle,
} from "./components";
import { tokens } from "./tokens";

describe("dark-first UI theme", () => {
  it("uses dark semantic surfaces for pages, cards and fields", () => {
    expect(tokens.color.surface.panelBg).toBe("#14161f");
    expect(String(surfaceModal.background)).toContain(
      tokens.color.surface.panelBg,
    );
    expect(cardStyle.background).toBe(tokens.color.surface.panelInset);
    expect(inputStyle.background).toBe(tokens.color.surface.panelInset);
    expect(inputStyle.color).toBe(tokens.color.surface.panelText);
  });

  it("keeps secondary controls dark instead of falling back to white", () => {
    expect(buttonStyle("secondary").background).toBe(
      tokens.color.surface.panelInset,
    );
    expect(tabStyle(false).background).toBe(
      tokens.color.surface.panelInset,
    );
  });

  it("uses a dark, blurred modal backdrop", () => {
    expect(backdropStyle.background).toBe("rgba(2, 6, 23, 0.72)");
    expect(backdropStyle.backdropFilter).toBe("blur(6px)");
  });
});
