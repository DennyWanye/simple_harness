// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SettingsPanel —— Provider、数据目录、Agent 预算、自启与只读状态卡。
 *
 * Provider rows are owned by SettingsProviders and persisted through the
 * backend registry. This component has no account-login or renderer Keychain
 * path; the control channel carries provider operations to the backend.
 *
 * The 今日使用 section reads from `fetchDailyBudget`, which round-trips
 * through the control WS to the BillingLedger (S8). The DailyBudgetStatus
 * contract (snake_case fields) is frozen in types/messages.ts.
 */
import { Icon } from "./Icon";
import { ModelContextCard } from "./ModelContextCard";
import { SettingsProviders } from "./SettingsProviders";
import { dark, titleText, transition } from "../theme/components";
import { tokens } from "../theme/tokens";
import type { ControlChannel } from "../ws/ControlChannel";
import { sectionStyle, h3Style, hintStyle } from "./settingsStyles";
import {
  StorageUsageSection,
  GlobalBudgetSection,
  PublishDirSection,
  SkillCatalogueSection,
} from "./SettingsOrchestrationSections";
import {
  UpdateSection,
  AutoModeToggle,
  ChatTurnTimeoutSetting,
  DangerZoneSection,
  DataDirSection,
} from "./SettingsSystemSections";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
  /** Accessor so the component can both `send` and subscribe without
   * recreating subscriptions every parent render. */
  getChannel: () => ControlChannel | null;
  /** The most recent incoming control message — we narrow to our reply
   * type inside an effect. Piggybacking the existing App-level state
   * avoids an extra onMessage listener that'd need manual teardown. */
  secret: string;
  onConfigChanged?: () => void;
  /** T11 (workbench-ui, D5 宿主模式)：
   * - "overlay"（默认）= 原浮层形态（backdrop + 居中模态）；
   * - "page" = 工作台 SettingsView 页面宿主 —— 去 backdrop/fixed，
   *   填满内容区，无关闭按钮（页面由侧栏导航切走）。 */
  variant?: "overlay" | "page";
  /** T11：自启开关从 Toolbar 移入设置页（B12 保留换位置）。
   * ready=false（如 dev 浏览器无 Tauri runtime）时整节隐藏。 */
  autostart?: {
    ready: boolean;
    enabled: boolean;
    toggle: () => void;
  };
}
export function SettingsPanel({
  open,
  onClose,
  getChannel,
  variant = "overlay",
  autostart,
}: SettingsPanelProps) {
  // 2026-05-26: 删除"今日使用"section — 用户要求，billing 状态不再在
  // Settings 里展示（如需查看请用后端 /budget_status 命令或 metrics）。
  if (!open) return null;

  const isPage = variant === "page";

  const panel = (
    <div style={isPage ? pagePanelStyle : panelStyle} onClick={(e) => e.stopPropagation()}>
      <header style={headerStyle}>
        <span style={{ display: "flex", alignItems: "center", gap: tokens.space.md }}>
          <Icon name="settings" size={18} style={{ color: dark.textMuted }} />
          <h2 style={titleText}>设置</h2>
        </span>
        {!isPage && (
          <button type="button" onClick={onClose} aria-label="关闭设置" style={closeBtnStyle}>
            <Icon name="close" size={16} />
          </button>
        )}
      </header>

      {/* P5-S2 multi-provider-management: legacy single-provider LLM
            configuration section was removed. All provider config now lives
            under the "LLM Providers" section below (drag-drop reorder,
            multiple endpoints, per-card pinning). */}

      {/* T11：「桌宠形象」区块删除（形象清单模块三件套随桌宠退役，
            acceptance only-add 显式删除例外清单）。 */}

      {/* ================ 通用（T11：自启开关自 Toolbar 移入） ================ */}
      {autostart?.ready && (
        <section style={sectionStyle}>
          <h3 style={h3Style}>通用</h3>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12.5,
              color: dark.text,
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              data-testid="autostart-toggle"
              checked={autostart.enabled}
              onChange={() => autostart.toggle()}
            />
            开机自动启动
          </label>
          <p style={hintStyle}>登录系统后自动启动 Simple Harness。</p>
        </section>
      )}

      {/* ================ LLM Providers (P5-S2 Phase 4) ================ */}
      <section style={sectionStyle}>
        <h3 style={h3Style}>LLM Providers</h3>
        <SettingsProviders getChannel={getChannel} />
      </section>

      {/* ================ 模型状态 (P4-S16) ================ */}
      <section style={sectionStyle}>
        <h3 style={h3Style}>模型状态</h3>
        {/* 2026-09-10：BGE-M3 / WeMM 嵌入器状态卡随认知记忆 SDK 一并移除
              ——本构建不再注册任何 embedder。 */}
        {/* Phase 1.1.6（context-1m-rearch）：per-model 上下文窗口卡片 */}
        <ModelContextCard getChannel={getChannel} />
      </section>

      {/* ================ 自动模式 (P4-S21 #13) ================ */}
      <section style={sectionStyle}>
        <h3 style={h3Style}>权限</h3>
        <AutoModeToggle getChannel={getChannel} />
        <ChatTurnTimeoutSetting getChannel={getChannel} />
      </section>

      {/* ================ 数据目录 (2026-05-21) ================ */}
      <DataDirSection />

      {/* ================ 任务与会话数据 (2026-09-25 条目 7：只统计、只提醒，不删除) ================ */}
      <StorageUsageSection getChannel={getChannel} />

      {/* ================ 本月全局预算 (第 2 批 H11 / 车道 P，原计划 §18.2) ================ */}
      <GlobalBudgetSection getChannel={getChannel} />

      {/* ================ 任务发布目录 (NEXT-TG-1.0 §9) ================ */}
      <PublishDirSection getChannel={getChannel} />

      {/* ================ 任务技能目录 (NEXT-TG-1.0 §11) ================ */}
      <SkillCatalogueSection getChannel={getChannel} />

      {/* ================ 关于与更新 (2026-06-05) ================ */}
      <UpdateSection />

      {/* ================ 危险区 (P3-S9) ================ */}
      <DangerZoneSection />

      {/* SettingsProviders directly persists each change; close with the
            header button instead of a misleading global save action. */}
    </div>
  );

  // page variant（D5 宿主模式）：无 backdrop、无 fixed 定位，直接填满
  // 工作台内容区；overlay variant 保持原浮层结构不变。
  if (isPage) return panel;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="设置"
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {panel}
    </div>
  );
}
// ---- inline styles (kept local so the panel has no CSS imports to wire) ----

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: dark.scrim,
  display: "grid",
  placeItems: "center",
  padding: tokens.space.md,
  zIndex: 1000,
  animation: `bp-fade-in ${tokens.duration.base}ms ${tokens.easing.out}`,
};
const panelStyle: React.CSSProperties = {
  background: dark.panel,
  padding: `0 ${tokens.space.xl}px ${tokens.space.xl}px`,
  borderRadius: tokens.radius.lg,
  width: "min(94vw, 560px)",
  boxSizing: "border-box",
  maxHeight: "92vh",
  overflowY: "auto",
  overflowX: "hidden",
  color: dark.text,
  border: `1px solid ${dark.hairline}`,
  boxShadow: tokens.shadow.overlay,
  fontFamily: tokens.font.ui,
  animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
};
// T11 page variant：填满工作台内容区（无模态圆角/边框/阴影/弹入动画），
// 自身滚动；宽度上限交给内容区布局。
const pagePanelStyle: React.CSSProperties = {
  background: dark.bgSolid,
  padding: `0 ${tokens.space.xl}px ${tokens.space.xxl}px`,
  width: "100%",
  height: "100%",
  boxSizing: "border-box",
  overflowY: "auto",
  overflowX: "hidden",
  color: dark.text,
  fontFamily: tokens.font.ui,
};
const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  position: "sticky",
  top: 0,
  zIndex: 2,
  margin: `0 -${tokens.space.xl}px ${tokens.space.lg}px`,
  padding: `${tokens.space.lg}px ${tokens.space.xl}px`,
  background: dark.panel,
  borderBottom: `1px solid ${dark.hairline}`,
};
const closeBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: tokens.controlHeight,
  height: tokens.controlHeight,
  background: "transparent",
  border: `1px solid ${dark.border}`,
  borderRadius: tokens.radius.md,
  cursor: "pointer",
  color: dark.textMuted,
  transition,
};
