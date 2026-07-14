import { useMemo } from "react";
import { Icon } from "../Icon";
import { CheckpointTimeline } from "./CheckpointTimeline";
import { DecisionPanel } from "./DecisionPanel";
import { EvaluationPanel, type EvaluationInput } from "./EvaluationPanel";
import { RunList } from "./RunList";
import { TraceTree } from "./TraceTree";
import { WorkflowGraph } from "./WorkflowGraph";
import type {
  CheckpointView,
  EvaluationView,
  TraceSpanView,
  WorkflowDecisionView,
  WorkflowDeliveryView,
  WorkflowEdgeView,
  WorkflowNodeView,
  WorkflowRunSummary,
} from "./types";

export type InspectorView = "runs" | "trace" | "checkpoints" | "evaluations";

type Props = {
  view?: InspectorView;
  runs: WorkflowRunSummary[];
  nodes: WorkflowNodeView[];
  edges: WorkflowEdgeView[];
  spans: TraceSpanView[];
  checkpoints: CheckpointView[];
  evaluations: EvaluationView[];
  decisions?: WorkflowDecisionView[];
  deliveries?: WorkflowDeliveryView[];
  selectedRunId?: string | null;
  loading?: boolean;
  detailLoading?: boolean;
  error?: string | null;
  actionError?: string | null;
  actionFeedback?: string | null;
  pendingActionId?: string | null;
  onSelectRun: (id: string) => void;
  onRefresh: () => void;
  onFork?: (checkpointId: string) => void;
  onResolveDecision?: (decision: WorkflowDecisionView, response: unknown) => void;
  onReopenDecision?: (decision: WorkflowDecisionView) => void;
  onCancelDecision?: (decision: WorkflowDecisionView) => void;
  onDeliveryAction?: (delivery: WorkflowDeliveryView, action: "retry" | "discard") => void;
  onSubmitEvaluation?: (input: EvaluationInput) => void;
};

export function RunInspectorPanel(props: Props) {
  const view = props.view ?? "runs";
  const selected = useMemo(() => props.runs.find((run) => run.run_id === props.selectedRunId), [props.runs, props.selectedRunId]);
  const actionableDecision = props.decisions?.find((decision) => decision.status === "open" || decision.status === "expired") ?? null;

  return (
    <div data-testid="run-inspector" className="workflow-inspector-layout" style={layoutStyle}>
      <style>{responsiveCss}</style>
      <aside className="workflow-inspector-aside" style={asideStyle}>
        <div style={toolbarStyle}>
          <strong style={{ fontSize: 12 }}>运行</strong>
          <button type="button" aria-label="刷新运行记录" title="刷新" disabled={props.loading} onClick={props.onRefresh} style={iconButtonStyle}>
            <Icon name={props.loading ? "loader" : "refresh"} size={14} />
          </button>
        </div>
        {props.loading && props.runs.length === 0 ? <LoadingState label="正在加载运行记录" /> : <RunList runs={props.runs} selectedRunId={props.selectedRunId} onSelect={props.onSelectRun} />}
      </aside>
      <section style={mainStyle}>
        <div style={toolbarStyle}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12 }}>{selected?.workflow_name ?? viewLabel[view]}</strong>
            {selected && <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: statusColor(selected.status), fontSize: 10 }}>{selected.status} · {selected.run_id}</span>}
          </div>
          {selected?.next_retry_at && <span title="下一重试时间" style={{ flex: "0 0 auto", color: "#fbbf24", fontSize: 9 }}>{formatTime(selected.next_retry_at)}</span>}
        </div>

        {props.error && (
          <div role="alert" style={errorStyle}>
            <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{props.error}</span>
            <button type="button" aria-label="重试加载" title="重试" onClick={props.onRefresh} style={inlineIconStyle}><Icon name="refresh" size={13} /></button>
          </div>
        )}
        {props.actionError && <div role="alert" style={actionErrorStyle}>{props.actionError}</div>}
        {props.actionFeedback && <div role="status" style={actionSuccessStyle}>{props.actionFeedback}</div>}

        <div style={contentStyle}>
          {!selected && !props.loading ? <EmptyState label="选择一条运行记录查看详情" /> : props.detailLoading ? <LoadingState label="正在加载运行详情" /> : (
            <>
              {selected?.error != null && <div role="alert" style={runErrorStyle}>{displayError(selected.error)}</div>}
              {selected?.recovery_action && <div style={recoveryStyle}><Icon name="alert" size={13} /><span>恢复操作：{selected.recovery_action}</span></div>}
              {view === "runs" && (
                <>
                  <DecisionPanel
                    decision={actionableDecision}
                    submitting={Boolean(actionableDecision && props.pendingActionId === actionableDecision.decision_id)}
                    feedback={props.actionFeedback}
                    error={props.actionError}
                    onResolve={props.onResolveDecision ?? noDecisionAction}
                    onReopen={props.onReopenDecision ?? noDecisionAction}
                    onCancel={props.onCancelDecision ?? noDecisionAction}
                  />
                  <WorkflowGraph nodes={props.nodes} edges={props.edges} />
                  <NodeTimeline nodes={props.nodes} />
                  <DeliveryList deliveries={props.deliveries ?? []} pendingActionId={props.pendingActionId} onAction={props.onDeliveryAction} />
                </>
              )}
              {view === "trace" && <TraceTree spans={props.spans} />}
              {view === "checkpoints" && <CheckpointTimeline checkpoints={props.checkpoints} onFork={props.onFork} />}
              {view === "evaluations" && (
                <EvaluationPanel
                  evaluations={props.evaluations}
                  submitting={props.pendingActionId === "evaluation"}
                  error={props.actionError}
                  feedback={props.actionFeedback}
                  onSubmit={props.onSubmitEvaluation ?? (() => undefined)}
                />
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function NodeTimeline({ nodes }: { nodes: WorkflowNodeView[] }) {
  const notable = nodes.filter((node) => node.attempt || node.error || node.next_retry_at || node.recovery_action);
  if (notable.length === 0) return null;
  return (
    <section aria-label="节点尝试" style={bandStyle}>
      <div style={bandHeaderStyle}><strong>尝试与恢复</strong><span>{notable.length}</span></div>
      {notable.map((node) => (
        <div key={node.id} style={nodeRowStyle}>
          <span style={{ minWidth: 0, flex: 1 }}>
            <strong style={{ display: "block", overflowWrap: "anywhere", fontSize: 10 }}>{node.label} · #{node.attempt ?? 1}</strong>
            {node.error && <span style={{ display: "block", marginTop: 2, color: "#fca5a5", overflowWrap: "anywhere", fontSize: 9 }}>{node.error}</span>}
            {node.recovery_action && <span style={{ display: "block", marginTop: 2, color: "#fbbf24", overflowWrap: "anywhere", fontSize: 9 }}>{node.recovery_action}</span>}
          </span>
          {node.next_retry_at && <span style={{ color: "#94a3b8", fontSize: 9 }}>{formatTime(node.next_retry_at)}</span>}
        </div>
      ))}
    </section>
  );
}

function DeliveryList({ deliveries, pendingActionId, onAction }: { deliveries: WorkflowDeliveryView[]; pendingActionId?: string | null; onAction?: (delivery: WorkflowDeliveryView, action: "retry" | "discard") => void }) {
  const blocked = deliveries.filter((delivery) => delivery.status === "blocked" || delivery.status === "retry_wait" || delivery.status === "dead_letter");
  if (blocked.length === 0) return null;
  return (
    <section aria-label="待恢复交付" style={bandStyle}>
      <div style={bandHeaderStyle}><strong>待恢复交付</strong><span>{blocked.length}</span></div>
      {blocked.map((delivery) => {
        const pending = pendingActionId === delivery.delivery_id;
        return (
          <div key={delivery.delivery_id} style={deliveryRowStyle}>
            <span style={{ minWidth: 0, flex: 1 }}>
              <strong style={{ display: "block", overflowWrap: "anywhere", fontSize: 10 }}>{delivery.channel ?? "delivery"} · {delivery.status}</strong>
              {delivery.last_error && <span style={{ display: "block", marginTop: 2, color: "#fca5a5", overflowWrap: "anywhere", fontSize: 9 }}>{delivery.last_error}</span>}
              {delivery.next_attempt_at && <span style={{ display: "block", marginTop: 2, color: "#94a3b8", fontSize: 9 }}>下一次：{formatTime(delivery.next_attempt_at)}</span>}
            </span>
            {onAction && <span style={{ display: "flex", gap: 5 }}>
              <button type="button" aria-label="重试交付" title="重试" disabled={pending} onClick={() => onAction(delivery, "retry")} style={inlineIconStyle}><Icon name="refresh" size={12} /></button>
              <button type="button" aria-label="丢弃交付" title="丢弃" disabled={pending} onClick={() => onAction(delivery, "discard")} style={{ ...inlineIconStyle, color: "#fca5a5" }}><Icon name="trash" size={12} /></button>
            </span>}
          </div>
        );
      })}
    </section>
  );
}

function EmptyState({ label }: { label: string }) { return <div style={emptyStyle}>{label}</div>; }
function LoadingState({ label }: { label: string }) { return <div role="status" style={emptyStyle}><Icon name="loader" size={15} /><span>{label}</span></div>; }
function noDecisionAction() { /* Optional integration callback. */ }
function formatTime(value: number) { const millis = value < 10_000_000_000 ? value * 1000 : value; return new Date(millis).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function statusColor(status: string) { if (status === "completed") return "#34d399"; if (status === "failed" || status === "blocked") return "#f87171"; if (status === "waiting" || status === "retryable") return "#fbbf24"; return "#94a3b8"; }
function displayError(value: unknown) { if (typeof value === "string") return value; if (value && typeof value === "object" && "message" in value && typeof value.message === "string") return value.message; try { return JSON.stringify(value); } catch { return "运行失败"; } }

const viewLabel: Record<InspectorView, string> = { runs: "运行详情", trace: "Trace", checkpoints: "Checkpoints", evaluations: "Evaluations" };
const responsiveCss = `
  @media (max-width: 560px) {
    .workflow-inspector-layout { grid-template-columns: minmax(0, 1fr) !important; grid-template-rows: minmax(112px, 34%) minmax(0, 1fr) !important; }
    .workflow-inspector-aside { border-right: 0 !important; border-bottom: 1px solid rgba(148,163,184,0.14); }
  }
`;
const layoutStyle: React.CSSProperties = { minHeight: 0, height: "100%", display: "grid", gridTemplateColumns: "minmax(150px,34%) minmax(0,1fr)", background: "#0b1120", color: "#e5e7eb" };
const asideStyle: React.CSSProperties = { minWidth: 0, minHeight: 0, overflow: "auto", borderRight: "1px solid rgba(148,163,184,0.14)" };
const mainStyle: React.CSSProperties = { minWidth: 0, minHeight: 0, display: "grid", gridTemplateRows: "auto auto minmax(0,1fr)" };
const toolbarStyle: React.CSSProperties = { minHeight: 44, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "7px 10px", borderBottom: "1px solid rgba(148,163,184,0.14)" };
const contentStyle: React.CSSProperties = { minHeight: 0, overflow: "auto" };
const iconButtonStyle: React.CSSProperties = { width: 30, height: 30, display: "grid", placeItems: "center", borderRadius: 6, border: "1px solid rgba(148,163,184,0.2)", background: "rgba(15,23,42,0.7)", color: "#cbd5e1", cursor: "pointer" };
const inlineIconStyle: React.CSSProperties = { width: 28, height: 28, display: "grid", placeItems: "center", flex: "0 0 auto", borderRadius: 5, border: "1px solid rgba(148,163,184,0.2)", background: "rgba(15,23,42,0.7)", color: "#bae6fd", cursor: "pointer" };
const errorStyle: React.CSSProperties = { minHeight: 38, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "6px 10px", borderBottom: "1px solid rgba(248,113,113,0.25)", color: "#fca5a5", background: "rgba(127,29,29,0.12)", fontSize: 10 };
const actionErrorStyle: React.CSSProperties = { padding: "7px 10px", borderBottom: "1px solid rgba(248,113,113,0.25)", color: "#fca5a5", background: "rgba(127,29,29,0.12)", fontSize: 10, overflowWrap: "anywhere" };
const actionSuccessStyle: React.CSSProperties = { padding: "7px 10px", borderBottom: "1px solid rgba(52,211,153,0.22)", color: "#86efac", background: "rgba(20,83,45,0.12)", fontSize: 10, overflowWrap: "anywhere" };
const runErrorStyle: React.CSSProperties = { padding: 8, borderBottom: "1px solid rgba(248,113,113,0.25)", color: "#fca5a5", background: "rgba(127,29,29,0.12)", fontSize: 10, overflowWrap: "anywhere" };
const recoveryStyle: React.CSSProperties = { minHeight: 34, display: "flex", alignItems: "center", gap: 7, padding: "6px 10px", borderBottom: "1px solid rgba(245,158,11,0.22)", color: "#fde68a", background: "rgba(120,53,15,0.1)", fontSize: 10, overflowWrap: "anywhere" };
const bandStyle: React.CSSProperties = { borderTop: "1px solid rgba(148,163,184,0.14)" };
const bandHeaderStyle: React.CSSProperties = { minHeight: 34, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 10px", color: "#94a3b8", fontSize: 10 };
const nodeRowStyle: React.CSSProperties = { minHeight: 39, display: "flex", alignItems: "flex-start", gap: 8, padding: "6px 10px", borderTop: "1px solid rgba(148,163,184,0.08)" };
const deliveryRowStyle: React.CSSProperties = { minHeight: 48, display: "flex", alignItems: "center", gap: 8, padding: "7px 10px", borderTop: "1px solid rgba(148,163,184,0.08)" };
const emptyStyle: React.CSSProperties = { minHeight: 92, display: "flex", alignItems: "center", justifyContent: "center", gap: 7, padding: 20, textAlign: "center", color: "#64748b", fontSize: 12 };
