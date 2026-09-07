import { expect, it } from "vitest";
import { TaskScopeRequests, parseTaskScopeEvidenceGroups, parseTaskScopeEvidencePage, parseTaskScopeOpen, parseTaskScopeSearch, parseTaskScopeView } from "./taskScopeRequests";
import { candidate, evidenceView, groups, open, page, planView, search } from "./testing/taskScopeFixture";
import { flush, wire } from "./testing/graphFixture";

const without = <T extends object>(value: T, key: keyof T) => { const copy = { ...value }; delete copy[key]; return copy; };

it("search parse accepts the real candidate shape and rejects missing, duplicate or oversized fields", () => {
  expect(parseTaskScopeSearch(search)).toEqual(search);
  expect(() => parseTaskScopeSearch(without(search, "receipt_hash"))).toThrow();
  expect(() => parseTaskScopeSearch({ ...search, candidates: [without(candidate, "source_hash")] })).toThrow();
  expect(() => parseTaskScopeSearch({ ...search, candidates: [candidate, candidate] })).toThrow();
  expect(() => parseTaskScopeSearch({ ...search, candidates: [{ ...candidate, snippet: "x".repeat(1025) }] })).toThrow();
  expect(() => parseTaskScopeSearch({ ...search, next_cursor: undefined })).toThrow();
});
it("open parse pins scope/source identity and requires every package view and the drift field", () => {
  expect(parseTaskScopeOpen(open, "scope-a")).toEqual(open);
  expect(() => parseTaskScopeOpen(open, "scope-b")).toThrow();
  expect(() => parseTaskScopeOpen(without(open, "drift_report"), "scope-a")).toThrow();
  expect(() => parseTaskScopeOpen({ ...open, resume_package: { ...open.resume_package, source_hash: "9".repeat(64) } }, "scope-a")).toThrow();
  expect(() => parseTaskScopeOpen({ ...open, resume_package: { ...open.resume_package, read_views: without(open.resume_package.read_views, "STATUS") } }, "scope-a")).toThrow();
  expect(() => parseTaskScopeOpen({ ...open, resume_package: { ...open.resume_package, binding_set_revision: "1" } }, "scope-a")).toThrow();
  const drift = { drifted: true, changed_fields: ["root_set_digest"], checkpoint_ref: "cp", checkpoint_hash: "5".repeat(64), report_hash: "6".repeat(64) };
  expect(parseTaskScopeOpen({ ...open, drift_report: drift }, "scope-a").drift_report).toEqual(drift);
  expect(() => parseTaskScopeOpen({ ...open, drift_report: without(drift, "report_hash") }, "scope-a")).toThrow();
});
it("view, group and page parses refuse another source revision or group binding", () => {
  expect(parseTaskScopeView(planView, open, "PLAN")).toEqual(planView);
  expect(() => parseTaskScopeView(planView, open, "RESUME")).toThrow();
  expect(() => parseTaskScopeView({ ...planView, source_hash: "9".repeat(64) }, open, "PLAN")).toThrow();
  expect(() => parseTaskScopeView(without(planView, "receipt_hash"), open, "PLAN")).toThrow();
  expect(parseTaskScopeEvidenceGroups(groups, open)).toEqual(groups);
  expect(() => parseTaskScopeEvidenceGroups({ ...groups, groups: [{ ...groups.groups[0], event_count: 5 }] }, open)).toThrow();
  expect(() => parseTaskScopeEvidenceGroups({ ...groups, source_ref: "source-b" }, open)).toThrow();
  expect(parseTaskScopeEvidencePage(page, open, groups.groups[0])).toEqual(page);
  expect(() => parseTaskScopeEvidencePage({ ...page, group_hash: "7".repeat(64) }, open, groups.groups[0])).toThrow();
  expect(() => parseTaskScopeEvidencePage({ ...page, page: { ...page.page, page_id: "sha256:other" } }, open, groups.groups[0])).toThrow();
  expect(() => parseTaskScopeEvidencePage({ ...page, page: without(page.page, "content_sha256") }, open, groups.groups[0])).toThrow();
});
it("client sends only read operations, pins expected_source_hash without live_probe, and invalidates on change or rebind", async () => {
  const w = wire(), c = new TaskScopeRequests();
  let stop = c.connect(w.port, "p", null, false);
  await c.search("照片"); expect(w.sent).toHaveLength(0);
  stop(); stop = c.connect(w.port, "p", "owner:1", true);
  void c.search("照片"); expect(w.sent[0].operation).toBe("task_scope.search");
  expect(w.sent[0].request).toEqual({ query: "照片", max_candidates: 8 });
  w.reply(0, search); await flush(); expect(c.getSnapshot().search?.candidates).toHaveLength(2);
  void c.open(candidate);
  expect(w.sent[1].operation).toBe("task_scope.open_exact");
  expect(w.sent[1].request).toEqual({ scope_ref: "scope-a", expected_source_hash: "a".repeat(64) });
  w.reply(1, open); await flush(); expect(c.getSnapshot().open?.resume_sha256).toBe("1".repeat(64));
  void c.loadView("PLAN"); expect(w.sent[2].request).toEqual({ scope_ref: "scope-a", kind: "PLAN" });
  w.emit({ type: "human_memory_changed", payload: {} });
  expect(c.getSnapshot().open).toBeNull(); expect(c.getSnapshot().search).toBeNull();
  w.reply(2, planView); await flush(); expect(c.getSnapshot().views.PLAN).toBeUndefined();
  void c.search("照片"); w.reply(3, search); await flush(); expect(c.getSnapshot().search).toBeTruthy();
  w.emit({ type: "companion_profile_bound", payload: { profile_id: "other", profile_generation: 2 } });
  expect(c.getSnapshot().search).toBeNull(); expect(c.getSnapshot().ready).toBe(false);
  expect(w.sent.every((r) => ["task_scope.search", "task_scope.open_exact", "task_scope.view"].includes(r.operation))).toBe(true);
  stop();
});
it("stale source and invalid replies surface as errors without keeping partial data", async () => {
  const w = wire(), c = new TaskScopeRequests(); const stop = c.connect(w.port, "p", "owner:1", true);
  void c.search("照片"); w.reply(0, search); await flush();
  void c.open(candidate);
  w.emit({ type: "human_memory_response", request_id: w.sent[1].request_id, payload: { ok: false, operation: "task_scope.open_exact", error: { code: "task_scope_source_stale" } } });
  await flush(); expect(c.getSnapshot().open).toBeNull(); expect(c.getSnapshot().error).toMatch(/过期/);
  void c.open(candidate); w.reply(2, { ...open, resume_package: { ...open.resume_package, read_views: { README: open.resume_package.read_views.README } } });
  await flush(); expect(c.getSnapshot().open).toBeNull(); expect(c.getSnapshot().error).toMatch(/未通过核对/);
  void c.open(candidate); w.reply(3, open); await flush();
  void c.loadView("EVIDENCE"); w.reply(4, evidenceView); await flush(); expect(c.getSnapshot().views.EVIDENCE?.block_count).toBe(3);
  stop();
});

it("evidence source stale/unavailable and cross-revision views drop the opened snapshot as stale", async () => {
  const w = wire(), c = new TaskScopeRequests(); const stop = c.connect(w.port, "p", "owner:1", true);
  void c.search("照片"); w.reply(0, search); await flush();
  void c.open(candidate); w.reply(1, open); await flush(); expect(c.getSnapshot().open).not.toBeNull();
  void c.loadEvidenceGroups();
  w.emit({ type: "human_memory_response", request_id: w.sent[2].request_id, payload: { ok: false, operation: "task_scope.evidence_groups", error: { code: "human_memory_evidence_source_stale" } } });
  await flush(); expect(c.getSnapshot().open).toBeNull(); expect(c.getSnapshot().views).toEqual({}); expect(c.getSnapshot().error).toMatch(/过期/);
  void c.open(candidate); w.reply(3, open); await flush();
  void c.loadView("EVIDENCE"); w.reply(4, { ...evidenceView, source_hash: "f".repeat(64) });
  await flush(); expect(c.getSnapshot().open).toBeNull(); expect(c.getSnapshot().error).toMatch(/过期/);
  void c.open(candidate); w.reply(5, open); await flush();
  void c.loadEvidenceGroups();
  w.emit({ type: "human_memory_response", request_id: w.sent[6].request_id, payload: { ok: false, operation: "task_scope.evidence_groups", error: { code: "human_memory_evidence_source_unavailable" } } });
  await flush(); expect(c.getSnapshot().open).toBeNull(); expect(c.getSnapshot().error).toMatch(/不可用/);
  stop();
});
