// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import "./RuntimeBackendBanner.styles.css";

export interface RuntimeBackendBannerProps {
  message: string;
  onRetry: () => void;
  onOpenLogDir: () => void;
  onDismiss: () => void;
}

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
    <div role="alert" data-testid="runtime-backend-banner" className="sh-RuntimeBackendBanner-1">
      <span aria-hidden="true">⚠</span>
      <span data-bp-selectable="" className="sh-RuntimeBackendBanner-2">
        {message}
      </span>
      <button type="button" onClick={onRetry} className="sh-RuntimeBackendBanner-3">
        重试后端
      </button>
      <button type="button" onClick={onOpenLogDir} className="sh-RuntimeBackendBanner-3">
        打开日志
      </button>
      <button
        type="button"
        onClick={onDismiss}
        title="关闭"
        aria-label="关闭后端错误提示"
        className="sh-RuntimeBackendBanner-4"
      >
        ✕
      </button>
    </div>
  );
}
