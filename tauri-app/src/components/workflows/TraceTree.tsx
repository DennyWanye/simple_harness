import type { TraceSpanView } from "./types";

export function TraceTree({ spans }: { spans: TraceSpanView[] }) {
  const children = new Map<string | null, TraceSpanView[]>();
  spans.forEach((span) => {
    const parent = span.parent_span_id ?? null;
    children.set(parent, [...(children.get(parent) ?? []), span]);
  });
  if (spans.length === 0) return <div style={emptyStyle}>暂无 Trace</div>;
  const roots = children.get(null) ?? spans.filter((span) => !spans.some((item) => item.span_id === span.parent_span_id));
  return <div role="tree" aria-label="Trace spans">{roots.map((span) => <Branch key={span.span_id} span={span} children={children} depth={0} />)}</div>;
}

function Branch({ span, children, depth }: { span: TraceSpanView; children: Map<string | null, TraceSpanView[]>; depth: number }) {
  const hasDetail = Boolean(span.error || span.input_summary || span.output_summary || span.redacted);
  return (
    <div role="treeitem" aria-level={depth + 1} style={{ marginLeft: Math.min(depth, 5) * 14 }}>
      <div style={rowStyle}>
        <span style={{ color: span.status === "error" ? "#f87171" : "#38bdf8", fontSize: 10 }}>{span.kind}</span>
        <strong style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11 }}>{span.name}</strong>
        <span style={{ color: "#94a3b8", fontSize: 10 }}>{span.duration_ms == null ? "…" : `${Math.round(span.duration_ms)} ms`}</span>
      </div>
      {hasDetail && (
        <div style={detailStyle}>
          {span.redacted && <span style={{ color: "#fbbf24" }}>敏感详情已脱敏</span>}
          {span.input_summary && <span><strong>输入</strong> {span.input_summary}</span>}
          {span.output_summary && <span><strong>输出</strong> {span.output_summary}</span>}
          {span.error && <span style={{ color: "#fca5a5" }}>{span.error}</span>}
        </div>
      )}
      {(children.get(span.span_id) ?? []).map((child) => <Branch key={child.span_id} span={child} children={children} depth={depth + 1} />)}
    </div>
  );
}

const rowStyle: React.CSSProperties = { minHeight: 34, display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid rgba(148,163,184,0.1)", color: "#e5e7eb" };
const detailStyle: React.CSSProperties = { display: "grid", gap: 3, marginLeft: 8, padding: "4px 7px 7px", color: "#94a3b8", fontSize: 10, overflowWrap: "anywhere" };
const emptyStyle: React.CSSProperties = { padding: 20, textAlign: "center", color: "#64748b", fontSize: 12 };
