// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../components/Icon";
import { buildWorkflowTaskTraces } from "../components/AgentActivityMessage";
import { DurableTaskSteps } from "../components/workflow/DurableTaskSteps";
import {
  configureHarnessPublicDetailsLoader,
  harnessPublicSnapshotStore,
  normalizePublicRunSnapshot,
  useHarnessPublicSnapshot,
} from "../stores/harnessPublicSnapshotStore";
import type { TaskRunProjectionState } from "../stores/sessionsStore";
import type {
  HarnessInspectorErrorResponse,
  HarnessInspectorDetailsResponse,
  HarnessInspectorSnapshotResponse,
  PublicRunSnapshotV3,
} from "../types/messages";
import { HarnessRunGraph } from "./HarnessRunGraph";
import "./HarnessInspectorPanel.css";

type SendCommand = (message: {
  type: string;
  request_id: string;
  payload: Record<string, unknown>;
}) => boolean;

type Subscribe = (listener: (message: unknown) => void) => () => void;

interface Props {
  open: boolean;
  sessionId: string;
  selectedRunId: string | null;
  runProjections: TaskRunProjectionState[];
  onSelectRun: (runId: string) => void;
  onClose: () => void;
  sendCommand: SendCommand;
  subscribe: Subscribe;
  /** @deprecated Shared store is the production authority. */
  onSnapshot?: (snapshot: PublicRunSnapshotV3 | null) => void;
}

const TERMINAL = new Set(["completed", "completed_with_recovery", "failed", "cancelled"]);

function outcomeTitle(snapshot: PublicRunSnapshotV3): string {
  const { status, child_warnings: childWarnings } = snapshot.aggregate_outcome;
  if (status === "completed_with_recovery" || (status === "completed" && childWarnings.length > 0)) {
    return "子任务遇到问题，主 Agent 已接管并完成";
  }
  return {
    running: "Agent 正在执行",
    waiting: "Agent 正在等待",
    blocked: "Agent 需要你的帮助",
    completed: "任务已完成",
    failed: "任务失败",
    cancelled: "任务已取消",
    unknown: "运行信息尚不完整",
  }[status] ?? "运行状态更新";
}

function outcomeHint(snapshot: PublicRunSnapshotV3): string {
  if (!snapshot.projection_complete) return "记录仍在加载，当前显示已确认的部分。";
  if (snapshot.aggregate_outcome.status === "cancelled") return "停止已确认；晚到记录不会重新把任务显示为运行中。";
  if (snapshot.aggregate_outcome.status === "completed_with_recovery") return "失败步骤保留在所属阶段内，整体结果以根任务为准。";
  return "运行图和消息区来自同一份已确认记录。";
}

export function HarnessInspectorPanel({
  open,
  sessionId,
  selectedRunId,
  runProjections,
  onSelectRun,
  onClose,
  sendCommand,
  subscribe,
  onSnapshot,
}: Props) {
  const snapshot = useHarnessPublicSnapshot(sessionId, selectedRunId);
  const [loading, setLoading] = useState(false);
  const [errorValue, setErrorValue] = useState<{
    sessionId: string;
    rootRunId: string;
    message: string;
  } | null>(null);
  const [selectedPhaseId, setSelectedPhaseId] = useState<string | null>(null);
  const requestCounter = useRef(0);
  const activeRequest = useRef<string | null>(null);
  const activeDetailsRequest = useRef<string | null>(null);
  const loadedDetailsProjection = useRef<string | null>(null);
  const refreshTimer = useRef<number | null>(null);
  const requestSnapshotRef = useRef<() => void>(() => undefined);
  const selectedProjection = runProjections.find((run) => run.run_id === selectedRunId);
  const awaitingDurableRun = selectedProjection?.status === "starting";
  const error = errorValue?.sessionId === sessionId && errorValue.rootRunId === selectedRunId
    ? errorValue.message
    : null;

  const clearRefresh = useCallback(() => {
    if (refreshTimer.current != null) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = null;
  }, []);

  const requestSnapshot = useCallback(() => {
    clearRefresh();
    if (!open || !selectedRunId || awaitingDurableRun || activeRequest.current) return;
    const requestId = `harness-public:${++requestCounter.current}:${selectedRunId}`;
    activeRequest.current = requestId;
    setLoading(true);
    const sent = sendCommand({
      type: "harness_inspector_snapshot_request",
      request_id: requestId,
      payload: {
        session_id: sessionId,
        root_run_id: selectedRunId,
        schema_version: "3",
      },
    });
    if (!sent) {
      activeRequest.current = null;
      setLoading(false);
      setErrorValue({ sessionId, rootRunId: selectedRunId, message: "控制通道未连接，等待自动重连" });
      refreshTimer.current = window.setTimeout(() => requestSnapshotRef.current(), 1200);
    }
  }, [awaitingDurableRun, clearRefresh, open, selectedRunId, sendCommand, sessionId]);
  useEffect(() => {
    requestSnapshotRef.current = requestSnapshot;
  }, [requestSnapshot]);

  const requestDetailsPage = useCallback((cursor?: string) => {
    if (!snapshot || activeDetailsRequest.current) return;
    const requestId = `harness-details:${++requestCounter.current}:${snapshot.root_run_id}`;
    activeDetailsRequest.current = requestId;
    const sent = sendCommand({
      type: "harness_inspector_details_request",
      request_id: requestId,
      payload: {
        session_id: snapshot.session_id,
        root_run_id: snapshot.root_run_id,
        projection_id: snapshot.projection_id,
        query_kind: "tool_details",
        page_size: 100,
        ...(cursor ? { cursor } : {}),
      },
    });
    if (!sent) activeDetailsRequest.current = null;
  }, [sendCommand, snapshot]);

  useEffect(() => {
    configureHarnessPublicDetailsLoader((detailRef) => {
      if (!snapshot || loadedDetailsProjection.current === snapshot.projection_id) return;
      if (!snapshot.tool_public_views.some((tool) => tool.detail_ref === detailRef)) return;
      requestDetailsPage();
    });
    return () => configureHarnessPublicDetailsLoader(null);
  }, [requestDetailsPage, snapshot]);

  useEffect(() => subscribe((raw) => {
    if (!raw || typeof raw !== "object") return;
    const message = raw as HarnessInspectorSnapshotResponse | HarnessInspectorErrorResponse | HarnessInspectorDetailsResponse;
    if (message.type === "harness_inspector_details_response") {
      if (message.request_id !== activeDetailsRequest.current || !snapshot) return;
      activeDetailsRequest.current = null;
      if (message.projection_id !== snapshot.projection_id || message.query_kind !== "tool_details") return;
      harnessPublicSnapshotStore.mergeToolDetails(
        snapshot.session_id,
        snapshot.root_run_id,
        snapshot.projection_id,
        message.items,
      );
      if (message.next_cursor) requestDetailsPage(message.next_cursor);
      else loadedDetailsProjection.current = snapshot.projection_id;
      return;
    }
    if (!activeRequest.current || message.request_id !== activeRequest.current) return;
    activeRequest.current = null;
    setLoading(false);
    if (message.type === "harness_inspector_snapshot_response") {
      const value = normalizePublicRunSnapshot(message.snapshot ?? message.payload);
      if (!value || value.session_id !== sessionId || value.root_run_id !== selectedRunId) return;
      harnessPublicSnapshotStore.publish(value);
      setErrorValue(null);
      if (!(TERMINAL.has(value.aggregate_outcome.status) && value.projection_complete)) {
        refreshTimer.current = window.setTimeout(requestSnapshot, 1200);
      }
      return;
    }
    if (message.type === "harness_inspector_error") {
      setErrorValue({
        sessionId,
        rootRunId: selectedRunId ?? "",
        message: message.payload?.detail ?? message.payload?.message ?? message.message ?? message.code ?? "运行记录读取失败",
      });
      refreshTimer.current = window.setTimeout(requestSnapshot, 1200);
    }
  }), [requestDetailsPage, requestSnapshot, selectedRunId, sessionId, snapshot, subscribe]);

  useEffect(() => {
    activeRequest.current = null;
    activeDetailsRequest.current = null;
    loadedDetailsProjection.current = null;
    clearRefresh();
    if (open && selectedRunId && !awaitingDurableRun) {
      refreshTimer.current = window.setTimeout(requestSnapshot, 0);
    }
    return () => {
      clearRefresh();
      if (!selectedRunId) return;
      sendCommand({
        type: "harness_inspector_cancel",
        request_id: `harness-cancel:${selectedRunId}`,
        payload: {
          session_id: sessionId,
          root_run_id: selectedRunId,
          request_kind: "all",
        },
      });
      activeRequest.current = null;
    };
  }, [awaitingDurableRun, clearRefresh, open, requestSnapshot, selectedRunId, sendCommand, sessionId]);

  useEffect(() => onSnapshot?.(snapshot), [onSnapshot, snapshot]);

  const nodes = useMemo(() => snapshot?.semantic_phases.map((phase) => ({
    id: phase.phase_id,
    taxonomy: phase.taxonomy,
    title: phase.title,
    summary: phase.total_steps
      ? `当前 ${phase.current_step == null ? 0 : phase.current_step + 1}/${phase.total_steps} 步`
      : phase.tool_refs.length > 0
        ? `${phase.tool_refs.length} 个工具操作`
        : "查看这一步的执行记录",
    status: phase.status,
  })) ?? [], [snapshot]);
  const currentPhase = snapshot?.semantic_phases.find((phase) =>
    ["running", "waiting", "failed", "cancelled"].includes(phase.status),
  ) ?? snapshot?.semantic_phases.at(-1) ?? null;
  const trace = useMemo(() => snapshot ? buildWorkflowTaskTraces(snapshot)[0] : undefined, [snapshot]);

  if (!open) return null;

  return (
    <aside className="harness-inspector" data-testid="harness-inspector" aria-label="Harness 运行观察">
      <header className="harness-inspector__header">
        <div>
          <strong>Agent 运行情况</strong>
          <span>每一步和消息区使用同一份记录</span>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭 Harness 运行观察">
          <Icon name="close" size={13} />
        </button>
      </header>

      <label className="harness-inspector__run-picker">
        <span>查看任务</span>
        <select
          aria-label="选择要观察的任务"
          value={selectedRunId ?? ""}
          onChange={(event) => onSelectRun(event.target.value)}
        >
          {runProjections.map((run) => (
            <option key={run.run_id} value={run.run_id}>{run.run_id.slice(0, 12)} · {run.status}</option>
          ))}
        </select>
      </label>

      {awaitingDurableRun ? <div className="harness-inspector__empty">正在启动任务…</div> : null}
      {!awaitingDurableRun && selectedRunId && !snapshot && !error ? (
        <div className="harness-inspector__empty">{loading ? "正在读取运行记录…" : "等待运行记录"}</div>
      ) : null}
      {error ? <div className="harness-inspector__error" role="alert">{error}</div> : null}

      {snapshot ? (
        <div className="harness-inspector__body">
          <section className="harness-activity-card" aria-live="polite">
            <strong>{outcomeTitle(snapshot)}</strong>
            <span>{outcomeHint(snapshot)}</span>
            {snapshot.aggregate_outcome.child_warnings.length > 0 ? (
              <small>{snapshot.aggregate_outcome.child_warnings.length} 个子任务问题已保留，可在对应阶段查看</small>
            ) : null}
          </section>

          <HarnessRunGraph
            nodes={nodes}
            selectedNodeId={selectedPhaseId}
            currentNodeId={currentPhase?.phase_id ?? null}
            headline={currentPhase?.title ?? outcomeTitle(snapshot)}
            subline={currentPhase?.total_steps
              ? `当前 ${currentPhase.current_step == null ? 0 : currentPhase.current_step + 1}/${currentPhase.total_steps} 步`
              : snapshot.projection_complete ? "记录已同步" : "仍在加载更多记录"}
            onSelectNode={setSelectedPhaseId}
          />

          <section className="harness-inspector__section">
            <div className="harness-inspector__section-title">
              <strong>步骤详情</strong>
              <span>默认展开当前阶段；工具结果默认收起</span>
            </div>
            {trace ? <DurableTaskSteps key={trace.runId} trace={trace} /> : (
              <div className="harness-inspector__empty">旧版记录没有可安全公开的阶段详情。</div>
            )}
          </section>

          {snapshot.diagnostics.length > 0 ? (
            <details className="harness-inspector__technical" data-testid="harness-technical-records">
              <summary>技术信息（排查问题时再看）</summary>
              <ul>{snapshot.diagnostics.map((item) => <li key={item}>{item}</li>)}</ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}
