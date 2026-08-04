// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useSyncExternalStore } from "react";

import type {
  PublicRunAggregateOutcome,
  PublicRunMessageView,
  PublicRunSemanticPhase,
  PublicRunSnapshotV3,
  ToolPublicView,
} from "../types/messages";

type Listener = () => void;

const snapshots = new Map<string, PublicRunSnapshotV3>();
const listeners = new Map<string, Set<Listener>>();
let detailsLoader: ((detailRef: string) => void) | null = null;
const TERMINAL_OUTCOMES = new Set(["completed", "completed_with_recovery", "failed", "cancelled"]);

function key(sessionId: string, rootRunId: string): string {
  return `${sessionId}\u0000${rootRunId}`;
}

function record(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function snapshotVersion(snapshot: PublicRunSnapshotV3): number {
  const capturedAt = Math.max(
    snapshot.read_cut?.workflow?.captured_at ?? 0,
    snapshot.read_cut?.state?.captured_at ?? 0,
  );
  if (capturedAt > 0) return capturedAt;
  // `PRAGMA data_version` is scoped to one SQLite connection.  It is only a
  // legacy fallback here; comparing it across independently opened read
  // connections can make a newer terminal snapshot look older.
  return Math.max(
    snapshot.read_cut?.workflow?.data_version ?? 0,
    snapshot.read_cut?.state?.data_version ?? 0,
  );
}

function outcome(value: unknown): PublicRunAggregateOutcome {
  const item = record(value);
  const status = String(item?.status ?? "unknown") as PublicRunAggregateOutcome["status"];
  return {
    status,
    explanation_code: String(item?.explanation_code ?? status),
    evidence_refs: strings(item?.evidence_refs),
    started_at: typeof item?.started_at === "number" ? item.started_at : null,
    ended_at: typeof item?.ended_at === "number" ? item.ended_at : null,
    child_warnings: Array.isArray(item?.child_warnings)
      ? item.child_warnings.flatMap((warning) => {
          const child = record(warning);
          return child
            ? [{
                run_id: String(child.run_id ?? ""),
                status: String(child.status ?? "unknown"),
                evidence_refs: strings(child.evidence_refs),
              }]
            : [];
        })
      : [],
  };
}

function workflowSteps(
  value: unknown,
  phaseId: string,
): PublicRunSemanticPhase["workflow_steps"] {
  if (!Array.isArray(value)) return [];
  const byId = new Map<string, PublicRunSemanticPhase["workflow_steps"][number]>();
  value.forEach((entry, index) => {
    const publicStep = record(entry);
    if (!publicStep) return;
    const workflowStepId = String(publicStep.workflow_step_id ?? `${phaseId}:${index}`);
    const previous = byId.get(workflowStepId);
    byId.set(workflowStepId, {
      workflow_step_id: workflowStepId,
      label: typeof publicStep.label === "string" ? publicStep.label : previous?.label ?? null,
      status: typeof publicStep.status === "string" ? publicStep.status : previous?.status ?? null,
      step_index: typeof publicStep.step_index === "number"
        ? publicStep.step_index
        : previous?.step_index ?? null,
      step_total: typeof publicStep.step_total === "number"
        ? publicStep.step_total
        : previous?.step_total ?? null,
    });
  });
  return [...byId.values()];
}

function phase(value: unknown, index: number): PublicRunSemanticPhase | null {
  const item = record(value);
  if (!item) return null;
  const phaseId = String(item.phase_id ?? item.id ?? `phase-${index}`);
  return {
    phase_id: phaseId,
    taxonomy: String(item.taxonomy ?? "execute") as PublicRunSemanticPhase["taxonomy"],
    title: String(item.title ?? "执行任务"),
    status: String(item.status ?? "unknown") as PublicRunSemanticPhase["status"],
    mapping_reason: String(item.mapping_reason ?? "public_projection"),
    evidence_refs: strings(item.evidence_refs),
    tool_refs: strings(item.tool_refs),
    child_refs: strings(item.child_refs),
    workflow_steps: workflowSteps(item.workflow_steps, phaseId),
    items: Array.isArray(item.items)
      ? item.items.flatMap((entry, itemIndex) => {
          const publicItem = record(entry);
          if (!publicItem) return [];
          return [{
            stable_id: String(publicItem.stable_id ?? `${phaseId}:item:${itemIndex}`),
            kind: String(publicItem.kind ?? "fact"),
            narration_code: typeof publicItem.narration_code === "string" ? publicItem.narration_code : null,
            safe_text: typeof publicItem.safe_text === "string" ? publicItem.safe_text : null,
            status: typeof publicItem.status === "string" ? publicItem.status : null,
            action_code: typeof publicItem.action_code === "string" ? publicItem.action_code : null,
            tool_name: typeof publicItem.tool_name === "string" ? publicItem.tool_name : null,
            public_name: typeof publicItem.public_name === "string" ? publicItem.public_name : null,
            safe_target_label: typeof publicItem.safe_target_label === "string" ? publicItem.safe_target_label : null,
            detail_ref: typeof publicItem.detail_ref === "string" ? publicItem.detail_ref : null,
            created_at: typeof publicItem.created_at === "number" ? publicItem.created_at : null,
          }];
        })
      : [],
    current_step: typeof item.current_step === "number" ? item.current_step : null,
    total_steps: typeof item.total_steps === "number" ? item.total_steps : null,
    order_key: Array.isArray(item.order_key) && item.order_key.length === 4
      ? item.order_key as [number, number, number, string]
      : [index, 0, 0, phaseId],
  };
}

function tool(value: unknown, index: number): ToolPublicView | null {
  const item = record(value);
  if (!item) return null;
  const payload = record(item.public_payload) ?? item;
  const toolRef = String(item.tool_ref ?? item.stable_id ?? `tool-${index}`);
  return {
    tool_ref: toolRef,
    stable_id: String(item.stable_id ?? toolRef),
    phase_id: typeof item.phase_id === "string" ? item.phase_id : null,
    public_name: String(payload.public_name ?? payload.tool_name ?? "工具"),
    action_label: String(payload.action_label ?? payload.activity_kind ?? payload.action_code ?? "执行操作"),
    safe_target_label: typeof payload.safe_target_label === "string" ? payload.safe_target_label : null,
    status: String(payload.status ?? "unknown"),
    duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : null,
    public_input: payload.public_input ?? payload.safe_input,
    public_result: payload.public_result ?? payload.bounded_result,
    result_available: payload.result_available === true ||
      payload.public_result !== undefined || payload.bounded_result !== undefined,
    details_available: payload.details_available === true ||
      payload.public_input !== undefined || payload.safe_input !== undefined,
    truncated: payload.truncated === true ||
      (Array.isArray(payload.truncation_hashes) && payload.truncation_hashes.length > 0),
    unavailable_reason: typeof payload.unavailable_reason === "string"
      ? payload.unavailable_reason
      : null,
    detail_ref: typeof item.detail_ref === "string" ? item.detail_ref : null,
    created_at: typeof item.created_at === "number" ? item.created_at : null,
  };
}

function narration(value: unknown, index: number): PublicRunMessageView | null {
  const item = record(value);
  if (!item || typeof item.text !== "string" || !item.text.trim()) return null;
  const messageId = String(item.message_id ?? item.stable_id ?? `message-${index}`);
  return {
    message_id: messageId,
    stable_id: String(item.stable_id ?? messageId),
    phase_id: typeof item.phase_id === "string" ? item.phase_id : null,
    text: item.text,
    created_at: numberValue(item.created_at),
  };
}

/**
 * The only compatibility boundary for inspector data. Components never inspect
 * the transport payload or durable/raw fields themselves.
 */
export function normalizePublicRunSnapshot(value: unknown): PublicRunSnapshotV3 | null {
  const item = record(value);
  if (!item) return null;
  const schema = String(item.schema_version ?? "");
  if (schema === "3") {
    const sessionId = String(item.session_id ?? "");
    const rootRunId = String(item.root_run_id ?? "");
    if (!sessionId || !rootRunId) return null;
    const totals = record(item.totals);
    const normalizedPhases = Array.isArray(item.semantic_phases)
      ? item.semantic_phases.flatMap((entry, index) => phase(entry, index) ?? [])
      : [];
    const toolValues = item.tool_public_views ?? item.tools;
    const messageValues = item.public_messages ?? item.messages;
    const phaseTools = normalizedPhases.flatMap((semanticPhase) =>
      semanticPhase.items.filter((publicItem) => publicItem.kind === "tool").map((publicItem) => ({
        tool_ref: publicItem.detail_ref ?? publicItem.stable_id,
        stable_id: publicItem.stable_id,
        phase_id: semanticPhase.phase_id,
        public_name: publicItem.public_name ?? publicItem.tool_name ?? publicItem.action_code ?? "工具",
        action_label: publicItem.action_code ?? "执行操作",
        safe_target_label: publicItem.safe_target_label,
        status: publicItem.status ?? "unknown",
        detail_ref: publicItem.detail_ref,
        details_available: Boolean(publicItem.detail_ref),
        result_available: Boolean(publicItem.detail_ref),
        created_at: publicItem.created_at,
      })));
    const phaseMessages = normalizedPhases.flatMap((semanticPhase) =>
      semanticPhase.items.filter((publicItem) =>
        publicItem.kind === "narration" && Boolean(publicItem.safe_text),
      ).map((publicItem) => ({
        message_id: publicItem.stable_id,
        stable_id: publicItem.stable_id,
        phase_id: semanticPhase.phase_id,
        text: publicItem.safe_text ?? "",
        created_at: publicItem.created_at ?? 0,
      })));
    return {
      schema_version: "3",
      projection_id: String(item.projection_id ?? `${sessionId}:${rootRunId}`),
      session_id: sessionId,
      root_run_id: rootRunId,
      aggregate_outcome: outcome(item.aggregate_outcome),
      semantic_phases: normalizedPhases,
      tool_public_views: Array.isArray(toolValues)
        ? toolValues.flatMap((entry, index) => tool(entry, index) ?? [])
        : phaseTools,
      public_messages: Array.isArray(messageValues)
        ? messageValues.flatMap((entry, index) => narration(entry, index) ?? [])
        : phaseMessages,
      totals: {
        workflow_facts: numberValue(totals?.workflow_facts),
        content_facts: numberValue(totals?.content_facts),
        provider_details: numberValue(totals?.provider_details),
        tool_details: numberValue(totals?.tool_details),
      },
      projection_complete: item.projection_complete === true,
      diagnostics: strings(item.diagnostics),
      read_cut: record(item.read_cut) as PublicRunSnapshotV3["read_cut"],
    };
  }

  // Unknown/legacy schemas get a deliberately tiny, safe view. No provider
  // bodies, tool arguments, durable prepared data, or results cross this seam.
  const run = record(item.run);
  const sessionId = String(item.session_id ?? "");
  const rootRunId = String(run?.root_run_id ?? run?.run_id ?? "");
  if (!sessionId || !rootRunId) return null;
  const status = String(run?.status ?? "unknown") as PublicRunAggregateOutcome["status"];
  const legacyTools = Array.isArray(item.tool_calls)
    ? item.tool_calls.flatMap((entry, index) => {
        const call = record(entry);
        if (!call) return [];
        const toolRef = String(call.call_record_id ?? call.call_id ?? `legacy-tool-${index}`);
        return [{
          tool_ref: toolRef,
          stable_id: toolRef,
          public_name: String(call.tool_name ?? "工具"),
          action_label: "历史记录",
          status: String(call.outcome_status ?? call.admission_state ?? "unknown"),
          result_available: false,
          details_available: false,
          unavailable_reason: "旧版记录没有可安全公开的详情",
        } satisfies ToolPublicView];
      })
    : [];
  return {
    schema_version: "3",
    projection_id: `legacy:${sessionId}:${rootRunId}`,
    session_id: sessionId,
    root_run_id: rootRunId,
    aggregate_outcome: {
      status,
      explanation_code: "legacy_projection_incomplete",
      evidence_refs: [],
      child_warnings: [],
    },
    semantic_phases: [],
    tool_public_views: legacyTools,
    public_messages: [],
    totals: { workflow_facts: 0, content_facts: 0, provider_details: 0, tool_details: legacyTools.length },
    projection_complete: false,
    diagnostics: ["旧版运行记录仅显示安全摘要，详细阶段不可用"],
    legacy_fallback: true,
  };
}

export const harnessPublicSnapshotStore = {
  get(sessionId: string, rootRunId: string): PublicRunSnapshotV3 | null {
    return snapshots.get(key(sessionId, rootRunId)) ?? null;
  },
  publish(snapshot: PublicRunSnapshotV3): void {
    const storeKey = key(snapshot.session_id, snapshot.root_run_id);
    const current = snapshots.get(storeKey);
    if (current?.projection_id === snapshot.projection_id) return;
    if (current) {
      const currentVersion = snapshotVersion(current);
      const nextVersion = snapshotVersion(snapshot);
      if (nextVersion > 0 && currentVersion > nextVersion) return;
      if (
        current.projection_complete &&
        TERMINAL_OUTCOMES.has(current.aggregate_outcome.status) &&
        !TERMINAL_OUTCOMES.has(snapshot.aggregate_outcome.status)
      ) return;
    }
    snapshots.set(storeKey, snapshot);
    listeners.get(storeKey)?.forEach((listener) => listener());
  },
  mergeToolDetails(
    sessionId: string,
    rootRunId: string,
    projectionId: string,
    items: unknown[],
  ): void {
    const storeKey = key(sessionId, rootRunId);
    const current = snapshots.get(storeKey);
    if (!current || current.projection_id !== projectionId) return;
    const updates = new Map(
      items.flatMap((entry, index) => {
        const value = tool(entry, index);
        return value ? [[value.tool_ref, value] as const] : [];
      }),
    );
    if (updates.size === 0) return;
    const next = {
      ...current,
      tool_public_views: current.tool_public_views.map((item) => {
        const update = updates.get(item.detail_ref ?? item.tool_ref) ?? updates.get(item.tool_ref);
        return update ? { ...item, ...update, stable_id: item.stable_id, phase_id: item.phase_id } : item;
      }),
    };
    snapshots.set(storeKey, next);
    listeners.get(storeKey)?.forEach((listener) => listener());
  },
  remove(sessionId: string, rootRunId: string): void {
    const storeKey = key(sessionId, rootRunId);
    if (!snapshots.delete(storeKey)) return;
    listeners.get(storeKey)?.forEach((listener) => listener());
  },
  subscribe(sessionId: string, rootRunId: string, listener: Listener): () => void {
    const storeKey = key(sessionId, rootRunId);
    const bucket = listeners.get(storeKey) ?? new Set<Listener>();
    bucket.add(listener);
    listeners.set(storeKey, bucket);
    return () => {
      bucket.delete(listener);
      if (bucket.size === 0) listeners.delete(storeKey);
    };
  },
  clearForTests(): void {
    snapshots.clear();
    listeners.forEach((bucket) => bucket.forEach((listener) => listener()));
  },
};

export function useHarnessPublicSnapshot(
  sessionId: string,
  rootRunId: string | null,
): PublicRunSnapshotV3 | null {
  const safeRoot = rootRunId ?? "";
  return useSyncExternalStore(
    (listener) => safeRoot
      ? harnessPublicSnapshotStore.subscribe(sessionId, safeRoot, listener)
      : () => undefined,
    () => safeRoot ? harnessPublicSnapshotStore.get(sessionId, safeRoot) : null,
    () => null,
  );
}

export function configureHarnessPublicDetailsLoader(
  loader: ((detailRef: string) => void) | null,
): void {
  detailsLoader = loader;
}

export function requestHarnessPublicToolDetails(detailRef: string | undefined): void {
  if (detailRef) detailsLoader?.(detailRef);
}
