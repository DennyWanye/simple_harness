// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 应用内确认框（2026-09-25 UI 全量点击发现）。
 *
 * 桌面程序的 WebView 不实现 `window.confirm`：调用不弹框、直接当作"确认"，
 * 于是「完全卸载」「迁移数据目录」「卸载 Skill」这些不可逆操作一点就执行。
 * 破坏性操作一律用这个 hook 弹程序自己的确认框，不要再用 `window.confirm`。
 *
 *   const [confirmDialog, ask] = useConfirm();
 *   if (!(await ask({ title: "…", message: "…", confirm_label: "删除" }))) return;
 *   ...
 *   return <>{confirmDialog}…</>;
 */
import { useCallback, useRef, useState } from "react";
import type React from "react";

import { ConfirmDialog } from "../code-panel/ConfirmDialog";

export interface ConfirmRequest {
  title: string;
  message: React.ReactNode;
  confirm_label?: string;
  variant?: "danger" | "primary";
}

export function useConfirm(): [React.ReactNode, (request: ConfirmRequest) => Promise<boolean>] {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const resolver = useRef<((value: boolean) => void) | null>(null);

  const ask = useCallback((next: ConfirmRequest) => {
    resolver.current?.(false); // a new question supersedes an unanswered one
    setRequest(next);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const settle = (value: boolean) => {
    resolver.current?.(value);
    resolver.current = null;
    setRequest(null);
  };

  const dialog = request ? (
    <ConfirmDialog
      title={request.title}
      message={<span style={{ whiteSpace: "pre-wrap" }}>{request.message}</span>}
      confirm_label={request.confirm_label}
      variant={request.variant ?? "danger"}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
  ) : null;

  return [dialog, ask];
}
