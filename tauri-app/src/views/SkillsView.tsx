// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SkillsView（T10，WB-6）— 技能中心页面宿主。
 *
 * D5 宿主模式：CapabilityCenterPanel / SkillStorePanel 以 variant:"page"
 * 内嵌（去 backdrop/fixed，填满内容区），组件本体不重写。
 * 互跳本地化：onOpenLegacySkillStore / onOpenCapabilityCenter 均只切换
 * 视图内部 state（center | legacy-store），不经 App 层双浮层开关。
 */
import React, { useState } from "react";

import { CapabilityCenterPanel } from "../components/CapabilityCenterPanel";
import { SkillStorePanel } from "../components/SkillStorePanel";
import type { ControlChannel } from "../ws/ControlChannel";

export interface SkillsViewProps {
  /** App 的 permissionChannel（connected 时的 ControlChannel）。 */
  channel: Pick<ControlChannel, "send" | "onMessage"> | null;
  /** 当前 Session；后端据此解析可信 Project scope，不接收前端身份字段。 */
  sessionId?: string | null;
}

export const SkillsView: React.FC<SkillsViewProps> = ({ channel, sessionId }) => {
  // 互跳 state 本地化（T6 合同声明；T10 实装）。
  const [mode, setMode] = useState<"center" | "legacy-store">("center");
  return (
    <section
      data-testid="view-skills"
      aria-label="技能中心"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {mode === "center" ? (
        <CapabilityCenterPanel
          open
          variant="page"
          channel={channel}
          sessionId={sessionId}
          onOpenLegacySkillStore={() => setMode("legacy-store")}
        />
      ) : (
        <SkillStorePanel
          open
          variant="page"
          channel={channel}
          onOpenCapabilityCenter={() => setMode("center")}
        />
      )}
    </section>
  );
};

export default SkillsView;
