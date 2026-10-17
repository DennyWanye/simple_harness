// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React, { useEffect, useRef } from "react";

import type { ExternalWaitRequest } from "../types/skillPlatform";
import { buttonStyle } from "../theme/components";
import "./ExternalWaitDialog.styles.css";

interface Props {
  request: ExternalWaitRequest["payload"] | null;
  onComplete: () => void;
}

export const ExternalWaitDialog: React.FC<Props> = ({ request, onComplete }) => {
  const completeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!request) return;
    completeRef.current?.focus();
  }, [request]);

  if (!request) return null;
  const isUac = request.wait_kind === "uac";

  return (
    <div
      className="sh-ExternalWaitDialog-1"
      role="dialog"
      aria-modal="true"
      aria-labelledby="external-wait-title"
      data-testid="external-wait-dialog"
    >
      <div className="sh-ExternalWaitDialog-2">
        <div className="sh-ExternalWaitDialog-3">
          <div className="sh-ExternalWaitDialog-4">{isUac ? "Windows 系统确认" : "外部步骤"}</div>
          <h3 id="external-wait-title" className="sh-ExternalWaitDialog-5">
            {request.title}
          </h3>
        </div>

        <div className="sh-ExternalWaitDialog-6">
          <div className="sh-ExternalWaitDialog-7">{request.required_action}</div>
          <div className="sh-ExternalWaitDialog-8" role="status">
            当前任务已安全暂停。处理上面的系统或第三方操作后返回检查；Simple Harness
            会重新探测真实结果，这不会记作一次执行失败。
          </div>
          <button
            ref={completeRef}
            type="button"
            className="bp-btn-primary"
            style={buttonStyle("primary", "md")}
            onClick={onComplete}
          >
            外部操作已处理，检查结果
          </button>
        </div>
      </div>
    </div>
  );
};

export default ExternalWaitDialog;
