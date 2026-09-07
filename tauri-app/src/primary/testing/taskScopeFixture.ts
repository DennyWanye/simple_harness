/** Protocol unit-test values only; shapes mirror human_memory_service task_scope.* results. */
import type { TaskScopeBindingSummary, TaskScopeCandidate, TaskScopeListPage, TaskScopeOpen, TaskScopeSearchPage, TaskScopeView, TaskScopeEvidenceGroups, TaskScopeEvidencePage } from "../taskScopeRequests";

export const candidate: TaskScopeCandidate = {
  scope_ref: "scope-a", source_ref: "source-a", source_hash: "a".repeat(64), title: "整理照片库", goal: "把 2025 照片按月归档",
  project: "家庭相册", status: "active", snippet: "…按月[归档]…", rank: -1.5,
};
export const search: TaskScopeSearchPage = { candidates: [candidate, { ...candidate, scope_ref: "scope-b", source_ref: "source-b", title: "整理视频库", goal: "视频归档" }], next_cursor: null, receipt_hash: "b".repeat(64) };
const view = (content: string) => ({ content, content_sha256: "c".repeat(64), root_block_id: null, block_count: 0, receipt_hash: "d".repeat(64) });
export const open: TaskScopeOpen = {
  scope_ref: "scope-a", receipt_ref: `sha256:${"e".repeat(64)}`, source_ref: "source-a", source_hash: "a".repeat(64),
  resume_package: {
    schema_version: 1, task_scope_id: "scope-a", source_id: "source-a", source_hash: "a".repeat(64), canonical_revision: 7,
    event_watermark: 12, binding_set_revision: 1, binding_receipt_hash: "f".repeat(64), checkpoint_sequence: 2, checkpoint_set_root: "0".repeat(64),
    read_views: {
      README: view("# 整理照片库\n\nStatus: active\nGoal: 把 2025 照片按月归档"),
      PLAN: view("{\"operations\":[\"归档秘密计划\"]}"),
      STATUS: view(JSON.stringify({ schema_version: 1, task_scope_id: "scope-a", status: "active", goal: "把 2025 照片按月归档", event_watermark: 12, checkpoint_sequence: 2, binding_set_revision: 1, semantic_closure_pending: false, pending_closure_count: 0, pending_closures: [] })),
      RESUME: view("{\"resume\":\"下一步：导入 3 月\"}"), EVIDENCE: view("{\"event_count\":12}"),
    },
  },
  resume_sha256: "1".repeat(64), receipt_hash: "e".repeat(64), drift_report: null, drift_probe: "host_unavailable", binding_summary: null,
};
export const bindingSummary: TaskScopeBindingSummary = {
  binding_ref: "binding-a", revision: 1, receipt_ref: "receipt-a", receipt_hash: "f".repeat(64), root_set_digest: "5".repeat(64), mode: "auto", state: "active",
  roots: [{ root_ref: "root-1", root_path: "/Users/me/photos", root_digest: "6".repeat(64), mode: "auto", revision: 1, receipt_hash: "f".repeat(64), state: "active" }],
};
export const boundOpen: TaskScopeOpen = { ...open, binding_summary: bindingSummary };
export const listPage: TaskScopeListPage = {
  items: [
    { ...candidate, snippet: "", rank: 0, updated_at: 1_757_200_000, canonical_revision: 7, event_watermark: 12, binding_summary: bindingSummary },
    { ...candidate, scope_ref: "scope-b", source_ref: "source-b", title: "整理视频库", goal: "视频归档", status: "paused", snippet: "", rank: 0, updated_at: 1_757_100_000, canonical_revision: 2, event_watermark: 3, binding_summary: null },
  ],
  next_cursor: null, receipt_hash: "7".repeat(64),
};
export const planView: TaskScopeView = { scope_ref: "scope-a", source_ref: "source-a", source_hash: "a".repeat(64), kind: "PLAN", ...view("{\"operations\":[\"完整计划正文\"]}") };
export const evidenceView: TaskScopeView = { ...planView, kind: "EVIDENCE", content: "{\"event_count\":12}", block_count: 3, root_block_id: "root-block" };
export const groups: TaskScopeEvidenceGroups = { scope_ref: "scope-a", source_ref: "source-a", source_hash: "a".repeat(64), next_cursor: null, receipt_hash: "2".repeat(64),
  groups: [{ group_ref: "group-1", group_hash: "3".repeat(64), logical_group: 1, first_event_sequence: 1, last_event_sequence: 12, event_count: 12 }] };
export const page: TaskScopeEvidencePage = { scope_ref: "scope-a", source_ref: "source-a", source_hash: "a".repeat(64), group_ref: "group-1", group_hash: "3".repeat(64),
  prior_page_hash: null, page: { page_id: `sha256:${"4".repeat(64)}`, content: "{\"events\":[\"证据事件正文\"]}", content_sha256: "4".repeat(64) }, next_cursor: null };
