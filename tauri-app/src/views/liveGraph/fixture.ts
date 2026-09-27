// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 测试用：一份 `taskgraph.execution_snapshot` 回复，形状照 SDK 真实输出（真机任务
 * mission-a22c9fc39b8abed8：写说明 → 发布；发布第一次被规则检查拒绝、修补请求、规划决定再执行、第二次通过）。
 */
export const M = "mission-1";

type Obj = Record<string, unknown>;

export function snapshot(extra: Obj = {}, overrides: { nodes?: Obj[]; edges?: Obj[]; phases?: Record<string, string> } = {}): Obj {
  const phases = { root: "waiting_children", a: "COMPLETED", b: "ACTIVE", ...(overrides.phases ?? {}) };
  const turn = (id: string, state = "SETTLED") => ({ intent_id: "intent-" + id, agent_id: "agent-" + id, state, profile_id: "p", model: "deepseek-v4.1-flash" });
  return {
    schema_version: 1, mission_id: M, view_mode: "CURRENT",
    read_token: { plan_revision: 1, through_seq: 10, validity_epochs: [], snapshot_hash: "s", manifest_hash: "m" },
    graph: {
      schema_version: 1, mission_id: M,
      nodes: [
        { occurrence_id: "root", task_id: "task-root", form: "compound", phase: phases.root, readiness: "NOT_SELECTED", reason_codes: [] },
        { occurrence_id: "a", task_id: "task-a", form: "primitive", phase: phases.a, readiness: "NOT_SELECTED", reason_codes: [] },
        { occurrence_id: "b", task_id: "task-b", form: "primitive", phase: phases.b, readiness: "READY_CANDIDATE", reason_codes: [] },
      ],
      edges: [
        { kind: "refinement", source: "root", target: "a", identity: "r1" },
        { kind: "refinement", source: "root", target: "b", identity: "r2" },
        { kind: "order", source: "a", target: "b", identity: "o1" },
        { kind: "data", source: "a", target: "b", identity: "d1" },
      ],
    },
    occurrence_labels: [
      { occurrence_id: "a", step_key: "notes", step_index: 0, duties: ["notes 步骤产出 NOTES.md"] },
      { occurrence_id: "b", step_key: "publish", step_index: 1, duties: ["把 NOTES.md 发布到授权目录"] },
    ],
    execution_cut: { observed_at_ms: 1, imported_through_seq: 10, execution_hash: "h1", runtime_source_watermarks: [], coverage: "COMPLETE" },
    execution_nodes: overrides.nodes ?? [
      { node_id: "planning:p1", kind: "planning", at_ms: 1000, role: "planner", decisions: [{ decision_id: "pd1", decision_type: "REFINE", status: "COMMITTED", rejection_codes: [] }], turn: null, summary: null },
      { node_id: "plan_revision:1", kind: "plan_revision", at_ms: 1100, plan_revision: 1, state: "ACTIVE", base_revision: null, summary: null },
      { node_id: "attempt:a1", kind: "attempt", at_ms: 2000, attempt_id: "a1", task_id: "task-a", occurrence_id: "a", ordinal: 1, status: "COMPLETED", turn: turn("a1"), summary: { text: "写好了 NOTES.md", source_kind: "result_envelope", source_ref: "r-a1" } },
      { node_id: "check:r-a1", kind: "check", at_ms: 2100, result_id: "r-a1", attempt_id: "a1", verdict: "PASS", layers: [{ layer: "rule_check", status: "PASS" }], turn: turn("c1"), summary: null },
      { node_id: "attempt:b1", kind: "attempt", at_ms: 3000, attempt_id: "b1", task_id: "task-b", occurrence_id: "b", ordinal: 1, status: "RETRY_WAIT", turn: turn("b1"), summary: { text: "写了发布候选", source_kind: "result_envelope", source_ref: "r-b1" } },
      { node_id: "check:r-b1", kind: "check", at_ms: 3100, result_id: "r-b1", attempt_id: "b1", verdict: "FAIL", layers: [{ layer: "rule_check", status: "FAIL", summary: "artifact_not_in_result: NOTES.md" }], turn: null, summary: { text: "artifact_not_in_result: NOTES.md", source_kind: "verification", source_ref: "r-b1:rule_check" } },
      { node_id: "repair_request:rr", kind: "repair_request", at_ms: 3200, request_id: "rr", trigger_refs: ["b1"], source_event_type: "VerificationFailed", summary: { text: "候选没把 NOTES.md 列进产物", source_kind: "repair_request", source_ref: "rr" } },
      { node_id: "planning:p2", kind: "planning", at_ms: 3300, role: "planner", decisions: [{ decision_id: "pd2", decision_type: "REPAIR", status: "COMMITTED", rejection_codes: [] }], turn: turn("p2"), summary: { text: "同一合同再执行一次", source_kind: "planning_decision", source_ref: "pd2" } },
      { node_id: "attempt:b2", kind: "attempt", at_ms: 4000, attempt_id: "b2", task_id: "task-b", occurrence_id: "b", ordinal: 2, status: "RUNNING", turn: turn("b2", "SUBMITTED"), summary: null },
    ],
    execution_edges: overrides.edges ?? [
      { kind: "committed_as", source: "planning:p1", target: "plan_revision:1", target_layer: "execution" },
      { kind: "attempt_of", source: "attempt:a1", target: "a", target_layer: "structure" },
      { kind: "review_of", source: "check:r-a1", target: "attempt:a1", target_layer: "execution" },
      { kind: "attempt_of", source: "attempt:b1", target: "b", target_layer: "structure" },
      { kind: "review_of", source: "check:r-b1", target: "attempt:b1", target_layer: "execution" },
      { kind: "repair_requested", source: "attempt:b1", target: "repair_request:rr", target_layer: "execution" },
      { kind: "decision_for", source: "planning:p2", target: "repair_request:rr", target_layer: "execution" },
      { kind: "retry_authorized", source: "planning:p2", target: "attempt:b2", target_layer: "execution" },
      { kind: "attempt_of", source: "attempt:b2", target: "b", target_layer: "structure" },
      { kind: "rework_of", source: "attempt:b2", target: "attempt:b1", target_layer: "execution" },
    ],
    next_cursor: null, complete: true,
    ...extra,
  };
}
