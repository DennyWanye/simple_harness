// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S24 followup — minimal modal confirm dialog.
 *
 * No external deps; styled to match the dark code-panel chrome. Used
 * for destructive actions like "delete project session" so users
 * can't lose state to a slipped click.
 *
 * Usage:
 *   const [confirm, setConfirm] = useState<null | { ... }>(null);
 *   ...
 *   {confirm && <ConfirmDialog {...confirm} onCancel={() => setConfirm(null)} />}
 */
import { useEffect, useRef } from "react";
import type React from "react";
import "./ConfirmDialog.styles.css";

export interface ConfirmDialogProps {
  title: string;
  message: React.ReactNode;
  /** Visible label on the destructive action button (defaults to "确认"). */
  confirm_label?: string;
  /** Visible label on the cancel button (defaults to "取消"). */
  cancel_label?: string;
  /**
   * "danger" → red confirm button (use for delete/destroy).
   * "primary" → blue (use for non-destructive flows).
   */
  variant?: "danger" | "primary";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  confirm_label = "确认",
  cancel_label = "取消",
  variant = "danger",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  // 2026-09-25 UI 全量点击：弹框出现时焦点没移进来，Esc/Enter 都到不了它。
  // 打开即聚焦「取消」（删除类操作默认停在安全按钮上）；Esc 取消；Enter 只触发
  // 当前焦点所在的按钮（浏览器默认行为），不再无条件等于"确认"。
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  const confirmBg = variant === "danger" ? "#dc2626" : "#2563eb";
  const confirmHover = variant === "danger" ? "#b91c1c" : "#1d4ed8";
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onClick={(e) => {
        // Click on backdrop = cancel. Stop on dialog body so inner
        // clicks don't bubble.
        if (e.target === e.currentTarget) onCancel();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
      tabIndex={-1}
      className="sh-ConfirmDialog-1"
    >
      <div className="sh-ConfirmDialog-2">
        <h3 id="confirm-dialog-title" className="sh-ConfirmDialog-3">
          {title}
        </h3>
        <div className="sh-ConfirmDialog-4">{message}</div>
        <div className="sh-ConfirmDialog-5">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            style={{
              background: "rgba(148, 163, 184, 0.18)",
              color: "#e2e8f0",
              border: "1px solid rgba(148, 163, 184, 0.30)",
              borderRadius: 5,
              padding: "5px 14px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {cancel_label}
          </button>
          <button
            type="button"
            autoFocus
            onClick={onConfirm}
            style={{
              background: confirmBg,
              color: "#fff",
              border: "none",
              borderRadius: 5,
              padding: "5px 14px",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = confirmHover;
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = confirmBg;
            }}
          >
            {confirm_label}
          </button>
        </div>
      </div>
    </div>
  );
}
