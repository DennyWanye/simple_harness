// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import type { PrimaryPort } from "./controller";
import { PrimaryRequestError, PrimaryRequests, record } from "./requests";

export const VIEW_KINDS = ["README", "PLAN", "STATUS", "DECISIONS", "RESUME", "EVIDENCE"] as const;
export type TaskScopeViewKind = (typeof VIEW_KINDS)[number];
/** Search candidate only: a visible summary that grants nothing. */
export interface TaskScopeCandidate {
  scope_ref: string; source_ref: string; source_hash: string; title: string; goal: string;
  project: string; status: string; snippet: string; rank: number;
}
export interface TaskScopeSearchPage { candidates: TaskScopeCandidate[]; next_cursor: string | null; receipt_hash: string }
export interface TaskScopePackageView { content: string; content_sha256: string; root_block_id: string | null; block_count: number; receipt_hash: string }
export interface TaskScopeResumePackage {
  schema_version: number; task_scope_id: string; source_id: string; source_hash: string; canonical_revision: number;
  event_watermark: number; binding_set_revision: number; binding_receipt_hash: string | null; checkpoint_sequence: number;
  checkpoint_set_root: string; read_views: Record<"README" | "PLAN" | "STATUS" | "RESUME" | "EVIDENCE", TaskScopePackageView>;
}
export interface TaskScopeDrift {
  drifted: boolean; changed_fields: string[]; checkpoint_ref: string | null; checkpoint_hash: string | null; report_hash: string;
}
/** Host-recorded binding facts (mode = grant source, state = Host's own re-stat). Display only; grants nothing. */
export interface TaskScopeBindingRoot {
  root_ref: string; root_path: string; root_digest: string; mode: "manual" | "auto"; revision: number; receipt_hash: string;
  state: "active" | "missing" | "drifted";
}
export interface TaskScopeBindingSummary {
  binding_ref: string; revision: number; receipt_ref: string; receipt_hash: string; root_set_digest: string;
  mode: "manual" | "auto" | "mixed" | "unknown"; state: "active" | "missing" | "drifted" | "unknown"; roots: TaskScopeBindingRoot[];
}
export interface TaskScopeOpen {
  scope_ref: string; receipt_ref: string; source_ref: string; source_hash: string; resume_package: TaskScopeResumePackage;
  resume_sha256: string; receipt_hash: string; drift_report: TaskScopeDrift | null;
  /** Who produced the drift probe: the Host never fabricates one on the public channel. */
  drift_probe: string; binding_summary: TaskScopeBindingSummary | null;
}
/** Recent/active listing item: the search candidate shape plus head timestamps; still grants nothing. */
export interface TaskScopeListItem extends TaskScopeCandidate {
  updated_at: number; canonical_revision: number; event_watermark: number; binding_summary: TaskScopeBindingSummary | null;
}
export interface TaskScopeListPage { items: TaskScopeListItem[]; next_cursor: string | null; receipt_hash: string }
export interface TaskScopeView {
  scope_ref: string; source_ref: string; source_hash: string; kind: TaskScopeViewKind; content: string;
  content_sha256: string; root_block_id: string | null; block_count: number; receipt_hash: string;
}
export interface TaskScopeEvidenceGroup {
  group_ref: string; group_hash: string; logical_group: number; first_event_sequence: number; last_event_sequence: number; event_count: number;
}
export interface TaskScopeEvidenceGroups {
  scope_ref: string; source_ref: string; source_hash: string; groups: TaskScopeEvidenceGroup[]; next_cursor: string | null; receipt_hash: string;
}
export interface TaskScopeEvidencePage {
  scope_ref: string; source_ref: string; source_hash: string; group_ref: string; group_hash: string; prior_page_hash: string | null;
  page: { page_id: string; content: string; content_sha256: string }; next_cursor: string | null;
}

const hash = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const text = (v: unknown, max: number): v is string => typeof v === "string" && Array.from(v).length <= max && !v.includes("\0");
const id = (v: unknown, max = 512): v is string => text(v, max) && v.length > 0 && v.trim() === v;
const optId = (v: unknown, max = 512): v is string | null => v === null || id(v, max);
const optHash = (v: unknown): v is string | null => v === null || hash(v);
const count = (v: unknown, min = 0): v is number => Number.isSafeInteger(v) && Number(v) >= min;
const invalid = () => new Error("任务响应未通过核对；本次结果尚未确认。");
/** The reply belongs to another source revision: the opened snapshot is stale, not malformed. */
export class TaskScopeStaleError extends Error { constructor() { super("已打开的任务来源已被更新，当前视图已过期；请重新搜索并精确打开。"); } }
const mismatch = (v: Record<string, unknown>, open: { scope_ref: string; source_ref: string; source_hash: string }) =>
  v.scope_ref !== open.scope_ref || v.source_ref !== open.source_ref || v.source_hash !== open.source_hash;

function candidateFields(c: Record<string, unknown>, seen: Set<string>): void {
  if (!id(c.scope_ref) || seen.has(c.scope_ref) || !id(c.source_ref) || !hash(c.source_hash) ||
      !text(c.title, 2048) || !text(c.goal, 4096) || !text(c.project, 2048) || !text(c.status, 256) || !text(c.snippet, 1024) ||
      typeof c.rank !== "number" || !Number.isFinite(c.rank)) throw invalid();
  seen.add(c.scope_ref);
}
const bindingModes = ["manual", "auto"], rootStates = ["active", "missing", "drifted"];
/** A binding summary is either absent (null) or complete; a partial one is rejected, never shown. */
function bindingSummary(raw: unknown): void {
  if (raw === null) return;
  const b = record(raw);
  if (!id(b.binding_ref) || !count(b.revision, 1) || !id(b.receipt_ref) || !hash(b.receipt_hash) || !hash(b.root_set_digest) ||
      ![...bindingModes, "mixed", "unknown"].includes(String(b.mode)) || ![...rootStates, "unknown"].includes(String(b.state)) ||
      !Array.isArray(b.roots) || b.roots.length > 64) throw invalid();
  for (const value of b.roots) {
    const r = record(value);
    if (!id(r.root_ref) || !text(r.root_path, 4096) || !hash(r.root_digest) || !bindingModes.includes(String(r.mode)) ||
        !count(r.revision, 1) || !hash(r.receipt_hash) || !rootStates.includes(String(r.state))) throw invalid();
  }
}
/** Candidates are bounded summaries; any missing or oversized field rejects the whole page. */
export function parseTaskScopeSearch(raw: unknown): TaskScopeSearchPage {
  const v = record(raw);
  if (!Array.isArray(v.candidates) || v.candidates.length > 100 || !optId(v.next_cursor, 4096) || !hash(v.receipt_hash)) throw invalid();
  const seen = new Set<string>();
  for (const value of v.candidates) candidateFields(record(value), seen);
  return v as unknown as TaskScopeSearchPage;
}
/** Recent/active list: candidate shape plus head facts and the read-only binding summary; bounded to 32. */
export function parseTaskScopeList(raw: unknown): TaskScopeListPage {
  const v = record(raw);
  if (!Array.isArray(v.items) || v.items.length > 32 || !optId(v.next_cursor, 4096) || !hash(v.receipt_hash)) throw invalid();
  const seen = new Set<string>();
  for (const value of v.items) {
    const c = record(value);
    candidateFields(c, seen);
    if (typeof c.updated_at !== "number" || !Number.isFinite(c.updated_at) || !count(c.canonical_revision, 1) || !count(c.event_watermark) ||
        !("binding_summary" in c)) throw invalid();
    bindingSummary(c.binding_summary);
  }
  return v as unknown as TaskScopeListPage;
}
function packageView(raw: unknown): void {
  const v = record(raw);
  if (!text(v.content, 16384) || !hash(v.content_sha256) || !optId(v.root_block_id) || !count(v.block_count) || !hash(v.receipt_hash)) throw invalid();
}
/** Exact open: the package must name the same scope and source the caller asked for. */
export function parseTaskScopeOpen(raw: unknown, scope: string): TaskScopeOpen {
  const v = record(raw), p = record(v.resume_package), views = record(p.read_views);
  if (v.scope_ref !== scope || !id(v.receipt_ref, 1024) || !id(v.source_ref) || !hash(v.source_hash) || !hash(v.resume_sha256) || !hash(v.receipt_hash) ||
      p.schema_version !== 1 || p.task_scope_id !== scope || p.source_id !== v.source_ref || p.source_hash !== v.source_hash ||
      !count(p.canonical_revision) || !count(p.event_watermark) || !count(p.binding_set_revision) || !optHash(p.binding_receipt_hash) ||
      !count(p.checkpoint_sequence) || !hash(p.checkpoint_set_root)) throw invalid();
  for (const kind of ["README", "PLAN", "STATUS", "RESUME", "EVIDENCE"]) packageView(views[kind]);
  if (v.drift_report !== null) {
    const d = record(v.drift_report);
    if (typeof d.drifted !== "boolean" || !Array.isArray(d.changed_fields) || d.changed_fields.length > 64 ||
        !d.changed_fields.every((f) => id(f, 128)) || !optId(d.checkpoint_ref) || !optHash(d.checkpoint_hash) || !hash(d.report_hash)) throw invalid();
  } else if (!("drift_report" in v)) throw invalid();
  if (!id(v.drift_probe, 64) || !("binding_summary" in v)) throw invalid();
  bindingSummary(v.binding_summary);
  return v as unknown as TaskScopeOpen;
}
/** A paged-in view must belong to the opened source; other revisions never merge into this snapshot. */
export function parseTaskScopeView(raw: unknown, open: { scope_ref: string; source_ref: string; source_hash: string }, kind: TaskScopeViewKind): TaskScopeView {
  const v = record(raw);
  if (mismatch(v, open)) throw new TaskScopeStaleError();
  if (v.kind !== kind ||
      !text(v.content, 65536) || !hash(v.content_sha256) || !optId(v.root_block_id) || !count(v.block_count) || !hash(v.receipt_hash)) throw invalid();
  return v as unknown as TaskScopeView;
}
export function parseTaskScopeEvidenceGroups(raw: unknown, open: { scope_ref: string; source_ref: string; source_hash: string }): TaskScopeEvidenceGroups {
  const v = record(raw);
  if (mismatch(v, open)) throw new TaskScopeStaleError();
  if (!Array.isArray(v.groups) || v.groups.length > 16 || !optId(v.next_cursor, 4096) || !hash(v.receipt_hash)) throw invalid();
  for (const value of v.groups) {
    const g = record(value);
    if (!id(g.group_ref) || !hash(g.group_hash) || !count(g.logical_group, 1) || !count(g.first_event_sequence, 1) ||
        !count(g.last_event_sequence, 1) || Number(g.last_event_sequence) < Number(g.first_event_sequence) ||
        g.event_count !== Number(g.last_event_sequence) - Number(g.first_event_sequence) + 1 || Number(g.event_count) > 500) throw invalid();
  }
  return v as unknown as TaskScopeEvidenceGroups;
}
export function parseTaskScopeEvidencePage(raw: unknown, open: { scope_ref: string; source_ref: string; source_hash: string }, group: TaskScopeEvidenceGroup): TaskScopeEvidencePage {
  const v = record(raw), page = record(v.page);
  if (mismatch(v, open)) throw new TaskScopeStaleError();
  if (v.group_ref !== group.group_ref || v.group_hash !== group.group_hash || !optHash(v.prior_page_hash) || !optId(v.next_cursor, 4096) ||
      !id(page.page_id, 1024) || !text(page.content, 65536) || !hash(page.content_sha256) || page.page_id !== `sha256:${page.content_sha256}`) throw invalid();
  return v as unknown as TaskScopeEvidencePage;
}

export interface TaskScopeSnapshot {
  ready: boolean; busy: boolean; query: string; search: TaskScopeSearchPage | null; list: TaskScopeListPage | null;
  open: TaskScopeOpen | null; views: Partial<Record<TaskScopeViewKind, TaskScopeView>>;
  evidenceGroups: TaskScopeEvidenceGroups | null; evidencePage: TaskScopeEvidencePage | null;
  notice: string; error: string;
}
const empty = (): TaskScopeSnapshot => ({ ready: false, busy: false, query: "", search: null, list: null, open: null, views: {}, evidenceGroups: null, evidencePage: null, notice: "", error: "" });

/** Read-only inspect client. No mutate/create/binding/enqueue, no live_probe, no polling, no storage. */
export class TaskScopeRequests {
  private value: TaskScopeSnapshot = empty();
  private listeners = new Set<() => void>();
  private reader?: PrimaryRequests;
  private epoch = 0;
  getSnapshot = () => this.value;
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  private update(patch: Partial<TaskScopeSnapshot>) { this.value = { ...this.value, ...patch }; this.listeners.forEach((fn) => fn()); }
  /** Drop every displayed result and reject in-flight replies; later replies for the old epoch are ignored. */
  private clear(notice = "") { ++this.epoch; this.reader?.invalidate(); this.update({ ...empty(), ready: this.value.ready, notice }); }
  connect(port: PrimaryPort, primary: string, owner: string | null, ready: boolean) {
    this.clear(); this.reader = new PrimaryRequests(port);
    this.update({ ready: Boolean(ready && owner && primary && port.state() === "connected") });
    const revoke = () => { this.clear(); this.update({ ready: false }); };
    const offState = port.on_state_change((value) => { if (value !== "connected") revoke(); });
    const off = port.on_message((raw) => {
      const m = record(raw), p = record(m.payload);
      if (["companion_control_rechallenge", "companion_profile_unbound", "companion_identity_unready"].includes(String(m.type)) ||
          (m.type === "companion_identity_status" && (p.ready === false || p.status === "unready"))) revoke();
      if (["companion_profile_bound", "companion_identity_status"].includes(String(m.type)) && p.profile_id &&
          `${String(p.profile_id)}:${String(p.profile_generation ?? "")}` !== owner) revoke();
      if (["human_memory_changed", "human_memory_privacy_changed", "human_memory_invalidated", "companion_projection_retracted"].includes(String(m.type))) {
        this.clear("记忆或任务数据已变更，之前的候选与视图已失效，请重新搜索。");
      }
    });
    return () => { revoke(); offState(); off(); this.reader?.dispose(); };
  }
  private async run<T>(operation: string, request: Record<string, unknown>, parse: (raw: unknown) => T, apply: (value: T) => Partial<TaskScopeSnapshot>) {
    if (!this.value.ready || !this.reader || this.value.busy) return;
    const epoch = this.epoch, reader = this.reader;
    this.update({ busy: true, error: "", notice: "" });
    try {
      const value = parse(await reader.request(operation, request));
      if (epoch === this.epoch) this.update(apply(value));
    } catch (error) {
      if (epoch !== this.epoch) return;
      const code = error instanceof PrimaryRequestError ? error.message : "";
      const staleCode = code === "human_memory_evidence_source_stale" || code === "human_memory_evidence_source_unavailable";
      if (staleCode || error instanceof TaskScopeStaleError) {
        // The opened snapshot must not stay on screen as if it were current.
        this.update({ open: null, views: {}, evidenceGroups: null, evidencePage: null,
          error: code === "human_memory_evidence_source_unavailable" ? "已打开的任务来源当前不可用，当前视图已过期；请重新搜索并精确打开。"
            : "已打开的任务来源已被更新，当前视图已过期；请重新搜索并精确打开。" });
        return;
      }
      this.update({ error: code === "task_scope_source_stale" ? "候选来源已过期，本次打开被拒绝；请重新搜索后再试。"
        : code === "human_memory_permission_denied" ? "该任务不属于当前身份，无法打开。"
        : error instanceof Error && !(error instanceof PrimaryRequestError) ? error.message : "任务数据暂时无法读取，请重试。" });
    } finally { if (epoch === this.epoch) this.update({ busy: false }); }
  }
  /** Recent/active owned scopes on explicit request only; items are candidates, not permissions. */
  list = (cursor: string | null = null) => {
    if (!cursor) this.update({ list: null, open: null, views: {}, evidenceGroups: null, evidencePage: null });
    return this.run("task_scope.list", { limit: 20, ...(cursor ? { cursor } : {}) }, parseTaskScopeList,
      (page) => ({ list: cursor && this.value.list ? { ...page, items: [...this.value.list.items, ...page.items] } : page }));
  };
  /** Candidates only. Never opens, never grants. */
  search = (query: string, cursor: string | null = null) => {
    const q = query.trim();
    if (!q) return Promise.resolve();
    if (!cursor) this.update({ search: null, open: null, views: {}, evidenceGroups: null, evidencePage: null, query: q });
    return this.run("task_scope.search", { query: q, max_candidates: 8, ...(cursor ? { cursor } : {}) }, parseTaskScopeSearch,
      (page) => ({ search: cursor && this.value.search ? { ...page, candidates: [...this.value.search.candidates, ...page.candidates] } : page }));
  };
  /** Exact open pins the candidate's source hash; the Host decides any live probe, the UI never self-reports one. */
  open = (candidate: TaskScopeCandidate) => {
    this.update({ open: null, views: {}, evidenceGroups: null, evidencePage: null });
    return this.run("task_scope.open_exact", { scope_ref: candidate.scope_ref, expected_source_hash: candidate.source_hash },
      (raw) => parseTaskScopeOpen(raw, candidate.scope_ref), (open) => ({ open }));
  };
  /** Page in one view on explicit request; a view from another source revision is rejected. */
  loadView = (kind: TaskScopeViewKind) => {
    const open = this.value.open;
    if (!open) return Promise.resolve();
    return this.run("task_scope.view", { scope_ref: open.scope_ref, kind }, (raw) => parseTaskScopeView(raw, open, kind),
      (view) => ({ views: { ...this.value.views, [kind]: view } }));
  };
  loadEvidenceGroups = (cursor: string | null = null) => {
    const open = this.value.open;
    if (!open) return Promise.resolve();
    return this.run("task_scope.evidence_groups", { scope_ref: open.scope_ref, source_ref: open.source_ref, source_hash: open.source_hash, limit: 8, ...(cursor ? { cursor } : {}) },
      (raw) => parseTaskScopeEvidenceGroups(raw, open),
      (groups) => ({ evidenceGroups: cursor && this.value.evidenceGroups ? { ...groups, groups: [...this.value.evidenceGroups.groups, ...groups.groups] } : groups, evidencePage: null }));
  };
  loadEvidencePage = (group: TaskScopeEvidenceGroup, cursor: string | null = null) => {
    const open = this.value.open;
    if (!open) return Promise.resolve();
    return this.run("task_scope.evidence_page", { scope_ref: open.scope_ref, source_ref: open.source_ref, source_hash: open.source_hash,
      group_ref: group.group_ref, group_hash: group.group_hash, ...(cursor ? { cursor } : {}) },
      (raw) => parseTaskScopeEvidencePage(raw, open, group), (evidencePage) => ({ evidencePage }));
  };
}
