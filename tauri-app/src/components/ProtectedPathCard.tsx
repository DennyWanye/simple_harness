// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/** 受保护核心文件的申请卡片：右下角浮动，不挡住界面；一次只显示最早的一张。 */
import React from "react";
import type {
  ProtectedPathDecision,
  ProtectedPathRequest,
} from "../hooks/useProtectedPathRequests";
import "./ProtectedPathCard.styles.css";

interface Props {
  queue: ProtectedPathRequest[];
  onDecide: (requestId: string, decision: ProtectedPathDecision) => void;
}

export const ProtectedPathCard: React.FC<Props> = ({ queue, onDecide }) => {
  const request = queue[0];
  if (!request) return null;
  const action = request.op === "read" ? "读取" : "写入";
  return (
    <div
      role="alertdialog"
      aria-labelledby="protected-path-title"
      data-testid="protected-path-card"
      className="sh-ProtectedPathCard-1"
    >
      <div id="protected-path-title" className="sh-ProtectedPathCard-2">
        {`助手想${action}一个受保护的文件`}
        {queue.length > 1 && (
          <span className="sh-ProtectedPathCard-3">{`（还有 ${queue.length - 1} 个）`}</span>
        )}
      </div>
      <div className="sh-ProtectedPathCard-4">{request.reason}</div>
      <code className="sh-ProtectedPathCard-5">{request.path}</code>
      <div className="sh-ProtectedPathCard-4">
        任务没有停下来等你。允许后，助手再试一次就能用；拒绝后它会换别的办法。
      </div>
      <div className="sh-ProtectedPathCard-6">
        <button
          type="button"
          className="bp-btn-primary sh-ProtectedPathCard-7"
          onClick={() => onDecide(request.request_id, "allow_once")}
        >
          允许这一次
        </button>
        <button
          type="button"
          className="sh-ProtectedPathCard-8"
          onClick={() => onDecide(request.request_id, "allow_session")}
        >
          本会话都允许
        </button>
        <button
          type="button"
          className="sh-ProtectedPathCard-9"
          onClick={() => onDecide(request.request_id, "deny")}
        >
          拒绝
        </button>
      </div>
    </div>
  );
};

export default ProtectedPathCard;
