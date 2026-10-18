// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Workbench 侧栏（T6/T7/T13，WB-3/WB-5/WB-11）。
 *
 * 结构：Logo → 三个主导航项（主对话 / 技能中心 / 产物库，会话项
 * 带 SessionList 展开区）→ 「更多」折叠组（Trace/反馈，
 * T13 自 Toolbar 迁入，点击行为=原浮层打开，能力不减）→ 底部「设置」+
 * 连接状态徽章。
 *
 * 连接徽章（T13，聚合口径见 T8/useControlWsState）：ControlChannel
 * （identity_bind，prop 下传）与 controlWS（companion_action，hook 订阅）
 * 两源取最差态；已连接时按 routeKind 显示 云端/本地/已连接。
 *
 * 样式纪律（WB-11 + 2026-09-09 高级感改版）：颜色一律取自
 * theme/tokens.ts 与 theme/components.ts 的 dark 套件，本文件零硬编码
 * 色值；图标为内联 SVG（components/Icon.tsx），不用 emoji、不引图标库。
 * 当前项高亮 = 左侧 2px 强调条（`.sh-nav-item`）+ 强调色极淡面色，
 * 不做大块填充；所有项统一高度 36。
 */
import React, { useState } from "react";

import { tokens } from "../theme/tokens";
import { dark, INTERACTIVE_CLASS, transition } from "../theme/components";
import type { WorkbenchView } from "./WorkbenchShell";
import { Icon, type IconName } from "./Icon";
import { useMissionsStore } from "../stores/missionsStore";
import {
  useControlWsState,
  worstConnectionState,
  type ControlWsState,
} from "../hooks/useControlWsState";
import "./Sidebar.styles.css";

export const SIDEBAR_WIDTH = 232;

export type RouteKind = "cloud" | "local" | null;

/** T13：Toolbar 面板入口迁移矩阵 —「更多」折叠组回调。 */
export interface SidebarMoreActions {
  onTrace: () => void;
  onFeedback: () => void;
}

interface SidebarProps {
  view: WorkbenchView;
  onViewChange: (view: WorkbenchView) => void;
  /** ControlChannel（identity_bind）连接状态 — 聚合的其一源。 */
  connectionState?: "disconnected" | "connecting" | "connected";
  /** 路由指示（chat_response/transcript 捎带 provider）— cloud/local。 */
  routeKind?: RouteKind;
  /** T13：「更多」折叠组入口（Trace/反馈）。2026-09-10 记忆入口随认知记忆 SDK 移除。 */
  moreActions?: SidebarMoreActions;
}

// ── 连接徽章逻辑（Toolbar getConnColor/getConnLabel 迁入，T13）──────

type ConnColor = "success" | "info" | "warning" | "muted";

function getConnColor(state: ControlWsState, route: RouteKind): ConnColor {
  if (state !== "connected") return "warning";
  if (route === "cloud") return "info";
  if (route === "local") return "success";
  return "muted";
}

function getConnLabel(state: ControlWsState, route: RouteKind): string {
  if (state === "connected") {
    if (route === "cloud") return "云端";
    if (route === "local") return "本地";
    return "已连接";
  }
  if (state === "connecting") return "连接中";
  return "未连接";
}

const CONN_DOT_COLOR: Record<ConnColor, string> = {
  success: tokens.color.success.bg,
  info: tokens.color.info.bg,
  warning: tokens.color.warning.bg,
  muted: dark.textMuted,
};

const NAV_ITEMS: ReadonlyArray<{
  view: WorkbenchView;
  icon: IconName;
  label: string;
}> = [
  { view: "chat", icon: "message", label: "主对话" },
  // plans/2026-09-11-orchestrator-host-integration：Agent 编排（用户 Phase3 P3.1）
  { view: "missions", icon: "compass", label: "任务编排" },
  { view: "skills", icon: "layers", label: "技能中心" },
  { view: "artifacts", icon: "file", label: "产物库" },
];

export const Sidebar: React.FC<SidebarProps> = ({
  view,
  onViewChange,
  connectionState,
  routeKind = null,
  moreActions,
}) => {
  // T13：「更多」折叠组展开态（默认收起，保持侧栏干净）。
  const [moreOpen, setMoreOpen] = useState(false);
  // 任务编排待审批数（角标；编排审批与主对话授权弹窗分开，plan §3.7）。
  const pendingApprovals = useMissionsStore((state) =>
    state.missions.reduce((total, row) => total + (row.pending_approvals || 0), 0),
  );
  // 双源聚合取最差态（T8/T13 口径）：ControlChannel prop + controlWS hook。
  const chatWsState = useControlWsState();
  const aggregated = worstConnectionState(connectionState ?? "disconnected", chatWsState);
  const connColor = getConnColor(aggregated, routeKind);
  return (
    <nav aria-label="工作台导航" data-testid="workbench-sidebar" className="sh-Sidebar-1">
      {/* Logo / 品牌区 */}
      <div data-testid="sidebar-logo" className="sh-Sidebar-2">
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
            badge={item.view === "missions" ? pendingApprovals : undefined}
          />
        </React.Fragment>
      ))}

      <div className="sh-Sidebar-3" />

      {/* T13：「更多」折叠组 — Trace/反馈（原 Toolbar 入口，
          点击行为=原浮层打开，能力不减；icon+tooltip 与原 Toolbar 同构）。 */}
      {moreActions && (
        <div className="sh-Sidebar-4">
          <button
            type="button"
            data-testid="sidebar-more-toggle"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((open) => !open)}
            className={`${INTERACTIVE_CLASS} sh-Sidebar-5`}
          >
            <Icon
              name="chevron-right"
              size={14}
              style={{
                transform: moreOpen ? "rotate(90deg)" : "none",
                transition: `transform ${tokens.duration.fast}ms ${tokens.easing.inOut}`,
              }}
            />
            更多
          </button>
          {moreOpen && (
            <div data-testid="sidebar-more-group" className="sh-Sidebar-6">
              <MoreActionButton
                title="ContextTrace"
                testId="trace-toggle"
                icon="compass"
                onClick={moreActions.onTrace}
              />
              <MoreActionButton
                title="反馈问题"
                testId="feedback-toggle"
                icon="bug"
                onClick={moreActions.onFeedback}
              />
            </div>
          )}
        </div>
      )}

      {/* 底部：设置入口 + 连接状态徽章（双源聚合，T13） */}
      <NavButton
        icon="settings"
        label="设置"
        active={view === "settings"}
        testId="nav-settings"
        onClick={() => onViewChange("settings")}
      />
      <div data-testid="sidebar-conn-badge" className="sh-Sidebar-7">
        <span
          aria-hidden
          style={{
            width: 6,
            height: 6,
            borderRadius: tokens.radius.pill,
            background: CONN_DOT_COLOR[connColor],
            flexShrink: 0,
          }}
        />
        {getConnLabel(aggregated, routeKind)}
      </div>
    </nav>
  );
};

const NavButton: React.FC<{
  icon: IconName;
  label: string;
  active: boolean;
  testId: string;
  onClick: () => void;
  /** 角标（任务编排的待审批数）；0 或缺省不显示。 */
  badge?: number;
}> = ({ icon, label, active, testId, onClick, badge }) => (
  <button
    type="button"
    data-testid={testId}
    aria-current={active ? "page" : undefined}
    onClick={onClick}
    className={`${INTERACTIVE_CLASS} sh-nav-item`}
    style={{
      display: "flex",
      alignItems: "center",
      gap: tokens.space.md,
      width: "100%",
      height: tokens.controlHeight,
      boxSizing: "border-box",
      padding: `0 ${tokens.space.md}px`,
      borderRadius: tokens.radius.md,
      border: "1px solid transparent",
      // 选中态：左侧 2px 强调条（.sh-nav-item::before）+ 极淡面色。
      background: active ? dark.accentSoft : "transparent",
      color: active ? dark.text : dark.textMuted,
      fontFamily: tokens.font.ui,
      fontSize: tokens.text.base.size,
      fontWeight: active ? tokens.weight.semibold : tokens.weight.regular,
      textAlign: "left",
      transition,
      flexShrink: 0,
    }}
  >
    <Icon name={icon} size={16} style={{ color: active ? dark.accent : "currentColor" }} />
    {label}
    {badge ? (
      <span data-testid={`${testId}-badge`} aria-label={`待处理 ${badge}`} className="sh-Sidebar-8">
        {badge}
      </span>
    ) : null}
  </button>
);

const MoreActionButton: React.FC<{
  icon: IconName;
  title: string;
  testId: string;
  onClick: () => void;
}> = ({ icon, title, testId, onClick }) => (
  <button
    type="button"
    data-testid={testId}
    aria-label={title}
    title={title}
    onClick={onClick}
    className={`${INTERACTIVE_CLASS} sh-Sidebar-9`}
  >
    <Icon name={icon} size={15} />
    <span>{title}</span>
  </button>
);

export default Sidebar;
