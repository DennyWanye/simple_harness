// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 任务范围面板（PrimaryTaskPanel）的画廊驱动：
// 面板内部自己 new 一个 TaskScopeRequests，外面拿不到。这里在场景首次渲染前包一层 connect：
// 原 connect 照常执行后，按 primaryRef 选一条固定剧本，依次调用客户端自己的公开方法
// （search / list / open / loadView / …），由假端口按操作名回固定夹具。不模拟任何点击。
import type { PrimaryPort } from "../../../src/primary/controller";
import { TaskScopeRequests, type TaskScopeCandidate } from "../../../src/primary/taskScopeRequests";
import {
  boundOpen,
  candidate,
  evidenceView,
  groups,
  listPage,
  open,
  page,
  planView,
  search,
} from "../../../src/primary/testing/taskScopeFixture";

type Json = Record<string, unknown>;
type Answer = { ok: true; result: unknown } | { ok: false; code: string };

const statusContent = JSON.stringify({
  schema_version: 1,
  task_scope_id: "scope-a",
  status: "active",
  goal: "把 2025 照片按月归档",
  event_watermark: 12,
  checkpoint_sequence: 2,
  binding_set_revision: 1,
  semantic_closure_pending: true,
  pending_closure_count: 2,
  pending_closures: [],
});

const fullOpen = {
  ...boundOpen,
  resume_package: {
    ...boundOpen.resume_package,
    read_views: {
      ...boundOpen.resume_package.read_views,
      STATUS: { ...boundOpen.resume_package.read_views.STATUS, content: statusContent },
    },
  },
};
const driftedOpen = {
  ...open,
  drift_report: {
    drifted: true,
    changed_fields: ["goal", "status"],
    checkpoint_ref: null,
    checkpoint_hash: null,
    report_hash: "9".repeat(64),
  },
  binding_summary: {
    ...boundOpen.binding_summary!,
    mode: "mixed",
    state: "drifted",
    roots: [
      { ...boundOpen.binding_summary!.roots[0], mode: "manual", state: "missing" },
      { ...boundOpen.binding_summary!.roots[0], root_ref: "root-2", root_path: "/Users/me/other", mode: "weird", state: "weird" },
    ],
  },
};
const cleanOpen = {
  ...open,
  resume_package: { ...open.resume_package, binding_set_revision: 2, binding_receipt_hash: null },
  drift_report: { drifted: false, changed_fields: [], checkpoint_ref: null, checkpoint_hash: null, report_hash: "8".repeat(64) },
};
const emptyStatusOpen = {
  ...open,
  resume_package: {
    ...open.resume_package,
    binding_set_revision: 0,
    binding_receipt_hash: null,
    read_views: { ...open.resume_package.read_views, STATUS: { ...open.resume_package.read_views.STATUS, content: "不是 JSON" } },
  },
  drift_report: { drifted: true, changed_fields: [], checkpoint_ref: null, checkpoint_hash: null, report_hash: "7".repeat(64) },
};

type Script = {
  /** 各操作的回答（同一操作可按次序给多个）。 */
  answers: Record<string, Answer[]>;
  run: (client: TaskScopeRequests) => Promise<void>;
};

const ok = (result: unknown): Answer => ({ ok: true, result });
const viewOf = (kind: string, extra: Json = {}) => ok({ ...planView, kind, content: `{"${kind}":"视图正文"}`, ...extra });

const SCRIPTS: Record<string, Script> = {
  full: {
    answers: {
      "task_scope.search": [ok({ ...search, next_cursor: "cursor-2" })],
      "task_scope.list": [ok({ ...listPage, next_cursor: "cursor-l2" })],
      "task_scope.open_exact": [ok(fullOpen)],
      "task_scope.view": [viewOf("PLAN"), viewOf("DECISIONS"), viewOf("RESUME"), ok(evidenceView)],
      "task_scope.evidence_groups": [ok({ ...groups, next_cursor: "group-cursor-2" })],
      "task_scope.evidence_page": [ok({ ...page, next_cursor: "page-cursor-2" })],
    },
    async run(client) {
      await client.search("照片");
      await client.list();
      await client.open(candidate);
      for (const kind of ["PLAN", "DECISIONS", "RESUME", "EVIDENCE"] as const) await client.loadView(kind);
      await client.loadEvidenceGroups();
      await client.loadEvidencePage(groups.groups[0]);
    },
  },
  drift: {
    answers: {
      "task_scope.search": [ok({ candidates: [], next_cursor: null, receipt_hash: "b".repeat(64) })],
      "task_scope.list": [ok({ items: [], next_cursor: null, receipt_hash: "7".repeat(64) })],
      "task_scope.open_exact": [ok(driftedOpen)],
      "task_scope.view": [ok(evidenceView)],
      "task_scope.evidence_groups": [ok({ ...groups, groups: [] })],
    },
    async run(client) {
      await client.search("不存在");
      await client.list();
      await client.open(candidate);
      await client.loadView("EVIDENCE");
      await client.loadEvidenceGroups();
    },
  },
  clean: {
    answers: {
      "task_scope.search": [
        ok({
          ...search,
          candidates: [{ ...candidate, title: "", status: "", project: "", goal: "", snippet: "", scope_ref: "scope-a" }],
        }),
      ],
      "task_scope.open_exact": [ok(cleanOpen)],
    },
    async run(client) {
      await client.search("无标题");
      await client.open(candidate);
    },
  },
  nostatus: {
    answers: { "task_scope.open_exact": [ok(emptyStatusOpen)] },
    async run(client) {
      await client.open(candidate);
    },
  },
  error: {
    answers: {
      "task_scope.list": [ok(listPage)],
      "task_scope.open_exact": [{ ok: false, code: "task_scope_source_stale" }],
    },
    async run(client) {
      await client.list();
      await client.open(candidate as TaskScopeCandidate);
    },
  },
};

/** 按剧本自动回包的假端口（回包在微任务里发出）。 */
export function misPrimaryPort(primaryRef: string): PrimaryPort {
  const script = SCRIPTS[primaryRef];
  const used: Record<string, number> = {};
  const listeners = new Set<(v: unknown) => void>();
  return {
    state: () => "connected",
    send_command: (r) => {
      const req = r as unknown as { request_id: string; operation: string };
      const list = script?.answers[req.operation] ?? [];
      const index = used[req.operation] ?? 0;
      used[req.operation] = index + 1;
      const answer = list[Math.min(index, list.length - 1)];
      if (answer)
        queueMicrotask(() => {
          const payload = answer.ok
            ? { ok: true, operation: req.operation, result: answer.result }
            : { ok: false, operation: req.operation, error: { code: answer.code } };
          listeners.forEach((fn) => fn({ type: "human_memory_response", request_id: req.request_id, payload }));
        });
      return true;
    },
    on_message: (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    on_state_change: () => () => undefined,
  };
}

let installed = false;

/** 包一层 TaskScopeRequests.prototype.connect：连上后按 primaryRef 跑剧本。只装一次。 */
export function installTaskScopeDriver() {
  if (installed) return;
  installed = true;
  const original = TaskScopeRequests.prototype.connect;
  TaskScopeRequests.prototype.connect = function patched(
    this: TaskScopeRequests,
    port: PrimaryPort,
    primary: string,
    owner: string | null,
    ready: boolean,
  ) {
    const dispose = original.call(this, port, primary, owner, ready);
    const script = SCRIPTS[primary];
    let alive = true;
    if (script && ready && owner) {
      queueMicrotask(() => {
        if (alive) void script.run(this).catch(() => undefined);
      });
    }
    return () => {
      alive = false;
      dispose();
    };
  };
}
