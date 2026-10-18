// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useMemo, useState } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";
import { sendWhenConnected } from "../ws/sendWhenConnected";
import { Icon } from "./Icon";
import { dark } from "../theme/components";
import type { ControlChannel } from "../ws/ControlChannel";
import type { DecisionRecord, DecisionsListResponse, IncomingMessage } from "../types/messages";
import "./ContextTracePanel.styles.css";

type Props = {
  open: boolean;
  onClose: () => void;
  getChannel: () => ControlChannel | null;
};

/** ContextTrace：上下文组装的决定记录。 */
export function ContextTracePanel({ open, onClose, getChannel }: Props) {
  if (!open) return null;

  return (
    <div
      data-testid="context-trace-panel"
      className="sh-ContextTracePanel-1"
      role="dialog"
      aria-label="ContextTrace"
    >
      <div className="sh-ContextTracePanel-2">
        <span className="sh-ContextTracePanel-3">
          <span className="sh-ContextTracePanel-4">
            <Icon name="compass" size={15} />
          </span>
          <strong className="sh-ContextTracePanel-5">ContextTrace</strong>
        </span>
        <button
          data-testid="trace-close"
          onClick={onClose}
          className="sh-ContextTracePanel-6"
          title="关闭"
          aria-label="关闭"
        >
          <Icon name="close" size={15} />
        </button>
      </div>

      <div className="sh-ContextTracePanel-8">
        <LegacyContextTrace getChannel={getChannel} active />
      </div>
    </div>
  );
}

function LegacyContextTrace({
  getChannel,
  active,
}: {
  getChannel: () => ControlChannel | null;
  active: boolean;
}) {
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [reason, setReason] = useState<string | null>(null);
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(false);
  const contextWindow = 32_000;
  // 只发请求不写状态；返回是否有通道。
  const sendList = useCallback(() => {
    const channel = getChannel();
    if (!channel) return false;
    return channel.send({ type: "decisions_list", payload: { limit } });
  }, [getChannel, limit]);
  const refresh = () => {
    if (getChannel()) setLoading(true);
    sendList();
  };
  // 激活（含挂载即激活）或条数变了：渲染期设读取中，effect 只发请求。比对项与订阅 effect 依赖一致。
  if (useDepsChanged([active, getChannel, sendList], { onMount: true }) && active && getChannel())
    setLoading(true);
  useEffect(() => {
    if (!active) return;
    const channel = getChannel();
    if (!channel) return;
    const unsubscribe = channel.onMessage((message: IncomingMessage) => {
      if (message.type !== "decisions_list_response") return;
      const payload = (message as DecisionsListResponse).payload;
      setDecisions(payload.decisions);
      setReason(payload.reason ?? null);
      setLoading(false);
    });
    // 没连上就等连上再发，等待期间保持读取中。
    const cancelRead = sendWhenConnected(channel, sendList);
    return () => {
      unsubscribe();
      cancelRead();
    };
  }, [active, getChannel, sendList]);
  const ordered = useMemo(
    () => [...decisions].sort((a, b) => cmpTimestamp(b.timestamp, a.timestamp)),
    [decisions],
  );
  const latest = ordered[0];
  const used = latest?.total_tokens ?? 0;
  const percent = contextWindow > 0 ? Math.min(1, used / contextWindow) : 0;
  const warning = percent >= 0.9;

  return (
    <div className="sh-ContextTracePanel-9">
      <div className="sh-ContextTracePanel-10">
        <button
          data-testid="trace-refresh"
          onClick={refresh}
          className="sh-ContextTracePanel-11"
          disabled={loading}
        >
          <Icon name={loading ? "loader" : "refresh"} size={13} />
          刷新
        </button>
        <label className="sh-ContextTracePanel-12">
          <span>条数</span>
          <input
            data-testid="trace-limit"
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={(event) =>
              setLimit(Math.max(1, Math.min(200, Number(event.target.value) || 50)))
            }
            className="sh-ContextTracePanel-13"
          />
        </label>
        <label className="sh-ContextTracePanel-12">
          <span>actual context_window</span>
          <input
            data-testid="trace-ctx-window"
            aria-label="actual context window"
            type="number"
            value={contextWindow}
            readOnly
            className="sh-ContextTracePanel-14"
          />
        </label>
      </div>
      {reason && (
        <div data-testid="trace-reason" className="sh-ContextTracePanel-15">
          {reason === "context_assembler_not_registered"
            ? "当前没有可观察的上下文记录（主对话和任务编排的上下文不经过这里）。"
            : `后端提示：${reason}`}
        </div>
      )}
      {latest && (
        <div
          data-testid="trace-budget"
          style={{
            padding: "9px 11px",
            border: `1px solid ${warning ? "rgba(249,115,22,0.5)" : dark.border}`,
            borderRadius: 7,
            background: warning ? "rgba(249,115,22,0.13)" : dark.card,
          }}
        >
          <div className="sh-ContextTracePanel-16">
            <span className="sh-ContextTracePanel-17">
              {warning && <Icon name="alert" size={12} />}
              {warning ? "预算接近上限" : "上一回合 token 预算"}
            </span>
            <span className="sh-ContextTracePanel-18">
              {used.toLocaleString()} / {contextWindow.toLocaleString()} (
              {(percent * 100).toFixed(1)}%)
            </span>
          </div>
          <div
            aria-label="budget usage"
            role="progressbar"
            aria-valuenow={Math.round(percent * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            className="sh-ContextTracePanel-19"
          >
            <div
              data-testid="trace-budget-bar"
              style={{
                width: `${percent * 100}%`,
                height: "100%",
                background: warning ? "#f97316" : "#2563eb",
              }}
            />
          </div>
        </div>
      )}
      <div className="sh-ContextTracePanel-20">
        {ordered.length === 0 && !loading && (
          <div className="sh-ContextTracePanel-21">(无决策记录)</div>
        )}
        {ordered.map((decision, index) => (
          <DecisionRow
            key={`${String(decision.timestamp)}-${index}`}
            index={index}
            decision={decision}
            contextWindow={contextWindow}
          />
        ))}
      </div>
    </div>
  );
}

function DecisionRow({
  index,
  decision,
  contextWindow,
}: {
  index: number;
  decision: DecisionRecord;
  contextWindow: number;
}) {
  const entries = Object.entries(decision.token_breakdown || {});
  const total = decision.total_tokens ?? 0;
  return (
    <div data-testid={`trace-decision-${index}`} className="sh-ContextTracePanel-22">
      <div className="sh-ContextTracePanel-23">
        <span className="sh-ContextTracePanel-29">
          <strong style={{ color: classifierColor(decision.classifier_path) }}>
            {decision.classifier_path || "?"}
          </strong>
          {decision.reason ? ` · ${decision.reason}` : ""}
        </span>
        <span className="sh-ContextTracePanel-30">{formatTimestamp(decision.timestamp)}</span>
      </div>
      <div className="sh-ContextTracePanel-31">
        <span>
          latency <strong>{fmtMs(decision.latency_ms)}</strong>
        </span>
        <span>
          total_tokens <strong>{total.toLocaleString()}</strong>
        </span>
        {decision.session_id && (
          <span title={decision.session_id}>
            sid{" "}
            <strong>
              {decision.session_id.length > 10
                ? `…${decision.session_id.slice(-8)}`
                : decision.session_id}
            </strong>
          </span>
        )}
      </div>
      {entries.length > 0 && (
        <TokenBreakdownBar
          entries={entries}
          total={total || sum(entries.map(([, value]) => value))}
          contextWindow={contextWindow}
        />
      )}
    </div>
  );
}

function TokenBreakdownBar({
  entries,
  total,
  contextWindow,
}: {
  entries: [string, number][];
  total: number;
  contextWindow: number;
}) {
  const palette: Record<string, string> = {
    system: "#3b82f6",
    l1: "#10b981",
    l2: "#f59e0b",
    l3: "#ef4444",
    tools: "#8b5cf6",
    history: "#06b6d4",
    summary: "#f472b6",
  };
  const safeTotal = total || 1;
  const usedPercent = contextWindow > 0 ? Math.min(1, total / contextWindow) : 0;
  return (
    <div data-testid="trace-breakdown" className="sh-ContextTracePanel-32">
      <div
        title={`使用 ${total.toLocaleString()} / ${contextWindow.toLocaleString()} tokens`}
        style={{
          display: "flex",
          height: 6,
          width: `${usedPercent * 100}%`,
          minWidth: "20%",
          overflow: "hidden",
          borderRadius: 3,
          background: "#0f172a",
        }}
      >
        {entries.map(([section, value]) =>
          value > 0 ? (
            <div
              key={section}
              data-testid={`trace-section-${section}`}
              title={`${section}: ${value.toLocaleString()}`}
              style={{
                width: `${(value / safeTotal) * 100}%`,
                background: palette[section] || "#64748b",
              }}
            />
          ) : null,
        )}
      </div>
      <div className="sh-ContextTracePanel-33">
        {entries.map(([section, value]) => (
          <span key={section}>
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                marginRight: 3,
                borderRadius: 2,
                verticalAlign: "middle",
                background: palette[section] || "#64748b",
              }}
            />
            {section} {value.toLocaleString()}
          </span>
        ))}
      </div>
    </div>
  );
}

function classifierColor(path?: string) {
  if (path === "cloud") return "#60a5fa";
  if (path === "local") return "#34d399";
  if (path === "echo") return "#fbbf24";
  return "#e2e8f0";
}
function formatTimestamp(value?: string | number) {
  if (value == null) return "-";
  const millis =
    typeof value === "number" ? (value < 10_000_000_000 ? value * 1000 : value) : Date.parse(value);
  if (Number.isNaN(millis)) return String(value);
  return new Date(millis).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
function fmtMs(value?: number) {
  if (value == null) return "-";
  return value < 1000 ? `${value.toFixed(0)}ms` : `${(value / 1000).toFixed(2)}s`;
}
function cmpTimestamp(a?: string | number, b?: string | number) {
  return toEpoch(a) - toEpoch(b);
}
function toEpoch(value?: string | number) {
  if (value == null) return 0;
  if (typeof value === "number") return value < 10_000_000_000 ? value * 1000 : value;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}
function sum(values: number[]) {
  return values.reduce((total, value) => total + value, 0);
}
