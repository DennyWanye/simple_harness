import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import type {
  Message,
  TaskRunProjectionState,
  TaskRunProjectionStatus,
  WorkflowProgressStatus,
  WorkflowV5ControlAction,
} from "../../stores/sessionsStore";
import type {
  CapabilityOperation,
  CapabilityOperationAction,
} from "../../types/capabilities";
import type { WorkflowTaskTrace } from "../AgentActivityMessage";
import { CapabilityOperationCard } from "../CapabilityOperationCard";
import { DurableTaskSteps } from "./DurableTaskSteps";

export interface WorkflowProgressGroupProps {
  runId: string;
  summary?: Message;
  stages: Message[];
  onWorkflowRetry?: (
    runId: string,
    actionId: Exclude<WorkflowV5ControlAction, "none">,
    retryKey: string,
  ) => Promise<{ run_id: string; accepted?: boolean }>;
  /** Optional projection from the same workflow push stream. */
  capabilityOperation?: CapabilityOperation;
  onCapabilityOperationAction?: (
    action: CapabilityOperationAction,
    operation: CapabilityOperation,
  ) => void;
  taskTrace?: WorkflowTaskTrace;
  runProjectionStatus?: TaskRunProjectionStatus;
  runProjection?: TaskRunProjectionState;
}

type TimelineStatus = "success" | "running" | "waiting" | "degraded" | "failed" | "cancelled";

const STATUS_TONES: Record<WorkflowProgressStatus, {
  label: string;
  accent: string;
  soft: string;
}> = {
  running: { label: "进行中", accent: "#38bdf8", soft: "rgba(56,189,248,0.14)" },
  waiting: { label: "等待操作", accent: "#fbbf24", soft: "rgba(251,191,36,0.14)" },
  completed: { label: "已完成", accent: "#34d399", soft: "rgba(52,211,153,0.14)" },
  failed: { label: "失败", accent: "#f87171", soft: "rgba(248,113,113,0.14)" },
  cancelled: { label: "已取消", accent: "#94a3b8", soft: "rgba(148,163,184,0.14)" },
};

const V7_CHILD_LABELS = {
  queued: "排队中",
  running: "调研中",
  retrying: "补救中",
  valid: "已验收",
  insufficient: "证据不足",
} as const;

const DELIVERY_LABELS = {
  queued: "交付排队中",
  delivering: "正在交付",
  delivered: "已交付",
  retrying: "交付重试中",
  fenced: "旧会话已隔离",
  failed: "交付失败",
} as const;

const TIMELINE_TONES: Record<TimelineStatus, { label: string; icon: string; color: string; soft: string }> = {
  success: { label: "完成", icon: "✓", color: "#34d399", soft: "rgba(52,211,153,0.11)" },
  running: { label: "进行中", icon: "●", color: "#38bdf8", soft: "rgba(56,189,248,0.11)" },
  waiting: { label: "等待", icon: "…", color: "#fbbf24", soft: "rgba(251,191,36,0.11)" },
  degraded: { label: "降级完成", icon: "!", color: "#fb923c", soft: "rgba(251,146,60,0.11)" },
  failed: { label: "失败", icon: "×", color: "#f87171", soft: "rgba(248,113,113,0.11)" },
  cancelled: { label: "已取消", icon: "–", color: "#94a3b8", soft: "rgba(148,163,184,0.11)" },
};

const METRIC_LABELS: Record<string, string> = {
  mode: "模式", question_count: "问题", active_branch_count: "分支", query_count: "查询",
  providers: "来源", providers_attempted: "尝试来源", providers_hit: "命中来源",
  actual_requests: "真实请求", hits: "命中", empty: "空结果", timeouts: "超时",
  cooldown_skips: "cooldown 跳过", busy_skips: "busy 跳过", queue_timeouts: "排队超时",
  probes: "probe", rescue_considered_count: "考虑救援", rescue_executed_count: "执行救援",
  candidates: "候选", kept: "保留", direct_sources: "一手来源", attempted: "尝试",
  succeeded: "成功", dropped: "丢弃", passages: "段落", iteration: "轮次",
  followup_count: "补充查询", new_evidence: "新证据", domains: "域名", sections: "章节",
  claim_count: "论断", factual_claims_pre_repair: "事实论断", supported_factual: "有支撑事实",
  citations: "引用", supported: "有支撑", unsupported: "无支撑", support_rate: "支撑率",
  published: "发布", discarded: "丢弃", repaired: "修复", body_bytes: "正文字节",
  artifact_count: "产物", report_bytes: "报告字节", status: "结果",
};

const DIAGNOSTIC_LABELS: Record<string, string> = {
  cooldown: "来源冷却中", half_open_busy: "来源正在试探恢复", timeout: "来源响应超时",
  blocked: "来源拒绝访问", captcha: "来源要求验证", rate_limit: "来源请求受限",
  http_error: "来源连接失败", invalid_response: "来源响应无效", budget_exhausted: "本轮时间预算已用完",
  provider_degraded: "部分来源已降级", partial_results: "仅获得部分结果", evidence_missing: "证据不足",
  low_quality_evidence: "低质量证据已丢弃", missing_exact_token: "关键数值未获证据支持",
  insufficient_support: "论断支持不足", claim_unsupported: "论断缺少支持", claim_pruned: "无支持论断已删除",
  deterministic_repair: "已收缩到证据支持范围", insufficient_evidence: "证据不足，未发布报告",
  artifact_missing: "报告产物未保存", no_results: "未找到可核验结果", degraded: "部分步骤已降级",
  deadline_exhausted: "本阶段已达到时间上限", search_port_unavailable: "搜索服务暂不可用",
  provider_failure: "搜索来源调用失败", direct_failure: "一手来源查询失败",
  fetch_failure: "来源正文抓取失败", blob_unavailable: "已抓取正文暂不可读取",
  low_quality_source: "低质量来源已过滤", low_quality_content: "低质量正文已过滤",
  search_degraded: "搜索能力已降级", support_rate_below_threshold: "论断证据支持率未达发布标准",
  published_factual_below_threshold: "可发布事实论断数量未达标准",
  citation_count_below_threshold: "有效引用数量未达标准",
  domain_count_below_threshold: "独立来源域名数量未达标准",
  body_bytes_below_threshold: "报告正文长度未达发布标准",
};

const V5_GAP_REASON_LABELS: Record<string, string> = {
  insufficient_admitted_passages: "通过筛选的证据段不足",
  insufficient_strong_distinct_families: "独立高质量来源不足",
  winning_relevance_below_threshold: "最佳证据相关度不足",
  duplicate_rate_above_threshold: "重复来源比例过高",
  invalid_rate_above_threshold: "无效来源比例过高",
  first_party_requirement_unsatisfied: "缺少所需第一方来源",
  evidence_gap: "证据覆盖不足",
};

const V5_REJECTION_REASON_LABELS: Record<string, string> = {
  invalid_page: "页面无效",
  body_too_short: "正文过短",
  body_span_missing: "未找到可引用正文",
  dimension_relevance_below_threshold: "与调研维度相关度不足",
  other_rejected: "其他未通过筛选的证据",
};

interface TimelineRow {
  key: string;
  message: Message;
  status: TimelineStatus;
  stageId: string;
  label: string;
  action: string;
  result: string;
  reasons: string[];
  durable: boolean;
}

const STAGE_LABELS: Record<string, string> = {
  fetch: "抓取正文", score: "筛选证据", gap: "检查证据缺口", rerank: "重排证据",
  synth: "撰写结论", cite: "核验引用", persist: "保存报告",
};

type RetryState = {
  status: "idle" | "pending" | "ambiguous" | "resolved" | "rejected";
  resultRunId?: string;
  error?: string;
};

function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function safeDomId(prefix: string, value: string): string {
  return `${prefix}-${value.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function sortStages(stages: Message[]): Message[] {
  return [...stages].sort((a, b) =>
    (a.workflow_seq ?? Number.MAX_SAFE_INTEGER) - (b.workflow_seq ?? Number.MAX_SAFE_INTEGER) ||
    String(a.workflow_event_id || a.id).localeCompare(String(b.workflow_event_id || b.id)),
  );
}

function currentTimelineStatus(status: WorkflowProgressStatus): TimelineStatus {
  if (status === "completed") return "success";
  return status;
}

function fallbackResult(status: TimelineStatus, message: Message): string {
  if (message.workflow_error) return message.workflow_error;
  if (status === "running") return "正在处理这一步";
  if (status === "waiting") return "等待你的操作后继续";
  if (status === "failed") return "这一步未能完成";
  if (status === "cancelled") return "任务已取消";
  if (status === "degraded") return "已完成，但存在降级或限制";
  return message.text || "已正常完成";
}

function rowFromMessage(message: Message, status: TimelineStatus, durable: boolean): TimelineRow {
  const stageId = message.workflow_stage_id || message.workflow_stage || (durable ? message.id : "current");
  const reasons = (message.workflow_diagnostic_codes || [])
    .map((code) => DIAGNOSTIC_LABELS[code])
    .filter((value): value is string => Boolean(value));
  if (message.workflow_error && !reasons.includes(message.workflow_error)) reasons.push(message.workflow_error);
  return {
    key: durable
      ? String(message.workflow_event_id || message.workflow_stage_instance_id || message.id)
      : `${message.workflow_run_id}:current:${stageId}`,
    message,
    status,
    stageId,
    label: message.workflow_stage || stageId || "当前阶段",
    action: message.workflow_action || message.workflow_stage || "处理当前阶段",
    result: message.workflow_result || fallbackResult(status, message),
    reasons,
    durable,
  };
}

export function WorkflowProgressGroup({
  runId,
  summary,
  stages,
  onWorkflowRetry,
  capabilityOperation,
  onCapabilityOperationAction,
  taskTrace,
  runProjectionStatus,
  runProjection,
}: WorkflowProgressGroupProps) {
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());
  const [now, setNow] = useState(() => Date.now());
  const timelineRef = useRef<HTMLDivElement>(null);
  const retryKeyRef = useRef<string | null>(null);
  const retryPendingRef = useRef(false);
  const retryTimerRef = useRef<number | null>(null);
  const [retryState, setRetryState] = useState<RetryState>({ status: "idle" });
  const orderedStages = useMemo(() => sortStages(stages), [stages]);
  const fallback = orderedStages.at(-1);
  const taskStatus: WorkflowProgressStatus | undefined = taskTrace?.status === "completed_with_recovery"
    ? "completed"
    : taskTrace && ["running", "waiting", "completed", "failed", "cancelled"].includes(taskTrace.status)
      ? taskTrace.status as WorkflowProgressStatus
      : undefined;
  const terminalRunProjectionStatus: WorkflowProgressStatus | undefined =
    runProjectionStatus === "completed" ||
    runProjectionStatus === "failed" ||
    runProjectionStatus === "cancelled"
      ? runProjectionStatus
      : undefined;
  const terminalSummaryStatus: WorkflowProgressStatus | undefined =
    summary?.workflow_status === "completed" ||
    summary?.workflow_status === "failed" ||
    summary?.workflow_status === "cancelled"
      ? summary.workflow_status
      : undefined;
  // The public Run projection is the authoritative read model. Legacy
  // workflow summaries may remain on the message stream after the Root has
  // already recovered or reached a terminal state. After an app restart the
  // detailed public snapshot may not be loaded yet, so the Session's canonical
  // terminal Run projection is the fail-safe fallback.
  const status = terminalSummaryStatus ?? (
    taskStatus && ["completed", "failed", "cancelled"].includes(taskStatus)
      ? taskStatus
      : terminalRunProjectionStatus
  ) ?? taskStatus ?? summary?.workflow_status ??
    (fallback?.workflow_stage_id === "finalize" ? "completed" : "running");
  const tone = STATUS_TONES[status];
  const isDeepResearch = orderedStages.length > 0 || ["v2", "v3", "v4", "v5", "v6", "v7"].includes(summary?.workflow_version || "");
  const v5 = summary?.workflow_v5;
  const v7Children = summary?.workflow_v7_children || [];

  useEffect(() => () => {
    if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
  }, []);

  useEffect(() => {
    if (status !== "running" && status !== "waiting") return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [status]);

  const timelineRows = useMemo(() => {
    const rows = orderedStages.map((stage) => rowFromMessage(
      stage,
      stage.workflow_degraded ? "degraded" : "success",
      true,
    ));
    if (!summary || !isDeepResearch || status === "completed") return rows;
    const currentStageId = summary.workflow_stage_id || summary.workflow_stage || "current";
    const currentSeq = summary.workflow_seq ?? Number.MAX_SAFE_INTEGER;
    const completedCurrent = orderedStages.some((stage) =>
      stage.workflow_stage_id === currentStageId && (stage.workflow_seq ?? -1) >= currentSeq,
    );
    if (!completedCurrent || status === "failed" || status === "cancelled") {
      rows.push(rowFromMessage(summary, currentTimelineStatus(status), false));
    }
    return rows;
  }, [isDeepResearch, orderedStages, status, summary]);

  useEffect(() => {
    if (timelineRef.current) timelineRef.current.scrollTop = timelineRef.current.scrollHeight;
  }, [timelineRows.length]);

  const total = Math.max(1, summary?.workflow_total ?? fallback?.workflow_total ?? (stages.length > 0 ? 13 : 1));
  const distinctCompleted = new Set(orderedStages.map((stage) => stage.workflow_stage_id).filter(Boolean)).size;
  const legacyOrdinal = Math.max(summary?.workflow_ordinal ?? 0, summary?.workflow_display_ordinal ?? 0);
  const completed = Math.min(total, Math.max(
    summary?.workflow_completed_count ?? 0,
    distinctCompleted,
    stages.length === 0 ? legacyOrdinal : 0,
  ));
  const percent = status === "completed" ? 100 : Math.min(99, Math.round((completed / total) * 100));
  const warningCount = Math.max(
    summary?.workflow_warning_count ?? 0,
    orderedStages.filter((stage) => stage.workflow_degraded).length,
  );
  const timestamps = [summary, ...orderedStages]
    .map((message) => message?.ts)
    .filter((ts): ts is number => typeof ts === "number" && Number.isFinite(ts));
  const startedAt = taskTrace?.startedAt ?? runProjection?.started_at ?? summary?.workflow_started_at ?? (
    timestamps.length > 0 ? Math.min(...timestamps) : 0
  );
  const endedAt = taskTrace?.endedAt ?? (
    terminalRunProjectionStatus ? runProjection?.last_activity : undefined
  ) ?? Math.max(...timestamps, startedAt);
  const recordedElapsed = summary?.workflow_elapsed_ms ?? Math.max(0, endedAt - startedAt);
  const stableWallElapsed = runProjection != null ||
    typeof summary?.workflow_started_at === "number"
    ? Math.max(0, (status === "running" || status === "waiting" ? now : endedAt) - startedAt)
    : 0;
  const elapsedMs = Math.max(recordedElapsed, stableWallElapsed);
  const elapsedKnown = status === "running" || status === "waiting" ||
    taskTrace?.endedAt != null ||
    (summary?.workflow_elapsed_ms ?? 0) > 0 ||
    (runProjection != null && runProjection.last_activity > runProjection.started_at) ||
    timestamps.length > 1;
  const elapsedLabel = elapsedKnown
    ? `已用时 ${formatDuration(elapsedMs)}`
    : "耗时未记录";
  const name = summary?.workflow_name || fallback?.workflow_name ||
    (taskTrace ? "Agent 执行过程" : "深度调研");
  const currentStage = summary?.workflow_error || summary?.workflow_stage || fallback?.workflow_stage || "等待总体进度";
  const detailsId = safeDomId("workflow-stages", runId);
  const durableTimelineRows = timelineRows.filter((row) => row.durable);
  const allExpanded = durableTimelineRows.length > 0 &&
    durableTimelineRows.every((row) => expandedKeys.has(row.key));
  const skippedStages = summary?.workflow_skipped_stage_ids || [];
  const [skippedExpanded, setSkippedExpanded] = useState(false);
  const v5Action = v5?.control_action && v5.control_action !== "none"
    ? v5.control_action
    : undefined;
  const actionId: Exclude<WorkflowV5ControlAction, "none"> | undefined =
    summary?.workflow_version === "v5" ? v5Action :
      summary?.workflow_name === "deep_research" && summary.workflow_version === "v6" &&
      status === "running" ? "generate_now" :
      status === "failed" && summary?.workflow_version === "v4" &&
      summary.workflow_retry_action_id === "retry_from_start" ? "retry_from_start" : undefined;
  const retryAvailable = Boolean(actionId && onWorkflowRetry);
  const settleRemaining = v5 && ["accepted", "observed"].includes(v5.control_status)
    ? Math.max(0, 30 - Math.floor(Math.max(0, now - (summary?.workflow_updated_at ?? now)) / 1000))
    : null;
  const hasDetails =
    timelineRows.length > 0 ||
    v7Children.length > 0 ||
    Boolean(capabilityOperation);

  useEffect(() => {
    retryKeyRef.current = null;
    retryPendingRef.current = false;
    setRetryState({ status: "idle" });
  }, [actionId]);

  const retry = () => {
    if (!retryAvailable || !onWorkflowRetry || retryPendingRef.current || retryState.status === "pending") return;
    retryPendingRef.current = true;
    const retryKey = retryKeyRef.current ?? globalThis.crypto.randomUUID();
    retryKeyRef.current = retryKey;
    setRetryState({ status: "pending" });
    if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
    retryTimerRef.current = window.setTimeout(() => {
      retryPendingRef.current = false;
      setRetryState((current) => current.status === "pending"
        ? { status: "ambiguous", error: "响应较慢，可确认重试状态" }
        : current);
    }, 15_000);
    void onWorkflowRetry(runId, actionId!, retryKey).then((result) => {
      retryPendingRef.current = false;
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
      setRetryState({ status: "resolved", resultRunId: result.run_id });
    }).catch((error: unknown) => {
      retryPendingRef.current = false;
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
      const value = error as { message?: string; definitive?: boolean; code?: string };
      const definitive = value?.definitive === true;
      if (definitive) retryKeyRef.current = null;
      setRetryState({
        status: definitive ? "rejected" : "ambiguous",
        error: value?.message || (definitive ? "重试请求被拒绝" : "连接后重试"),
      });
    });
  };

  const toggleRow = (key: string) => setExpandedKeys((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const toggleAll = () => setExpandedKeys((current) => {
    const next = new Set(current);
    durableTimelineRows.forEach((row) => {
      if (allExpanded) next.delete(row.key); else next.add(row.key);
    });
    return next;
  });

  if (taskTrace) {
    return (
      <section
        data-testid={`workflow-progress-${runId}`}
        data-role="workflow_group"
        data-status={status}
        aria-label={`${name}总体进度`}
        style={{
          alignSelf: "stretch",
          flexShrink: 0,
          display: "grid",
          gap: 4,
          padding: "7px 2px",
          borderBottom: "1px solid rgba(148, 163, 184, 0.12)",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <strong style={{ color: "#e8edf6", fontSize: 12.5 }}>{name}</strong>
          <span style={{ color: tone.accent, fontSize: 10.5 }}>{tone.label}</span>
          <span style={{ marginLeft: "auto", color: "#7f8a99", fontSize: 10.5 }}>
            {elapsedLabel}
          </span>
        </div>
        <DurableTaskSteps trace={taskTrace} />
      </section>
    );
  }

  if (!hasDetails) {
    return (
      <div
        data-testid={`workflow-progress-${runId}`}
        data-role="workflow_group"
        data-status={status}
        aria-label={`${name}总体进度`}
        style={{
          alignSelf: "stretch",
          flexShrink: 0,
          display: "grid",
          gap: 3,
          padding: "7px 2px",
          borderBottom: "1px solid rgba(148, 163, 184, 0.12)",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <strong style={{ color: "#e8edf6", fontSize: 12.5 }}>{name}</strong>
          <span style={{ color: tone.accent, fontSize: 10.5 }}>{tone.label}</span>
          {summary?.workflow_delivery ? (
            <span
              data-testid="workflow-delivery-status"
              style={{ color: "#7f8a99", fontSize: 10.5 }}
            >
              {DELIVERY_LABELS[summary.workflow_delivery.status]}
            </span>
          ) : null}
        </div>
        <div
          title={currentStage}
          aria-live={status === "waiting" || status === "failed" ? "assertive" : "polite"}
          style={{ color: summary?.workflow_error ? tone.accent : "#b9c2d0", fontSize: 11.5 }}
        >
          {currentStage}
        </div>
        <span
          role="progressbar"
          aria-label={`${name}进度`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-valuetext={`${tone.label}，${currentStage}，${completed}/${total}，${elapsedLabel}`}
          style={{ color: "#7f8a99", fontSize: 10.5 }}
        >
          {completed}/{total} · {elapsedLabel} · {percent}%
        </span>
      </div>
    );
  }

  return (
    <section
      data-testid={`workflow-progress-${runId}`}
      data-role="workflow_group"
      data-status={status}
      aria-label={`${name}总体进度`}
      style={{
        alignSelf: "stretch", flexShrink: 0,
        height: hasDetails ? undefined : 104,
        minHeight: 104,
        maxHeight: hasDetails ? undefined : 104,
        boxSizing: "border-box", overflow: hasDetails ? "visible" : "hidden",
        borderRadius: 8, border: `1px solid ${tone.accent}55`, background: "rgba(18, 24, 35, 0.92)",
      }}
    >
      <div style={{ padding: "11px 13px", display: "grid", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <strong style={titleStyle}>{name}</strong>
          <span style={{ ...statusStyle, color: tone.accent, background: tone.soft }}>{tone.label}</span>
        </div>
        <div
          title={currentStage}
          aria-live={status === "waiting" || status === "failed" ? "assertive" : "polite"}
          style={{ ...detailStyle, color: summary?.workflow_error ? tone.accent : "#b9c2d0" }}
        >
          {currentStage}
        </div>
        <div
          role="progressbar" aria-label={`${name}进度`} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={percent}
          aria-valuetext={`${tone.label}，${currentStage}，${completed}/${total}，${elapsedLabel}`}
          style={progressTrackStyle}
        >
          <div style={{
            width: `${percent}%`, height: "100%", borderRadius: 3, background: tone.accent,
            transition: status === "running" ? "width 220ms ease" : "none",
          }} />
        </div>
        <div style={footerStyle}>
          <span>{completed}/{total} · {elapsedLabel}</span>
          {summary?.workflow_delivery ? (
            <span data-testid="workflow-delivery-status">
              {DELIVERY_LABELS[summary.workflow_delivery.status]}
            </span>
          ) : null}
          <span>{warningCount > 0 ? `⚠ ${warningCount}` : `${percent}%`}</span>
          {orderedStages.length > 0 ? (
            <button
              type="button" aria-expanded={allExpanded} aria-controls={detailsId}
              onClick={toggleAll} style={toggleStyle}
            >
              {allExpanded ? "收起阶段" : `查看阶段 (${orderedStages.length})`}
            </button>
          ) : null}
        </div>
        {capabilityOperation ? (
          <CapabilityOperationCard
            operation={capabilityOperation}
            onAction={onCapabilityOperationAction}
            compact
          />
        ) : null}
        {v5 ? (
          <div data-testid="workflow-v5-overview" style={v5OverviewStyle}>
            <span>核心覆盖 {v5.dimension_counts.core_covered}/{v5.dimension_counts.core_total}</span>
            <span>有效/第一方来源 {v5.source_counts.valid}/{v5.source_counts.first_party}</span>
            <span>当前缺口 {v5.active_gap === "none" ? "无" : `维度 ${v5.active_gap.dimension_ordinal + 1}`}</span>
            <span>质量 {v5.quality_score === "none" ? "待评估" : v5.quality_score}</span>
            <span>硬失败 {v5.hard_failures.length}</span>
            <span>软检查点 {v5.soft_checkpoint === "reached" ? "已到达" : v5.soft_checkpoint === "before" ? "未到达" : "无"}</span>
            <span>续租 {v5.lease_reason === "none" ? "无" : v5.lease_reason}</span>
            <span>预计终态 {v5.predicted_delivery}</span>
            {v5.failed_dimensions.map((dimension) => (
              <span key={`failed-dimension-${dimension.dimension_ordinal}`}>
                维度 {dimension.dimension_ordinal + 1}
                （{dimension.status === "uncovered" ? "未覆盖" : "部分覆盖"}）：
                {dimension.reason_codes.map((code) => V5_GAP_REASON_LABELS[code]).join("、")}
              </span>
            ))}
            {v5.rejection_reasons.length > 0 ? (
              <span>
                证据未通过原因：{v5.rejection_reasons
                  .map((item) => `${V5_REJECTION_REASON_LABELS[item.reason_code]} ${item.count}`)
                  .join("、")}
              </span>
            ) : null}
          </div>
        ) : null}
        {v7Children.length > 0 ? (
          <div
            data-testid="workflow-v7-children"
            aria-label={`调研子方向，共 ${v7Children.length} 个`}
            style={{ display: "grid", gap: 6, marginTop: 4 }}
          >
            <strong style={{ color: "#dbeafe", fontSize: 12 }}>
              主 Agent 拆出的 {v7Children.length} 个子方向
            </strong>
            {v7Children.map((child) => (
              <div
                key={child.child_id}
                data-testid={`workflow-v7-child-${child.child_id}`}
                data-status={child.status}
                style={{
                  display: "grid", gap: 2, padding: "6px 8px", borderRadius: 6,
                  background: "rgba(30,41,59,0.72)", border: "1px solid rgba(148,163,184,0.18)",
                }}
              >
                <span title={child.question} style={{ color: "#e2e8f0", fontSize: 12 }}>
                  {child.question}
                </span>
                <span style={{ color: child.status === "insufficient" ? "#fbbf24" : "#94a3b8", fontSize: 11 }}>
                  {V7_CHILD_LABELS[child.status]} · 尝试 {child.attempt}/{child.max_attempts} · 来源 {child.n_sources}
                </span>
              </div>
            ))}
          </div>
        ) : null}
        {retryAvailable && status !== "failed" ? (
          <div aria-live="polite" style={controlStyle}>
            <span>{v5?.control_status === "accepted" || v5?.control_status === "observed"
              ? `正在收敛（${v5.control_status}，剩余 ${settleRemaining ?? 0} 秒）`
              : "可执行调研操作"}</span>
            <button
              type="button"
              onClick={retry}
              disabled={retryState.status === "pending" || retryState.status === "resolved"}
              style={retryButtonStyle}
            >
              {retryState.status === "pending" ? "正在提交…"
                : retryState.status === "resolved" ? "已接受"
                  : actionId === "generate_now" ? "立即用现有证据生成"
                    : actionId === "cancel_settle" ? "停止当前收敛并生成"
                      : actionId === "continue_research" ? "继续补充调研"
                        : "从头重试"}
            </button>
            {retryState.error ? <span role="alert">{retryState.error}</span> : null}
          </div>
        ) : null}
        {status === "failed" ? (
          <div role="alert" aria-live="assertive" style={failureSummaryStyle}>
            <strong>{summary?.workflow_error || "调研未能完成"}</strong>
            {summary?.workflow_metrics ? (
              <span>{[
                ["真实请求", summary.workflow_metrics.actual_requests],
                ["空结果", summary.workflow_metrics.empty],
                ["超时", summary.workflow_metrics.timeouts],
                ["cooldown 跳过", summary.workflow_metrics.cooldown_skips],
                ["probe", summary.workflow_metrics.probes],
              ].filter(([, value]) => value !== undefined).map(([label, value]) => `${label} ${value}`).join(" / ")}</span>
            ) : null}
            {retryAvailable ? (
              <button
                type="button"
                onClick={retry}
                disabled={retryState.status === "pending" || retryState.status === "resolved"}
                style={retryButtonStyle}
              >
                {retryState.status === "pending" ? "正在创建新调研…"
                  : retryState.status === "ambiguous" ? "确认重试状态"
                    : retryState.status === "resolved" ? "新调研已创建"
                      : actionId === "retry_from_start" ? "从头重试" : "执行操作"}
              </button>
            ) : null}
            {retryState.error ? <span>{retryState.error}</span> : null}
          </div>
        ) : null}
      </div>

      {skippedStages.length > 0 ? (
        <div style={skippedStyle}>
          <button
            type="button"
            aria-expanded={skippedExpanded}
            aria-controls={safeDomId("workflow-skipped", runId)}
            onClick={() => setSkippedExpanded((value) => !value)}
            style={skippedButtonStyle}
          >
            <span aria-hidden="true">⊘</span>
            <strong>后续 {skippedStages.length} 步未执行</strong>
            <span>{skippedExpanded ? "收起" : "查看"}</span>
          </button>
          <div id={safeDomId("workflow-skipped", runId)} hidden={!skippedExpanded} style={skippedListStyle}>
            {skippedStages.map((stageId) => <span key={stageId}>{STAGE_LABELS[stageId] || stageId}</span>)}
          </div>
        </div>
      ) : null}

      {timelineRows.length > 0 ? (
        <div
          id={detailsId}
          ref={timelineRef}
          data-testid={`workflow-compact-timeline-${runId}`}
          aria-label="调研阶段时间线"
          style={timelineStyle}
        >
          {timelineRows.map((row) => {
            const rowTone = TIMELINE_TONES[row.status];
            const expanded = expandedKeys.has(row.key);
            const rowDetailsId = safeDomId(`workflow-stage-detail-${runId}`, row.key);
            const message = row.message;
            return (
              <div key={row.key} data-testid={`timeline-row-${row.key}`} style={timelineItemStyle}>
                <button
                  type="button"
                  aria-expanded={expanded}
                  aria-controls={rowDetailsId}
                  aria-label={`${row.label}，${rowTone.label}，查看详情`}
                  onClick={() => toggleRow(row.key)}
                  style={{ ...timelineButtonStyle, background: rowTone.soft }}
                >
                  <span aria-hidden="true" style={{ ...timelineIconStyle, color: rowTone.color, borderColor: rowTone.color }}>
                    {rowTone.icon}
                  </span>
                  <span style={timelineTextStyle}>
                    <span style={timelineHeadingStyle}>
                      <strong>{row.label}</strong>
                      <span style={{ color: rowTone.color }}>{rowTone.label}</span>
                    </span>
                    <span style={timelineActionStyle}>{row.action}</span>
                    <span title={row.result} style={timelineResultStyle}>{row.result}</span>
                    {row.reasons.length > 0 ? (
                      <span style={{ ...timelineReasonStyle, color: rowTone.color }}>{row.reasons.join("；")}</span>
                    ) : null}
                  </span>
                  <span aria-hidden="true" style={{ color: "#8492a6", fontSize: 11 }}>{expanded ? "▴" : "▾"}</span>
                </button>
                <div id={rowDetailsId} hidden={!expanded}>
                  <article
                    data-testid={row.durable ? `workflow-stage-${message.workflow_event_id || message.id}` : undefined}
                    data-degraded={row.status === "degraded" ? "true" : "false"}
                    style={stageBubbleStyle}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <strong style={{ color: "#dce8f5", fontSize: 11.5 }}>{row.action}</strong>
                      <span style={{ color: "#8492a6", fontSize: 10 }}>
                        {message.workflow_duration_ms === undefined ? rowTone.label : formatDuration(message.workflow_duration_ms)}
                      </span>
                    </div>
                    <div style={stageSummaryStyle}>{row.result}</div>
                    {message.workflow_v5 ? (
                      <dl style={v5DetailsStyle}>
                        <dt>做了什么</dt><dd>{message.workflow_v5.action}</dd>
                        <dt>得到什么</dt><dd>{message.workflow_v5.result}</dd>
                        <dt>舍弃什么</dt><dd>{message.workflow_v5.discarded === "none" ? "无" : message.workflow_v5.discarded}</dd>
                        <dt>仍缺什么</dt><dd>{message.workflow_v5.remaining_gap === "none" ? "无" : message.workflow_v5.remaining_gap}</dd>
                        <dt>下一步</dt><dd>{message.workflow_v5.next_step === "none" ? "无" : message.workflow_v5.next_step}</dd>
                      </dl>
                    ) : null}
                    {row.reasons.length > 0 ? <div style={stageReasonStyle}>{row.reasons.join("；")}</div> : null}
                    {message.workflow_metrics ? (
                      <div style={metricsStyle}>
                        {Object.entries(message.workflow_metrics)
                          .filter(([key]) => key in METRIC_LABELS)
                          .map(([key, value]) => (
                            <span key={key}>{METRIC_LABELS[key]}: {String(value)}</span>
                          ))}
                      </div>
                    ) : null}
                    <div style={stageFooterStyle}>
                      <span>{rowTone.label}</span>
                      {message.workflow_next_stage ? <span>下一步：{message.workflow_next_stage}</span> : null}
                    </div>
                  </article>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

const titleStyle: CSSProperties = { minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#e8edf6", fontSize: 12.5 };
const statusStyle: CSSProperties = { flexShrink: 0, padding: "2px 7px", borderRadius: 4, fontSize: 10.5, fontWeight: 600, whiteSpace: "nowrap" };
const detailStyle: CSSProperties = { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11.5, lineHeight: "18px" };
const progressTrackStyle: CSSProperties = { height: 6, alignSelf: "center", borderRadius: 3, overflow: "hidden", background: "rgba(148,163,184,0.18)" };
const footerStyle: CSSProperties = { display: "flex", alignItems: "center", gap: 8, color: "#7f8a99", fontSize: 10.5, lineHeight: "18px" };
const toggleStyle: CSSProperties = { marginLeft: "auto", padding: 0, border: 0, background: "transparent", color: "#8bcdf4", font: "inherit", cursor: "pointer" };
const timelineStyle: CSSProperties = { display: "grid", gap: 6, maxHeight: 210, overflowY: "auto", padding: "0 10px 10px", scrollbarWidth: "thin" };
const timelineItemStyle: CSSProperties = { display: "grid", gap: 5 };
const timelineButtonStyle: CSSProperties = { width: "100%", display: "flex", alignItems: "flex-start", gap: 8, padding: "8px 9px", border: "1px solid rgba(148,163,184,0.18)", borderRadius: 7, color: "inherit", textAlign: "left", cursor: "pointer", font: "inherit" };
const timelineIconStyle: CSSProperties = { flex: "0 0 18px", width: 18, height: 18, display: "grid", placeItems: "center", border: "1px solid", borderRadius: "50%", fontWeight: 700, fontSize: 11 };
const timelineTextStyle: CSSProperties = { minWidth: 0, flex: 1, display: "grid", gap: 2 };
const timelineHeadingStyle: CSSProperties = { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, color: "#dce8f5", fontSize: 11.5 };
const timelineActionStyle: CSSProperties = { color: "#b9c2d0", fontSize: 11 };
const timelineResultStyle: CSSProperties = { color: "#91a1b5", fontSize: 10.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const timelineReasonStyle: CSSProperties = { fontSize: 10.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const stageBubbleStyle: CSSProperties = { display: "grid", gap: 5, margin: "0 4px", padding: "9px 10px", borderRadius: 7, border: "1px solid rgba(148,163,184,0.2)", background: "rgba(255,255,255,0.04)" };
const stageSummaryStyle: CSSProperties = { color: "#b9c2d0", fontSize: 11, lineHeight: 1.45 };
const stageReasonStyle: CSSProperties = { color: "#fb923c", fontSize: 10.5, lineHeight: 1.4 };
const failureSummaryStyle: CSSProperties = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, paddingTop: 3, color: "#fca5a5", fontSize: 10.5 };
const retryButtonStyle: CSSProperties = { border: "1px solid rgba(248,113,113,0.55)", borderRadius: 5, padding: "3px 8px", background: "rgba(248,113,113,0.12)", color: "#fecaca", cursor: "pointer", font: "inherit" };
const skippedStyle: CSSProperties = { display: "grid", gap: 4, margin: "0 10px 8px", border: "1px solid rgba(248,113,113,0.3)", borderRadius: 7, background: "rgba(127,29,29,0.12)" };
const skippedButtonStyle: CSSProperties = { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, width: "100%", padding: "7px 9px", border: 0, background: "transparent", color: "#fca5a5", cursor: "pointer", font: "inherit" };
const skippedListStyle: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 6, padding: "0 9px 8px", color: "#cbd5e1", fontSize: 10.5 };
const metricsStyle: CSSProperties = { display: "flex", flexWrap: "wrap", gap: "3px 9px", color: "#91a1b5", fontSize: 10 };
const stageFooterStyle: CSSProperties = { display: "flex", justifyContent: "space-between", gap: 8, color: "#8492a6", fontSize: 10 };
const v5OverviewStyle: CSSProperties = { display: "flex", flexWrap: "wrap", gap: "4px 10px", color: "#a9bad0", fontSize: 10.5, lineHeight: 1.45 };
const controlStyle: CSSProperties = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, color: "#b9c2d0", fontSize: 10.5 };
const v5DetailsStyle: CSSProperties = { display: "grid", gridTemplateColumns: "72px 1fr", gap: "3px 8px", margin: 0, color: "#a9bad0", fontSize: 10.5 };
