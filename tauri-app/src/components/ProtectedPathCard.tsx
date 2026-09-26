// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/** 受保护核心文件的申请卡片：右下角浮动，不挡住界面；一次只显示最早的一张。 */
import React from "react";
import type { ProtectedPathDecision, ProtectedPathRequest } from "../hooks/useProtectedPathRequests";
import { buttonStyle, dark, surfaceModal } from "../theme/components";
import { tokens } from "../theme/tokens";

interface Props {
  queue: ProtectedPathRequest[];
  onDecide: (requestId: string, decision: ProtectedPathDecision) => void;
}

export const ProtectedPathCard: React.FC<Props> = ({ queue, onDecide }) => {
  const request = queue[0];
  if (!request) return null;
  const action = request.op === "read" ? "读取" : "写入";
  return (
    <div role="alertdialog" aria-labelledby="protected-path-title" data-testid="protected-path-card"
      style={{ ...surfaceModal, position: "fixed", right: 20, bottom: 20, zIndex: 9999, width: 420, maxWidth: "92vw",
        padding: tokens.space.lg, borderTop: `4px solid ${tokens.color.warning.bg}`, display: "flex", flexDirection: "column", gap: tokens.space.sm }}>
      <div id="protected-path-title" style={{ color: dark.text, fontWeight: 600 }}>
        {`助手想${action}一个受保护的文件`}
        {queue.length > 1 && <span style={{ color: dark.textMuted, fontWeight: 400 }}>{`（还有 ${queue.length - 1} 个）`}</span>}
      </div>
      <div style={{ color: dark.textMuted, fontSize: tokens.text.xs.size }}>{request.reason}</div>
      <code style={{ overflowWrap: "anywhere", color: dark.text }}>{request.path}</code>
      <div style={{ color: dark.textMuted, fontSize: tokens.text.xs.size }}>
        任务没有停下来等你。允许后，助手再试一次就能用；拒绝后它会换别的办法。
      </div>
      <div style={{ display: "flex", gap: tokens.space.sm, flexWrap: "wrap" }}>
        <button type="button" className="bp-btn-primary" style={buttonStyle("primary", "sm")}
          onClick={() => onDecide(request.request_id, "allow_once")}>允许这一次</button>
        <button type="button" style={buttonStyle("secondary", "sm")}
          onClick={() => onDecide(request.request_id, "allow_session")}>本会话都允许</button>
        <button type="button" style={buttonStyle("ghost", "sm")}
          onClick={() => onDecide(request.request_id, "deny")}>拒绝</button>
      </div>
    </div>
  );
};

export default ProtectedPathCard;
