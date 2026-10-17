// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * ModelDownloadBanner — Option A（2026-06-05）首启模型下载进度条。
 *
 * 瘦 NSIS 包不内嵌 ~2.6GB 模型；backend 首启从 hf-mirror 后台下载缺失的
 * BGE-M3 / faster-whisper。本组件轮询 control-WS `model_provision_status`，
 * 下载期间在底部显示一条进度横幅；ready/idle 时不渲染任何东西。
 *
 * 轮询而非订阅推送：复用现有"请求→响应"模式（同 EmbedderStatusCard），
 * 无需后端主动 broadcast 基建。state=ready 后停止轮询。
 */
import { useEffect, useRef, useState } from "react";

import type { ControlChannel } from "../ws/ControlChannel";
import type { IncomingMessage, ModelProvisionStatusResponse } from "../types/messages";
import "./ModelDownloadBanner.styles.css";

type Props = {
  getChannel: () => ControlChannel | null;
};

type Payload = ModelProvisionStatusResponse["payload"];

const POLL_MS = 1500;

function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)}MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}KB`;
  return `${n}B`;
}

export function ModelDownloadBanner({ getChannel }: Props) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;

    const stop = () => {
      if (timer.current) {
        clearInterval(timer.current);
        timer.current = null;
      }
    };

    const unsub = ch.onMessage((msg: IncomingMessage) => {
      if (msg.type !== "model_provision_status_response") return;
      const p = (msg as ModelProvisionStatusResponse).payload;
      setPayload(p);
      // 终态(ready)停止轮询；error 也停（避免无意义刷屏，靠重启重试）。
      if (p.state === "ready" || p.state === "error") stop();
    });

    const poll = () => {
      const c = getChannel();
      if (c) c.send({ type: "model_provision_status", payload: {} });
    };
    poll();
    timer.current = setInterval(poll, POLL_MS);

    return () => {
      stop();
      unsub();
    };
  }, [getChannel]);

  if (!payload) return null;
  const { state } = payload;
  // 只在"有事发生"时显示：检查中 / 下载中 / 出错。ready/idle 不渲染。
  if (state !== "checking" && state !== "downloading" && state !== "error") {
    return null;
  }

  const total = payload.total ?? 0;
  const index = payload.index ?? 0;
  const done = payload.downloaded_bytes ?? 0;
  const totalBytes = payload.total_bytes ?? 0;
  const pct = totalBytes > 0 ? Math.min(100, Math.round((done / totalBytes) * 100)) : null;

  return (
    <div className="sh-ModelDownloadBanner-1" data-testid="model-download-banner">
      {state === "error" ? (
        <div className="sh-ModelDownloadBanner-2">
          ⚠ 模型下载失败：{payload.error || "未知错误"}。请检查网络后重启 Simple Harness 重试。
        </div>
      ) : (
        <>
          <div className="sh-ModelDownloadBanner-3">
            <span>
              {state === "checking"
                ? "正在检查模型…"
                : `正在下载模型 ${index}/${total}${payload.current ? `：${payload.current}` : ""}`}
            </span>
            <span className="sh-ModelDownloadBanner-4">
              {pct !== null ? `${pct}%` : state === "downloading" ? "…" : ""}
            </span>
          </div>
          {state === "downloading" && (
            <>
              <div className="sh-ModelDownloadBanner-5">
                <div
                  style={{
                    ...barStyle,
                    width: pct !== null ? `${pct}%` : "30%",
                    // 未知总量时给个轻微"流动"宽度，避免显示成 0。
                    opacity: pct !== null ? 1 : 0.6,
                  }}
                />
              </div>
              <div className="sh-ModelDownloadBanner-6">
                首次启动需下载语音/记忆模型（约 2.6GB，仅此一次）。
                {totalBytes > 0 ? ` ${fmtBytes(done)} / ${fmtBytes(totalBytes)}` : ""}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

const barStyle: React.CSSProperties = {
  height: "100%",
  borderRadius: 4,
  background: "linear-gradient(90deg,#3b82f6,#60a5fa)",
  transition: "width 400ms ease",
};
