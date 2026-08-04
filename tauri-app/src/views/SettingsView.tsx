// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * SettingsView stub（T6 交付 props 合同；T11 填充实现）。
 *
 * 六项 props 合同（App :2734 现有 props 原样下传 + 第 7 轮补第六项
 * autostart —— useAutostart 状态下传，供 T11 的自启开关落位）：
 *   getChannel / lastMessage / secret / relayAdapter / onConfigChanged /
 *   autostart。
 *
 * T11 实现面：SettingsPanel variant:"page" 宿主 + 删桌宠形象区块 +
 * 自启开关从 Toolbar 移入。
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";
import type { ControlChannel } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";
import type { RelayAuthAdapter } from "../auth/RelayAuthAdapter";

export interface SettingsViewProps {
  getChannel: () => ControlChannel | null;
  lastMessage: IncomingMessage | null;
  secret: string;
  relayAdapter: RelayAuthAdapter | null;
  onConfigChanged?: () => void;
  /** useAutostart 状态（App 层持有），T11 自启开关落位于设置页。 */
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
  // T11 之前六项 props 仅由合同占位（SettingsPanel page 宿主接入时消费）。
  void getChannel;
  void lastMessage;
  void secret;
  void relayAdapter;
  void onConfigChanged;
  void autostart;
  return (
    <section
      data-testid="view-settings"
      aria-label="设置"
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
      设置视图（T11 实装）
    </section>
  );
};

export default SettingsView;
