// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Safe frontend projections for DeskPet capabilities.
 *
 * These types intentionally omit raw tool schemas, credentials, command
 * arguments, and environment variables. The Capability Center consumes the
 * same push stream as workflow progress; it never owns a polling job.
 */

export type CapabilityCategory = "instruction" | "tool" | "mcp" | "pack";
export type CapabilityScope = "run" | "project" | "user" | "builtin";
export type CapabilityHealth =
  | "healthy"
  | "degraded"
  | "unavailable"
  | "validating"
  | "unknown";

export type CapabilityOperationKind =
  | "activate"
  | "install"
  | "update"
  | "build"
  | "repair"
  | "rollback"
  | "uninstall";

export const CAPABILITY_OPERATION_PHASES = [
  "planned",
  "staged",
  "verified",
  "environment_ready",
  "candidate_ready",
  "publish_intent",
  "catalog_swapped",
  "bound",
  "published",
] as const;

export type CapabilityOperationPhase =
  (typeof CAPABILITY_OPERATION_PHASES)[number];

export type CapabilityOperationStatus =
  | "queued"
  | "running"
  | "waiting_external"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";

export type CapabilityOperationAction =
  | "cancel"
  | "retry"
  | "rollback"
  | "uninstall";

export type CapabilityAuthorizationMode = "manual" | "auto";

export interface CapabilitySourceSummary {
  type: "builtin" | "local" | "git" | "marketplace" | "generated" | "unknown";
  label: string;
}

export interface CapabilityManifestSummary {
  schema_version: number;
  manifest_hash: string;
  compatibility: {
    deskpet: string;
    os: string[];
    architectures: string[];
    python: string;
  };
  entries: {
    skills: Array<{ path: string }>;
    tools: Array<{
      id: string;
      provider_name: string;
      runtime: string;
      execution_profile: string;
      input_views: string[];
      entry: string;
      schema: string;
      healthcheck: string;
    }>;
    mcp_servers: Array<{ id: string; config_ref: string }>;
  };
  permissions: string[];
  effects: string[];
  dependencies: {
    python: string[];
    commands: Array<{ name: string; version: string }>;
  };
  files: Array<{ path: string; sha256: string }>;
  uninstall: {
    stop_servers: boolean;
    remove_environment_when_unreferenced: boolean;
  };
}

export interface CapabilityDescriptor {
  capability_id: string;
  name: string;
  description?: string;
  categories: CapabilityCategory[];
  version: string;
  source: CapabilitySourceSummary;
  scope: CapabilityScope;
  health: CapabilityHealth;
  health_summary?: string;
  installed: boolean;
  manifest?: CapabilityManifestSummary;
  available_actions?: Array<"install" | "activate" | "repair" | "uninstall">;
}

export interface CapabilityArtifactSummary {
  artifact_id: string;
  name: string;
  kind?: string;
  ref?: string;
}

export interface CapabilityVerificationReceipt {
  receipt_id: string;
  status: "passed" | "failed" | "unknown";
  summary: string;
  ref?: string;
}

export interface CapabilityOperation {
  operation_id: string;
  capability_id: string;
  capability_name: string;
  kind: CapabilityOperationKind;
  phase: CapabilityOperationPhase;
  status: CapabilityOperationStatus;
  authorization_mode: CapabilityAuthorizationMode;
  current_validation?: string;
  latest_result?: string;
  error_code?: string;
  error_message?: string;
  recovery_hint?: string;
  available_actions: CapabilityOperationAction[];
  artifacts: CapabilityArtifactSummary[];
  verification_receipts: CapabilityVerificationReceipt[];
  progress_percent?: number;
  event_seq?: number;
  started_at?: string;
  updated_at?: string;
}

export type CapabilityOperationPatch = Pick<
  CapabilityOperation,
  "operation_id"
> &
  Partial<Omit<CapabilityOperation, "operation_id">>;

export interface CapabilityOperationEvent {
  operation: CapabilityOperationPatch;
}

export interface CapabilityListResponse {
  type: "capability_list_response" | "capabilities_list_response";
  payload: { capabilities: CapabilityDescriptor[] };
}

export interface CapabilityOperationsResponse {
  type: "capability_operations_response";
  payload: { operations: CapabilityOperation[] };
}

export interface CapabilityOperationEventMessage {
  type: "capability_operation_event" | "capability_operation_update";
  payload: CapabilityOperationEvent;
}

const PHASE_PROGRESS: Record<CapabilityOperationPhase, number> = {
  planned: 5,
  staged: 15,
  verified: 30,
  environment_ready: 45,
  candidate_ready: 60,
  publish_intent: 72,
  catalog_swapped: 82,
  bound: 92,
  published: 100,
};

export function capabilityOperationProgress(
  operation: CapabilityOperation,
): number {
  if (operation.status === "succeeded" || operation.phase === "published") {
    return 100;
  }
  const projected =
    operation.progress_percent ?? PHASE_PROGRESS[operation.phase] ?? 0;
  return Math.max(0, Math.min(100, Math.round(projected)));
}

/**
 * Merge an authoritative snapshot or push event without allowing an older
 * event sequence to overwrite a newer operation projection.
 */
export function mergeCapabilityOperations(
  current: CapabilityOperation[],
  incoming: CapabilityOperation[],
): CapabilityOperation[] {
  const order = current.map((item) => item.operation_id);
  const byId = new Map(current.map((item) => [item.operation_id, item]));

  for (const next of incoming) {
    const previous = byId.get(next.operation_id);
    if (
      previous?.event_seq !== undefined &&
      next.event_seq !== undefined &&
      next.event_seq < previous.event_seq
    ) {
      continue;
    }
    if (!previous) order.unshift(next.operation_id);
    byId.set(next.operation_id, previous ? { ...previous, ...next } : next);
  }

  return order
    .map((operationId) => byId.get(operationId))
    .filter((item): item is CapabilityOperation => Boolean(item));
}

export function reduceCapabilityOperationEvent(
  current: CapabilityOperation[],
  event: CapabilityOperationEvent,
): CapabilityOperation[] {
  const previous = current.find(
    (item) => item.operation_id === event.operation.operation_id,
  );
  if (!previous) {
    // A new operation must first arrive as a complete projection. This avoids
    // rendering internal/raw event fragments as if they were safe UI data.
    const candidate = event.operation as Partial<CapabilityOperation>;
    if (
      !candidate.capability_id ||
      !candidate.capability_name ||
      !candidate.kind ||
      !candidate.phase ||
      !candidate.status ||
      !candidate.authorization_mode ||
      !candidate.available_actions ||
      !candidate.artifacts ||
      !candidate.verification_receipts
    ) {
      return current;
    }
    return mergeCapabilityOperations(current, [
      candidate as CapabilityOperation,
    ]);
  }
  return mergeCapabilityOperations(current, [
    { ...previous, ...event.operation },
  ]);
}

export function buildCapabilityOperationActionMessage(
  action: CapabilityOperationAction,
  operation: CapabilityOperation,
) {
  const type =
    action === "cancel"
      ? "capability_operation_cancel"
      : action === "retry"
        ? "capability_operation_retry"
        : action === "rollback"
          ? "capability_rollback"
          : "capability_uninstall";
  return {
    type,
    payload: {
      operation_id: operation.operation_id,
      capability_id: operation.capability_id,
    },
  };
}

export function buildCapabilityMutationMessage(
  action: "install" | "activate" | "repair" | "uninstall",
  capabilityId: string,
) {
  return {
    type: `capability_${action}`,
    payload: { capability_id: capabilityId },
  };
}

const SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/(authorization\s*:\s*bearer\s+)[^\s,;]+/gi, "$1[REDACTED]"],
  [/\b(?:sk|tsk|key)_[a-z0-9_-]{6,}\b/gi, "[REDACTED]"],
  [
    /((?:api[_-]?key|token|password|secret)\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)/gi,
    "$1[REDACTED]",
  ],
  [/(--(?:api-key|token|password|secret)\s+)[^\s]+/gi, "$1[REDACTED]"],
];

export function redactCapabilityText(value: unknown): string {
  let text = typeof value === "string" ? value : String(value ?? "");
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    text = text.replace(pattern, replacement);
  }
  return text;
}

export function isCapabilityListResponse(
  message: unknown,
): message is CapabilityListResponse {
  if (!message || typeof message !== "object") return false;
  const typed = message as { type?: unknown; payload?: { capabilities?: unknown } };
  return (
    (typed.type === "capability_list_response" ||
      typed.type === "capabilities_list_response") &&
    Array.isArray(typed.payload?.capabilities)
  );
}

export function isCapabilityOperationsResponse(
  message: unknown,
): message is CapabilityOperationsResponse {
  if (!message || typeof message !== "object") return false;
  const typed = message as { type?: unknown; payload?: { operations?: unknown } };
  return (
    typed.type === "capability_operations_response" &&
    Array.isArray(typed.payload?.operations)
  );
}

export function isCapabilityOperationEventMessage(
  message: unknown,
): message is CapabilityOperationEventMessage {
  if (!message || typeof message !== "object") return false;
  const typed = message as {
    type?: unknown;
    payload?: { operation?: { operation_id?: unknown } };
  };
  return (
    (typed.type === "capability_operation_event" ||
      typed.type === "capability_operation_update") &&
    typeof typed.payload?.operation?.operation_id === "string"
  );
}
