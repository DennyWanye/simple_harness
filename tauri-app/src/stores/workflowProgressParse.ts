// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { WorkflowDeliveryAggregate } from "../types/messages";
import type {
  WorkflowV5ControlAction,
  WorkflowV5ControlStatus,
  WorkflowV5ProgressProjection,
  WorkflowV7ChildStatus,
  WorkflowV7ChildProgress,
  WorkflowEventEnvelope,
} from "./sessionTypes";

export const WORKFLOW_CARD_EVENTS = new Set([
  "workflow.accepted",
  "workflow.progress",
  "workflow.decision",
  "workflow.final",
]);

export const DEEP_RESEARCH_STAGE_METRICS: Record<string, readonly string[]> = {
  normalize: ["mode"],
  plan: ["question_count", "active_branch_count"],
  expand: ["query_count"],
  search: [
    "providers",
    "providers_attempted",
    "providers_hit",
    "actual_requests",
    "hits",
    "empty",
    "timeouts",
    "cooldown_skips",
    "busy_skips",
    "queue_timeouts",
    "probes",
    "rescue_considered_count",
    "rescue_executed_count",
    "candidates",
    "kept",
  ],
  direct: ["direct_sources", "candidates"],
  fetch: ["attempted", "succeeded", "dropped"],
  score: ["passages", "kept"],
  gap: ["iteration", "followup_count", "new_evidence"],
  rerank: ["passages", "domains"],
  synth: ["sections", "claim_count"],
  cite: [
    "citations",
    "domains",
    "supported",
    "unsupported",
    "support_rate",
    "factual_claims_pre_repair",
    "supported_factual",
    "published",
    "discarded",
    "repaired",
    "body_bytes",
  ],
  persist: ["artifact_count", "report_bytes", "status"],
  finalize: [
    "citations",
    "status",
    "published",
    "discarded",
    "repaired",
    "actual_requests",
    "hits",
    "empty",
    "timeouts",
    "cooldown_skips",
    "busy_skips",
    "queue_timeouts",
    "probes",
    "rescue_considered_count",
    "rescue_executed_count",
    "candidates",
  ],
};

export const DEEP_RESEARCH_SKIPPABLE_STAGES = new Set([
  "fetch",
  "score",
  "gap",
  "rerank",
  "synth",
  "cite",
  "persist",
]);

export const WORKFLOW_RESULT_CODES = new Set([
  "started",
  "waiting",
  "failed",
  "cancelled",
  "stage_ok",
  "stage_degraded",
  "success",
  "partial",
  "degraded",
  "completed",
  "insufficient_evidence",
  "no_results",
]);

export const WORKFLOW_DIAGNOSTIC_CODES = new Set([
  "cooldown",
  "half_open_busy",
  "timeout",
  "blocked",
  "captcha",
  "rate_limit",
  "http_error",
  "invalid_response",
  "budget_exhausted",
  "provider_degraded",
  "partial_results",
  "evidence_missing",
  "low_quality_evidence",
  "missing_exact_token",
  "insufficient_support",
  "claim_unsupported",
  "claim_pruned",
  "deterministic_repair",
  "insufficient_evidence",
  "artifact_missing",
  "no_results",
  "degraded",
  "deadline_exhausted",
  "search_port_unavailable",
  "provider_failure",
  "direct_failure",
  "fetch_failure",
  "blob_unavailable",
  "low_quality_source",
  "low_quality_content",
  "search_degraded",
  "support_rate_below_threshold",
  "published_factual_below_threshold",
  "citation_count_below_threshold",
  "domain_count_below_threshold",
  "body_bytes_below_threshold",
]);

export const WORKFLOW_DELIVERY_STATUSES = new Set<WorkflowDeliveryAggregate["status"]>([
  "queued",
  "delivering",
  "delivered",
  "retrying",
  "fenced",
  "failed",
]);

export function workflowEventTime(event: WorkflowEventEnvelope): number {
  const created = Number(event.created_at);
  return Number.isFinite(created) && created > 0 ? created * 1000 : Date.now();
}

export function workflowErrorText(value: unknown): string | undefined {
  if (!value) return undefined;
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    for (const key of ["message", "message_ref", "reason", "code"]) {
      if (typeof item[key] === "string" && item[key]) {
        const text = item[key] as string;
        const friendly: Record<string, string> = {
          "provider:insufficient_balance": "模型服务余额不足，请充值或切换模型后重试。",
          // 2026-08-09：托管登录移除后没有"重新登录"这回事，改为指向设置页改 key。
          "provider:relay_key_invalid":
            "模型服务密钥无效，请在「设置 → LLM Provider」更新 apiKey 后重试。",
          "provider:empty_api_key": "没有可用的模型服务凭据，请登录或配置 API Key。",
        };
        return friendly[text] ?? text;
      }
    }
  }
  return undefined;
}

export function workflowNumber(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : undefined;
}

export function parseWorkflowDeliveryAggregate(
  value: unknown,
): WorkflowDeliveryAggregate | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const raw = value as Record<string, unknown>;
  const status = String(raw.status || "") as WorkflowDeliveryAggregate["status"];
  const runId = String(raw.run_id || "").trim();
  const manifestRef = String(raw.manifest_ref || "").trim();
  const countKeys = [
    "required_total",
    "pending",
    "delivering",
    "delivered",
    "retrying",
    "fenced",
    "failed",
  ] as const;
  const counts = Object.fromEntries(countKeys.map((key) => [key, Number(raw[key])])) as Record<
    (typeof countKeys)[number],
    number
  >;
  const updatedAt = Number(raw.updated_at);
  if (
    raw.schema_version !== 1 ||
    !runId ||
    !manifestRef ||
    !WORKFLOW_DELIVERY_STATUSES.has(status) ||
    !countKeys.every((key) => Number.isInteger(counts[key]) && counts[key] >= 0) ||
    !Number.isFinite(updatedAt) ||
    updatedAt < 0
  )
    return undefined;
  if (
    counts.pending +
      counts.delivering +
      counts.delivered +
      counts.retrying +
      counts.fenced +
      counts.failed !==
      counts.required_total ||
    counts.required_total < 1
  )
    return undefined;
  return {
    schema_version: 1,
    run_id: runId,
    manifest_ref: manifestRef,
    status,
    required_total: counts.required_total,
    pending: counts.pending,
    delivering: counts.delivering,
    delivered: counts.delivered,
    retrying: counts.retrying,
    fenced: counts.fenced,
    failed: counts.failed,
    updated_at: updatedAt,
  };
}

export function workflowSafeText(value: unknown, maximum = 240): string | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.trim().replace(/\s+/g, " ").slice(0, maximum);
  if (!text || /https?:\/\//i.test(text)) return undefined;
  return text;
}

export function workflowResultCode(value: unknown): string | undefined {
  const code = typeof value === "string" ? value.trim() : "";
  return WORKFLOW_RESULT_CODES.has(code) ? code : undefined;
}

export function workflowDiagnosticCodes(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const codes = Array.from(
    new Set(
      value.map((item) => String(item)).filter((item) => WORKFLOW_DIAGNOSTIC_CODES.has(item)),
    ),
  ).sort();
  return codes.length > 0 ? codes : undefined;
}

export function workflowSkippedStageIds(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const stages = Array.from(
    new Set(
      value.map((item) => String(item)).filter((item) => DEEP_RESEARCH_SKIPPABLE_STAGES.has(item)),
    ),
  );
  return stages.length > 0 ? stages.slice(0, 7) : undefined;
}

export function sanitizeWorkflowMetrics(
  stageId: string,
  value: unknown,
): Record<string, string | number | boolean> | undefined {
  if (!value || typeof value !== "object") return undefined;
  const allowed = DEEP_RESEARCH_STAGE_METRICS[stageId] ?? [];
  const source = value as Record<string, unknown>;
  const metrics: Record<string, string | number | boolean> = {};
  for (const key of allowed) {
    const item = source[key];
    if (typeof item === "string" || typeof item === "boolean") {
      metrics[key] = item;
    } else if (typeof item === "number" && Number.isFinite(item)) {
      metrics[key] = item;
    }
  }
  return Object.keys(metrics).length > 0 ? metrics : undefined;
}

export const V5_ACTIONS = new Set([
  "normalize_request",
  "model_dimensions",
  "plan_queries",
  "expand_queries",
  "search_sources",
  "find_first_party_sources",
  "fetch_sources",
  "score_evidence",
  "evaluate_gaps",
  "research_gap",
  "commit_gap_result",
  "rerank_evidence",
  "synthesize_report",
  "audit_quality",
  "repair_report",
  "commit_repair",
  "persist_report",
  "finalize_delivery",
  "finalize_insufficient",
]);

export const V5_RESULTS = new Set([
  "started",
  "waiting",
  "failed",
  "cancelled",
  "completed",
  "insufficient_evidence",
]);

export const V5_CONTROL_ACTIONS = new Set<WorkflowV5ControlAction>([
  "none",
  "generate_now",
  "continue_research",
  "retry_from_start",
  "cancel_settle",
]);

export const V5_CONTROL_STATUSES = new Set<WorkflowV5ControlStatus>([
  "none",
  "open",
  "accepted",
  "observed",
  "settled",
  "consumed",
  "rejected",
  "expired",
]);

export const V5_HARD_FAILURES = new Set([
  "completed_core_uncovered",
  "unsupported_key_claim",
  "invalid_or_captcha_citation",
  "secondary_replaces_available_official",
  "citation_dimension_mismatch",
  "internal_diagnostics_leak",
]);

export const V5_LEASE_REASONS = new Set([
  "none",
  "before_soft_or_lease_checkpoint",
  "lease_renewed_measurable_gain",
  "lease_not_renewed_no_gain",
  "automatic_cap",
  "running",
  "generate_now_settling",
  "cancelled",
  "plateau_settling",
  "lease_no_gain_settling",
  "automatic_cap_settling",
]);

export const V5_GAP_REASONS = new Set([
  "insufficient_admitted_passages",
  "insufficient_strong_distinct_families",
  "winning_relevance_below_threshold",
  "duplicate_rate_above_threshold",
  "invalid_rate_above_threshold",
  "first_party_requirement_unsatisfied",
  "evidence_gap",
]);

export const V5_REJECTION_REASONS = new Set([
  "invalid_page",
  "body_too_short",
  "body_span_missing",
  "dimension_relevance_below_threshold",
  "other_rejected",
]);

export function nonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

export function v5MaybeCount(value: unknown): number | "none" | undefined {
  return value === "none" ? "none" : nonNegativeInteger(value);
}

export function parseV5Progress(
  payload: Record<string, unknown>,
): WorkflowV5ProgressProjection | undefined {
  if (
    payload.schema_version !== 5 ||
    payload.capability !== "deep_research_progress_v5" ||
    payload.workflow_name !== "deep_research" ||
    payload.workflow_version !== "v5"
  )
    return undefined;
  const action = String(payload.action || "");
  const result = String(payload.result || "");
  const nextStep = String(payload.next_step || "");
  const controlAction = String(payload.control_action || "") as WorkflowV5ControlAction;
  const controlStatus = String(payload.control_status || "") as WorkflowV5ControlStatus;
  if (
    !V5_ACTIONS.has(action) ||
    !V5_RESULTS.has(result) ||
    (nextStep !== "none" && !V5_ACTIONS.has(nextStep)) ||
    !V5_CONTROL_ACTIONS.has(controlAction) ||
    !V5_CONTROL_STATUSES.has(controlStatus)
  ) {
    return undefined;
  }
  const dimensions = payload.dimension_counts as Record<string, unknown> | undefined;
  const changes = payload.dimension_status_changes as Record<string, unknown> | undefined;
  const sources = payload.source_counts as Record<string, unknown> | undefined;
  if (!dimensions || !changes || !sources) return undefined;
  const dimensionCounts = {
    total: nonNegativeInteger(dimensions.total),
    core_total: nonNegativeInteger(dimensions.core_total),
    covered: nonNegativeInteger(dimensions.covered),
    partially_covered: nonNegativeInteger(dimensions.partially_covered),
    uncovered: nonNegativeInteger(dimensions.uncovered),
    not_applicable: nonNegativeInteger(dimensions.not_applicable),
    core_covered: nonNegativeInteger(dimensions.core_covered),
    core_partially_covered: nonNegativeInteger(dimensions.core_partially_covered),
    core_uncovered: nonNegativeInteger(dimensions.core_uncovered),
  };
  const dimensionChanges = {
    improved: v5MaybeCount(changes.improved),
    regressed: v5MaybeCount(changes.regressed),
    unchanged: v5MaybeCount(changes.unchanged),
  };
  const sourceCounts = {
    valid: nonNegativeInteger(sources.valid),
    first_party: nonNegativeInteger(sources.first_party),
  };
  if (
    Object.values(dimensionCounts).some((value) => value === undefined) ||
    Object.values(dimensionChanges).some((value) => value === undefined) ||
    Object.values(sourceCounts).some((value) => value === undefined)
  )
    return undefined;

  let activeGap: WorkflowV5ProgressProjection["active_gap"] = "none";
  if (payload.active_gap !== "none") {
    if (!payload.active_gap || typeof payload.active_gap !== "object") return undefined;
    const gap = payload.active_gap as Record<string, unknown>;
    const gapStatus = String(gap.status || "") as Exclude<
      WorkflowV5ProgressProjection["active_gap"],
      "none"
    >["status"];
    const workKind = String(gap.work_kind || "") as Exclude<
      WorkflowV5ProgressProjection["active_gap"],
      "none"
    >["work_kind"];
    const dimensionOrdinal = nonNegativeInteger(gap.dimension_ordinal);
    if (
      !["pending", "running", "completed", "failed", "cancelled"].includes(gapStatus) ||
      !["query", "source_target", "fetch"].includes(workKind) ||
      dimensionOrdinal === undefined
    ) {
      return undefined;
    }
    activeGap = { status: gapStatus, work_kind: workKind, dimension_ordinal: dimensionOrdinal };
  }
  if (
    !Array.isArray(payload.hard_failures) ||
    payload.hard_failures.some((item) => typeof item !== "string" || !V5_HARD_FAILURES.has(item))
  ) {
    return undefined;
  }
  if (
    !Array.isArray(payload.failed_dimensions) ||
    payload.failed_dimensions.length > 8 ||
    !Array.isArray(payload.rejection_reasons) ||
    payload.rejection_reasons.length > 5
  ) {
    return undefined;
  }
  const failedDimensions: WorkflowV5ProgressProjection["failed_dimensions"] = [];
  for (const value of payload.failed_dimensions) {
    if (!value || typeof value !== "object") return undefined;
    const item = value as Record<string, unknown>;
    const ordinal = nonNegativeInteger(item.dimension_ordinal);
    const failedStatus = String(item.status || "") as "uncovered" | "partially_covered";
    if (
      ordinal === undefined ||
      !["uncovered", "partially_covered"].includes(failedStatus) ||
      !Array.isArray(item.reason_codes) ||
      item.reason_codes.length < 1 ||
      item.reason_codes.length > 6 ||
      item.reason_codes.some((reason) => typeof reason !== "string" || !V5_GAP_REASONS.has(reason))
    ) {
      return undefined;
    }
    failedDimensions.push({
      dimension_ordinal: ordinal,
      status: failedStatus,
      reason_codes: [...item.reason_codes] as string[],
    });
  }
  const rejectionReasons: WorkflowV5ProgressProjection["rejection_reasons"] = [];
  for (const value of payload.rejection_reasons) {
    if (!value || typeof value !== "object") return undefined;
    const item = value as Record<string, unknown>;
    const reasonCode = String(item.reason_code || "");
    const count = nonNegativeInteger(item.count);
    if (!V5_REJECTION_REASONS.has(reasonCode) || count === undefined || count < 1) return undefined;
    rejectionReasons.push({ reason_code: reasonCode, count });
  }
  const discarded = v5MaybeCount(payload.discarded);
  const remainingGap = v5MaybeCount(payload.remaining_gap);
  const qualityScore = v5MaybeCount(payload.quality_score);
  const tokenBudgetRatio = v5MaybeCount(payload.token_budget_ratio);
  const elapsedSeconds = nonNegativeInteger(payload.elapsed_seconds);
  const predicted = String(
    payload.predicted_delivery || "",
  ) as WorkflowV5ProgressProjection["predicted_delivery"];
  const softCheckpoint = String(
    payload.soft_checkpoint || "",
  ) as WorkflowV5ProgressProjection["soft_checkpoint"];
  const leaseReason = String(payload.lease_reason || "");
  const parentOperation = String(payload.parent_operation || "");
  if (
    [discarded, remainingGap, qualityScore, tokenBudgetRatio, elapsedSeconds].some(
      (value) => value === undefined,
    ) ||
    !["pending", "completed", "partial", "insufficient_evidence"].includes(predicted) ||
    !["none", "before", "reached"].includes(softCheckpoint) ||
    !V5_LEASE_REASONS.has(leaseReason) ||
    !(parentOperation === "none" || /^op_[0-9a-f]{20}$/.test(parentOperation))
  )
    return undefined;
  return {
    action,
    result,
    discarded: discarded!,
    remaining_gap: remainingGap!,
    next_step: nextStep,
    dimension_counts: dimensionCounts as WorkflowV5ProgressProjection["dimension_counts"],
    dimension_status_changes:
      dimensionChanges as WorkflowV5ProgressProjection["dimension_status_changes"],
    source_counts: sourceCounts as WorkflowV5ProgressProjection["source_counts"],
    active_gap: activeGap,
    elapsed_seconds: elapsedSeconds!,
    soft_checkpoint: softCheckpoint,
    lease_reason: leaseReason,
    quality_score: qualityScore!,
    hard_failures: [...payload.hard_failures] as string[],
    predicted_delivery: predicted,
    token_budget_ratio: tokenBudgetRatio!,
    control_action: controlAction,
    control_status: controlStatus,
    parent_operation: parentOperation,
    failed_dimensions: failedDimensions,
    rejection_reasons: rejectionReasons,
  };
}

export function isDeepResearchCompletedStage(payload: Record<string, unknown>): boolean {
  const stageId = String(payload.stage_id || "");
  return (
    Number(payload.schema_version) === 2 &&
    String(payload.kind || "") === "stage" &&
    String(payload.workflow_name || "") === "deep_research" &&
    stageId in DEEP_RESEARCH_STAGE_METRICS &&
    String(payload.status || payload.transition || "") === "completed"
  );
}

export const V7_CHILD_STATUSES = new Set<WorkflowV7ChildStatus>([
  "queued",
  "running",
  "retrying",
  "valid",
  "insufficient",
]);

export function parseV7Children(
  payload: Record<string, unknown>,
): WorkflowV7ChildProgress[] | undefined {
  if (
    payload.schema_version !== 7 ||
    payload.kind !== "research_children" ||
    payload.workflow_version !== "v7" ||
    !Array.isArray(payload.children) ||
    payload.children.length < 2 ||
    payload.children.length > 6
  )
    return undefined;
  const seen = new Set<string>();
  const result: WorkflowV7ChildProgress[] = [];
  for (const raw of payload.children) {
    if (!raw || typeof raw !== "object") return undefined;
    const item = raw as Record<string, unknown>;
    const childId = String(item.child_id || "").trim();
    const question = String(item.question || "").trim();
    const status = String(item.status || "") as WorkflowV7ChildStatus;
    const attempt = Number(item.attempt);
    const maxAttempts = Number(item.max_attempts);
    const nSources = Number(item.n_sources);
    if (
      !/^dr-\d+$/.test(childId) ||
      seen.has(childId) ||
      !question ||
      question.length > 600 ||
      !V7_CHILD_STATUSES.has(status) ||
      !Number.isInteger(attempt) ||
      !Number.isInteger(maxAttempts) ||
      attempt < 0 ||
      maxAttempts < 1 ||
      maxAttempts > 3 ||
      attempt > maxAttempts ||
      !Number.isInteger(nSources) ||
      nSources < 0 ||
      nSources > 10_000
    )
      return undefined;
    result.push({
      child_id: childId,
      question,
      status,
      attempt,
      max_attempts: maxAttempts,
      n_sources: nSources,
      reason_code:
        typeof item.reason_code === "string" ? item.reason_code.slice(0, 128) : undefined,
    });
    seen.add(childId);
  }
  return result;
}
