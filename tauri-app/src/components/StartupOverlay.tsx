// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Startup splash + error overlay（高级感重设计）.
 *
 * 加载态：居中的深色玻璃卡片 + 双环旋转指示器 + 文案。
 * 失败态：换成错误卡片，配 alert 图标 + 重试 / 打开日志目录 / 退出。
 *
 * Why not `tauri-plugin-dialog::blocking_show`? The blocking dialog
 * freezes the Tauri event loop, which — because our window uses
 * `transparent:true` + `decorations:false` — can leave the user
 * looking at a black rectangle before the dialog paints. An in-DOM
 * overlay renders immediately inside the WebView2 layer.
 */
import { memo } from "react";

import { Icon } from "./Icon";
import "./StartupOverlay.styles.css";

export type BootState = "starting" | "ready" | "failed";

export interface StartupOverlayProps {
  state: BootState;
  errorMessage: string | null;
  onRetry: () => void;
  onOpenLogDir: () => void;
  onExit: () => void;
}

function StartupOverlayImpl({
  state,
  errorMessage,
  onRetry,
  onOpenLogDir,
  onExit,
}: StartupOverlayProps) {
  if (state === "ready") return null;

  const isError = state === "failed";

  return (
    <div data-testid="startup-overlay" className="sh-StartupOverlay-1">
      <div className="sh-StartupOverlay-2" role={isError ? "alertdialog" : "status"}>
        {!isError && (
          <>
            <div className="sh-StartupOverlay-3" aria-hidden>
              <div className="sh-StartupOverlay-4" />
              <div className="sh-StartupOverlay-5">
                <Icon name="sparkle" size={20} />
              </div>
            </div>
            <div className="sh-StartupOverlay-6">正在启动语音服务</div>
            <div className="sh-StartupOverlay-7">首次启动需要 20–60 秒加载模型，请耐心等待</div>
          </>
        )}
        {isError && (
          <>
            <div className="sh-StartupOverlay-8" aria-hidden>
              <Icon name="alert" size={22} />
            </div>
            <div className="sh-StartupOverlay-6">启动失败</div>
            <pre className="sh-StartupOverlay-9">{errorMessage ?? "未知错误"}</pre>
            <div className="sh-StartupOverlay-10">
              <button
                type="button"
                data-testid="startup-retry"
                onClick={onRetry}
                className="sh-StartupOverlay-11"
              >
                <Icon name="refresh" size={14} />
                重试
              </button>
              <button
                type="button"
                data-testid="startup-open-log"
                onClick={onOpenLogDir}
                className="sh-StartupOverlay-12"
              >
                <Icon name="folder" size={14} />
                打开日志目录
              </button>
              <button
                type="button"
                data-testid="startup-exit"
                onClick={onExit}
                className="sh-StartupOverlay-12"
              >
                退出
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export const StartupOverlay = memo(StartupOverlayImpl);
