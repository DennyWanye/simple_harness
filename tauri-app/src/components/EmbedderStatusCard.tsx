// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useState } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  EmbedderStatusResponse,
  IncomingMessage,
} from "../types/messages";

type Props = {
  getChannel: () => ControlChannel | null;
};

// Metadata-only status: refreshing never requests a model load.
type Status =
  | { kind: "loading" }
  | { kind: "cold" }
  | { kind: "failed"; reason: string }
  | { kind: "real"; modelPath: string }
  | { kind: "mock"; modelPath: string }
  | { kind: "error"; reason: string };

export function EmbedderStatusCard({ getChannel }: Props) {
  const [modelName, setModelName] = useState("语义嵌入模型");
  const [modelPath, setModelPath] = useState("");
  const [checking, setChecking] = useState(true);
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const refresh = useCallback(() => {
    const ch = getChannel();
    if (!ch) {
      setStatus({ kind: "error", reason: "control channel unavailable" });
      return;
    }
    setChecking(true);
    ch.send({ type: "embedder_status", payload: {} });
  }, [getChannel]);

  // 订阅 embedder_status_response —— 同 ControlChannel.onMessage 广播路径，
  // 不会偷走 App.tsx 主分发逻辑。
  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const unsub = ch.onMessage((msg: IncomingMessage) => {
      if (msg.type !== "embedder_status_response") return;
      const m = msg as EmbedderStatusResponse;
      const p = m.payload;
      setChecking(false);
      setModelName(p.model_name || "语义嵌入模型");
      setModelPath(p.model_path);
      if (p.state === "cold") {
        setStatus({ kind: "cold" });
        return;
      }
      if (p.state === "failed") {
        setStatus({ kind: "failed", reason: p.reason || "模型加载失败" });
        return;
      }
      if (p.reason) {
        setStatus({ kind: "error", reason: p.reason });
        return;
      }
      if (!p.is_ready) {
        setStatus({ kind: "loading" });
        return;
      }
      setStatus(
        p.is_mock
          ? { kind: "mock", modelPath: p.model_path }
          : { kind: "real", modelPath: p.model_path },
      );
    });
    refresh();
    return unsub;
  }, [getChannel, refresh]);

  return (
    <div
      data-testid="embedder-status-card"
      style={{
        border: "1px solid #2d3748",
        borderRadius: "6px",
        padding: "8px 10px",
        marginTop: "8px",
        background: "rgba(15,23,42,0.4)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "4px",
        }}
      >
        <strong style={{ fontSize: "12px" }}>{modelName}</strong>
        <button
          data-testid="embedder-status-refresh"
          onClick={refresh}
          style={{
            background: "transparent",
            color: "#cbd5e1",
            border: "1px solid #475569",
            borderRadius: "3px",
            padding: "1px 6px",
            fontSize: "10px",
            cursor: "pointer",
          }}
        >
          刷新
        </button>
      </div>

      {status.kind === "loading" && (
        <>
          <Badge color="#64748b" label={checking ? "读取状态…" : "加载中…"} />
          <PathLine path={modelPath} />
        </>
      )}

      {status.kind === "cold" && (
        <>
          <Badge color="#64748b" label="按需加载" />
          <Hint>模型已配置，首次语义请求时加载。</Hint>
          <PathLine path={modelPath} />
        </>
      )}

      {status.kind === "failed" && (
        <>
          <Badge color="#ef4444" label="加载失败" />
          <Hint>下次语义请求可重试；刷新仅查看状态。{status.reason}</Hint>
          <PathLine path={modelPath} />
        </>
      )}

      {status.kind === "real" && (
        <>
          <Badge color="#10b981" label={`${modelName} 已就绪 ✓`} />
          <Hint>语义搜索完整激活（向量召回 + 跨语言）。</Hint>
          <PathLine path={status.modelPath} />
        </>
      )}

      {status.kind === "mock" && (
        <>
          <Badge color="#f59e0b" label="Mock 模式 ⚠" />
          <Hint>
            当前使用模拟嵌入，语义搜索能力受限。请检查已选择模型的本地资源配置。
          </Hint>
          <PathLine path={status.modelPath} />
        </>
      )}

      {status.kind === "error" && (
        <>
          <Badge color="#94a3b8" label="未启动" />
          <Hint>后端提示：{status.reason}</Hint>
        </>
      )}
    </div>
  );
}

// --- helpers --------------------------------------------------------------

function Badge({ color, label }: { color: string; label: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        background: color,
        color: "white",
        padding: "2px 8px",
        borderRadius: "10px",
        fontSize: "11px",
        fontWeight: 600,
        marginRight: "6px",
      }}
    >
      {label}
    </span>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "11px",
        color: "#cbd5e1",
        marginTop: "4px",
        lineHeight: 1.5,
      }}
    >
      {children}
    </div>
  );
}

function PathLine({ path }: { path: string }) {
  if (!path) return null;
  // 路径太长时截断中间，保留头尾两端最相关的部分。
  const display = path.length > 64 ? `${path.slice(0, 22)} … ${path.slice(-32)}` : path;
  return (
    <div
      style={{
        fontSize: "10px",
        color: "#64748b",
        marginTop: "3px",
        fontFamily: "monospace",
        wordBreak: "break-all",
      }}
      title={path}
    >
      {display}
    </div>
  );
}
