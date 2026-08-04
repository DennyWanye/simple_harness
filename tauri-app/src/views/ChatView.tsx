// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * ChatView stub（T6 交付 props 合同；T8 填充实现，不回头改 App.tsx）。
 *
 * T8 实现面（按 plan）：MessageStreamPanel(embedded) + InputBar
 * (sessionId=activeSid) + hydration + Harness 开关 + ContextRing +
 * 模型按钮；连接/错误显示以 controlWS.state() 为源；mic 禁用占位。
 */
import React from "react";

import { tokens } from "../theme/tokens";
import { dark } from "../theme/components";

export interface ChatViewProps {
  activeSid: string;
  secret: string;
}

export const ChatView: React.FC<ChatViewProps> = ({ activeSid, secret }) => {
  // T8 之前 secret 仅由合同占位（InputBar/hydration 接入时消费）。
  void secret;
  return (
    <section
      data-testid="view-chat"
      aria-label="会话"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        color: dark.textMuted,
        fontFamily: tokens.font.ui,
        fontSize: tokens.text.base.size,
      }}
    >
      会话视图（T8 实装）— 当前会话：{activeSid}
    </section>
  );
};

export default ChatView;
