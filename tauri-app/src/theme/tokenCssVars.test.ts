// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 尺寸令牌变量：值与令牌一一相等，并且写进了注入的主题样式。
import { describe, expect, it } from "vitest";

import { THEME_CSS, TOKEN_CSS_VARS, tokens } from "./tokens";

describe("尺寸令牌 CSS 变量", () => {
  it("间距、圆角、控件高度、字号、行高、字重、字体与令牌相等", () => {
    for (const [k, n] of Object.entries(tokens.space))
      expect(TOKEN_CSS_VARS[`--sh-space-${k}`]).toBe(`${n}px`);
    for (const [k, n] of Object.entries(tokens.radius))
      expect(TOKEN_CSS_VARS[`--sh-radius-${k}`]).toBe(`${n}px`);
    expect(TOKEN_CSS_VARS["--sh-control-height"]).toBe(`${tokens.controlHeight}px`);
    for (const [k, t] of Object.entries(tokens.text)) {
      expect(TOKEN_CSS_VARS[`--sh-text-${k}-size`]).toBe(`${t.size}px`);
      expect(TOKEN_CSS_VARS[`--sh-text-${k}-lh`]).toBe(String(t.lh));
    }
    for (const [k, n] of Object.entries(tokens.weight))
      expect(TOKEN_CSS_VARS[`--sh-weight-${k}`]).toBe(String(n));
    expect(TOKEN_CSS_VARS["--sh-font-ui"]).toBe(tokens.font.ui);
    expect(TOKEN_CSS_VARS["--sh-font-mono"]).toBe(tokens.font.mono);
  });

  it("每个变量都在注入的主题样式里定义一次", () => {
    for (const [name, value] of Object.entries(TOKEN_CSS_VARS)) {
      expect(THEME_CSS.split(`  ${name}: ${value};`).length - 1, name).toBe(1);
    }
  });
});
