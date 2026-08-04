// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SkillsView stub（T6 交付 props 合同；T10 填充实现，不回头改 App.tsx）。
 *
 * T10 实现面（按 plan）：CapabilityCenterPanel variant:"page" 宿主；
 * onOpenLegacySkillStore 的互跳**本地化**为视图内部 state
 * （center | legacy-store），不再经 App 层双浮层开关。
 */
import React, { useState } from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import type { ControlChannel } from "../ws/ControlChannel";

export interface SkillsViewProps {
  /** App 的 permissionChannel（connected 时的 ControlChannel）。 */
  channel: Pick<ControlChannel, "send" | "onMessage"> | null;
}

export const SkillsView: React.FC<SkillsViewProps> = ({ channel }) => {
  // 互跳 state 本地化（T6 合同即声明；T10 接 CapabilityCenterPanel /
  // SkillStorePanel 的 page variant）。
  const [mode] = useState<"center" | "legacy-store">("center");
  void channel;
  return (
    <section
      data-testid="view-skills"
      aria-label="技能中心"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: dark.textMuted,
        fontFamily: tokens.font.ui,
        fontSize: tokens.text.base.size,
      }}
    >
      技能中心视图（T10 实装）— {mode}
    </section>
  );
};

export default SkillsView;
