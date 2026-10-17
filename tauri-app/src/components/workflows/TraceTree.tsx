import type { TraceSpanView } from "./types";
import "./TraceTree.styles.css";

export function TraceTree({ spans }: { spans: TraceSpanView[] }) {
  const children = new Map<string | null, TraceSpanView[]>();
  spans.forEach((span) => {
    const parent = span.parent_span_id ?? null;
    children.set(parent, [...(children.get(parent) ?? []), span]);
  });
  if (spans.length === 0) return <div className="sh-TraceTree-1">暂无 Trace</div>;
  const roots =
    children.get(null) ??
    spans.filter((span) => !spans.some((item) => item.span_id === span.parent_span_id));
  return (
    <div role="tree" aria-label="Trace spans">
      {roots.map((span) => (
        <Branch key={span.span_id} span={span} children={children} depth={0} />
      ))}
    </div>
  );
}

function Branch({
  span,
  children,
  depth,
}: {
  span: TraceSpanView;
  children: Map<string | null, TraceSpanView[]>;
  depth: number;
}) {
  const hasDetail = Boolean(
    span.error || span.input_summary || span.output_summary || span.redacted,
  );
  return (
    <div role="treeitem" aria-level={depth + 1} style={{ marginLeft: Math.min(depth, 5) * 14 }}>
      <div className="sh-TraceTree-2">
        <span style={{ color: span.status === "error" ? "#f87171" : "#38bdf8", fontSize: 10 }}>
          {span.kind}
        </span>
        <strong className="sh-TraceTree-3">{span.name}</strong>
        <span className="sh-TraceTree-4">
          {span.duration_ms == null ? "…" : `${Math.round(span.duration_ms)} ms`}
        </span>
      </div>
      {hasDetail && (
        <div className="sh-TraceTree-5">
          {span.redacted && <span className="sh-TraceTree-6">敏感详情已脱敏</span>}
          {span.input_summary && (
            <span>
              <strong>输入</strong> {span.input_summary}
            </span>
          )}
          {span.output_summary && (
            <span>
              <strong>输出</strong> {span.output_summary}
            </span>
          )}
          {span.error && <span className="sh-TraceTree-7">{span.error}</span>}
        </div>
      )}
      {(children.get(span.span_id) ?? []).map((child) => (
        <Branch key={child.span_id} span={child} children={children} depth={depth + 1} />
      ))}
    </div>
  );
}
