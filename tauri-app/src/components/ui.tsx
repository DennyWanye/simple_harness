// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 共享 UI 原语（T13，WB-3/WB-11）— 自 Toolbar 退役迁出。
 *
 * IconButton：26px 级图标按钮（hover 高亮 + 1px 上抬，danger 变体）。
 * ToggleButton：胶囊形开关按钮（原 Toolbar ToggleChip 原语，标准化
 * aria-pressed 语义）。
 *
 * 样式纪律（WB-11）：颜色一律取 theme/tokens + dark 套件，零硬编码色值。
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import { Icon, type IconName } from "./Icon";

export const IconButton: React.FC<{
  icon: IconName;
  onClick: () => void;
  title: string;
  testId?: string;
  active?: boolean;
  danger?: boolean;
}> = ({ icon, onClick, title, testId, active, danger }) => (
  <button
    type="button"
    data-testid={testId}
    onClick={onClick}
    title={title}
    aria-label={title}
    style={{
      width: 27,
      height: 27,
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      padding: 0,
      borderRadius: tokens.radius.md,
      cursor: "pointer",
      background: active ? dark.card : "transparent",
      border: `1px solid ${active ? dark.accent : "transparent"}`,
      color: active ? dark.accent : dark.textMuted,
      transition: `background ${tokens.duration.fast}ms ${tokens.easing.inOut}, color ${tokens.duration.fast}ms ${tokens.easing.inOut}, transform ${tokens.duration.fast}ms ${tokens.easing.inOut}`,
    }}
    onMouseEnter={(e) => {
      const b = e.currentTarget;
      if (!active) {
        b.style.background = danger
          ? tokens.color.danger.soft
          : dark.cardHover;
        b.style.color = danger ? tokens.color.danger.bg : dark.text;
      }
      b.style.transform = "translateY(-1px)";
    }}
    onMouseLeave={(e) => {
      const b = e.currentTarget;
      if (!active) {
        b.style.background = "transparent";
        b.style.color = dark.textMuted;
      }
      b.style.transform = "translateY(0)";
    }}
  >
    <Icon name={icon} size={15} />
  </button>
);

export const ToggleButton: React.FC<{
  active: boolean;
  onClick: () => void;
  label: string;
  title: string;
  testId?: string;
}> = ({ active, onClick, label, title, testId }) => (
  <button
    type="button"
    data-testid={testId}
    onClick={onClick}
    title={title}
    aria-pressed={active}
    style={{
      fontFamily: tokens.font.ui,
      fontSize: tokens.text.xs.size,
      fontWeight: tokens.weight.semibold,
      height: 21,
      padding: `0 ${tokens.space.sm + 1}px`,
      borderRadius: tokens.radius.pill,
      border: `1px solid ${active ? tokens.color.success.border : dark.border}`,
      background: active ? dark.successGrad : dark.card,
      color: active ? tokens.color.success.fg : dark.textMuted,
      cursor: "pointer",
      transition: `background ${tokens.duration.fast}ms ${tokens.easing.inOut}, border-color ${tokens.duration.fast}ms ${tokens.easing.inOut}, color ${tokens.duration.fast}ms ${tokens.easing.inOut}`,
      whiteSpace: "nowrap",
      letterSpacing: 0.3,
    }}
    onMouseEnter={(e) => {
      if (!active) {
        e.currentTarget.style.background = dark.cardHover;
        e.currentTarget.style.color = dark.text;
      }
    }}
    onMouseLeave={(e) => {
      if (!active) {
        e.currentTarget.style.background = dark.card;
        e.currentTarget.style.color = dark.textMuted;
      }
    }}
  >
    {label}
  </button>
);
