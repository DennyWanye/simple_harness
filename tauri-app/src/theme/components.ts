// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 基于设计令牌的可复用样式工厂（2026-09-09「克制的高级感」改版）。
 *
 * 纯 CSSProperties，不含 React 状态；调用方用展开覆盖。
 * 颜色一律来自 theme/tokens.ts 的 `var(--sh-*)` 令牌，因此深浅主题
 * 自动镜像；hover/active/focus-visible 三态由 index.css 的
 * `.sh-*` 类提供（见 `interactive()`）。
 */
import type { CSSProperties } from "react";

import { tokens } from "./tokens";

const {
  color,
  radius,
  shadow,
  space,
  text,
  weight,
  font,
  duration,
  easing,
  controlHeight,
  tracking,
} = tokens;

/** 统一过渡（120–160ms，reduced-motion 由 index.css 关闭）。 */
export const transition = `background-color ${duration.fast}ms ${easing.inOut}, border-color ${duration.fast}ms ${easing.inOut}, color ${duration.fast}ms ${easing.inOut}, opacity ${duration.fast}ms ${easing.inOut}`;

/**
 * 所有可点元素统一带上这个 className：hover/active/focus-visible
 * 三态 + 明确 cursor，实现集中在 index.css。
 */
export const INTERACTIVE_CLASS = "sh-interactive";

// ---------- Button ----------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "icon";
type ButtonSize = "sm" | "md";

export function buttonStyle(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  disabled = false
): CSSProperties {
  const height = size === "sm" ? 28 : controlHeight;
  const padX = size === "sm" ? 10 : 14;
  const fontSize = size === "sm" ? text.sm.size : text.base.size;

  const base: CSSProperties = {
    fontFamily: font.ui,
    fontSize,
    fontWeight: weight.medium,
    fontVariantNumeric: font.numeric,
    lineHeight: 1,
    height: variant === "icon" ? controlHeight : height,
    padding: variant === "icon" ? 0 : `0 ${padX}px`,
    border: "1px solid transparent",
    borderRadius: radius.md,
    cursor: disabled ? "not-allowed" : "pointer",
    transition,
    userSelect: "none",
    opacity: disabled ? 0.45 : 1,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs + 2,
    whiteSpace: "nowrap",
    boxSizing: "border-box",
  };

  switch (variant) {
    case "primary":
      return {
        ...base,
        background: color.accent.bg,
        color: color.accent.on,
        fontWeight: weight.semibold,
        borderColor: "transparent",
      };
    case "danger":
      return {
        ...base,
        background: "transparent",
        color: color.danger.bg,
        borderColor: color.surface.hairlineStrong,
      };
    case "secondary":
      return {
        ...base,
        background: "transparent",
        color: color.text.primary,
        borderColor: color.surface.hairlineStrong,
      };
    case "ghost":
      return {
        ...base,
        background: "transparent",
        color: color.text.secondary,
      };
    case "icon":
      return {
        ...base,
        width: controlHeight,
        background: "transparent",
        color: color.text.secondary,
        borderColor: color.surface.hairline,
      };
  }
}

/** 细文字链接（「查看更早消息」这类次要动作）。 */
export function textLinkStyle(disabled = false): CSSProperties {
  return {
    fontFamily: font.ui,
    fontSize: text.sm.size,
    fontWeight: weight.medium,
    color: disabled ? color.text.faint : color.text.secondary,
    background: "transparent",
    border: 0,
    padding: `${space.xs}px ${space.sm}px`,
    borderRadius: radius.sm,
    cursor: disabled ? "not-allowed" : "pointer",
    transition,
  };
}

// ---------- Surface (panel / card) ----------

export const surfaceModal: CSSProperties = {
  background: color.surface.panel,
  color: color.text.primary,
  border: `1px solid ${color.surface.hairline}`,
  borderRadius: radius.lg,
  boxShadow: shadow.overlay,
  fontFamily: font.ui,
};

export const cardStyle: CSSProperties = {
  background: color.surface.card,
  border: `1px solid ${color.surface.hairline}`,
  borderRadius: radius.lg,
  padding: space.md,
  transition,
};

// ---------- Tab ----------

/**
 * 下划线 tab —— 选中态只用 2px 强调下划线 + 正文色，不做大块填充。
 * 保留原函数名与签名，所有旧调用点自动获得新外观。
 */
export function tabStyle(active: boolean): CSSProperties {
  return {
    fontFamily: font.ui,
    fontSize: text.base.size,
    fontWeight: active ? weight.semibold : weight.medium,
    height: controlHeight,
    padding: `0 ${space.xs}px`,
    margin: `0 ${space.sm}px 0 0`,
    border: 0,
    borderBottom: `2px solid ${active ? color.accent.bg : "transparent"}`,
    background: "transparent",
    color: active ? color.text.primary : color.text.secondary,
    borderRadius: 0,
    cursor: "pointer",
    transition,
    boxSizing: "border-box",
  };
}

// ---------- Banner / inline alert ----------

type BannerLevel = "info" | "warning" | "error" | "success";

const LEVEL_COLOR: Record<BannerLevel, string> = {
  info: color.info.bg,
  warning: color.warning.bg,
  error: color.danger.bg,
  success: color.success.bg,
};

export function bannerStyle(level: BannerLevel): CSSProperties {
  return {
    fontFamily: font.ui,
    fontSize: text.base.size,
    lineHeight: text.base.lh,
    padding: `${space.sm}px ${space.md}px`,
    background: color.surface.card,
    color: LEVEL_COLOR[level],
    border: `1px solid ${color.surface.hairline}`,
    borderLeft: `2px solid ${LEVEL_COLOR[level]}`,
    borderRadius: radius.md,
  };
}

// ---------- Badge / chip ----------

export function badgeStyle(level: BannerLevel = "info"): CSSProperties {
  return {
    fontFamily: font.ui,
    fontSize: text.xs.size,
    fontWeight: weight.medium,
    fontVariantNumeric: font.numeric,
    padding: "2px 8px",
    background: "transparent",
    color: LEVEL_COLOR[level],
    border: `1px solid ${color.surface.hairlineStrong}`,
    borderRadius: radius.pill,
    display: "inline-flex",
    alignItems: "center",
    lineHeight: 1.5,
  };
}

// ---------- Input ----------

export const inputStyle: CSSProperties = {
  fontFamily: font.ui,
  fontSize: text.base.size,
  width: "100%",
  height: controlHeight,
  padding: `0 ${space.md}px`,
  border: `1px solid ${color.surface.hairlineStrong}`,
  borderRadius: radius.md,
  outline: "none",
  background: color.surface.card,
  color: color.text.primary,
  transition,
  boxSizing: "border-box",
};

// ---------- Modal backdrop ----------

export const backdropStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  background: color.surface.darkBackdrop,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9000,
  animation: `bp-fade-in ${duration.base}ms ${easing.out}`,
};

export const tokensExport = tokens;

// ============================================================
// 深色（主题感知）面板套件 —— 记忆抽屉 / 设置 / 技能中心 / 反馈…
// 键名沿用历史命名以免大范围改写；值已全部换成主题令牌。
// ============================================================

/** 面板调色板 —— 各面板内联取色的统一入口。 */
export const dark = {
  // 表面三级
  bg: color.surface.panel,
  bgSolid: color.surface.base,
  panel: color.surface.panel,
  // 内嵌区块（列表/滚动容器）
  inset: color.surface.card,
  insetBorder: color.surface.hairline,
  // 卡片
  card: color.surface.card,
  cardHover: color.surface.raised,
  cardBorder: color.surface.hairline,
  raised: color.surface.raised,
  // 描边
  border: color.surface.hairline,
  borderStrong: color.surface.hairlineStrong,
  hairline: color.surface.hairline,
  // 文字
  text: color.text.primary,
  textMuted: color.text.secondary,
  textFaint: color.text.faint,
  // 唯一强调色 + 状态色
  accent: color.accent.bg,
  accentText: color.accent.fg,
  accentSoft: color.accent.soft,
  accentBorder: color.accent.border,
  onAccent: color.accent.on,
  success: color.success.bg,
  warning: color.warning.bg,
  danger: color.danger.bg,
  info: color.info.bg,
  scrim: color.surface.darkBackdrop,
  // 历史渐变键 —— 克制风格下改为纯色，保持调用点不破
  accentGrad: color.accent.bg,
  successGrad: color.success.bg,
  dangerGrad: color.danger.bg,
} as const;

/** 面板根容器 —— 覆盖式面板。 */
export const darkPanelSurface: CSSProperties = {
  position: "absolute",
  inset: 0,
  zIndex: 1000,
  display: "flex",
  flexDirection: "column",
  background: dark.panel,
  color: dark.text,
  fontFamily: font.ui,
  fontSize: text.base.size,
  lineHeight: text.base.lh,
  animation: `bp-fade-in ${duration.base}ms ${easing.out}`,
};

/** 面板顶栏 —— 标题 + 关闭，底部一条 hairline。 */
export const darkPanelHeader: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: space.md,
  padding: `${space.md}px ${space.lg}px`,
  borderBottom: `1px solid ${dark.hairline}`,
  flexShrink: 0,
};

/** 关闭按钮（幽灵图标按钮）。 */
export const darkCloseBtn: CSSProperties = {
  width: controlHeight,
  height: controlHeight,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  borderRadius: radius.md,
  background: "transparent",
  border: `1px solid ${dark.border}`,
  color: dark.textMuted,
  cursor: "pointer",
  transition,
};

/** 下划线 tab 组容器。 */
export const segGroup: CSSProperties = {
  display: "inline-flex",
  gap: space.lg,
  padding: 0,
  background: "transparent",
  borderBottom: `1px solid ${dark.hairline}`,
};

/** 下划线 tab 单项 —— 选中态 2px 强调下划线。 */
export function segTab(active: boolean): CSSProperties {
  return {
    fontFamily: font.ui,
    fontSize: text.base.size,
    fontWeight: active ? weight.semibold : weight.medium,
    height: controlHeight,
    padding: `0 ${space.xxs}px`,
    border: 0,
    borderBottom: `2px solid ${active ? dark.accent : "transparent"}`,
    borderRadius: 0,
    background: "transparent",
    color: active ? dark.text : dark.textMuted,
    cursor: "pointer",
    whiteSpace: "nowrap",
    transition,
    boxSizing: "border-box",
  };
}

type DarkBtnVariant = "primary" | "success" | "danger" | "neutral" | "ghost";

/** 面板内的动作按钮 —— 只有一个主按钮用实心强调色，其余幽灵。 */
export function darkButton(
  variant: DarkBtnVariant = "neutral",
  size: "sm" | "md" = "sm"
): CSSProperties {
  const height = size === "sm" ? 28 : controlHeight;
  const padX = size === "sm" ? 10 : 14;
  const fs = size === "sm" ? text.sm.size : text.base.size;
  const base: CSSProperties = {
    fontFamily: font.ui,
    fontSize: fs,
    fontWeight: weight.medium,
    fontVariantNumeric: font.numeric,
    height,
    padding: `0 ${padX}px`,
    borderRadius: radius.md,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: space.xs + 1,
    whiteSpace: "nowrap",
    border: "1px solid transparent",
    transition,
    boxSizing: "border-box",
  };
  switch (variant) {
    case "primary":
      return { ...base, background: dark.accent, color: dark.onAccent, fontWeight: weight.semibold };
    case "success":
      return { ...base, background: "transparent", color: dark.success, borderColor: dark.borderStrong };
    case "danger":
      return { ...base, background: "transparent", color: dark.danger, borderColor: dark.borderStrong };
    case "ghost":
      return { ...base, background: "transparent", color: dark.textMuted, borderColor: "transparent" };
    case "neutral":
    default:
      return { ...base, background: "transparent", color: dark.text, borderColor: dark.borderStrong };
  }
}

/** 内嵌列表/滚动区容器。 */
export const darkListSurface: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  background: "transparent",
  border: `1px solid ${dark.insetBorder}`,
  borderRadius: radius.lg,
  padding: space.sm,
};

/** 面板内文本输入。 */
export const darkInput: CSSProperties = {
  fontFamily: font.ui,
  fontSize: text.base.size,
  background: dark.card,
  color: dark.text,
  border: `1px solid ${dark.borderStrong}`,
  borderRadius: radius.md,
  height: controlHeight,
  padding: `0 ${space.md}px`,
  outline: "none",
  transition,
  boxSizing: "border-box",
};

// ============================================================
// 新增：本轮改版引入的版式 / 空态 / 抽屉助手
// ============================================================

/** 单行低调 meta 文本（状态栏、脚注）。 */
export const metaText: CSSProperties = {
  fontFamily: font.ui,
  fontSize: text.sm.size,
  lineHeight: text.sm.lh,
  fontVariantNumeric: font.numeric,
  color: dark.textMuted,
  letterSpacing: tracking.normal,
};

/** 视图标题（20/600，字距细微收紧）。 */
export const titleText: CSSProperties = {
  fontFamily: font.ui,
  fontSize: text.xl.size,
  lineHeight: text.xl.lh,
  fontWeight: weight.semibold,
  letterSpacing: tracking.tight,
  color: dark.text,
  margin: 0,
};

/** 小节标题（14/600）。 */
export const sectionTitleText: CSSProperties = {
  fontFamily: font.ui,
  fontSize: text.md.size,
  lineHeight: text.md.lh,
  fontWeight: weight.semibold,
  letterSpacing: tracking.tight,
  color: dark.text,
  margin: 0,
};

/** 视图顶栏 —— 幽灵按钮排右，底部 hairline。 */
export const viewHeader: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: space.sm,
  padding: `${space.lg}px ${space.xl}px`,
  borderBottom: `1px solid ${dark.hairline}`,
  flexShrink: 0,
  background: dark.panel,
};

/** 空态容器 —— 细线图标 + 一句提示。 */
export const emptyState: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: space.md,
  padding: `${space.xxxl}px ${space.xl}px`,
  color: dark.textMuted,
  textAlign: "center",
  fontSize: text.base.size,
  lineHeight: text.base.lh,
};

/** 抽屉遮罩（160ms 淡入）。 */
export const drawerScrim: CSSProperties = {
  position: "absolute",
  inset: 0,
  background: dark.scrim,
  zIndex: 40,
  animation: `bp-fade-in ${duration.base}ms ${easing.out}`,
};

/** 右侧抽屉容器（宽 420）。 */
export const DRAWER_WIDTH = 420;

export const drawerSurface: CSSProperties = {
  position: "absolute",
  top: 0,
  right: 0,
  bottom: 0,
  width: DRAWER_WIDTH,
  maxWidth: "100%",
  zIndex: 41,
  display: "flex",
  flexDirection: "column",
  background: dark.panel,
  borderLeft: `1px solid ${dark.hairline}`,
  boxShadow: shadow.overlay,
  color: dark.text,
  fontFamily: font.ui,
  fontSize: text.base.size,
  lineHeight: text.base.lh,
  animation: `sh-drawer-in ${duration.base}ms ${easing.out}`,
};

/** 主对话消息区最大宽度（760，居中）。 */
export const MESSAGE_MAX_WIDTH = 760;
