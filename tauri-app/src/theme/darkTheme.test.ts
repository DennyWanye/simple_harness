// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 令牌契约回归（2026-09-09「克制的高级感」）：
 *  - 深浅两套主题是同一组 CSS 变量的镜像，浅色不能缺席；
 *  - 组件颜色一律走 `var(--sh-*)`，不得写死；
 *  - 只有一个强调色，状态色不当装饰色；
 *  - 圆角/控件高度/动效时长遵守设计口径。
 */
import { describe, expect, it } from "vitest";

import {
  backdropStyle,
  buttonStyle,
  cardStyle,
  dark,
  inputStyle,
  segTab,
  surfaceModal,
  tabStyle,
} from "./components";
import { THEME_CSS, cssVarName, palette, tokens, v } from "./tokens";

const VAR_KEYS = Object.keys(cssVarName) as Array<keyof typeof cssVarName>;

describe("design tokens — 深浅镜像", () => {
  it("每个令牌在深浅两套里都有字面值", () => {
    for (const key of VAR_KEYS) {
      expect(palette.dark[key], `dark.${key}`).toBeTruthy();
      expect(palette.light[key], `light.${key}`).toBeTruthy();
    }
    expect(Object.keys(palette.light)).toEqual(Object.keys(palette.dark));
  });

  it("变量表同时给出浅色基线、系统深色与显式 data-theme 覆盖", () => {
    expect(THEME_CSS).toContain(":root {");
    expect(THEME_CSS).toContain("@media (prefers-color-scheme: dark)");
    expect(THEME_CSS).toContain(':root:not([data-theme="light"])');
    expect(THEME_CSS).toContain(':root[data-theme="dark"]');
    for (const key of VAR_KEYS) {
      const name = cssVarName[key];
      // 浅色一次 + 深色两次
      const hits = THEME_CSS.split(`${name}:`).length - 1;
      expect(hits, name).toBe(3);
    }
  });

  it("令牌以 var() 下发并带深色兜底", () => {
    expect(v("card")).toBe(`var(--sh-card, ${palette.dark.card})`);
    expect(tokens.color.surface.card).toContain("var(--sh-card");
    expect(tokens.color.text.primary).toContain("var(--sh-text");
    expect(tokens.color.accent.bg).toContain("var(--sh-accent");
  });
});

describe("design tokens — 克制的高级感", () => {
  it("表面分三级，正文分三级，组件不写死颜色", () => {
    expect(surfaceModal.background).toBe(tokens.color.surface.panel);
    expect(cardStyle.background).toBe(tokens.color.surface.card);
    expect(inputStyle.background).toBe(tokens.color.surface.card);
    expect(inputStyle.color).toBe(tokens.color.text.primary);
    expect(dark.bgSolid).toBe(tokens.color.surface.base);
    expect(dark.card).toBe(tokens.color.surface.card);
    expect(dark.raised).toBe(tokens.color.surface.raised);
    for (const value of [
      surfaceModal.background,
      cardStyle.background,
      inputStyle.background,
      dark.text,
      dark.accent,
    ]) {
      expect(String(value)).toContain("var(--sh-");
    }
  });

  it("只有一个强调色，用户气泡不再是纯蓝", () => {
    expect(dark.accent).toBe(tokens.color.accent.bg);
    expect(dark.accentGrad).toBe(tokens.color.accent.bg);
    // 雾青，不是纯蓝
    expect(palette.dark.accent).toBe("#7FB2AA");
    expect(palette.light.accent).toBe("#3F7F77");
    // 强调色的淡填充是同一色相的低透明度，不是另一个色相
    expect(palette.dark.accentSoft).toContain("127,178,170");
  });

  it("次级控件与未选中 tab 不做大块填充", () => {
    expect(buttonStyle("secondary").background).toBe("transparent");
    expect(buttonStyle("ghost").background).toBe("transparent");
    expect(tabStyle(false).background).toBe("transparent");
    expect(tabStyle(false).borderBottom).toBe("2px solid transparent");
    expect(tabStyle(true).borderBottom).toBe(`2px solid ${tokens.color.accent.bg}`);
    expect(segTab(true).borderBottom).toBe(`2px solid ${dark.accent}`);
  });

  it("按钮/圆角/动效遵守设计口径", () => {
    expect(tokens.controlHeight).toBe(36);
    expect(buttonStyle("primary", "md").height).toBe(36);
    expect(tokens.radius.md).toBe(8);
    expect(tokens.radius.lg).toBe(12);
    expect(tokens.radius.bubble).toBe(14);
    expect(tokens.duration.fast).toBe(120);
    expect(tokens.duration.base).toBe(160);
  });

  it("遮罩用主题化 scrim，弹层只有一层柔和阴影", () => {
    expect(backdropStyle.background).toBe(tokens.color.surface.darkBackdrop);
    expect(String(backdropStyle.background)).toContain("var(--sh-scrim");
    expect(tokens.shadow.sm).toBe("none");
    expect(String(tokens.shadow.overlay)).toContain("var(--sh-shadow-overlay");
  });

  it("字号阶与字体栈按设计口径收敛", () => {
    expect([
      tokens.text.sm.size,
      tokens.text.base.size,
      tokens.text.md.size,
      tokens.text.lg.size,
      tokens.text.xl.size,
      tokens.text.xxl.size,
    ]).toEqual([12, 13, 14, 16, 20, 24]);
    expect(tokens.font.ui).toContain("-apple-system");
    expect(tokens.font.ui).toContain('"PingFang SC"');
    expect(tokens.font.numeric).toBe("tabular-nums");
  });
});
