// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 设计令牌 — 单一真相源（2026-09-09「克制的高级感」改版）。
 *
 * 设计口径（真人验收 2026-09-09：「整体 UI 很难看，需要高级感」）：
 *  - 色彩：石墨/近黑三级表面（底 bg / 面板 surface / 卡片 card），暖调
 *    灰白正文；**唯一**强调色为雾青（misty teal），全局一致；状态色
 *    （success/warning/danger/info）只用于状态，不得当装饰色。
 *  - 主题：深浅两套是同一组令牌的镜像。所有颜色以 CSS 变量下发
 *    （`--sh-*`），TS 侧只持有 `var(--sh-x, <深色兜底>)` 字符串，
 *    因此组件代码零改动即可跟随主题；`palette` 保留字面值，供
 *    cytoscape/canvas 这类无法解析 CSS 变量的场景使用。
 *  - 版式：8pt 网格，字号阶 12/13/14/16/20/24，行高 1.5–1.6，
 *    标题 600 / 正文 400，数字 tabular-nums。
 *  - 形状：卡片 12、按钮 8、气泡 14；1px 半透明 hairline；阴影只在
 *    弹层用一层柔和阴影。
 *  - 动效：120–160ms；`prefers-reduced-motion` 时由 index.css 统一关闭。
 *
 * 使用：
 *   import { tokens } from "../theme/tokens";
 *   style={{ background: tokens.color.surface.card }}
 */

// ── 间距：8pt 网格（xs/sm 为 4 的半格，用于图标与文字的细微间隙）──
export const space = {
  xxs: 2,
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

// ── 圆角：卡片 12 / 按钮 8 / 气泡 14 ──
export const radius = {
  none: 0,
  sm: 4,
  md: 8, // 按钮
  lg: 12, // 卡片、面板
  bubble: 14, // 消息气泡
  xl: 16,
  pill: 999,
} as const;

/** 统一控件高度 —— 侧栏项、按钮、输入行都对齐到 36。 */
export const controlHeight = 36;

// ── 字号阶 12 / 13 / 14 / 16 / 20 / 24，行高 1.5–1.6 ──
export const text = {
  xs: { size: 12, lh: 1.5 },
  sm: { size: 12, lh: 1.5 },
  base: { size: 13, lh: 1.6 },
  md: { size: 14, lh: 1.6 },
  lg: { size: 16, lh: 1.55 },
  xl: { size: 20, lh: 1.4 },
  xxl: { size: 24, lh: 1.3 },
} as const;

export const weight = {
  regular: 400,
  medium: 500,
  semibold: 600,
  bold: 600, // 克制：不再用 700，最重到 600
} as const;

/** 字距 —— 中文正文不加字距，拉丁标题细微收紧。 */
export const tracking = {
  tight: "-0.01em",
  normal: "0",
  wide: "0.02em",
} as const;

// ── 阴影：只保留一层柔和弹层阴影 + 焦点环 ──
export const shadow = {
  none: "none",
  sm: "none",
  md: "none",
  lg: "var(--sh-shadow-overlay, 0 16px 48px rgba(0,0,0,0.44))",
  xl: "var(--sh-shadow-overlay, 0 16px 48px rgba(0,0,0,0.44))",
  overlay: "var(--sh-shadow-overlay, 0 16px 48px rgba(0,0,0,0.44))",
  glow: "0 0 0 3px var(--sh-focus-ring, rgba(127,178,170,0.32))",
} as const;

// ── 动效 120–160ms ──
export const duration = {
  fast: 120,
  base: 160,
  slow: 240,
} as const;

export const easing = {
  inOut: "cubic-bezier(0.4, 0, 0.2, 1)",
  out: "cubic-bezier(0.22, 1, 0.36, 1)",
} as const;

/**
 * 字面值调色板 —— 唯一允许写死颜色的地方。
 * 供两个用途：① 生成 CSS 变量；② cytoscape/canvas 等无法解析
 * `var()` 的渲染上下文（见 primary/graphStyle.ts）。
 */
export const palette = {
  dark: {
    bg: "#0E1013", // 底：窗体
    surface: "#15181C", // 面板：侧栏 / 抽屉 / 顶栏
    card: "#1C2026", // 卡片：消息、列表项
    raised: "#232830", // 卡片 hover / 内嵌高一级
    hairline: "rgba(255,255,255,0.075)",
    hairlineStrong: "rgba(255,255,255,0.14)",
    text: "#E8E5DF", // 暖调灰白
    text2: "#A7A29A",
    text3: "#736F69",
    accent: "#7FB2AA", // 雾青（唯一强调色）
    accentHover: "#93C3BB",
    accentPress: "#6A9C95",
    accentSoft: "rgba(127,178,170,0.13)",
    accentBorder: "rgba(127,178,170,0.34)",
    accentText: "#9FCDC5",
    onAccent: "#0E1013",
    success: "#6FBF8F",
    warning: "#D9A441",
    danger: "#D9645F",
    info: "#6E9FD1",
    scrim: "rgba(6,8,10,0.56)",
    shadowOverlay: "0 16px 48px rgba(0,0,0,0.44)",
    focusRing: "rgba(127,178,170,0.32)",
    selection: "rgba(127,178,170,0.26)",
    scrollThumb: "rgba(255,255,255,0.14)",
    scrollThumbHover: "rgba(255,255,255,0.24)",
  },
  light: {
    bg: "#F4F2EF", // 暖纸底
    surface: "#FAF9F7",
    card: "#FFFFFF",
    raised: "#F0EEEA",
    hairline: "rgba(18,20,24,0.09)",
    hairlineStrong: "rgba(18,20,24,0.17)",
    text: "#1A1C20",
    text2: "#5B5E65",
    text3: "#8A8D94",
    accent: "#3F7F77",
    accentHover: "#4B9089",
    accentPress: "#336862",
    accentSoft: "rgba(63,127,119,0.10)",
    accentBorder: "rgba(63,127,119,0.30)",
    accentText: "#2F6862",
    onAccent: "#FFFFFF",
    success: "#2E8B57",
    warning: "#B07818",
    danger: "#BE4640",
    info: "#3C6FA8",
    scrim: "rgba(28,30,34,0.34)",
    shadowOverlay: "0 16px 40px rgba(20,22,26,0.16)",
    focusRing: "rgba(63,127,119,0.26)",
    selection: "rgba(63,127,119,0.20)",
    scrollThumb: "rgba(18,20,24,0.18)",
    scrollThumbHover: "rgba(18,20,24,0.30)",
  },
} as const;

export type ThemeName = keyof typeof palette;
export type PaletteKey = keyof (typeof palette)["dark"];

/** CSS 变量名映射 —— TS 与 CSS 的唯一桥。 */
export const cssVarName: Record<PaletteKey, string> = {
  bg: "--sh-bg",
  surface: "--sh-surface",
  card: "--sh-card",
  raised: "--sh-raised",
  hairline: "--sh-hairline",
  hairlineStrong: "--sh-hairline-strong",
  text: "--sh-text",
  text2: "--sh-text-2",
  text3: "--sh-text-3",
  accent: "--sh-accent",
  accentHover: "--sh-accent-hover",
  accentPress: "--sh-accent-press",
  accentSoft: "--sh-accent-soft",
  accentBorder: "--sh-accent-border",
  accentText: "--sh-accent-text",
  onAccent: "--sh-on-accent",
  success: "--sh-success",
  warning: "--sh-warning",
  danger: "--sh-danger",
  info: "--sh-info",
  scrim: "--sh-scrim",
  shadowOverlay: "--sh-shadow-overlay",
  focusRing: "--sh-focus-ring",
  selection: "--sh-selection",
  scrollThumb: "--sh-scroll-thumb",
  scrollThumbHover: "--sh-scroll-thumb-hover",
};

/** `var(--sh-x, 深色字面值)` —— 深色是兜底，浅色靠 CSS 覆盖。 */
export function v(key: PaletteKey): string {
  return `var(${cssVarName[key]}, ${palette.dark[key]})`;
}

function block(theme: ThemeName): string {
  return (Object.keys(cssVarName) as PaletteKey[])
    .map((key) => `  ${cssVarName[key]}: ${palette[theme][key]};`)
    .join("\n");
}

/**
 * 主题变量表 —— 由 main.tsx 注入 `<style id="sh-theme">`。
 * 浅色是 `:root` 基线；深色分别在「系统深色」与显式
 * `data-theme="dark"` 两处覆盖，显式 `data-theme="light"` 永远赢。
 */
export const THEME_CSS = `:root {
  color-scheme: light dark;
${block("light")}
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
${block("dark")}
  }
}

:root[data-theme="dark"] {
${block("dark")}
}
`;

/**
 * 颜色令牌 —— 组件只读这里。值都是 `var()`，因此跟随主题。
 */
export const color = {
  // 字面中性色（图表/画布等少数场景）
  neutral: {
    0: "#ffffff",
    50: "#f8fafc",
    100: "#f1f5f9",
    200: "#e2e8f0",
    300: "#cbd5e1",
    400: "#94a3b8",
    500: "#64748b",
    600: "#475569",
    700: "#334155",
    800: "#1e293b",
    900: "#0f172a",
    950: "#020617",
  },
  // 唯一强调色
  accent: {
    bg: v("accent"),
    bgHover: v("accentHover"),
    bgActive: v("accentPress"),
    soft: v("accentSoft"),
    fg: v("accentText"),
    on: v("onAccent"),
    border: v("accentBorder"),
  },
  // 状态色 —— 只用于状态
  success: { bg: v("success"), soft: v("accentSoft"), fg: v("success"), border: v("hairlineStrong") },
  warning: { bg: v("warning"), soft: v("accentSoft"), fg: v("warning"), border: v("hairlineStrong") },
  danger: { bg: v("danger"), soft: v("accentSoft"), fg: v("danger"), border: v("hairlineStrong") },
  info: { bg: v("info"), soft: v("accentSoft"), fg: v("info"), border: v("hairlineStrong") },

  // 表面三级 + 文字三级
  surface: {
    base: v("bg"),
    panel: v("surface"),
    card: v("card"),
    raised: v("raised"),
    hairline: v("hairline"),
    hairlineStrong: v("hairlineStrong"),

    // ↓ 历史键名（大量组件在用），语义映射到新三级表面
    darkOverlay: v("surface"),
    darkOverlayHover: v("raised"),
    darkBorder: v("hairline"),
    darkText: v("text"),
    darkTextMuted: v("text2"),
    darkBackdrop: v("scrim"),
    panelBg: v("surface"),
    panelRaised: v("card"),
    panelInset: v("card"),
    panelBorder: v("hairline"),
    panelText: v("text"),
    panelTextMuted: v("text2"),
  },

  text: {
    primary: v("text"),
    secondary: v("text2"),
    faint: v("text3"),
    onAccent: v("onAccent"),
  },
} as const;

// ── 字体栈：系统原生优先，中文 PingFang，数字 tabular-nums ──
export const font = {
  ui: [
    "-apple-system",
    '"SF Pro Text"',
    '"PingFang SC"',
    "system-ui",
    '"Segoe UI"',
    '"Microsoft YaHei UI"',
    '"Helvetica Neue"',
    "Arial",
    "sans-serif",
    '"Apple Color Emoji"',
    '"Segoe UI Emoji"',
  ].join(", "),
  mono: [
    '"SF Mono"',
    '"JetBrains Mono"',
    '"Cascadia Code"',
    "ui-monospace",
    "Consolas",
    "monospace",
  ].join(", "),
  /** 中文标点与数字对齐：等宽数字。 */
  numeric: "tabular-nums" as const,
} as const;

// 综合输出（习惯写法）
export const tokens = {
  space,
  radius,
  controlHeight,
  text,
  weight,
  tracking,
  shadow,
  duration,
  easing,
  color,
  font,
  palette,
} as const;

export type Tokens = typeof tokens;
