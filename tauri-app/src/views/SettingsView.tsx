// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SettingsView（T11 实装，WB-8）— SettingsPanel variant:"page" 宿主。
 *
 * 六项 props 合同（T6 冻结；App :SettingsPanel 原浮层 props 原样下传 +
 * 第 7 轮补第六项 autostart —— useAutostart 状态下传，自启开关自
 * Toolbar 移入设置页，B12 保留换位置）：
 *   getChannel / lastMessage / secret / relayAdapter / onConfigChanged /
 *   autostart。
 *
 * lastMessage/getChannel/secret 由 App 的 ControlChannel 继续下传
 * （顺风车不断）；页面无关闭动作（侧栏导航切走即离开）。
 */
import React from "react";

import { SettingsPanel } from "../components/SettingsPanel";
import type { ControlChannel } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";
import type { RelayAuthAdapter } from "../auth/RelayAuthAdapter";

export interface SettingsViewProps {
  getChannel: () => ControlChannel | null;
  lastMessage: IncomingMessage | null;
  secret: string;
  relayAdapter: RelayAuthAdapter | null;
  onConfigChanged?: () => void;
  /** useAutostart 状态（App 层持有），自启开关落位设置页。 */
  autostart: {
    ready: boolean;
    enabled: boolean;
    toggle: () => void;
  };
}

export const SettingsView: React.FC<SettingsViewProps> = ({
  getChannel,
  lastMessage,
  secret,
  relayAdapter,
  onConfigChanged,
  autostart,
}) => {
  return (
    <section
      data-testid="view-settings"
      aria-label="设置"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <SettingsPanel
        variant="page"
        open
        // page 模式无关闭按钮；onClose 仅满足合同（浮层遗留 API）。
        onClose={() => undefined}
        getChannel={getChannel}
        lastMessage={lastMessage}
        secret={secret}
        relayAdapter={relayAdapter}
        onConfigChanged={onConfigChanged}
        autostart={autostart}
      />
    </section>
  );
};

export default SettingsView;
