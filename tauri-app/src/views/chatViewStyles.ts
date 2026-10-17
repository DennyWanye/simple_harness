// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CSSProperties } from "react";
import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";

// 统一图标按钮 — 深色套件低对比（零硬编码色值，WB-11）。
export const iconBtnStyle: CSSProperties = {
  width: 28,
  height: 28,
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: dark.card,
  color: dark.textMuted,
  border: `1px solid ${dark.border}`,
  borderRadius: tokens.radius.md,
  cursor: "pointer",
  padding: 0,
};

export const modelChipStyle: CSSProperties = {
  flexShrink: 0,
  maxWidth: 170,
  height: 28,
  padding: `0 ${tokens.space.md - 1}px`,
  display: "flex",
  alignItems: "center",
  gap: tokens.space.xs + 1,
  background: dark.card,
  color: dark.textMuted,
  border: `1px solid ${dark.cardBorder}`,
  borderRadius: tokens.radius.pill,
  fontSize: tokens.text.xs.size,
  fontWeight: tokens.weight.semibold,
  fontFamily: tokens.font.ui,
  cursor: "pointer",
};
