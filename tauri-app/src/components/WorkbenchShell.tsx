// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * WorkbenchShell（T6，WB-3/WB-11）— 工作台布局壳。
 *
 * flex 行布局：Sidebar 固定 240px + 内容区 flex:1 min-width:0。
 * view state 由 App 层 `useState<WorkbenchView>` 下传（D2：不引路由库，
 * D2 反对的是路由库，不是 state 归属层级）。
 *
 * 视图挂载策略（D3）：chat 视图**常挂载**（切页仅 display 隐藏，
 * MessageStream 的 WS 订阅/滚动位置/运行投影不反复冷启动）；
 * skills/artifacts/settings 无常驻状态，按需挂载（unmount 即可）。
 *
 * ── 视图 props 合同（各视图只在自己的 view
 * 文件内填充实现，不回头改 App.tsx）──
 *   ChatView      ← { activeSid, secret }
 *   SkillsView    ← { channel: permissionChannel }（互跳 state 本地化）
 *   ArtifactsView ← 无 App props（invoke 自足）
 *   SettingsView  ← { getChannel, secret,
 *                     onConfigChanged, autostart }（六项，autostart 供
 *                     自启开关用）
 *
 * banner 插槽：顶部横幅区域（T4 把 petError 以 bannerStyle("error")
 * 形态搬迁进来；relay 供给分支保留）。
 */
import React from "react";

import { Sidebar, type RouteKind, type SidebarMoreActions } from "./Sidebar";
import { PrimaryChatView } from "../views/PrimaryChatView";
import type { ControlChannel } from "../ws/ControlChannel";
import { SkillsView, type SkillsViewProps } from "../views/SkillsView";
import { ArtifactsView } from "../views/ArtifactsView";
import { SettingsView, type SettingsViewProps } from "../views/SettingsView";
import { MissionsView, type MissionsViewProps } from "../views/MissionsView";
import "./WorkbenchShell.styles.css";

export type WorkbenchView = "chat" | "missions" | "skills" | "artifacts" | "settings";

export interface WorkbenchShellProps {
  view: WorkbenchView;
  onViewChange: (view: WorkbenchView) => void;
  /** 顶部横幅插槽（petError 等）——渲染在内容区最顶端、视图之上。 */
  banner?: React.ReactNode;
  /** 侧栏底部连接状态徽章占位输入（T13 迁入聚合逻辑）。 */
  connectionState?: "disconnected" | "connecting" | "connected";
  primaryChannel?: ControlChannel | null;
  skillsProps: SkillsViewProps;
  /** 任务编排视图（plans/2026-09-11-orchestrator-host-integration）：控制通道。 */
  missionsProps?: MissionsViewProps;
  settingsProps: SettingsViewProps;
  /** T13：路由指示（cloud/local）— 侧栏连接徽章文案输入。 */
  routeKind?: RouteKind;
  /** T13：侧栏「更多」折叠组入口（记忆/Trace/反馈/账户）。 */
  moreActions?: SidebarMoreActions;
}

export const WorkbenchShell: React.FC<WorkbenchShellProps> = ({
  view,
  onViewChange,
  banner,
  connectionState,
  primaryChannel,
  skillsProps,
  missionsProps,
  settingsProps,
  routeKind,
  moreActions,
}) => {
  return (
    <div data-testid="workbench-shell" className="sh-WorkbenchShell-1">
      <Sidebar
        view={view}
        onViewChange={onViewChange}
        connectionState={connectionState}
        routeKind={routeKind}
        moreActions={moreActions}
      />

      {/* 内容区 — flex:1 + min-width:0（防止子内容把布局撑破，WB-3
          min 尺寸 800×560 下布局不破的关键约束）。 */}
      <main data-testid="workbench-content" className="sh-WorkbenchShell-2">
        {/* 顶部横幅插槽（T4 落位 petError 横幅）。 */}
        {banner != null && (
          <div data-testid="workbench-banner" className="sh-WorkbenchShell-3">
            {banner}
          </div>
        )}

        {/* D3：chat 常挂载，其余按需挂载。 */}
        <div
          style={{
            flex: 1,
            minHeight: 0,
            minWidth: 0,
            display: view === "chat" ? "flex" : "none",
            flexDirection: "column",
          }}
        >
          <PrimaryChatView
            active={view === "chat"}
            channel={primaryChannel}
            onOpenSettings={() => onViewChange("settings")}
          />
        </div>
        {view === "missions" && <MissionsView {...(missionsProps ?? { channel: null })} />}
        {view === "skills" && <SkillsView {...skillsProps} />}
        {view === "artifacts" && <ArtifactsView />}
        {view === "settings" && <SettingsView {...settingsProps} />}
      </main>
    </div>
  );
};

export default WorkbenchShell;
