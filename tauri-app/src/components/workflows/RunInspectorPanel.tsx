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
import "./RunInspectorPanel.css";
import "./RunInspectorPanel.styles.css";

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
  const selected = useMemo(
    () => props.runs.find((run) => run.run_id === props.selectedRunId),
    [props.runs, props.selectedRunId],
  );
  const actionableDecision =
    props.decisions?.find(
      (decision) => decision.status === "open" || decision.status === "expired",
    ) ?? null;

  return (
    <div data-testid="run-inspector" className="workflow-inspector-layout sh-RunInspectorPanel-1">
      <aside className="workflow-inspector-aside sh-RunInspectorPanel-2">
        <div className="sh-RunInspectorPanel-3">
          <strong className="sh-RunInspectorPanel-4">运行</strong>
          <button
            type="button"
            aria-label="刷新运行记录"
            title="刷新"
            disabled={props.loading}
            onClick={props.onRefresh}
            className="sh-RunInspectorPanel-5"
          >
            <Icon name={props.loading ? "loader" : "refresh"} size={14} />
          </button>
        </div>
        {props.loading && props.runs.length === 0 ? (
          <LoadingState label="正在加载运行记录" />
        ) : (
          <RunList
            runs={props.runs}
            selectedRunId={props.selectedRunId}
            onSelect={props.onSelectRun}
          />
        )}
      </aside>
      <section className="sh-RunInspectorPanel-6">
        <div className="sh-RunInspectorPanel-3">
          <div className="sh-RunInspectorPanel-7">
            <strong className="sh-RunInspectorPanel-8">
              {selected?.workflow_name ?? viewLabel[view]}
            </strong>
            {selected && (
              <span
                style={{
                  display: "block",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  color: statusColor(selected.status),
                  fontSize: 10,
                }}
              >
                {selected.status} · {selected.run_id}
              </span>
            )}
          </div>
          {selected?.next_retry_at && (
            <span title="下一重试时间" className="sh-RunInspectorPanel-9">
              {formatTime(selected.next_retry_at)}
            </span>
          )}
        </div>

        {props.error && (
          <div role="alert" className="sh-RunInspectorPanel-10">
            <span className="sh-RunInspectorPanel-11">{props.error}</span>
            <button
              type="button"
              aria-label="重试加载"
              title="重试"
              onClick={props.onRefresh}
              className="sh-RunInspectorPanel-12"
            >
              <Icon name="refresh" size={13} />
            </button>
          </div>
        )}
        {props.actionError && (
          <div role="alert" className="sh-RunInspectorPanel-13">
            {props.actionError}
          </div>
        )}
        {props.actionFeedback && (
          <div role="status" className="sh-RunInspectorPanel-14">
            {props.actionFeedback}
          </div>
        )}

        <div className="sh-RunInspectorPanel-15">
          {!selected && !props.loading ? (
            <EmptyState label="选择一条运行记录查看详情" />
          ) : props.detailLoading ? (
            <LoadingState label="正在加载运行详情" />
          ) : (
            <>
              {selected?.error != null && (
                <div role="alert" className="sh-RunInspectorPanel-16">
                  {displayError(selected.error)}
                </div>
              )}
              {selected?.recovery_action && (
                <div className="sh-RunInspectorPanel-17">
                  <Icon name="alert" size={13} />
                  <span>恢复操作：{selected.recovery_action}</span>
                </div>
              )}
              {view === "runs" && (
                <>
                  <DecisionPanel
                    decision={actionableDecision}
                    submitting={Boolean(
                      actionableDecision &&
                        props.pendingActionId === actionableDecision.decision_id,
                    )}
                    feedback={props.actionFeedback}
                    error={props.actionError}
                    onResolve={props.onResolveDecision ?? noDecisionAction}
                    onReopen={props.onReopenDecision ?? noDecisionAction}
                    onCancel={props.onCancelDecision ?? noDecisionAction}
                  />
                  <WorkflowGraph nodes={props.nodes} edges={props.edges} />
                  <NodeTimeline nodes={props.nodes} />
                  <DeliveryList
                    deliveries={props.deliveries ?? []}
                    pendingActionId={props.pendingActionId}
                    onAction={props.onDeliveryAction}
                  />
                </>
              )}
              {view === "trace" && <TraceTree spans={props.spans} />}
              {view === "checkpoints" && (
                <CheckpointTimeline checkpoints={props.checkpoints} onFork={props.onFork} />
              )}
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
  const notable = nodes.filter(
    (node) => node.attempt || node.error || node.next_retry_at || node.recovery_action,
  );
  if (notable.length === 0) return null;
  return (
    <section aria-label="节点尝试" className="sh-RunInspectorPanel-18">
      <div className="sh-RunInspectorPanel-19">
        <strong>尝试与恢复</strong>
        <span>{notable.length}</span>
      </div>
      {notable.map((node) => (
        <div key={node.id} className="sh-RunInspectorPanel-20">
          <span className="sh-RunInspectorPanel-7">
            <strong className="sh-RunInspectorPanel-21">
              {node.label} · #{node.attempt ?? 1}
            </strong>
            {node.error && <span className="sh-RunInspectorPanel-22">{node.error}</span>}
            {node.recovery_action && (
              <span className="sh-RunInspectorPanel-23">{node.recovery_action}</span>
            )}
          </span>
          {node.next_retry_at && (
            <span className="sh-RunInspectorPanel-24">{formatTime(node.next_retry_at)}</span>
          )}
        </div>
      ))}
    </section>
  );
}

function DeliveryList({
  deliveries,
  pendingActionId,
  onAction,
}: {
  deliveries: WorkflowDeliveryView[];
  pendingActionId?: string | null;
  onAction?: (delivery: WorkflowDeliveryView, action: "retry" | "discard") => void;
}) {
  const blocked = deliveries.filter(
    (delivery) =>
      delivery.status === "blocked" ||
      delivery.status === "retry_wait" ||
      delivery.status === "dead_letter",
  );
  if (blocked.length === 0) return null;
  return (
    <section aria-label="待恢复交付" className="sh-RunInspectorPanel-18">
      <div className="sh-RunInspectorPanel-19">
        <strong>待恢复交付</strong>
        <span>{blocked.length}</span>
      </div>
      {blocked.map((delivery) => {
        const pending = pendingActionId === delivery.delivery_id;
        return (
          <div key={delivery.delivery_id} className="sh-RunInspectorPanel-25">
            <span className="sh-RunInspectorPanel-7">
              <strong className="sh-RunInspectorPanel-21">
                {delivery.channel ?? "delivery"} · {delivery.status}
              </strong>
              {delivery.last_error && (
                <span className="sh-RunInspectorPanel-22">{delivery.last_error}</span>
              )}
              {delivery.next_attempt_at && (
                <span className="sh-RunInspectorPanel-26">
                  下一次：{formatTime(delivery.next_attempt_at)}
                </span>
              )}
            </span>
            {onAction && (
              <span className="sh-RunInspectorPanel-27">
                <button
                  type="button"
                  aria-label="重试交付"
                  title="重试"
                  disabled={pending}
                  onClick={() => onAction(delivery, "retry")}
                  className="sh-RunInspectorPanel-12"
                >
                  <Icon name="refresh" size={12} />
                </button>
                <button
                  type="button"
                  aria-label="丢弃交付"
                  title="丢弃"
                  disabled={pending}
                  onClick={() => onAction(delivery, "discard")}
                  className="sh-RunInspectorPanel-28"
                >
                  <Icon name="trash" size={12} />
                </button>
              </span>
            )}
          </div>
        );
      })}
    </section>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="sh-RunInspectorPanel-29">{label}</div>;
}
function LoadingState({ label }: { label: string }) {
  return (
    <div role="status" className="sh-RunInspectorPanel-29">
      <Icon name="loader" size={15} />
      <span>{label}</span>
    </div>
  );
}
function noDecisionAction() {
  /* Optional integration callback. */
}
function formatTime(value: number) {
  const millis = value < 10_000_000_000 ? value * 1000 : value;
  return new Date(millis).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
function statusColor(status: string) {
  if (status === "completed") return "#34d399";
  if (status === "failed" || status === "blocked") return "#f87171";
  if (status === "waiting" || status === "retryable") return "#fbbf24";
  return "#94a3b8";
}
function displayError(value: unknown) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "message" in value && typeof value.message === "string")
    return value.message;
  try {
    return JSON.stringify(value);
  } catch {
    return "运行失败";
  }
}

const viewLabel: Record<InspectorView, string> = {
  runs: "运行详情",
  trace: "Trace",
  checkpoints: "Checkpoints",
  evaluations: "Evaluations",
};
