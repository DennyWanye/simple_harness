// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";
import { sendWhenConnected } from "../ws/sendWhenConnected";
import { Icon } from "./Icon";
import { ReplayDialog, type ReplayForkInput } from "./workflows/ReplayDialog";
import { RunInspectorPanel, type InspectorView } from "./workflows/RunInspectorPanel";
import type { EvaluationInput } from "./workflows/EvaluationPanel";
import type {
  CheckpointView,
  EvaluationView,
  TraceSpanView,
  WorkflowDecisionView,
  WorkflowDeliveryView,
  WorkflowEdgeView,
  WorkflowNodeView,
  WorkflowRunSummary,
} from "./workflows/types";
import { dark } from "../theme/components";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  DecisionRecord,
  DecisionsListResponse,
  IncomingMessage,
  WorkflowCheckpointForkResponse,
  WorkflowDecisionResolveResponse,
  WorkflowDeliveryMutationResponse,
  WorkflowEvaluationSubmitResponse,
  WorkflowIPCErrorResponse,
  WorkflowRunDetailResponse,
  WorkflowRunsListResponse,
} from "../types/messages";
import "./ContextTracePanel.styles.css";

type Props = {
  open: boolean;
  onClose: () => void;
  getChannel: () => ControlChannel | null;
};

type PanelTab = InspectorView | "context";
type DetailState = {
  runId: string | null;
  runVersion?: number;
  nodes: WorkflowNodeView[];
  edges: WorkflowEdgeView[];
  spans: TraceSpanView[];
  checkpoints: CheckpointView[];
  evaluations: EvaluationView[];
  decisions: WorkflowDecisionView[];
  deliveries: WorkflowDeliveryView[];
};
type PendingAction = {
  kind: "fork" | "decision" | "delivery" | "evaluation";
  id: string;
  requestId: string;
};

const EMPTY_DETAIL: DetailState = {
  runId: null,
  nodes: [],
  edges: [],
  spans: [],
  checkpoints: [],
  evaluations: [],
  decisions: [],
  deliveries: [],
};
const tabLabel: Record<PanelTab, string> = {
  runs: "Runs",
  trace: "Trace",
  checkpoints: "Checkpoints",
  evaluations: "Evaluations",
  context: "Context",
};
let requestSequence = 0;

export function ContextTracePanel({ open, onClose, getChannel }: Props) {
  const [tab, setTab] = useState<PanelTab>("runs");
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailState>(EMPTY_DETAIL);
  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [forkCheckpoint, setForkCheckpoint] = useState<CheckpointView | null>(null);
  const listRequestRef = useRef<string | null>(null);
  const detailRequestRef = useRef<string | null>(null);
  const selectedRunRef = useRef<string | null>(null);
  const pendingRef = useRef<PendingAction | null>(null);

  useEffect(() => {
    selectedRunRef.current = selectedRunId;
  }, [selectedRunId]);

  // 只记请求号并发出，不写状态（effect 与下面的 refresh* 共用）。
  const sendRunsList = useCallback(() => {
    const channel = getChannel();
    if (!channel) return false;
    const requestId = nextRequestId("runs");
    listRequestRef.current = requestId;
    return channel.send({
      type: "workflow_runs_list",
      request_id: requestId,
      payload: { limit: 100 },
    });
  }, [getChannel]);
  const sendRunDetail = useCallback(
    (target: string) => {
      const channel = getChannel();
      if (!channel) return false;
      const requestId = nextRequestId("detail");
      detailRequestRef.current = requestId;
      return channel.send({
        type: "workflow_run_detail",
        request_id: requestId,
        payload: { run_id: target },
      });
    },
    [getChannel],
  );

  const refreshRuns = useCallback(() => {
    const hasChannel = getChannel() !== null;
    setListLoading(hasChannel);
    setLoadError(hasChannel ? null : "控制通道尚未连接");
    sendRunsList();
  }, [getChannel, sendRunsList]);

  const refreshDetail = useCallback(
    (runId?: string | null) => {
      const target = runId ?? selectedRunRef.current;
      if (!target) return;
      const hasChannel = getChannel() !== null;
      setDetailLoading(hasChannel);
      setLoadError(hasChannel ? null : "控制通道尚未连接");
      sendRunDetail(target);
    },
    [getChannel, sendRunDetail],
  );

  // 打开（含挂载即打开）与换选中运行：渲染期设好读取状态（读当前通道，与原 effect 读到的一致），
  // effect 只发请求。比对项与原 effect 依赖一致。
  if (useDepsChanged([open, refreshRuns], { onMount: true }) && open) {
    const hasChannel = getChannel() !== null;
    setListLoading(hasChannel);
    setLoadError(hasChannel ? null : "控制通道尚未连接");
  }
  if (
    useDepsChanged([open, refreshDetail, selectedRunId], { onMount: true }) &&
    open &&
    selectedRunId
  ) {
    setDetail((current) => (current.runId === selectedRunId ? current : EMPTY_DETAIL));
    const hasChannel = getChannel() !== null;
    setDetailLoading(hasChannel);
    setLoadError(hasChannel ? null : "控制通道尚未连接");
  }

  // 没连上就等连上再发（换通道时新通道还没连），等待期间保持读取中，不报错。
  useEffect(() => {
    const channel = getChannel();
    if (!open || !channel) return;
    return sendWhenConnected(channel, sendRunsList);
  }, [getChannel, open, sendRunsList]);

  useEffect(() => {
    const channel = getChannel();
    if (!open || !selectedRunId || !channel) return;
    return sendWhenConnected(channel, () => sendRunDetail(selectedRunId));
  }, [getChannel, open, sendRunDetail, selectedRunId]);

  const beginAction = (action: PendingAction) => {
    if (pendingRef.current) return false;
    pendingRef.current = action;
    setPendingAction(action);
    setActionError(null);
    setActionFeedback(null);
    return true;
  };
  const finishAction = (
    payload: { ok: boolean; code?: string; error?: string | null; audit?: string | null },
    successText: string,
  ) => {
    pendingRef.current = null;
    setPendingAction(null);
    if (!payload.ok) {
      const stale = /stale|already|expired/i.test(payload.code ?? "");
      setActionError(
        stale ? "状态已更新，已重新加载最新数据。" : (payload.error ?? payload.code ?? "操作失败"),
      );
    } else {
      setActionFeedback(payload.audit ?? successText);
    }
  };
  const matchesPending = (requestId?: string | null) =>
    Boolean(requestId && requestId === pendingRef.current?.requestId);

  function handleForkResponse(message: WorkflowCheckpointForkResponse) {
    if (pendingRef.current?.kind !== "fork" || !matchesPending(message.request_id)) return;
    finishAction({ ok: true, audit: message.payload.audit }, "已创建分支运行");
    setForkCheckpoint(null);
    if (message.payload.run_id) setSelectedRunId(message.payload.run_id);
    refreshRuns();
    refreshDetail(message.payload.source_run_id ?? selectedRunRef.current);
  }

  function handleDecisionResponse(message: WorkflowDecisionResolveResponse) {
    if (pendingRef.current?.kind !== "decision" || !matchesPending(message.request_id)) return;
    finishAction({ ok: true, audit: message.payload.audit }, "决定已记录");
    refreshRuns();
    refreshDetail(message.payload.run_id ?? selectedRunRef.current);
  }

  function handleEvaluationResponse(message: WorkflowEvaluationSubmitResponse) {
    if (pendingRef.current?.kind !== "evaluation" || !matchesPending(message.request_id)) return;
    finishAction({ ok: true, audit: message.payload.audit }, "评分已保存");
    refreshDetail(message.payload.run_id);
  }

  function handleDeliveryResponse(message: WorkflowDeliveryMutationResponse) {
    if (pendingRef.current?.kind !== "delivery" || !matchesPending(message.request_id)) return;
    finishAction(
      { ok: true, audit: message.payload.audit },
      message.type === "workflow_delivery_retry_response" ? "已安排重试" : "已丢弃交付",
    );
    refreshDetail(message.payload.run_id ?? selectedRunRef.current);
  }

  function handleIPCError(message: WorkflowIPCErrorResponse) {
    if (message.request_id === listRequestRef.current) {
      setListLoading(false);
      setLoadError(message.error.message);
      return;
    }
    if (message.request_id === detailRequestRef.current) {
      setDetailLoading(false);
      setLoadError(message.error.message);
      return;
    }
    if (!pendingRef.current || !matchesPending(message.request_id)) return;
    const runId = selectedRunRef.current;
    finishAction({ ok: false, code: message.error.code, error: message.error.message }, "");
    refreshRuns();
    refreshDetail(runId);
  }

  // 订阅只随通道与开关重建；回执处理用 effect event，读到的是最新一次渲染的处理函数。
  const onChannelMessage = useEffectEvent((message: IncomingMessage) => {
    switch (message.type) {
      case "workflow_runs_list_response": {
        const payload = (message as WorkflowRunsListResponse).payload;
        if ((message as WorkflowRunsListResponse).request_id !== listRequestRef.current) return;
        setListLoading(false);
        setRuns(payload.runs);
        setSelectedRunId((current) => {
          if (current && payload.runs.some((run) => run.run_id === current)) return current;
          return payload.runs[0]?.run_id ?? null;
        });
        break;
      }
      case "workflow_run_detail_response": {
        const payload = (message as WorkflowRunDetailResponse).payload;
        if ((message as WorkflowRunDetailResponse).request_id !== detailRequestRef.current) return;
        if (payload.run_id !== selectedRunRef.current) return;
        setDetailLoading(false);
        if (payload.run)
          setRuns((current) =>
            current.map((run) => (run.run_id === payload.run_id ? payload.run! : run)),
          );
        setDetail({
          runId: payload.run_id,
          runVersion: payload.run_version ?? payload.run?.run_version,
          nodes: payload.nodes,
          edges: payload.edges,
          spans: payload.spans,
          checkpoints: payload.checkpoints,
          evaluations: payload.evaluations,
          decisions: payload.decisions ?? [],
          deliveries: payload.deliveries ?? [],
        });
        break;
      }
      case "workflow_checkpoint_fork_response":
        handleForkResponse(message as WorkflowCheckpointForkResponse);
        break;
      case "workflow_decision_resolve_response":
        handleDecisionResponse(message as WorkflowDecisionResolveResponse);
        break;
      case "workflow_evaluation_submit_response":
        handleEvaluationResponse(message as WorkflowEvaluationSubmitResponse);
        break;
      case "workflow_delivery_retry_response":
      case "workflow_delivery_discard_response":
        handleDeliveryResponse(message as WorkflowDeliveryMutationResponse);
        break;
      case "workflow_ipc_error":
        handleIPCError(message as WorkflowIPCErrorResponse);
        break;
    }
  });

  useEffect(() => {
    if (!open) return;
    const channel = getChannel();
    if (!channel) return;
    return channel.onMessage((message: IncomingMessage) => onChannelMessage(message));
  }, [getChannel, open]);

  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? null;
  const sendDecision = (
    decision: WorkflowDecisionView,
    action: "resolve" | "reopen" | "cancel",
    response?: unknown,
  ) => {
    const channel = getChannel();
    if (!channel) return setActionError("控制通道尚未连接");
    const requestId = nextRequestId("decision");
    if (!beginAction({ kind: "decision", id: decision.decision_id, requestId })) return;
    channel.send({
      type: "workflow_decision_resolve",
      request_id: requestId,
      payload: {
        decision_id: decision.decision_id,
        nonce: decision.nonce,
        expected_version: decision.version,
        response: response ?? { action },
      },
    });
  };

  const sendDeliveryAction = (delivery: WorkflowDeliveryView, action: "retry" | "discard") => {
    if (!selectedRunId) return;
    const channel = getChannel();
    if (!channel) return setActionError("控制通道尚未连接");
    const requestId = nextRequestId("delivery");
    if (!beginAction({ kind: "delivery", id: delivery.delivery_id, requestId })) return;
    channel.send({
      type: action === "retry" ? "workflow_delivery_retry" : "workflow_delivery_discard",
      request_id: requestId,
      payload: {
        delivery_id: delivery.delivery_id,
        expected_version: delivery.version,
        reason: "run_inspector",
      },
    });
  };

  const submitFork = (input: ReplayForkInput) => {
    if (!selectedRunId || !forkCheckpoint) return;
    const channel = getChannel();
    if (!channel) return setActionError("控制通道尚未连接");
    const expectedVersion = detail.runVersion ?? selectedRun?.run_version;
    if (expectedVersion == null) return setActionError("运行版本缺失，请刷新后重试");
    const requestId = nextRequestId("fork");
    if (!beginAction({ kind: "fork", id: forkCheckpoint.checkpoint_id, requestId })) return;
    channel.send({
      type: "workflow_checkpoint_fork",
      request_id: requestId,
      expected_version: expectedVersion,
      payload: {
        run_id: selectedRunId,
        checkpoint_id: forkCheckpoint.checkpoint_id,
        expected_version: expectedVersion,
        state_patch: input.statePatch ?? {},
        confirm_dangerous_effects: input.confirmEffects,
      },
    });
  };

  const submitEvaluation = (input: EvaluationInput) => {
    if (!selectedRunId) return;
    const channel = getChannel();
    if (!channel) return setActionError("控制通道尚未连接");
    if (!selectedRun?.trace_id) return setActionError("该运行尚无可评分的 Trace");
    const requestId = nextRequestId("evaluation");
    if (!beginAction({ kind: "evaluation", id: "evaluation", requestId })) return;
    channel.send({
      type: "workflow_evaluation_submit",
      request_id: requestId,
      payload: {
        trace_id: selectedRun.trace_id,
        run_id: selectedRunId,
        evaluator_name: "human",
        evaluator_version: "ui-v1",
        evaluator_type: "human",
        verdict: input.score >= 0.5 ? "pass" : "fail",
        score: input.score,
        labels: input.labels,
        explanation: input.comment,
        evidence_refs: [],
        degraded: false,
        ...(input.experimentVersion ? { left_version_key: input.experimentVersion } : {}),
      },
    });
  };

  const openForkDialog = (checkpointId: string) => {
    const checkpoint = detail.checkpoints.find((item) => item.checkpoint_id === checkpointId);
    if (checkpoint) {
      setActionError(null);
      setActionFeedback(null);
      setForkCheckpoint(checkpoint);
    }
  };

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

      <div role="tablist" aria-label="ContextTrace views" className="sh-ContextTracePanel-7">
        {(Object.keys(tabLabel) as PanelTab[]).map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={tab === item}
            onClick={() => setTab(item)}
            style={{
              ...topTabStyle,
              color: tab === item ? "#67e8f9" : dark.textMuted,
              borderBottomColor: tab === item ? "#22d3ee" : "transparent",
            }}
          >
            {tabLabel[item]}
          </button>
        ))}
      </div>

      <div className="sh-ContextTracePanel-8">
        {tab === "context" ? (
          <LegacyContextTrace getChannel={getChannel} active />
        ) : (
          <RunInspectorPanel
            view={tab}
            runs={runs}
            nodes={detail.nodes}
            edges={detail.edges}
            spans={detail.spans}
            checkpoints={detail.checkpoints}
            evaluations={detail.evaluations}
            decisions={detail.decisions}
            deliveries={detail.deliveries}
            selectedRunId={selectedRunId}
            loading={listLoading}
            detailLoading={detailLoading}
            error={loadError}
            actionError={actionError}
            actionFeedback={actionFeedback}
            pendingActionId={pendingAction?.id}
            onSelectRun={setSelectedRunId}
            onRefresh={refreshRuns}
            onFork={openForkDialog}
            onResolveDecision={(decision, response) => sendDecision(decision, "resolve", response)}
            onReopenDecision={(decision) => sendDecision(decision, "reopen")}
            onCancelDecision={(decision) => sendDecision(decision, "cancel")}
            onDeliveryAction={sendDeliveryAction}
            onSubmitEvaluation={submitEvaluation}
          />
        )}
        <ReplayDialog
          open={Boolean(forkCheckpoint)}
          checkpoint={forkCheckpoint}
          submitting={pendingAction?.kind === "fork"}
          error={forkCheckpoint ? actionError : null}
          onClose={() => !pendingAction && setForkCheckpoint(null)}
          onSubmit={submitFork}
        />
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

function nextRequestId(prefix: string) {
  requestSequence += 1;
  return `${prefix}-${Date.now()}-${requestSequence}`;
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

const topTabStyle: React.CSSProperties = {
  minWidth: 72,
  padding: "0 10px",
  border: 0,
  borderBottom: "2px solid",
  background: "transparent",
  cursor: "pointer",
  fontSize: 11,
};
