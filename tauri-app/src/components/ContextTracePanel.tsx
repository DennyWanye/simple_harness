// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import {
  dark,
  darkButton,
  darkCloseBtn,
  darkInput,
  darkListSurface,
  darkPanelHeader,
  darkPanelSurface,
} from "../theme/components";
import type { ControlChannel } from "../ws/ControlChannel";
import type {
  ContextAttemptSnapshot,
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
type PendingAction = { kind: "fork" | "decision" | "delivery" | "evaluation"; id: string; requestId: string };

const EMPTY_DETAIL: DetailState = { runId: null, nodes: [], edges: [], spans: [], checkpoints: [], evaluations: [], decisions: [], deliveries: [] };
const tabLabel: Record<PanelTab, string> = { runs: "Runs", trace: "Trace", checkpoints: "Checkpoints", evaluations: "Evaluations", context: "Context" };
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

  const refreshRuns = useCallback(() => {
    const channel = getChannel();
    if (!channel) {
      setListLoading(false);
      setLoadError("控制通道尚未连接");
      return;
    }
    const requestId = nextRequestId("runs");
    listRequestRef.current = requestId;
    setListLoading(true);
    setLoadError(null);
    channel.send({ type: "workflow_runs_list", request_id: requestId, payload: { limit: 100 } });
  }, [getChannel]);

  const refreshDetail = useCallback((runId?: string | null) => {
    const target = runId ?? selectedRunRef.current;
    if (!target) return;
    const channel = getChannel();
    if (!channel) {
      setDetailLoading(false);
      setLoadError("控制通道尚未连接");
      return;
    }
    const requestId = nextRequestId("detail");
    detailRequestRef.current = requestId;
    setDetailLoading(true);
    setLoadError(null);
    channel.send({ type: "workflow_run_detail", request_id: requestId, payload: { run_id: target } });
  }, [getChannel]);

  useEffect(() => {
    if (open) refreshRuns();
  }, [open, refreshRuns]);

  useEffect(() => {
    if (!open || !selectedRunId) return;
    setDetail((current) => current.runId === selectedRunId ? current : EMPTY_DETAIL);
    refreshDetail(selectedRunId);
  }, [open, refreshDetail, selectedRunId]);

  useEffect(() => {
    if (!open) return;
    const channel = getChannel();
    if (!channel) return;
    return channel.onMessage((message: IncomingMessage) => {
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
          if (payload.run) setRuns((current) => current.map((run) => run.run_id === payload.run_id ? payload.run! : run));
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
  }, [getChannel, open]);

  const beginAction = (action: PendingAction) => {
    if (pendingRef.current) return false;
    pendingRef.current = action;
    setPendingAction(action);
    setActionError(null);
    setActionFeedback(null);
    return true;
  };
  const finishAction = (payload: { ok: boolean; code?: string; error?: string | null; audit?: string | null }, successText: string) => {
    pendingRef.current = null;
    setPendingAction(null);
    if (!payload.ok) {
      const stale = /stale|already|expired/i.test(payload.code ?? "");
      setActionError(stale ? "状态已更新，已重新加载最新数据。" : payload.error ?? payload.code ?? "操作失败");
    } else {
      setActionFeedback(payload.audit ?? successText);
    }
  };
  const matchesPending = (requestId?: string | null) => Boolean(requestId && requestId === pendingRef.current?.requestId);

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
    finishAction({ ok: true, audit: message.payload.audit }, message.type === "workflow_delivery_retry_response" ? "已安排重试" : "已丢弃交付");
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

  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? null;
  const sendDecision = (decision: WorkflowDecisionView, action: "resolve" | "reopen" | "cancel", response?: unknown) => {
    const channel = getChannel();
    if (!channel) return setActionError("控制通道尚未连接");
    const requestId = nextRequestId("decision");
    if (!beginAction({ kind: "decision", id: decision.decision_id, requestId })) return;
    channel.send({ type: "workflow_decision_resolve", request_id: requestId, payload: { decision_id: decision.decision_id, nonce: decision.nonce, expected_version: decision.version, response: response ?? { action } } });
  };

  const sendDeliveryAction = (delivery: WorkflowDeliveryView, action: "retry" | "discard") => {
    if (!selectedRunId) return;
    const channel = getChannel();
    if (!channel) return setActionError("控制通道尚未连接");
    const requestId = nextRequestId("delivery");
    if (!beginAction({ kind: "delivery", id: delivery.delivery_id, requestId })) return;
    channel.send({ type: action === "retry" ? "workflow_delivery_retry" : "workflow_delivery_discard", request_id: requestId, payload: { delivery_id: delivery.delivery_id, expected_version: delivery.version, reason: "run_inspector" } });
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
    channel.send({ type: "workflow_evaluation_submit", request_id: requestId, payload: { trace_id: selectedRun.trace_id, run_id: selectedRunId, evaluator_name: "human", evaluator_version: "ui-v1", evaluator_type: "human", verdict: input.score >= 0.5 ? "pass" : "fail", score: input.score, labels: input.labels, explanation: input.comment, evidence_refs: [], degraded: false, ...(input.experimentVersion ? { left_version_key: input.experimentVersion } : {}) } });
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
    <div data-testid="context-trace-panel" style={darkPanelSurface} role="dialog" aria-label="ContextTrace">
      <div style={darkPanelHeader}>
        <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={titleIconStyle}><Icon name="compass" size={15} /></span>
          <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 14, fontWeight: 600 }}>ContextTrace</strong>
        </span>
        <button data-testid="trace-close" onClick={onClose} style={darkCloseBtn} title="关闭" aria-label="关闭"><Icon name="close" size={15} /></button>
      </div>

      <div role="tablist" aria-label="ContextTrace views" style={topTabsStyle}>
        {(Object.keys(tabLabel) as PanelTab[]).map((item) => (
          <button key={item} type="button" role="tab" aria-selected={tab === item} onClick={() => setTab(item)} style={{ ...topTabStyle, color: tab === item ? "#67e8f9" : dark.textMuted, borderBottomColor: tab === item ? "#22d3ee" : "transparent" }}>{tabLabel[item]}</button>
        ))}
      </div>

      <div style={{ minHeight: 0, flex: 1, position: "relative" }}>
        {tab === "context" ? <LegacyContextTrace getChannel={getChannel} active /> : (
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
        <ReplayDialog open={Boolean(forkCheckpoint)} checkpoint={forkCheckpoint} submitting={pendingAction?.kind === "fork"} error={forkCheckpoint ? actionError : null} onClose={() => !pendingAction && setForkCheckpoint(null)} onSubmit={submitFork} />
      </div>
    </div>
  );
}

function LegacyContextTrace({ getChannel, active }: { getChannel: () => ControlChannel | null; active: boolean }) {
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [attempts, setAttempts] = useState<ContextAttemptSnapshot[]>([]);
  const [reason, setReason] = useState<string | null>(null);
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(false);
  const [contextWindow, setContextWindow] = useState(32_000);
  const refresh = useCallback(() => {
    const channel = getChannel();
    if (!channel) return;
    setLoading(true);
    channel.send({ type: "decisions_list", payload: { limit } });
  }, [getChannel, limit]);
  useEffect(() => {
    if (!active) return;
    const channel = getChannel();
    if (!channel) return;
    const unsubscribe = channel.onMessage((message: IncomingMessage) => {
      if (message.type !== "decisions_list_response") return;
      const payload = (message as DecisionsListResponse).payload;
      setDecisions(payload.decisions);
      setAttempts(payload.attempts ?? []);
      const latestAttempt = [...(payload.attempts ?? [])]
        .filter((attempt) => Number(attempt.context_window) > 0)
        .sort((a, b) => b.updated_at - a.updated_at)[0];
      if (latestAttempt) setContextWindow(latestAttempt.context_window);
      setReason(payload.reason ?? null);
      setLoading(false);
    });
    refresh();
    return unsubscribe;
  }, [active, getChannel, refresh]);
  const ordered = useMemo(() => [...decisions].sort((a, b) => cmpTimestamp(b.timestamp, a.timestamp)), [decisions]);
  const orderedAttempts = useMemo(
    () => [...attempts].sort((a, b) => b.updated_at - a.updated_at),
    [attempts],
  );
  const latest = ordered[0];
  const used = latest?.total_tokens ?? 0;
  const percent = contextWindow > 0 ? Math.min(1, used / contextWindow) : 0;
  const warning = percent >= 0.9;

  return (
    <div style={legacyLayoutStyle}>
      <div style={legacyControlsStyle}>
        <button data-testid="trace-refresh" onClick={refresh} style={btnStyle()} disabled={loading}><Icon name={loading ? "loader" : "refresh"} size={13} />刷新</button>
        <label style={legacyLabelStyle}><span>条数</span><input data-testid="trace-limit" type="number" min={1} max={200} value={limit} onChange={(event) => setLimit(Math.max(1, Math.min(200, Number(event.target.value) || 50)))} style={numInputStyle} /></label>
        <label style={legacyLabelStyle}><span>actual context_window</span><input data-testid="trace-ctx-window" aria-label="actual context window" type="number" value={contextWindow} readOnly style={{ ...numInputStyle, width: 86 }} /></label>
      </div>
      {reason && <div data-testid="trace-reason" style={{ color: dark.textFaint, fontSize: 10 }}>{reason === "context_assembler_not_registered" ? "当前没有可观察的上下文记录（主对话和任务编排的上下文不经过这里）。" : `后端提示：${reason}`}</div>}
      {latest && (
        <div data-testid="trace-budget" style={{ padding: "9px 11px", border: `1px solid ${warning ? "rgba(249,115,22,0.5)" : dark.border}`, borderRadius: 7, background: warning ? "rgba(249,115,22,0.13)" : dark.card }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, fontSize: 11 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>{warning && <Icon name="alert" size={12} />}{warning ? "预算接近上限" : "上一回合 token 预算"}</span>
            <span style={{ textAlign: "right", overflowWrap: "anywhere" }}>{used.toLocaleString()} / {contextWindow.toLocaleString()} ({(percent * 100).toFixed(1)}%)</span>
          </div>
          <div aria-label="budget usage" role="progressbar" aria-valuenow={Math.round(percent * 100)} aria-valuemin={0} aria-valuemax={100} style={progressStyle}><div data-testid="trace-budget-bar" style={{ width: `${percent * 100}%`, height: "100%", background: warning ? "#f97316" : "#2563eb" }} /></div>
        </div>
      )}
      {orderedAttempts.length > 0 && (
        <div data-testid="context-attempts" style={darkListSurface}>
          {orderedAttempts.map((attempt) => (
            <ContextAttemptRow key={`${attempt.request_id}:${attempt.attempt_id}`} attempt={attempt} />
          ))}
        </div>
      )}
      <div style={darkListSurface}>
        {ordered.length === 0 && !loading && <div style={legacyEmptyStyle}>(无决策记录)</div>}
        {ordered.map((decision, index) => <DecisionRow key={`${String(decision.timestamp)}-${index}`} index={index} decision={decision} contextWindow={contextWindow} />)}
      </div>
    </div>
  );
}

function ContextAttemptRow({ attempt }: { attempt: ContextAttemptSnapshot }) {
  const compressionModel = attempt.actual_compression_model || attempt.resolved_compression_model || attempt.requested_compression_model;
  const shortHash = (value: string) => value ? value.slice(0, 10) : "-";
  return (
    <div data-testid={`context-attempt-${attempt.attempt_id}`} style={decisionRowStyle}>
      <div style={{ display: "flex", gap: 8, justifyContent: "space-between", alignItems: "baseline" }}>
        <strong>{attempt.purpose || "unclassified"} · {attempt.state}</strong>
        <span style={{ color: dark.textFaint, fontSize: 10 }}>{attempt.provider_id || attempt.adapter_id} / {attempt.model_id || "-"}</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, color: dark.textMuted, fontSize: 10 }}>
        <span>planned <strong>{attempt.planned_tokens.toLocaleString()}</strong></span>
        <span>window <strong>{attempt.context_window.toLocaleString()}</strong></span>
        <span>budget <strong>{attempt.effective_input_budget.toLocaleString()}</strong></span>
        <span>reserve <strong>{attempt.reserve_tokens.toLocaleString()}</strong></span>
        <span>actual in/out <strong>{attempt.actual_input_tokens ?? "-"}/{attempt.actual_output_tokens ?? "-"}</strong></span>
        <span>tools <strong>{attempt.direct_tool_count}+{attempt.activated_tool_count}</strong> deferred {attempt.deferred_tool_count}</span>
        <span>retries <strong>{attempt.transport_retry_count}</strong></span>
      </div>
      <div style={{ color: dark.textFaint, fontSize: 9, overflowWrap: "anywhere" }}>
        wire {shortHash(attempt.wire_tool_hash)} · schema {shortHash(attempt.schema_fingerprint)} · policy {shortHash(attempt.policy_fingerprint)} · cache {shortHash(attempt.cache_fingerprint)}
      </div>
      {compressionModel && (
        <div data-testid="context-attempt-compression" style={{ color: dark.textMuted, fontSize: 10 }}>
          compression {attempt.requested_compression_model || "-"} → {attempt.resolved_compression_model || "-"} → {attempt.actual_compression_model || "-"}
          {attempt.compression_failure ? ` · failed: ${attempt.compression_failure}` : ""}
        </div>
      )}
      {attempt.fragments.length > 0 && (
        <div data-testid="context-attempt-fragments" style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 9 }}>
          {attempt.fragments.map((fragment) => (
            <span key={fragment.fragment_id}>
              {fragment.action} · {fragment.fragment_id} · {fragment.estimated_tokens}t{fragment.reason ? ` · ${fragment.reason}` : ""}
            </span>
          ))}
        </div>
      )}
      {attempt.coverage_valid != null && (
        <div data-testid="context-attempt-coverage" style={{ display: "flex", flexDirection: "column", gap: 2, color: dark.textMuted, fontSize: 9 }}>
          <span>
            coverage {attempt.coverage_valid ? "valid" : "invalid"} · page-in refs {attempt.coverage_page_in_refs} · gaps {attempt.coverage_gaps.length} · overlaps {attempt.coverage_overlaps.length} · stale {attempt.coverage_stale_segment_ids.length}
          </span>
          {attempt.coverage_entries.map((entry, index) => (
            <span key={`${entry.kind}:${entry.segment_id}:${index}`}>
              {entry.kind} · ids {entry.message_ids.join(",") || "-"}{entry.segment_id ? ` · segment ${entry.segment_id}` : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function DecisionRow({ index, decision, contextWindow }: { index: number; decision: DecisionRecord; contextWindow: number }) {
  const entries = Object.entries(decision.token_breakdown || {});
  const total = decision.total_tokens ?? 0;
  return (
    <div data-testid={`trace-decision-${index}`} style={decisionRowStyle}>
      <div style={{ display: "flex", gap: 8, justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ minWidth: 0, overflowWrap: "anywhere", fontSize: 11 }}><strong style={{ color: classifierColor(decision.classifier_path) }}>{decision.classifier_path || "?"}</strong>{decision.reason ? ` · ${decision.reason}` : ""}</span>
        <span style={{ flex: "0 0 auto", color: dark.textFaint, fontSize: 10 }}>{formatTimestamp(decision.timestamp)}</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, color: dark.textMuted, fontSize: 10 }}><span>latency <strong>{fmtMs(decision.latency_ms)}</strong></span><span>total_tokens <strong>{total.toLocaleString()}</strong></span>{decision.session_id && <span title={decision.session_id}>sid <strong>{decision.session_id.length > 10 ? `…${decision.session_id.slice(-8)}` : decision.session_id}</strong></span>}</div>
      {entries.length > 0 && <TokenBreakdownBar entries={entries} total={total || sum(entries.map(([, value]) => value))} contextWindow={contextWindow} />}
    </div>
  );
}

function TokenBreakdownBar({ entries, total, contextWindow }: { entries: [string, number][]; total: number; contextWindow: number }) {
  const palette: Record<string, string> = { system: "#3b82f6", l1: "#10b981", l2: "#f59e0b", l3: "#ef4444", tools: "#8b5cf6", history: "#06b6d4", summary: "#f472b6" };
  const safeTotal = total || 1;
  const usedPercent = contextWindow > 0 ? Math.min(1, total / contextWindow) : 0;
  return (
    <div data-testid="trace-breakdown" style={{ marginTop: 2 }}>
      <div title={`使用 ${total.toLocaleString()} / ${contextWindow.toLocaleString()} tokens`} style={{ display: "flex", height: 6, width: `${usedPercent * 100}%`, minWidth: "20%", overflow: "hidden", borderRadius: 3, background: "#0f172a" }}>{entries.map(([section, value]) => value > 0 ? <div key={section} data-testid={`trace-section-${section}`} title={`${section}: ${value.toLocaleString()}`} style={{ width: `${(value / safeTotal) * 100}%`, background: palette[section] || "#64748b" }} /> : null)}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 3, color: dark.textFaint, fontSize: 10 }}>{entries.map(([section, value]) => <span key={section}><span style={{ display: "inline-block", width: 8, height: 8, marginRight: 3, borderRadius: 2, verticalAlign: "middle", background: palette[section] || "#64748b" }} />{section} {value.toLocaleString()}</span>)}</div>
    </div>
  );
}

function nextRequestId(prefix: string) { requestSequence += 1; return `${prefix}-${Date.now()}-${requestSequence}`; }
function classifierColor(path?: string) { if (path === "cloud") return "#60a5fa"; if (path === "local") return "#34d399"; if (path === "echo") return "#fbbf24"; return "#e2e8f0"; }
function formatTimestamp(value?: string | number) { if (value == null) return "-"; const millis = typeof value === "number" ? (value < 10_000_000_000 ? value * 1000 : value) : Date.parse(value); if (Number.isNaN(millis)) return String(value); return new Date(millis).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function fmtMs(value?: number) { if (value == null) return "-"; return value < 1000 ? `${value.toFixed(0)}ms` : `${(value / 1000).toFixed(2)}s`; }
function cmpTimestamp(a?: string | number, b?: string | number) { return toEpoch(a) - toEpoch(b); }
function toEpoch(value?: string | number) { if (value == null) return 0; if (typeof value === "number") return value < 10_000_000_000 ? value * 1000 : value; const parsed = Date.parse(value); return Number.isNaN(parsed) ? 0 : parsed; }
function sum(values: number[]) { return values.reduce((total, value) => total + value, 0); }
function btnStyle(): React.CSSProperties { return darkButton("primary", "sm"); }

const titleIconStyle: React.CSSProperties = { width: 26, height: 26, display: "inline-flex", alignItems: "center", justifyContent: "center", flex: "0 0 auto", borderRadius: 7, background: "rgba(34,211,238,0.15)", color: "#67e8f9" };
const topTabsStyle: React.CSSProperties = { minHeight: 38, display: "flex", flex: "0 0 auto", overflowX: "auto", borderBottom: `1px solid ${dark.hairline}` };
const topTabStyle: React.CSSProperties = { minWidth: 72, padding: "0 10px", border: 0, borderBottom: "2px solid", background: "transparent", cursor: "pointer", fontSize: 11 };
const legacyLayoutStyle: React.CSSProperties = { height: "100%", minHeight: 0, display: "flex", flexDirection: "column", gap: 8, padding: 13 };
const legacyControlsStyle: React.CSSProperties = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 };
const legacyLabelStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 4, color: dark.textMuted, fontSize: 10 };
const numInputStyle: React.CSSProperties = { ...darkInput, width: 58, padding: "4px 6px", textAlign: "center" };
const progressStyle: React.CSSProperties = { height: 6, marginTop: 4, overflow: "hidden", borderRadius: 3, background: "#0f172a" };
const legacyEmptyStyle: React.CSSProperties = { marginTop: 32, color: dark.textFaint, textAlign: "center", fontSize: 12 };
const decisionRowStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 4, padding: "8px 6px", borderBottom: `1px solid ${dark.hairline}` };
