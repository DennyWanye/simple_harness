// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { dark, transition } from "../theme/components";
import { tokens } from "../theme/tokens";

export const sectionStyle: React.CSSProperties = {
  borderTop: `1px solid ${dark.hairline}`,
  paddingTop: tokens.space.xl,
  marginTop: tokens.space.xl,
  display: "grid",
  gap: tokens.space.md,
  maxWidth: 760,
};

export const h3Style: React.CSSProperties = {
  margin: 0,
  fontSize: tokens.text.md.size,
  color: dark.text,
  fontWeight: tokens.weight.semibold,
  letterSpacing: tokens.tracking.tight,
};

export const btnRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
};

export const btnStyle: React.CSSProperties = {
  height: tokens.controlHeight,
  padding: `0 ${tokens.space.lg}px`,
  borderRadius: tokens.radius.md,
  border: `1px solid ${dark.borderStrong}`,
  background: "transparent",
  color: dark.text,
  fontFamily: tokens.font.ui,
  fontSize: tokens.text.base.size,
  fontWeight: tokens.weight.medium,
  cursor: "pointer",
  transition,
  boxSizing: "border-box",
};

/** 每屏只有一个主按钮用实心强调色。 */
export const primaryBtnStyle: React.CSSProperties = {
  ...btnStyle,
  border: "1px solid transparent",
  background: dark.accent,
  color: dark.onAccent,
  fontWeight: tokens.weight.semibold,
};

export const statusStyle: React.CSSProperties = {
  fontSize: tokens.text.sm.size,
  padding: `${tokens.space.sm}px ${tokens.space.md}px`,
  background: dark.card,
  border: `1px solid ${dark.insetBorder}`,
  borderRadius: tokens.radius.md,
  lineHeight: tokens.text.sm.lh,
};

export const hintStyle: React.CSSProperties = {
  fontSize: tokens.text.sm.size,
  color: dark.textMuted,
  margin: 0,
  lineHeight: tokens.text.sm.lh,
};
