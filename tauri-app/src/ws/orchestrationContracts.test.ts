// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 推后第 2 批 U02：前端用 SDK 发布的同一批公开 Schema 核编排回复（HTN §17.2 L856）。
 * 正样本用 SDK 自己的执行图合同样例（同一份文件，不另造）；Assurance 正样本照 host-*-v1 手写。
 */
import { describe, expect, it } from "vitest";

import graphExamples from "../../../sdk/simple-harness-sdk/tests/orchestrator/acceptance_assets/taskgraph_schema_examples.json";
import {
  CONTRACT_SCHEMAS, PROTOCOL_ERROR, PROTOCOL_ERROR_TEXT, STALE_NOTE, contractViolation, guardIncoming, supportedKeywords,
} from "./orchestrationContracts";

const HASH = "a".repeat(64);
const snapshot = (extra: Record<string, unknown> = {}) => ({
  schema_version: 1, request_id: "r1", mission_id: "m1", view: "CURRENT", snapshot_seq: 7,
  root_incarnation_id: "root-1", sdk_fingerprint: HASH, host_fingerprint: "b".repeat(64),
  items: [{ kind: "REVIEW", id: "rv-1", history_state: "OFFICIAL", current_use: "STALE", reason_codes: ["SOURCE_CHANGED"],
    evidence_count: 2, artifact_ref: { kind: "artifact", pin: { id: "art-1", revision: 3, content_hash: HASH } } }],
  next_cursor: null, truncated: false, ...extra,
});
const wire = (extra: Record<string, unknown> = {}) =>
  ({ schema_version: 1, request_id: "r1", code: "NOT_FOUND", message: "why", retryable: false, ...extra });
const reply = (type: string, payload: Record<string, unknown>) => ({ type: type + "_response", payload: { request_id: "r1", ...payload } });
const protocolError = (type: string) =>
  ({ type: type + "_response", payload: { request_id: "r1", ok: false, error_code: PROTOCOL_ERROR, error: PROTOCOL_ERROR_TEXT } });

describe("U02 编排回复按公开 Schema 核", () => {
  it("uses the SDK schema files themselves and supports every keyword they use", () => {
    expect(CONTRACT_SCHEMAS["host-response-v1"].$id).toMatch(/assurance\/1\.1\/host-response-v1\.schema\.json$/);
    expect(CONTRACT_SCHEMAS["taskgraph-convergence-view-v2"].$id).toBeTruthy();
    const used = new Set<string>();
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (!node || typeof node !== "object") return;
      for (const [key, value] of Object.entries(node)) {
        used.add(key);
        if (key === "properties" || key === "$defs") Object.values(value as object).forEach(walk);
        else if (key !== "const" && key !== "enum") walk(value);
      }
    };
    Object.values(CONTRACT_SCHEMAS).forEach(walk);
    for (const keyword of used) expect(supportedKeywords.has(keyword), keyword).toBe(true);
  });

  it("accepts the SDK's own positive examples for every taskgraph reply contract", () => {
    const examples = graphExamples as Record<string, unknown>;
    for (const name of ["taskgraph-view-v1", "taskgraph-explanation-v1", "taskgraph-diff-v1",
      "taskgraph-convergence-view-v2", "taskgraph-error-v1"] as const) {
      expect(contractViolation(name, examples[name]), name).toBeNull();
    }
  });

  it("refuses unknown fields, foreign enum values, broken bounds and wrong refs", () => {
    expect(contractViolation("host-response-v1", snapshot())).toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ tenant_id: "other" }))).toMatch(/tenant_id/);
    expect(contractViolation("host-response-v1", snapshot({ view: "LIVE" }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ snapshot_seq: 1.5 }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ sdk_fingerprint: "A".repeat(64) }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ items: [{ ...snapshot().items[0],
      artifact_ref: { kind: "review_package", pin: { id: "x", revision: 1, content_hash: HASH } } }] }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ items: [{ ...snapshot().items[0], reason_codes: ["A", "A"] }] }))).not.toBeNull();
    expect(contractViolation("host-error-v1", wire())).toBeNull();
    expect(contractViolation("host-error-v1", wire({ code: "SOMETHING_NEW" }))).not.toBeNull();
    const convergence = (graphExamples as Record<string, Record<string, unknown>>)["taskgraph-convergence-view-v2"];
    expect(contractViolation("taskgraph-convergence-view-v2", { ...convergence, debug: {} })).toMatch(/debug/);
    expect(contractViolation("taskgraph-convergence-view-v2", { ...convergence, schema_version: 1 })).not.toBeNull();
  });

  it("turns a checker that throws into a protocol error for the same request instead of dropping it", () => {
    // opt.170 发版前评估建议 3：核对器自己出错时，消息不能被吞掉、界面不能一直停在"读取中"
    const exploding = { type: "mission_assurance_snapshot_response", payload: { request_id: "r1", ok: true,
      get data(): unknown { throw new Error("boom"); } } };
    expect(guardIncoming(exploding as never)).toEqual(protocolError("mission_assurance_snapshot"));
  });

  it("passes valid replies through untouched and turns bad ones into a protocol error", () => {
    const good = reply("mission_assurance_snapshot", { ok: true, data: snapshot() });
    expect(guardIncoming(good)).toBe(good);
    expect(guardIncoming(reply("mission_assurance_snapshot", { ok: true, data: snapshot({ tenant_id: "x" }) })))
      .toEqual(protocolError("mission_assurance_snapshot"));
    const refused = reply("mission_assurance_review", { ok: false, error_code: "NOT_FOUND", error: "t", assurance_error: wire() });
    expect(guardIncoming(refused)).toBe(refused);
    expect(guardIncoming(reply("mission_assurance_review", { ok: false, error_code: "NOT_FOUND", error: "t",
      assurance_error: { code: "NOT_FOUND" } }))).toEqual(protocolError("mission_assurance_review"));
    const examples = graphExamples as Record<string, unknown>;
    const why = reply("taskgraph.why_not_ready", { ok: true, data: examples["taskgraph-explanation-v1"] });
    expect(guardIncoming(why)).toBe(why);
    expect(guardIncoming(reply("taskgraph.convergence", { ok: true, data: { schema_version: 2 } })))
      .toEqual(protocolError("taskgraph.convergence"));
    // an error wire on any taskgraph verb is checked, even one without a reply contract
    expect(guardIncoming(reply("taskgraph.execution_snapshot", { ok: false, error_code: "X", error: "t",
      taskgraph_error: { code: "X" } }))).toEqual(protocolError("taskgraph.execution_snapshot"));
    // a response without a boolean ok is not a reply at all
    expect(guardIncoming(reply("taskgraph.convergence", { data: examples["taskgraph-convergence-view-v2"] })))
      .toEqual(protocolError("taskgraph.convergence"));
  });

  it("leaves messages without a public contract alone", () => {
    const other = { type: "mission_artifact_read_response", payload: { request_id: "r1", ok: true, data: { anything: 1 } } };
    expect(guardIncoming(other)).toBe(other);
    const operate = { type: "taskgraph.abandon_convergence_response", payload: { request_id: "r1", ok: true, data: { done: 1 } } };
    expect(guardIncoming(operate)).toBe(operate);
    const push = { type: "mission_changed", payload: { mission_id: "m1" } };
    expect(guardIncoming(push)).toBe(push);
  });
});

/** Host 任务投影的正样本，与 Host 用例 `test_contract_projection.py` 同形。 */
const ROW = { mission_id: "m1", goal: "写一份 NOTES.md", status: "ACTIVE", stop_reason: null, created_at: 1791365208.4,
  pending_approvals: 0, id: "m1", blocked: false, recovery_isolated: null, task_counts: { completed: 0, total: 1 }, ui_state: "running" };
const DETAIL = {
  mission: { id: "m1", goal: "写一份 NOTES.md", status: "ACTIVE", stop_reason: null, created_at: 1791365208.4, version: 3,
    budget: { max_tokens: 100 }, allowed_tools: [], untrusted_sources: [], ui_state: "running" },
  tasks: [{ id: "t1", goal: { text: "写 NOTES.md", source: "model" }, status: "READY", kind: "work", dependency_ids: [],
    verification_policy: ["format_check"], attempt_count: 0, failure_reason: null, paused: false }],
  attempts: [], results: [], artifacts: [], actions: [], approvals: [], planning_questions: [],
  planning_authorization_requests: [], operation_workspace: null, budget_by_duty: [], unrefined_goals: [],
  steps_no_longer_counting: [], waiting_on: [], blocked: [], disputes: [],
  mission_policy: { version_id: "policy-1", source: "active" },
  usage: { attempts: 0, reserved_tokens: 0, settled_tokens: 0, ledger_version: 1 },
  event_count: 5, through_seq: 9, recovery_isolated: null,
};

const EVENTS = { mission_id: "m1", has_more: false, through_seq: 9, events: [{ seq: 9, type: "HumanCommentAdded",
  created_at: 1791365208.4, task_id: null, attempt_id: null, actor_type: "human", summary: "看一下" }] };
const APPROVAL = { request_id: "ap-1", kind: "action", mission_id: "m1", task_id: "t1", state: "PENDING", level: "L2",
  required_count: 1, grant_count: 0, expires_at: 1791369999, topic: null, options: [],
  summary: { connector: "file_publish", operation: "publish", target: "README.md", params: { artifact_path: "README.md" },
    reason: "发布", reason_source: "system" },
  created_at: 1791365208.4,
  action: { connector: "file_publish", operation: "publish", target: "README.md", params: { artifact_path: "README.md" },
    params_hash: HASH, state: "PROPOSED", reason: { text: "发布", source: "model" } },
  comments: [] };
const NOTICE = { notice_id: "ev-1", mission_id: "m1", state_version: 7, notified_at: 1791365208.4, acked_at: null,
  status: "COMPLETED", status_zh: "已完成", goal: "写一份 NOTES.md", stop_reason: null, unresolved_actions: 0 };

describe("U09 执行图主画面、回合详情、任务列表、任务详情、事件、待批准列表、通知也按公开 Schema 核", () => {
  const examples = graphExamples as Record<string, Record<string, unknown>>;
  const view = examples["taskgraph-execution-view-v1"];
  const detail = examples["taskgraph-execution-detail-v1"];
  const node = (view.execution_nodes as Record<string, unknown>[])[0];

  it("uses the same SDK files: two execution contracts next to the graph ones, two Host ones next to the Assurance ones", () => {
    expect(CONTRACT_SCHEMAS["taskgraph-execution-view-v1"].$id).toBe("urn:simpleharness:taskgraph:execution-view:v1");
    expect(CONTRACT_SCHEMAS["taskgraph-execution-detail-v1"].$id).toBe("urn:simpleharness:taskgraph:execution-detail:v1");
    expect(CONTRACT_SCHEMAS["host-mission-list-v1"].$id).toMatch(/host\/host-mission-list-v1\.schema\.json$/);
    expect(CONTRACT_SCHEMAS["host-mission-detail-v1"].$id).toMatch(/host\/host-mission-detail-v1\.schema\.json$/);
    expect(contractViolation("taskgraph-execution-view-v1", view)).toBeNull();
    expect(contractViolation("taskgraph-execution-detail-v1", detail)).toBeNull();
    expect(contractViolation("host-mission-list-v1", { missions: [ROW] })).toBeNull();
    expect(contractViolation("host-mission-detail-v1", DETAIL)).toBeNull();
    for (const name of ["host-mission-events-v1", "host-mission-approval-list-v1", "host-mission-notices-v1"] as const) {
      expect(CONTRACT_SCHEMAS[name].$id).toMatch(new RegExp("host/" + name + "\\.schema\\.json$"));
    }
    expect(contractViolation("host-mission-events-v1", EVENTS)).toBeNull();
    expect(contractViolation("host-mission-approval-list-v1", { approvals: [APPROVAL] })).toBeNull();
    expect(contractViolation("host-mission-notices-v1", [NOTICE])).toBeNull();
  });

  it.each([
    ["taskgraph.execution_snapshot", view, { ...view, execution_nodes: [{ ...node, debug_row: 1 }] }],
    ["taskgraph.execution_snapshot", view, { ...view, execution_nodes: [{ ...node, kind: "magic" }] }],
    ["taskgraph.execution_snapshot", view, { ...view, graph: { ...(view.graph as object), complete: "yes" } }],
    ["taskgraph.execution_detail", detail, { ...detail, items: [{ t: "tool", tool: "x", ok: true, raw_args: {} }] }],
    ["taskgraph.execution_detail", detail, { ...detail, hidden_items: -1 }],
    ["mission_list", { missions: [ROW] }, { missions: [{ ...ROW, tenant_id: "other" }] }],
    ["mission_list", { missions: [ROW] }, { missions: [{ ...ROW, ui_state: "done" }] }],
    ["mission_get", DETAIL, { ...DETAIL, tasks: [{ ...DETAIL.tasks[0], goal: "写 NOTES.md" }] }],
    ["mission_get", DETAIL, { ...DETAIL, internal_paths: ["/tmp/x"] }],
    // 裁决后续做（推后第 3 批偏差裁决第 5 件）：另三个读动词
    ["mission_events", EVENTS, { ...EVENTS, events: [{ ...EVENTS.events[0], payload: { text: "原始正文" } }] }],
    ["mission_events", EVENTS, { ...EVENTS, has_more: "no" }],
    ["mission_approval_list", { approvals: [APPROVAL] }, { approvals: [{ ...APPROVAL, binding: { path: "/x" } }] }],
    ["mission_approval_list", { approvals: [APPROVAL] }, { approvals: [{ ...APPROVAL, summary: "发布" }] }],
    ["mission_notices", [NOTICE], [{ ...NOTICE, acked_at: 1791365300 }]],
    ["mission_notices", [NOTICE], { notices: [NOTICE] }],
  ])("%s：合格放行（同一个对象），漂移改成协议错", (verb, good, drifted) => {
    const ok = reply(verb, { ok: true, data: good });
    expect(guardIncoming(ok)).toBe(ok);
    expect(guardIncoming(reply(verb, { ok: true, data: drifted }))).toEqual(protocolError(verb));
  });

  it("has one stale note for every view that keeps its last picture", () => {
    expect(STALE_NOTE).toBe("（下面是上次读到的内容，可能已过期）");
  });
});
