// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Workbench 侧栏（T6，WB-3/WB-11）。
 *
 * 结构：Logo → 三个主导航项（💬会话 / 🧩技能中心 / 📄产物库）→
 * 弹性空隙 → 底部 ⚙️设置 + 连接状态徽章占位。
 *
 * 样式纪律（WB-11）：颜色一律取自 theme/tokens.ts 与
 * theme/components.ts 的 dark 套件，本文件零硬编码色值。
 * 当前项高亮使用 `dark.accent`。
 *
 * 连接状态徽章此处仅为**占位**（T13 会把 Toolbar 的
 * getConnColor/getConnLabel 聚合逻辑迁过来）。
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import type { WorkbenchView } from "./WorkbenchShell";
import { SessionList, type SessionListProps } from "./SessionList";

export const SIDEBAR_WIDTH = 240;

interface SidebarProps {
  view: WorkbenchView;
  onViewChange: (view: WorkbenchView) => void;
  /** 连接状态占位徽章文案（T13 迁入真实聚合逻辑前的简单直通）。 */
  connectionState?: "disconnected" | "connecting" | "connected";
  /** T7（WB-5）：「会话」导航项展开区的会话列表 props。 */
  sessionProps?: SessionListProps;
}

const NAV_ITEMS: ReadonlyArray<{
  view: WorkbenchView;
  icon: string;
  label: string;
}> = [
  { view: "chat", icon: "💬", label: "会话" },
  { view: "skills", icon: "🧩", label: "技能中心" },
  { view: "artifacts", icon: "📄", label: "产物库" },
];

export const Sidebar: React.FC<SidebarProps> = ({
  view,
  onViewChange,
  connectionState,
  sessionProps,
}) => {
  return (
    <nav
      aria-label="工作台导航"
      data-testid="workbench-sidebar"
      style={{
        width: SIDEBAR_WIDTH,
        minWidth: SIDEBAR_WIDTH,
        maxWidth: SIDEBAR_WIDTH,
        boxSizing: "border-box",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: tokens.space.xs,
        padding: tokens.space.sm,
        background: dark.bgSolid,
        borderRight: `1px solid ${dark.border}`,
        color: dark.text,
        fontFamily: tokens.font.ui,
        overflow: "hidden",
      }}
    >
      {/* Logo / 品牌区 */}
      <div
        data-testid="sidebar-logo"
        style={{
          display: "flex",
          alignItems: "center",
          gap: tokens.space.sm,
          padding: `${tokens.space.sm}px ${tokens.space.sm}px ${tokens.space.md}px`,
          fontSize: tokens.text.md.size,
          fontWeight: tokens.weight.bold,
          letterSpacing: 0.3,
          color: dark.text,
          borderBottom: `1px solid ${dark.hairline}`,
          flexShrink: 0,
        }}
      >
        Simple Harness
      </div>

      {/* 主导航 */}
      {NAV_ITEMS.map((item) => (
        <React.Fragment key={item.view}>
          <NavButton
            icon={item.icon}
            label={item.label}
            active={view === item.view}
            testId={`nav-${item.view}`}
            onClick={() => onViewChange(item.view)}
          />
          {/* T7（WB-5）：「会话」项展开区 — chat 视图激活时展开会话列表。 */}
          {item.view === "chat" && view === "chat" && sessionProps && (
            <div
              data-testid="sidebar-session-area"
              style={{
                flex: 1,
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
                paddingLeft: tokens.space.sm,
              }}
            >
              <SessionList {...sessionProps} />
            </div>
          )}
        </React.Fragment>
      ))}

      {/* 弹性空隙 — 会话区未展开时把设置与状态徽章推到底部 */}
      {!(view === "chat" && sessionProps) && (
        <div style={{ flex: 1, minHeight: 0 }} />
      )}

      {/* 底部：设置入口 + 连接状态徽章占位 */}
      <NavButton
        icon="⚙️"
        label="设置"
        active={view === "settings"}
        testId="nav-settings"
        onClick={() => onViewChange("settings")}
      />
      <div
        data-testid="sidebar-conn-badge"
        style={{
          display: "flex",
          alignItems: "center",
          gap: tokens.space.xs,
          padding: `${tokens.space.xs}px ${tokens.space.sm}px`,
          fontSize: tokens.text.xs.size,
          color: dark.textMuted,
          flexShrink: 0,
        }}
      >
        <span
          aria-hidden
          style={{
            width: 6,
            height: 6,
            borderRadius: tokens.radius.pill,
            background:
              connectionState === "connected" ? dark.accent : dark.textFaint,
            flexShrink: 0,
          }}
        />
        {connectionState === "connected"
          ? "已连接"
          : connectionState === "connecting"
            ? "连接中"
            : "未连接"}
      </div>
    </nav>
  );
};

const NavButton: React.FC<{
  icon: string;
  label: string;
  active: boolean;
  testId: string;
  onClick: () => void;
}> = ({ icon, label, active, testId, onClick }) => (
  <button
    type="button"
    data-testid={testId}
    aria-current={active ? "page" : undefined}
    onClick={onClick}
    style={{
      display: "flex",
      alignItems: "center",
      gap: tokens.space.sm,
      width: "100%",
      boxSizing: "border-box",
      padding: `${tokens.space.sm}px ${tokens.space.md}px`,
      borderRadius: tokens.radius.md,
      border: `1px solid ${active ? dark.accent : "transparent"}`,
      background: active ? dark.card : "transparent",
      color: active ? dark.accent : dark.textMuted,
      fontFamily: tokens.font.ui,
      fontSize: tokens.text.base.size,
      fontWeight: active ? tokens.weight.semibold : tokens.weight.medium,
      textAlign: "left",
      cursor: "pointer",
      transition: `background ${tokens.duration.fast}ms ${tokens.easing.inOut}, color ${tokens.duration.fast}ms ${tokens.easing.inOut}`,
      flexShrink: 0,
    }}
    onMouseEnter={(e) => {
      if (!active) {
        e.currentTarget.style.background = dark.cardHover;
        e.currentTarget.style.color = dark.text;
      }
    }}
    onMouseLeave={(e) => {
      if (!active) {
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.color = dark.textMuted;
      }
    }}
  >
    <span aria-hidden style={{ fontSize: tokens.text.md.size, lineHeight: 1 }}>
      {icon}
    </span>
    {label}
  </button>
);

export default Sidebar;
