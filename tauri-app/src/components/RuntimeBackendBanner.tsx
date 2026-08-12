// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CSSProperties } from "react";

import { dark, bannerStyle } from "../theme/components";

export interface RuntimeBackendBannerProps {
  message: string;
  onRetry: () => void;
  onOpenLogDir: () => void;
  onDismiss: () => void;
}

const actionStyle: CSSProperties = {
  flexShrink: 0,
  background: "transparent",
  border: `1px solid ${dark.borderStrong}`,
  borderRadius: 4,
  color: "inherit",
  cursor: "pointer",
  font: "inherit",
  padding: "2px 8px",
};

/**
 * Non-blocking recovery surface for a backend that dies after the workbench
 * has already started. Initial boot failures still use StartupOverlay; a
 * runtime failure must leave ChatView and the sidebar connection badge visible.
 */
export function RuntimeBackendBanner({
  message,
  onRetry,
  onOpenLogDir,
  onDismiss,
}: RuntimeBackendBannerProps) {
  return (
    <div
      role="alert"
      data-testid="runtime-backend-banner"
      style={{
        ...bannerStyle("error"),
        display: "flex",
        alignItems: "center",
        gap: 8,
        borderRadius: 0,
      }}
    >
      <span aria-hidden="true">⚠</span>
      <span data-bp-selectable="" style={{ flex: 1, minWidth: 0 }}>
        {message}
      </span>
      <button type="button" onClick={onRetry} style={actionStyle}>
        重试后端
      </button>
      <button type="button" onClick={onOpenLogDir} style={actionStyle}>
        打开日志
      </button>
      <button
        type="button"
        onClick={onDismiss}
        title="关闭"
        aria-label="关闭后端错误提示"
        style={{ ...actionStyle, border: "none", paddingInline: 4 }}
      >
        ✕
      </button>
    </div>
  );
}
