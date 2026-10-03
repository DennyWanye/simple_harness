// SPDX-License-Identifier: BUSL-1.1
/** Strict, session-local Assurance read models (host-*-v1 contracts). Read-only: no use grant. */
export type AssuranceRef = { kind: string; pin: { id: string; revision: number; content_hash: string } };
export type AssuranceItem = {
  kind: "CRITERION" | "REVIEW" | "EFFECT" | "CLOSEOUT" | "CONTRIBUTION";
  id: string; history_state: string;
  current_use: "USABLE" | "STALE" | "BLOCKED" | "UNAVAILABLE" | "NOT_APPLICABLE";
  reason_codes: string[]; evidence_count: number; artifact_ref: AssuranceRef | null;
};
export type AssuranceEnvelope = {
  schema_version: 1; request_id: string; mission_id: string; view: "CURRENT" | "HISTORY";
  snapshot_seq: number; root_incarnation_id: string; sdk_fingerprint: string; host_fingerprint: string;
};
export type AssuranceSnapshot = AssuranceEnvelope & { items: AssuranceItem[]; next_cursor: string | null; truncated: boolean };
export type AssuranceAssessment = {
  criterion_id: string; model_grade: "PASS" | "FAIL" | "UNKNOWN"; effective_grade: "PASS" | "FAIL" | "UNKNOWN";
  check_gate: "PASS" | "FAIL" | "UNKNOWN" | "NOT_APPLICABLE"; reason_codes: string[]; evidence_labels: string[];
};
export type AssuranceReview = AssuranceEnvelope & {
  review_key: string; purpose: string; package_ref: AssuranceRef; record_ref: AssuranceRef | null;
  official_status: "PENDING" | "OFFICIAL" | "REJECTED_SOURCE" | "UNAVAILABLE";
  verdict: "ACCEPT" | "REWORK" | "REJECT" | "INCONCLUSIVE" | null;
  assessments: AssuranceAssessment[]; next_cursor: string | null; truncated: boolean;
  current_use: "USABLE" | "STALE" | "BLOCKED" | "UNAVAILABLE";
};
export type AssuranceUseCheck = AssuranceEnvelope & {
  subject_ref: AssuranceRef; purpose: string;
  decision: "USABLE" | "RECHECK_REQUIRED" | "BLOCKED" | "UNAVAILABLE"; diagnostic_only: true;
  coverage: "COMPLETE" | "INCOMPLETE"; reason_codes: string[]; checked_at_ms: number;
  expires_at_ms: number | null; certificate_ref: null;
};
export type AssuranceError = { schema_version: 1; request_id: string; code: string; message: string; retryable: boolean };

type Obj = Record<string, unknown>;
const ITEM_KINDS = ["CRITERION", "REVIEW", "EFFECT", "CLOSEOUT", "CONTRIBUTION"] as const;
const ITEM_USE = ["USABLE", "STALE", "BLOCKED", "UNAVAILABLE", "NOT_APPLICABLE"] as const;
const REVIEW_USE = ["USABLE", "STALE", "BLOCKED", "UNAVAILABLE"] as const;
const GRADES = ["PASS", "FAIL", "UNKNOWN"] as const;
const ERROR_CODES = ["NOT_FOUND", "PROFILE_UNBOUND", "ROOT_QUARANTINED", "SNAPSHOT_CHANGED",
  "SOURCE_UNAVAILABLE", "LIMIT_REACHED", "CONTRACT_INVALID"];

function invalid(): never { throw new Error("Assurance 返回的数据不完整或格式不符"); }
function obj(value: unknown, keys: string[]): Obj {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const raw = value as Obj;
  if (Object.keys(raw).length !== keys.length || keys.some((key) => !(key in raw))) return invalid();
  return raw;
}
function text(value: unknown, limit = 2048): string {
  if (typeof value !== "string" || !value.length || value.length > limit) return invalid();
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return invalid();
  return value;
}
function hash(value: unknown): string {
  const s = text(value); if (!/^[a-f0-9]{64}$/.test(s)) return invalid(); return s;
}
function bool(value: unknown): boolean { if (typeof value !== "boolean") return invalid(); return value; }
function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T {
  const s = text(value); if (!(allowed as readonly string[]).includes(s)) return invalid(); return s as T;
}
function nullable<T>(value: unknown, parse: (item: unknown) => T): T | null { return value === null ? null : parse(value); }
function array<T>(value: unknown, bound: number, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > bound) return invalid();
  return value.map(parse);
}
function strings(value: unknown, bound: number): string[] { return array(value, bound, (item) => text(item)); }
function ref(value: unknown): AssuranceRef {
  const r = obj(value, ["kind", "pin"]);
  const pin = obj(r.pin, ["id", "revision", "content_hash"]);
  return { kind: text(r.kind), pin: { id: text(pin.id), revision: integer(pin.revision), content_hash: hash(pin.content_hash) } };
}
function envelope(raw: Obj, missionId: string): AssuranceEnvelope {
  if (raw.schema_version !== 1) invalid();
  const mission_id = text(raw.mission_id);
  if (mission_id !== missionId) throw new Error("返回了其他任务的 Assurance 数据");
  return {
    schema_version: 1, request_id: text(raw.request_id), mission_id,
    view: oneOf(raw.view, ["CURRENT", "HISTORY"] as const), snapshot_seq: integer(raw.snapshot_seq),
    root_incarnation_id: text(raw.root_incarnation_id), sdk_fingerprint: hash(raw.sdk_fingerprint),
    host_fingerprint: hash(raw.host_fingerprint),
  };
}
const ENVELOPE = ["schema_version", "request_id", "mission_id", "view", "snapshot_seq", "root_incarnation_id",
  "sdk_fingerprint", "host_fingerprint"];

function item(value: unknown): AssuranceItem {
  const r = obj(value, ["kind", "id", "history_state", "current_use", "reason_codes", "evidence_count", "artifact_ref"]);
  const artifact = nullable(r.artifact_ref, ref);
  if (artifact && artifact.kind !== "artifact") invalid();
  return {
    kind: oneOf(r.kind, ITEM_KINDS), id: text(r.id), history_state: text(r.history_state),
    current_use: oneOf(r.current_use, ITEM_USE), reason_codes: strings(r.reason_codes, 64),
    evidence_count: integer(r.evidence_count), artifact_ref: artifact,
  };
}

export function parseSnapshot(value: unknown, missionId: string): AssuranceSnapshot {
  const raw = obj(value, [...ENVELOPE, "items", "next_cursor", "truncated"]);
  const items = array(raw.items, 100, item);
  const keys = items.map((entry) => entry.kind + ":" + entry.id);
  if (new Set(keys).size !== keys.length) invalid();
  return { ...envelope(raw, missionId), items, next_cursor: nullable(raw.next_cursor, (c) => text(c)), truncated: bool(raw.truncated) };
}

function assessment(value: unknown): AssuranceAssessment {
  const r = obj(value, ["criterion_id", "model_grade", "effective_grade", "check_gate", "reason_codes", "evidence_labels"]);
  return {
    criterion_id: text(r.criterion_id), model_grade: oneOf(r.model_grade, GRADES), effective_grade: oneOf(r.effective_grade, GRADES),
    check_gate: oneOf(r.check_gate, ["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"] as const),
    reason_codes: strings(r.reason_codes, 64),
    evidence_labels: array(r.evidence_labels, 256, (label) => { const s = text(label); if (!/^ev-[0-9a-f]{64}$/.test(s)) invalid(); return s; }),
  };
}

export function parseReview(value: unknown, missionId: string): AssuranceReview {
  const raw = obj(value, [...ENVELOPE, "review_key", "purpose", "package_ref", "record_ref", "official_status", "verdict",
    "assessments", "next_cursor", "truncated", "current_use"]);
  const packageRef = ref(raw.package_ref);
  if (packageRef.kind !== "review_package") invalid();
  return {
    ...envelope(raw, missionId), review_key: text(raw.review_key),
    purpose: oneOf(raw.purpose, ["TASK_CONTENT", "METHOD_PLAN", "COMPOSITION", "ACTION_PROPOSAL", "OPERATION_OUTCOME", "MISSION_FINAL"] as const),
    package_ref: packageRef, record_ref: nullable(raw.record_ref, ref),
    official_status: oneOf(raw.official_status, ["PENDING", "OFFICIAL", "REJECTED_SOURCE", "UNAVAILABLE"] as const),
    verdict: nullable(raw.verdict, (v) => oneOf(v, ["ACCEPT", "REWORK", "REJECT", "INCONCLUSIVE"] as const)),
    assessments: array(raw.assessments, 100, assessment), next_cursor: nullable(raw.next_cursor, (c) => text(c)),
    truncated: bool(raw.truncated), current_use: oneOf(raw.current_use, REVIEW_USE),
  };
}

export function parseUseCheck(value: unknown, missionId: string): AssuranceUseCheck {
  const raw = obj(value, [...ENVELOPE, "subject_ref", "purpose", "decision", "diagnostic_only", "coverage", "reason_codes",
    "checked_at_ms", "expires_at_ms", "certificate_ref"]);
  if (raw.diagnostic_only !== true || raw.certificate_ref !== null) invalid();
  return {
    ...envelope(raw, missionId), subject_ref: ref(raw.subject_ref),
    purpose: oneOf(raw.purpose, ["PLAN", "START", "MAINTAIN", "ACCEPT", "CONTEXT", "DISCLOSE", "RECOVERY"] as const),
    decision: oneOf(raw.decision, ["USABLE", "RECHECK_REQUIRED", "BLOCKED", "UNAVAILABLE"] as const), diagnostic_only: true,
    coverage: oneOf(raw.coverage, ["COMPLETE", "INCOMPLETE"] as const), reason_codes: strings(raw.reason_codes, 64),
    checked_at_ms: integer(raw.checked_at_ms), expires_at_ms: nullable(raw.expires_at_ms, integer), certificate_ref: null,
  };
}

export function parseError(value: unknown): AssuranceError {
  const raw = obj(value, ["schema_version", "request_id", "code", "message", "retryable"]);
  if (raw.schema_version !== 1) invalid();
  const code = text(raw.code); if (!ERROR_CODES.includes(code)) invalid();
  const message = raw.message; if (typeof message !== "string" || message.length > 2000) invalid();
  return { schema_version: 1, request_id: text(raw.request_id), code, message, retryable: bool(raw.retryable) };
}

const ERROR_TEXT: Record<string, string> = {
  NOT_FOUND: "当前身份下没有这个对象", PROFILE_UNBOUND: "该任务不在 Assurance 通道上",
  ROOT_QUARANTINED: "保证通道根状态异常，已隔离，只开放管理读取", SNAPSHOT_CHANGED: "状态已变化，请从第一页重新读取",
  SOURCE_UNAVAILABLE: "来源暂不可读（历史视图可能没有覆盖）", LIMIT_REACHED: "超过读取上限，请缩小范围", CONTRACT_INVALID: "请求不符合合同",
};
export function errorMessage(error: AssuranceError): string {
  return (ERROR_TEXT[error.code] || error.code) + (error.message ? "：" + error.message : "") + (error.retryable ? "（可重试）" : "");
}

export const USE_LABELS: Record<string, string> = {
  USABLE: "当前可用", STALE: "已过期或来源已变", BLOCKED: "被反证阻断", UNAVAILABLE: "无可用证书", NOT_APPLICABLE: "不适用",
  RECHECK_REQUIRED: "需要重新核查",
};
export const KIND_LABELS: Record<string, string> = {
  CRITERION: "准则", REVIEW: "审阅", EFFECT: "待效果", CLOSEOUT: "结案", CONTRIBUTION: "贡献",
};
