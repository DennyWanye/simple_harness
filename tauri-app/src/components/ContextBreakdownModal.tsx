// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 2026-05-31 restore — Drill-down modal opened by clicking <ContextRing>.
 *
 * Mirrors Claude Code's "where did my context go" view:
 *   1. Header: model name, current % filled, "if I keep going I'll
 *      hit compact at X tokens".
 *   2. Stacked bar by section (system / memory / tools / history).
 *   3. Each section row — token count, preview snippet (expandable).
 *
 * Pulls its data via WS request `context_breakdown_request` and waits
 * for `context_breakdown_response`. Caller passes a `sessionId` + a
 * `send` + `onMessage` plumbing pair.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";

import type { ContextUsageSnapshot } from "../stores/sessionsStore";
import {
  canonicalJson,
  contextDisplayState,
  newContextRequestId,
} from "../context/contextAuthority";
import { ringColor, ringPercent } from "./ContextRing";

interface BreakdownSection {
  kind: string;
  label: string;
  tokens: number | null;
  token_source: "measured" | "estimated" | "unavailable";
  availability: "available" | "unavailable";
  public_preview: string | null;
  preview_truncated: boolean;
  count?: number;
}

interface ContextHistorySample {
  sample_id: string;
  session_id: string;
  run_id?: string | null;
  request_id?: string | null;
  attempt_id?: string | null;
  event_type: string;
  source?: "measured" | "compacted";
  tokens_before?: number | null;
  tokens_after: number;
  prompt_tokens: number;
  context_window: number;
  effective_ceiling: number;
  estimate_method: string;
  metadata?: Record<string, unknown>;
  created_at: number;
}

interface BreakdownResponse {
  session_id: string;
  correlation_id: string;
  snapshot_id: string | null;
  sample_id: string | null;
  snapshot_version: number;
  snapshot_fingerprint: string;
  availability: "available" | "unavailable";
  project_name?: string | null;
  project_root?: string | null;
  model: string;
  sections: BreakdownSection[];
  total_estimated_tokens: number;
  last_usage_prompt_tokens: number | null;
  context_window: number;
  effective_ceiling: number;
  compact_at: number;
  updated_at: number;
  history?: ContextHistorySample[];
  ts: number;
}

const PUBLIC_PREVIEW_LIMIT = 600;

function finiteNonNegative(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function optionalIdentity(value: unknown): string | null | undefined {
  if (value === null) return null;
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  return normalized || undefined;
}

function normalizeBreakdownResponse(value: unknown): BreakdownResponse | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const sessionId = optionalIdentity(raw.session_id);
  const correlationId = optionalIdentity(raw.correlation_id);
  const snapshotId = optionalIdentity(raw.snapshot_id);
  const sampleId = optionalIdentity(raw.sample_id);
  const version = finiteNonNegative(raw.snapshot_version);
  if (
    !sessionId || !correlationId || snapshotId === undefined ||
    sampleId === undefined || version === null || !Number.isInteger(version) ||
    !Array.isArray(raw.sections)
  ) return null;

  const sections: BreakdownSection[] = [];
  for (const item of raw.sections) {
    if (!item || typeof item !== "object") return null;
    const section = item as Record<string, unknown>;
    const kind = optionalIdentity(section.kind);
    const label = optionalIdentity(section.label);
    if (!kind || !label) return null;
    const tokenSource = section.token_source;
    if (tokenSource !== "measured" && tokenSource !== "estimated" && tokenSource !== "unavailable") {
      return null;
    }
    const tokens = finiteNonNegative(section.tokens);
    if (tokenSource !== "unavailable" && tokens === null) return null;
    const publicPreview = typeof section.public_preview === "string"
      ? section.public_preview.slice(0, PUBLIC_PREVIEW_LIMIT)
      : null;
    sections.push({
      kind,
      label,
      tokens,
      token_source: tokenSource,
      availability: section.availability === "unavailable" || tokenSource === "unavailable"
        ? "unavailable"
        : "available",
      public_preview: publicPreview,
      preview_truncated: section.preview_truncated === true ||
        (typeof section.public_preview === "string" && section.public_preview.length > PUBLIC_PREVIEW_LIMIT),
      count: finiteNonNegative(section.count) ?? undefined,
    });
  }

  const history = Array.isArray(raw.history)
    ? raw.history.filter((sample): sample is ContextHistorySample => Boolean(
      sample && typeof sample === "object" &&
      optionalIdentity((sample as Record<string, unknown>).sample_id) &&
      optionalIdentity((sample as Record<string, unknown>).session_id) === sessionId &&
      finiteNonNegative((sample as Record<string, unknown>).tokens_after) !== null &&
      finiteNonNegative((sample as Record<string, unknown>).created_at) !== null,
    ))
    : [];

  return {
    session_id: sessionId,
    correlation_id: correlationId,
    snapshot_id: snapshotId,
    sample_id: sampleId,
    snapshot_version: version,
    snapshot_fingerprint: typeof raw.snapshot_fingerprint === "string"
      ? raw.snapshot_fingerprint.slice(0, 160)
      : "",
    availability: raw.availability === "available" ? "available" : "unavailable",
    project_name: typeof raw.project_name === "string" ? raw.project_name.slice(0, 160) : null,
    project_root: null,
    model: typeof raw.model === "string" ? raw.model.slice(0, 160) : "",
    sections,
    total_estimated_tokens: finiteNonNegative(raw.total_estimated_tokens) ?? 0,
    last_usage_prompt_tokens: finiteNonNegative(raw.last_usage_prompt_tokens),
    context_window: finiteNonNegative(raw.context_window) ?? 0,
    effective_ceiling: finiteNonNegative(raw.effective_ceiling) ?? 0,
    compact_at: finiteNonNegative(raw.compact_at) ?? 0,
    updated_at: finiteNonNegative(raw.updated_at) ?? 0,
    history,
    ts: finiteNonNegative(raw.ts) ?? 0,
  };
}

export interface ContextBreakdownModalProps {
  open: boolean;
  onClose(): void;
  sessionId: string;
  projectName?: string;
  projectRoot?: string | null;
  /** Live snapshot — drives the header gauge while we wait for the
   *  breakdown response. */
  snapshot: ContextUsageSnapshot | null | undefined;
  /** Sender. */
  send(msg: { type: string; payload?: Record<string, unknown> }): void;
  /** Subscriber — returns an unsubscribe. */
  onMessage(fn: (msg: any) => void): () => void;
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

function formatClock(value: number): string {
  return new Date(value * 1000).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function ContextHistoryChart({
  history,
}: {
  history: ContextHistorySample[];
}) {
  const points = history.filter((sample) => (
    sample.source === "measured" ||
    sample.source === "compacted" ||
    sample.event_type === "provider_attempt" ||
    sample.event_type === "compaction"
  )).flatMap((sample) => {
    const after = {
      time: sample.created_at,
      tokens: sample.tokens_after,
      sample,
      phase: "after",
    };
    return sample.event_type === "compaction" &&
      sample.tokens_before != null
      ? [
          {
            time: sample.created_at - 0.001,
            tokens: sample.tokens_before,
            sample,
            phase: "before",
          },
          after,
        ]
      : [after];
  });
  if (!points.length) {
    return <div style={chartEmptyStyle}>暂无 Context 变化记录</div>;
  }
  const width = 640;
  const height = 190;
  const pad = { left: 46, right: 16, top: 16, bottom: 30 };
  const minTime = Math.min(...points.map((point) => point.time));
  const maxTime = Math.max(...points.map((point) => point.time));
  const maxObservedTokens = Math.max(
    1,
    ...points.map((point) => point.tokens),
  );
  const maxTokens = Math.ceil(maxObservedTokens * 1.1);
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const x = (time: number, index: number) =>
    pad.left +
    (maxTime === minTime
      ? (index / Math.max(1, points.length - 1)) * plotWidth
      : ((time - minTime) / (maxTime - minTime)) * plotWidth);
  const y = (tokens: number) =>
    pad.top + plotHeight - (tokens / maxTokens) * plotHeight;
  const line = points
    .map((point, index) => `${x(point.time, index)},${y(point.tokens)}`)
    .join(" ");
  return (
    <div style={chartWrapStyle}>
      <svg
        role="img"
        aria-label="Context 大小随时间变化折线图"
        viewBox={`0 0 ${width} ${height}`}
        style={{ display: "block", width: "100%", height: "auto" }}
      >
        {[0, 0.5, 1].map((ratio) => (
          <g key={ratio}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={pad.top + plotHeight * ratio}
              y2={pad.top + plotHeight * ratio}
              stroke="rgba(148,163,184,0.12)"
            />
            <text
              x={pad.left - 7}
              y={pad.top + plotHeight * ratio + 3}
              textAnchor="end"
              fill="#64748b"
              fontSize="9"
            >
              {fmtTokens(Math.round(maxTokens * (1 - ratio)))}
            </text>
          </g>
        ))}
        <polyline
          points={line}
          fill="none"
          stroke="#38bdf8"
          strokeWidth="2.2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {points.map((point, index) => {
          const compact = point.sample.event_type === "compaction";
          return (
            <circle
              key={`${point.sample.sample_id}:${point.phase}`}
              cx={x(point.time, index)}
              cy={y(point.tokens)}
              r={compact ? 4 : 3}
              fill={
                compact
                  ? point.phase === "before"
                    ? "#f59e0b"
                    : "#a855f7"
                  : "#38bdf8"
              }
              stroke="#07101d"
              strokeWidth="1.5"
            >
              <title>
                {formatClock(point.sample.created_at)} ·{" "}
                {compact
                  ? `压缩${point.phase === "before" ? "前" : "后"}`
                  : point.sample.event_type}{" "}
                · {point.tokens.toLocaleString()} tokens
              </title>
            </circle>
          );
        })}
        <text
          x={pad.left}
          y={height - 8}
          fill="#64748b"
          fontSize="9"
        >
          {formatClock(minTime)}
        </text>
        <text
          x={width - pad.right}
          y={height - 8}
          textAnchor="end"
          fill="#64748b"
          fontSize="9"
        >
          {formatClock(maxTime)}
        </text>
        <text
          x="10"
          y={pad.top + plotHeight / 2}
          transform={`rotate(-90 10 ${pad.top + plotHeight / 2})`}
          textAnchor="middle"
          fill="#64748b"
          fontSize="9"
        >
          tokens
        </text>
      </svg>
      <div style={chartLegendStyle}>
        <span><i style={{ ...legendDotStyle, background: "#38bdf8" }} />Context 大小</span>
        <span><i style={{ ...legendDotStyle, background: "#f59e0b" }} />压缩前</span>
        <span><i style={{ ...legendDotStyle, background: "#a855f7" }} />压缩后</span>
      </div>
    </div>
  );
}

const SECTION_COLOR: Record<string, string> = {
  system: "#3b82f6",
  memory: "#a855f7",
  tools: "#f59e0b",
  history: "#10b981",
  current: "#ec4899",
};

export function ContextBreakdownModal({
  open,
  onClose,
  sessionId,
  projectName,
  projectRoot,
  snapshot,
  send,
  onMessage,
}: ContextBreakdownModalProps) {
  const [data, setData] = useState<BreakdownResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // 2026-05-31 restore — parent commonly passes inline lambdas for send /
  // onMessage which change identity every render. If we depended on them in
  // our effect, each re-render (e.g. ws lastMessage tick) would unsubscribe
  // + resubscribe, racing with the response arrival. Pin the latest
  // callbacks in refs and only re-run the effect on open/sessionId changes.
  const sendRef = useRef(send);
  const onMessageRef = useRef(onMessage);
  const acceptedRef = useRef<{ version: number; fingerprint: string } | null>(null);
  useEffect(() => { sendRef.current = send; }, [send]);
  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);

  useEffect(() => {
    if (!open) {
      setData(null);
      setExpanded({});
      return;
    }
    setLoading(true);
    setData(null);
    acceptedRef.current = null;
    const requestId = newContextRequestId();
    const expectedSnapshotId = snapshot?.snapshot_id?.trim() || null;
    const expectedSampleId = snapshot?.sample_id?.trim() || null;
    const expectedVersion = Number.isInteger(snapshot?.version) && (snapshot?.version ?? -1) >= 0
      ? snapshot!.version!
      : null;
    let active = true;
    const off = onMessageRef.current((msg) => {
      if (msg?.type === "context_breakdown_response") {
        const p = normalizeBreakdownResponse(msg.payload);
        if (
          !active || !p || p.session_id !== sessionId ||
          p.correlation_id !== requestId ||
          p.snapshot_id !== expectedSnapshotId ||
          p.sample_id !== expectedSampleId ||
          expectedVersion === null || p.snapshot_version !== expectedVersion
        ) return;
        const fingerprint = canonicalJson(p);
        const accepted = acceptedRef.current;
        if (accepted) {
          if (p.snapshot_version < accepted.version) return;
          if (p.snapshot_version === accepted.version && accepted.fingerprint !== fingerprint) return;
        }
        acceptedRef.current = { version: p.snapshot_version, fingerprint };
        setData(p);
        setLoading(false);
      }
    });
    try {
      sendRef.current({
        type: "context_breakdown_request",
        payload: {
          session_id: sessionId,
          request_id: requestId,
          expected_snapshot_id: expectedSnapshotId,
          expected_sample_id: expectedSampleId,
          expected_snapshot_version: expectedVersion,
        },
      });
    } catch {
      setLoading(false);
    }
    return () => {
      active = false;
      off();
    };
  }, [open, sessionId, snapshot?.snapshot_id, snapshot?.sample_id, snapshot?.version]);

  const displayState = contextDisplayState(snapshot);
  const measured = displayState === "measured";
  const pct = ringPercent(snapshot);
  const color = ringColor(pct);
  const sectionTotal = useMemo(
    () => (data?.sections || []).reduce((a, s) => a + (s.tokens ?? 0), 0),
    [data],
  );
  const effectiveProjectName =
    projectName && projectName !== "(untitled)"
      ? projectName
      : data?.project_name || "当前项目";
  const effectiveProjectRoot = projectRoot || data?.project_root;

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Context usage breakdown"
      style={overlayStyle}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={modalStyle}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <h3
              title={`Context usage · ${sessionId}`}
              style={{
                margin: 0,
                fontSize: 14,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              Context usage · <span style={{ color: "#94a3b8", fontWeight: 400 }}>{sessionId}</span>
            </h3>
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
              {effectiveProjectName} · {snapshot?.model?.trim() || "模型信息不可用"}
            </div>
            {effectiveProjectRoot && (
              <div
                title={effectiveProjectRoot}
                style={{
                  maxWidth: 560,
                  marginTop: 3,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  color: "#64748b",
                  fontSize: 10,
                }}
              >
                {effectiveProjectRoot}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="关闭"
            style={{ ...closeBtn, flexShrink: 0 }}
          >
            ✕
          </button>
        </header>

        <div style={{ marginBottom: 12 }}>
          {!measured && (
            <div
              role="status"
              data-testid="context-availability"
              style={{ color: displayState === "binding_only" ? "#94a3b8" : "#f87171", fontSize: 11, marginBottom: 8 }}
            >
              {displayState === "binding_only"
                ? "仅有本 Session 模型绑定；尚无真实模型请求，Context 用量不可用"
                : displayState === "legacy_incomplete"
                  ? "旧 Context 记录不完整，用量不可用"
                  : "本 Session 尚无可信测量，Context 用量不可用"}
            </div>
          )}
          {measured && snapshot && (
            <>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
              <span><b style={{ color }}>{snapshot.prompt_tokens.toLocaleString()}</b> / {snapshot.effective_ceiling.toLocaleString()} tokens</span>
              <span style={{ color: "#94a3b8" }}>{pct.toFixed(1)}% measured</span>
            </div>
            <div style={{ position: "relative", height: 8, background: "#1f2937", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 240ms" }} />
              {snapshot.compact_at > 0 && snapshot.effective_ceiling > 0 && (
                <div title="compact 阈值" style={{
                  position: "absolute",
                  left: `${(snapshot.compact_at / snapshot.effective_ceiling) * 100}%`,
                  top: 0, bottom: 0, width: 2, background: "#f59e0b", opacity: 0.8,
                }} />
              )}
              {snapshot.recall_sweet > 0 && snapshot.effective_ceiling > 0 && (
                <div title="recall sweet spot" style={{
                  position: "absolute",
                  left: `${(snapshot.recall_sweet / snapshot.effective_ceiling) * 100}%`,
                  top: 0, bottom: 0, width: 1, background: "#fbbf24", opacity: 0.7,
                }} />
              )}
            </div>
            <div style={{ fontSize: 10, color: "#6b7280", marginTop: 4, display: "flex", gap: 12 }}>
              {snapshot.cached_tokens > 0 && <span>cache hit: {fmtTokens(snapshot.cached_tokens)}</span>}
              {snapshot.compact_at > 0 && <span>compact @ {fmtTokens(snapshot.compact_at)}</span>}
              {snapshot.recall_sweet > 0 && <span>sweet @ {fmtTokens(snapshot.recall_sweet)}</span>}
            </div>
            <div style={{ color: "#64748b", fontSize: 10, marginTop: 5 }}>
              实测 · sample {snapshot.sample_id || "不可用"} · snapshot {snapshot.snapshot_id || "不可用"}
            </div>
            </>
          )}
        </div>

        <div style={{ borderTop: "1px solid #374151", paddingTop: 12 }}>
          <div style={{ fontSize: 12, color: "#cbd5e1", marginBottom: 8 }}>
            Context 变化记录
          </div>
          <ContextHistoryChart history={data?.history ?? []} />
        </div>

        <div style={{ borderTop: "1px solid #374151", paddingTop: 12 }}>
          <div style={{ fontSize: 12, color: "#cbd5e1", marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span>冻结请求构成（按项标记 measured / estimated / unavailable）</span>
            {data && (
              <span style={{ color: "#94a3b8", fontSize: 10 }}>
                估算合计 {fmtTokens(sectionTotal)} · LLM 实测 {data.last_usage_prompt_tokens === null ? "不可用" : fmtTokens(data.last_usage_prompt_tokens)}
              </span>
            )}
          </div>

          {loading && (
            <div style={{ color: "#94a3b8", fontSize: 12, padding: 12, textAlign: "center" }}>
              加载 breakdown …
            </div>
          )}
          {!loading && !data && (
            <div style={{ color: "#94a3b8", fontSize: 12, padding: 12 }}>
              暂无数据
            </div>
          )}

          {data && (
            <>
              <div style={{ display: "flex", height: 10, borderRadius: 4, overflow: "hidden", marginBottom: 8, background: "#1f2937" }}>
                {data.sections.map((s, index) => {
                  const w = sectionTotal > 0 ? ((s.tokens ?? 0) / sectionTotal) * 100 : 0;
                  if (w <= 0) return null;
                  return (
                    <div
                      key={`${s.kind}:${index}`}
                      title={`${s.label}: ${s.tokens === null ? "不可用" : fmtTokens(s.tokens)} (${w.toFixed(1)}%)`}
                      style={{ width: `${w}%`, background: SECTION_COLOR[s.kind] || "#64748b" }}
                    />
                  );
                })}
              </div>

              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
                {data.sections.map((s, index) => (
                  <li key={`${s.kind}:${index}`} style={{
                    background: "#111827",
                    borderRadius: 4,
                    padding: "8px 10px",
                    border: "1px solid #1f2937",
                  }}>
                    <button
                      type="button"
                      onClick={() => setExpanded((e) => ({ ...e, [s.kind]: !e[s.kind] }))}
                      style={{
                        background: "transparent", border: "none", padding: 0, color: "inherit",
                        cursor: "pointer", width: "100%",
                        display: "flex", justifyContent: "space-between", alignItems: "center",
                      }}
                    >
                      <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                        <span style={{
                          display: "inline-block",
                          width: 8, height: 8, borderRadius: 2,
                          background: SECTION_COLOR[s.kind] || "#64748b",
                        }} />
                        <b>{s.label}</b>
                        {typeof s.count === "number" && (
                          <span style={{ color: "#94a3b8", fontSize: 11 }}>· {s.count} 项</span>
                        )}
                      </span>
                      <span style={{ color: "#94a3b8", fontSize: 11 }}>
                        {s.tokens === null ? "tokens 不可用" : `${fmtTokens(s.tokens)} tokens`} · {s.token_source} {expanded[s.kind] ? "▴" : "▾"}
                      </span>
                    </button>
                    {expanded[s.kind] && (
                      <div style={{ marginTop: 8 }}>
                        <pre style={{
                          margin: 0,
                          fontSize: 10.5,
                          color: "#cbd5e1",
                          background: "#0b1120",
                          padding: 8,
                          borderRadius: 3,
                          maxHeight: 180,
                          overflow: "auto",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                        }}>
                          {s.public_preview || "无公开预览"}
                          {s.preview_truncated ? "…" : ""}
                        </pre>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.55)",
  display: "grid",
  placeItems: "center",
  padding: 8,
  zIndex: 1250,
};

const modalStyle: React.CSSProperties = {
  background: "#0f172a",
  color: "#e2e8f0",
  boxSizing: "border-box",
  padding: 16,
  borderRadius: 8,
  width: "min(94vw, 720px)",
  maxHeight: "90vh",
  overflowY: "auto",
  overflowX: "hidden",
  border: "1px solid #1e293b",
  boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
  fontFamily: "inherit",
};

const chartWrapStyle: React.CSSProperties = {
  marginBottom: 12,
  padding: 8,
  border: "1px solid rgba(56,189,248,0.14)",
  borderRadius: 7,
  background: "rgba(2,6,23,0.54)",
};

const chartLegendStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "center",
  gap: 14,
  color: "#94a3b8",
  fontSize: 9.5,
};

const legendDotStyle: React.CSSProperties = {
  display: "inline-block",
  width: 7,
  height: 7,
  marginRight: 4,
  borderRadius: "50%",
};

const chartEmptyStyle: React.CSSProperties = {
  minHeight: 110,
  display: "grid",
  placeItems: "center",
  marginBottom: 12,
  border: "1px dashed rgba(148,163,184,0.2)",
  borderRadius: 7,
  color: "#64748b",
  fontSize: 11,
};

const closeBtn: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#94a3b8",
  cursor: "pointer",
  fontSize: 14,
  padding: 4,
};
