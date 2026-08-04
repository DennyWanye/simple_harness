// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import React, { useEffect, useRef } from "react";

import type { ExternalWaitRequest } from "../types/skillPlatform";
import {
  backdropStyle,
  bannerStyle,
  buttonStyle,
  dark,
  surfaceModal,
} from "../theme/components";
import { tokens } from "../theme/tokens";

interface Props {
  request: ExternalWaitRequest["payload"] | null;
  onComplete: () => void;
}

export const ExternalWaitDialog: React.FC<Props> = ({
  request,
  onComplete,
}) => {
  const completeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!request) return;
    completeRef.current?.focus();
  }, [request]);

  if (!request) return null;
  const isUac = request.wait_kind === "uac";

  return (
    <div
      style={{ ...backdropStyle, zIndex: 10000 }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="external-wait-title"
      data-testid="external-wait-dialog"
    >
      <div
        style={{
          ...surfaceModal,
          width: 460,
          maxWidth: "92vw",
          display: "flex",
          flexDirection: "column",
          borderTop: `4px solid ${tokens.color.warning.bg}`,
          animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
        }}
      >
        <div style={{ padding: tokens.space.lg }}>
          <div
            style={{
              color: dark.textMuted,
              fontSize: tokens.text.xs.size,
              marginBottom: tokens.space.xs,
            }}
          >
            {isUac ? "Windows 系统确认" : "外部步骤"}
          </div>
          <h3
            id="external-wait-title"
            style={{
              margin: 0,
              color: dark.text,
              fontSize: tokens.text.lg.size,
            }}
          >
            {request.title}
          </h3>
        </div>

        <div
          style={{
            padding: `0 ${tokens.space.lg}px ${tokens.space.lg}px`,
            display: "flex",
            flexDirection: "column",
            gap: tokens.space.md,
          }}
        >
          <div
            style={{
              color: dark.text,
              fontSize: tokens.text.md.size,
              lineHeight: 1.55,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
            }}
          >
            {request.required_action}
          </div>
          <div style={bannerStyle("warning")} role="status">
            当前任务已安全暂停。处理上面的系统或第三方操作后返回检查；DeskPet
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
