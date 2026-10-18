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
 * 数据由调用方传入的 `query()` 读取（主对话发 `primary.context.breakdown`，
 * 取最近一轮运行的公开快照明细）；每次打开或用量样本换代时重读一次。
 */
import { useEffect, useMemo, useRef, useState } from "react";

import type { ContextUsageSnapshot } from "../stores/sessionsStore";
import { contextDisplayState } from "../context/contextAuthority";
import { ringColor, ringPercent } from "./contextRingMath";
import { useDepsChanged } from "../hooks/useDepsChanged";
import "./ContextBreakdownModal.styles.css";

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
  snapshot_id: string | null;
  sample_id: string | null;
  snapshot_version: number | null;
  usage_version: number | null;
  snapshot_fingerprint: string;
  availability: "available" | "unavailable";
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
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
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
  if (!("snapshot_version" in raw) || !("usage_version" in raw)) return null;
  const sessionId = optionalIdentity(raw.session_id);
  const snapshotId = optionalIdentity(raw.snapshot_id);
  const sampleId = optionalIdentity(raw.sample_id);
  const version = raw.snapshot_version === null ? null : finiteNonNegative(raw.snapshot_version);
  const usageVersion = raw.usage_version === null ? null : finiteNonNegative(raw.usage_version);
  if (
    !sessionId ||
    snapshotId === undefined ||
    sampleId === undefined ||
    version === undefined ||
    (version !== null && !Number.isInteger(version)) ||
    usageVersion === undefined ||
    (usageVersion !== null && !Number.isInteger(usageVersion)) ||
    !Array.isArray(raw.sections)
  )
    return null;

  const sections: BreakdownSection[] = [];
  for (const item of raw.sections) {
    if (!item || typeof item !== "object") return null;
    const section = item as Record<string, unknown>;
    const kind = optionalIdentity(section.kind);
    const label = optionalIdentity(section.label);
    if (!kind || !label) return null;
    const tokenSource = section.token_source;
    if (
      tokenSource !== "measured" &&
      tokenSource !== "estimated" &&
      tokenSource !== "unavailable"
    ) {
      return null;
    }
    const tokens = finiteNonNegative(section.tokens);
    if (tokenSource !== "unavailable" && tokens === null) return null;
    const publicPreview =
      typeof section.public_preview === "string"
        ? section.public_preview.slice(0, PUBLIC_PREVIEW_LIMIT)
        : null;
    sections.push({
      kind,
      label,
      tokens,
      token_source: tokenSource,
      availability:
        section.availability === "unavailable" || tokenSource === "unavailable"
          ? "unavailable"
          : "available",
      public_preview: publicPreview,
      preview_truncated:
        section.preview_truncated === true ||
        (typeof section.public_preview === "string" &&
          section.public_preview.length > PUBLIC_PREVIEW_LIMIT),
      count: finiteNonNegative(section.count) ?? undefined,
    });
  }

  const history = Array.isArray(raw.history)
    ? raw.history.filter((sample): sample is ContextHistorySample =>
        Boolean(
          sample &&
            typeof sample === "object" &&
            optionalIdentity((sample as Record<string, unknown>).sample_id) &&
            optionalIdentity((sample as Record<string, unknown>).session_id) === sessionId &&
            finiteNonNegative((sample as Record<string, unknown>).tokens_after) !== null &&
            finiteNonNegative((sample as Record<string, unknown>).created_at) !== null,
        ),
      )
    : [];

  return {
    session_id: sessionId,
    snapshot_id: snapshotId,
    sample_id: sampleId,
    snapshot_version: version,
    usage_version: usageVersion,
    snapshot_fingerprint:
      typeof raw.snapshot_fingerprint === "string" ? raw.snapshot_fingerprint.slice(0, 160) : "",
    availability: raw.availability === "available" ? "available" : "unavailable",
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

interface ContextBreakdownModalProps {
  open: boolean;
  onClose(): void;
  /** 当前用量（环上那份），等明细回来前先画顶部进度条。 */
  snapshot: ContextUsageSnapshot | null | undefined;
  /** 读一次明细；没有运行或读不到时给 null，失败时抛错。 */
  query(): Promise<unknown>;
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

function ContextHistoryChart({ history }: { history: ContextHistorySample[] }) {
  const points = history
    .filter(
      (sample) =>
        sample.source === "measured" ||
        sample.source === "compacted" ||
        sample.event_type === "provider_attempt" ||
        sample.event_type === "compaction",
    )
    .flatMap((sample) => {
      const after = {
        time: sample.created_at,
        tokens: sample.tokens_after,
        sample,
        phase: "after",
      };
      return sample.event_type === "compaction" && sample.tokens_before != null
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
    return <div className="sh-ContextBreakdownModal-1">暂无 Context 变化记录</div>;
  }
  const width = 640;
  const height = 190;
  const pad = { left: 46, right: 16, top: 16, bottom: 30 };
  const minTime = Math.min(...points.map((point) => point.time));
  const maxTime = Math.max(...points.map((point) => point.time));
  const maxObservedTokens = Math.max(1, ...points.map((point) => point.tokens));
  const maxTokens = Math.ceil(maxObservedTokens * 1.1);
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const x = (time: number, index: number) =>
    pad.left +
    (maxTime === minTime
      ? (index / Math.max(1, points.length - 1)) * plotWidth
      : ((time - minTime) / (maxTime - minTime)) * plotWidth);
  const y = (tokens: number) => pad.top + plotHeight - (tokens / maxTokens) * plotHeight;
  const line = points.map((point, index) => `${x(point.time, index)},${y(point.tokens)}`).join(" ");
  return (
    <div className="sh-ContextBreakdownModal-2">
      <svg
        role="img"
        aria-label="Context 大小随时间变化折线图"
        viewBox={`0 0 ${width} ${height}`}
        className="sh-ContextBreakdownModal-3"
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
              fill={compact ? (point.phase === "before" ? "#f59e0b" : "#a855f7") : "#38bdf8"}
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
        <text x={pad.left} y={height - 8} fill="#64748b" fontSize="9">
          {formatClock(minTime)}
        </text>
        <text x={width - pad.right} y={height - 8} textAnchor="end" fill="#64748b" fontSize="9">
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
      <div className="sh-ContextBreakdownModal-4">
        <span>
          <i className="sh-ContextBreakdownModal-5" />
          Context 大小
        </span>
        <span>
          <i className="sh-ContextBreakdownModal-6" />
          压缩前
        </span>
        <span>
          <i className="sh-ContextBreakdownModal-7" />
          压缩后
        </span>
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
  snapshot,
  query,
}: ContextBreakdownModalProps) {
  const [data, setData] = useState<BreakdownResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // query 由调用方每次渲染新建；只认最新一份，按打开与样本换代重读。
  const queryRef = useRef(query);
  useEffect(() => {
    queryRef.current = query;
  }, [query]);

  // 新样本到了就重读一次，明细跟环上的用量同代。
  const sampleId = snapshot?.sample_id?.trim() || null;
  if (useDepsChanged([open, sampleId], { onMount: true })) {
    setData(null);
    if (open) setLoading(true);
    else setExpanded({});
  }

  useEffect(() => {
    if (!open) return;
    let active = true;
    queryRef
      .current()
      .then((raw) => {
        if (active) setData(normalizeBreakdownResponse(raw));
      })
      .catch((error) => {
        console.warn("[context-breakdown] read failed", error);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, sampleId]);

  const displayState = contextDisplayState(snapshot);
  const measured = displayState === "measured";
  const pct = ringPercent(snapshot);
  const color = ringColor(pct);
  const sectionTotal = useMemo(
    () => (data?.sections || []).reduce((a, s) => a + (s.tokens ?? 0), 0),
    [data],
  );

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Context usage breakdown"
      className="sh-ContextBreakdownModal-8"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="sh-ContextBreakdownModal-9">
        <header className="sh-ContextBreakdownModal-10">
          <div className="sh-ContextBreakdownModal-11">
            <h3 className="sh-ContextBreakdownModal-12">上下文用量</h3>
            <div className="sh-ContextBreakdownModal-14">
              {snapshot?.model?.trim() || data?.model || "模型信息不可用"}
            </div>
          </div>
          <button onClick={onClose} aria-label="关闭" className="sh-ContextBreakdownModal-16">
            ✕
          </button>
        </header>

        <div className="sh-ContextBreakdownModal-17">
          {!measured && (
            <div
              role="status"
              data-testid="context-availability"
              style={{
                color: displayState === "binding_only" ? "#94a3b8" : "#f87171",
                fontSize: 11,
                marginBottom: 8,
              }}
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
              <div className="sh-ContextBreakdownModal-18">
                <span>
                  <b style={{ color }}>{snapshot.prompt_tokens.toLocaleString()}</b> /{" "}
                  {snapshot.effective_ceiling.toLocaleString()} tokens
                </span>
                <span className="sh-ContextBreakdownModal-19">{pct.toFixed(1)}% measured</span>
              </div>
              <div className="sh-ContextBreakdownModal-20">
                <div
                  style={{
                    width: `${pct}%`,
                    height: "100%",
                    background: color,
                    transition: "width 240ms",
                  }}
                />
                {snapshot.compact_at > 0 && snapshot.effective_ceiling > 0 && (
                  <div
                    title="compact 阈值"
                    style={{
                      position: "absolute",
                      left: `${(snapshot.compact_at / snapshot.effective_ceiling) * 100}%`,
                      top: 0,
                      bottom: 0,
                      width: 2,
                      background: "#f59e0b",
                      opacity: 0.8,
                    }}
                  />
                )}
                {snapshot.recall_sweet > 0 && snapshot.effective_ceiling > 0 && (
                  <div
                    title="recall sweet spot"
                    style={{
                      position: "absolute",
                      left: `${(snapshot.recall_sweet / snapshot.effective_ceiling) * 100}%`,
                      top: 0,
                      bottom: 0,
                      width: 1,
                      background: "#fbbf24",
                      opacity: 0.7,
                    }}
                  />
                )}
              </div>
              <div className="sh-ContextBreakdownModal-21">
                {snapshot.cached_tokens > 0 && (
                  <span>cache hit: {fmtTokens(snapshot.cached_tokens)}</span>
                )}
                {snapshot.compact_at > 0 && <span>compact @ {fmtTokens(snapshot.compact_at)}</span>}
                {snapshot.recall_sweet > 0 && (
                  <span>sweet @ {fmtTokens(snapshot.recall_sweet)}</span>
                )}
              </div>
              <div className="sh-ContextBreakdownModal-22">
                实测 · sample {snapshot.sample_id || "不可用"} · snapshot{" "}
                {snapshot.snapshot_id || "不可用"}
              </div>
            </>
          )}
        </div>

        <div className="sh-ContextBreakdownModal-23">
          <div className="sh-ContextBreakdownModal-24">Context 变化记录</div>
          <ContextHistoryChart history={data?.history ?? []} />
        </div>

        <div className="sh-ContextBreakdownModal-23">
          <div className="sh-ContextBreakdownModal-25">
            <span>冻结请求构成（按项标记 measured / estimated / unavailable）</span>
            {data && (
              <span className="sh-ContextBreakdownModal-26">
                {data.availability === "available"
                  ? `估算合计 ${fmtTokens(sectionTotal)} · LLM 实测 ${data.last_usage_prompt_tokens === null ? "不可用" : fmtTokens(data.last_usage_prompt_tokens)}`
                  : "冻结请求构成不可用"}
              </span>
            )}
          </div>

          {loading && <div className="sh-ContextBreakdownModal-27">加载 breakdown …</div>}
          {!loading && !data && <div className="sh-ContextBreakdownModal-28">暂无数据</div>}

          {data && (
            <>
              <div className="sh-ContextBreakdownModal-29">
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

              <ul className="sh-ContextBreakdownModal-30">
                {data.sections.map((s, index) => (
                  <li key={`${s.kind}:${index}`} className="sh-ContextBreakdownModal-31">
                    <button
                      type="button"
                      onClick={() => setExpanded((e) => ({ ...e, [s.kind]: !e[s.kind] }))}
                      className="sh-ContextBreakdownModal-32"
                    >
                      <span className="sh-ContextBreakdownModal-33">
                        <span
                          style={{
                            display: "inline-block",
                            width: 8,
                            height: 8,
                            borderRadius: 2,
                            background: SECTION_COLOR[s.kind] || "#64748b",
                          }}
                        />
                        <b>{s.label}</b>
                        {typeof s.count === "number" && (
                          <span className="sh-ContextBreakdownModal-34">· {s.count} 项</span>
                        )}
                      </span>
                      <span className="sh-ContextBreakdownModal-34">
                        {s.tokens === null ? "tokens 不可用" : `${fmtTokens(s.tokens)} tokens`} ·{" "}
                        {s.token_source} {expanded[s.kind] ? "▴" : "▾"}
                      </span>
                    </button>
                    {expanded[s.kind] && (
                      <div className="sh-ContextBreakdownModal-35">
                        <pre className="sh-ContextBreakdownModal-36">
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
