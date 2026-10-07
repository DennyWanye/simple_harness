// SPDX-License-Identifier: BUSL-1.1
/**
 * Session-local Assurance read models (host-*-v1 contracts). Read-only: no use grant.
 * 形状只由公开合同核一次：控制通道收到回复时按 SDK 的 host-*-v1 Schema 核（`ws/orchestrationContracts.ts`，
 * 推后第 2 批 U02），这里只留类型和给人看的文字，不再手抄一份解析器。
 */
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
