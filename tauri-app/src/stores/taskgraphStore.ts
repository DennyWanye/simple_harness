// SPDX-License-Identifier: BUSL-1.1
/** Strict, session-local TaskGraph read models. No graph editing or execution authority. */
export type ReadToken = {
  plan_revision: number; through_seq: number; snapshot_hash: string; manifest_hash: string;
  validity_epochs: { scope_id: string; epoch: number }[];
};
export type GraphNode = {
  occurrence_id: string; task_id: string; obligation_id: string; form: "primitive" | "compound";
  contract_revision: number; dispatch_generation: number; phase: string; readiness: string; reason_codes: string[];
};
export type GraphEdge = { kind: "refinement" | "order" | "data"; source: string; target: string; identity: string };
export type SourceRef = { kind: string; id: string; revision: number; content_hash: string };
export type GraphSnapshot = {
  schema_version: 1; mission_id: string; read_token: ReadToken; nodes: GraphNode[]; edges: GraphEdge[];
  planning_frontier: string[]; execution_frontier: string[]; root_resolution_refs: SourceRef[];
  complete: true; next_cursor: null; view_mode: "CURRENT" | "HISTORICAL_STRUCTURE";
};
export type GraphExplanation = {
  schema_version: 1; mission_id: string; occurrence_id: string; read_token: ReadToken;
  readiness: string; reason_codes: string[]; source_refs: SourceRef[]; details: string[];
};
export type GraphDiff = {
  schema_version: 1; mission_id: string; from_revision: number; to_revision: number;
  from_manifest_hash: string; to_manifest_hash: string; complete: true;
  changes: { kind: string; identity: string; change: string; before_hash: string | null; after_hash: string | null }[];
};
export type ConvergenceView = {
  schema_version: 1; mission_id: string; through_seq: number; complete: true;
  jobs: { job_id: string; decision_id: string; source_revision: number; candidate_hash: string; impact_hash: string;
    state: string; row_version: number; diagnostic_refs: SourceRef[];
    targets: { occurrence_id: string; task_id: string; expected_generation: number; target_kind: string }[];
  }[];
};

const readiness = new Set(["NOT_SELECTED", "NEEDS_REFINEMENT", "WAITING_ORDER", "WAITING_DATA",
  "WAITING_EVIDENCE", "WAITING_APPROVAL", "STALE_BINDING", "READY_CANDIDATE",
  "WAITING_OPERATION_UNKNOWN", "OBSERVER_UNAVAILABLE", "GRAPH_INTEGRITY", "VALIDITY_RECHECK_PENDING"]);
type Obj = Record<string, unknown>;
function invalid(): never { throw new Error("执行图返回的数据不完整或格式不符"); }
function obj(value: unknown, keys: string[]): Obj {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const raw = value as Obj;
  if (Object.keys(raw).length !== keys.length || keys.some(key => !(key in raw))) return invalid();
  return raw;
}
function text(value: unknown, limit = 512): string {
  if (typeof value !== "string" || !value.length || value.length > limit) return invalid();
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return invalid();
  return value;
}
function hash(value: unknown): string {
  const s = text(value);
  if (!/^[a-f0-9]{64}$/.test(s)) return invalid();
  return s;
}
function array<T>(value: unknown, bound: number, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > bound) return invalid();
  return value.map(parse);
}
function strings(value: unknown, bound: number): string[] { return array(value, bound, item => text(item)); }
function oneOf(value: unknown, allowed: readonly string[]): string {
  const s = text(value); if (!allowed.includes(s)) return invalid(); return s;
}
function unique(values: string[]): void { if (new Set(values).size !== values.length) invalid(); }
function source(value: unknown): SourceRef {
  const r = obj(value, ["kind", "id", "revision", "content_hash"]);
  return { kind: text(r.kind), id: text(r.id), revision: integer(r.revision), content_hash: hash(r.content_hash) };
}
export function graphErrorMessage(value: unknown): string {
  const r = obj(value, ["schema_version", "origin", "stage", "code", "detail", "retry_kind", "source_identity"]);
  if (r.schema_version !== 1) invalid();
  oneOf(r.origin, ["MODEL", "SYSTEM", "RUNTIME"]);
  oneOf(r.stage, ["SOURCE", "PREVIEW", "ADMISSION", "CONVERGENCE", "COMMIT", "DISPATCH", "REPLAY", "READ"]);
  if (r.source_identity !== null) text(r.source_identity);
  if (typeof r.detail !== "string" || r.detail.length > 2000) invalid();
  const retry = oneOf(r.retry_kind, ["NONE", "REQUERY", "RECONCILE", "NEW_PLANNER_REQUEST", "OPERATOR_REPAIR"]);
  const guidance: Record<string, string> = { NONE: "", REQUERY: "请重新读取。", RECONCILE: "等待外部结果核对。",
    NEW_PLANNER_REQUEST: "需要重新规划。", OPERATOR_REPAIR: "需要修复来源后再读取。" };
  // 2026-09-25 UI 全量点击：普通任务不启用执行图，这是正常状态，不是错误。
  if (r.code === "NOT_ENABLED") return "这个任务按常规方式执行，没有启用执行图，这里没有图可看。";
  return (r.detail || text(r.code)) + " " + guidance[retry] + "（" + text(r.code) + "）";
}
function token(value: unknown): ReadToken {
  const r = obj(value, ["plan_revision", "through_seq", "snapshot_hash", "manifest_hash", "validity_epochs"]);
  const epochs = array(r.validity_epochs, 256, item => {
    const e = obj(item, ["scope_id", "epoch"]); return { scope_id: text(e.scope_id), epoch: integer(e.epoch) };
  });
  unique(epochs.map(e => e.scope_id));
  return { plan_revision: integer(r.plan_revision), through_seq: integer(r.through_seq),
    snapshot_hash: hash(r.snapshot_hash), manifest_hash: hash(r.manifest_hash), validity_epochs: epochs };
}
export function sameToken(a: ReadToken, b: ReadToken): boolean {
  return a.plan_revision === b.plan_revision && a.through_seq === b.through_seq
    && a.snapshot_hash === b.snapshot_hash && a.manifest_hash === b.manifest_hash
    && JSON.stringify(a.validity_epochs) === JSON.stringify(b.validity_epochs);
}
function identity(r: Obj, mission: string): void {
  if (r.schema_version !== 1 || r.mission_id !== mission) invalid();
}
export function parseSnapshot(value: unknown, mission: string, revision: number | null): GraphSnapshot {
  const r = obj(value, ["schema_version", "mission_id", "read_token", "nodes", "edges", "planning_frontier",
    "execution_frontier", "root_resolution_refs", "complete", "next_cursor", "view_mode"]);
  identity(r, mission);
  if (r.complete !== true || r.next_cursor !== null) invalid();
  const readToken = token(r.read_token);
  const mode = oneOf(r.view_mode, ["CURRENT", "HISTORICAL_STRUCTURE"]) as GraphSnapshot["view_mode"];
  if ((revision === null) !== (mode === "CURRENT") || (revision !== null && readToken.plan_revision !== revision)) invalid();
  const nodes = array(r.nodes, 4096, value => {
    const n = obj(value, ["occurrence_id", "task_id", "obligation_id", "form", "contract_revision",
      "dispatch_generation", "phase", "readiness", "reason_codes"]);
    const reason = text(n.readiness); if (!readiness.has(reason)) invalid();
    return { occurrence_id: text(n.occurrence_id), task_id: text(n.task_id), obligation_id: text(n.obligation_id),
      form: oneOf(n.form, ["primitive", "compound"]) as GraphNode["form"], contract_revision: integer(n.contract_revision),
      dispatch_generation: integer(n.dispatch_generation), phase: text(n.phase), readiness: reason,
      reason_codes: strings(n.reason_codes, 32) };
  });
  const ids = new Set(nodes.map(n => n.occurrence_id)); unique(nodes.map(n => n.occurrence_id));
  const edges = array(r.edges, 8192, value => {
    const e = obj(value, ["kind", "source", "target", "identity"]);
    const edge = { kind: oneOf(e.kind, ["refinement", "order", "data"]) as GraphEdge["kind"],
      source: text(e.source), target: text(e.target), identity: text(e.identity) };
    if (!ids.has(edge.source) || !ids.has(edge.target)) invalid(); return edge;
  });
  unique(edges.map(e => JSON.stringify([e.kind, e.identity])));
  const planning = strings(r.planning_frontier, 4096), execution = strings(r.execution_frontier, 4096);
  unique(planning); unique(execution);
  if ([...planning, ...execution].some(id => !ids.has(id))) invalid();
  const byId = new Map(nodes.map(node => [node.occurrence_id, node]));
  if (execution.some(id => byId.get(id)?.form !== "primitive" || byId.get(id)?.readiness !== "READY_CANDIDATE")) invalid();
  if (mode === "HISTORICAL_STRUCTURE" && (planning.length || execution.length ||
    nodes.some(n => n.phase !== "HISTORY_ONLY" || n.readiness !== "NOT_SELECTED" ||
      !n.reason_codes.includes("HISTORICAL_VIEW_NON_EXECUTABLE")))) invalid();
  const roots = array(r.root_resolution_refs, 256, source);
  if (roots.some(ref => ref.kind !== "resolution")) invalid();
  return { schema_version: 1, mission_id: mission, read_token: readToken, nodes, edges,
    planning_frontier: planning, execution_frontier: execution, root_resolution_refs: roots,
    complete: true, next_cursor: null, view_mode: mode };
}
export function parseExplanation(value: unknown, mission: string): GraphExplanation {
  const r = obj(value, ["schema_version", "mission_id", "occurrence_id", "read_token", "readiness",
    "reason_codes", "source_refs", "details"]); identity(r, mission);
  const reason = text(r.readiness); if (!readiness.has(reason)) invalid();
  return { schema_version: 1, mission_id: mission, occurrence_id: text(r.occurrence_id), read_token: token(r.read_token),
    readiness: reason, reason_codes: strings(r.reason_codes, 32), source_refs: array(r.source_refs, 64, source),
    details: array(r.details, 32, value => {
      if (typeof value !== "string" || value.length > 2000) invalid(); return value as string;
    }) };
}
export function parseDiff(value: unknown, mission: string): GraphDiff {
  const r = obj(value, ["schema_version", "mission_id", "from_revision", "to_revision",
    "from_manifest_hash", "to_manifest_hash", "changes", "complete"]); identity(r, mission);
  if (r.complete !== true) invalid();
  return { schema_version: 1, mission_id: mission, from_revision: integer(r.from_revision),
    to_revision: integer(r.to_revision), from_manifest_hash: hash(r.from_manifest_hash),
    to_manifest_hash: hash(r.to_manifest_hash), complete: true,
    changes: array(r.changes, 16384, value => {
      const c = obj(value, ["kind", "identity", "before_hash", "after_hash"]);
      if (c.before_hash === c.after_hash) invalid();
      return { kind: text(c.kind), identity: text(c.identity),
        change: c.before_hash === null ? "ADDED" : c.after_hash === null ? "REMOVED" : "CHANGED",
        before_hash: c.before_hash === null ? null : hash(c.before_hash),
        after_hash: c.after_hash === null ? null : hash(c.after_hash) };
    }) };
}
export function parseConvergence(value: unknown, mission: string): ConvergenceView {
  const r = obj(value, ["schema_version", "mission_id", "through_seq", "jobs", "complete"]); identity(r, mission);
  if (r.complete !== true) invalid();
  return { schema_version: 1, mission_id: mission, through_seq: integer(r.through_seq), complete: true,
    jobs: array(r.jobs, 4096, value => {
      const j = obj(value, ["job_id", "decision_id", "source_revision", "candidate_hash", "impact_hash",
        "state", "row_version", "targets", "diagnostic_refs"]);
      if (integer(j.row_version) < 1) invalid();
      return { job_id: text(j.job_id), decision_id: text(j.decision_id), source_revision: integer(j.source_revision),
        candidate_hash: hash(j.candidate_hash), impact_hash: hash(j.impact_hash), row_version: integer(j.row_version),
        state: oneOf(j.state, ["FENCED", "WAITING", "READY", "APPLIED", "ABANDONED"]),
        diagnostic_refs: array(j.diagnostic_refs, 64, source),
        targets: array(j.targets, 4096, value => {
          const t = obj(value, ["occurrence_id", "task_id", "expected_generation", "target_kind"]);
          return { occurrence_id: text(t.occurrence_id), task_id: text(t.task_id),
            expected_generation: integer(t.expected_generation), target_kind: oneOf(t.target_kind, ["RETIRING", "INPUT_REPLACED"]) };
        }) };
    }) };
}

export type GraphReadState = { snapshot: GraphSnapshot | null; explanation: GraphExplanation | null;
  diff: GraphDiff | null; convergence: ConvergenceView | null; stale: boolean; convergenceStale: boolean;
  currentThroughSeq: number; error: string };
export const emptyGraphState: GraphReadState = {
  snapshot: null, explanation: null, diff: null, convergence: null, stale: false, convergenceStale: false,
  currentThroughSeq: 0, error: "",
};
export type GraphAction = { type: "reset" } | { type: "stale" } | { type: "error"; error: string }
  | { type: "snapshot"; value: GraphSnapshot } | { type: "explanation"; value: GraphExplanation }
  | { type: "diff"; value: GraphDiff } | { type: "convergence"; value: ConvergenceView };
export function graphReducer(state: GraphReadState, action: GraphAction): GraphReadState {
  switch (action.type) {
    case "reset": return { ...emptyGraphState };
    case "stale": return { ...state, stale: state.snapshot?.view_mode === "CURRENT",
      convergenceStale: !!state.convergence, explanation: null };
    case "error": return { ...state, stale: !!state.snapshot, convergenceStale: !!state.convergence,
      error: action.error, explanation: null };
    case "snapshot": {
      const current = action.value.view_mode === "CURRENT";
      const sequence = action.value.read_token.through_seq;
      // Keep the latest live watermark across switches to historical structure.
      // A reconnect/identity change resets the entire reducer, including this mark.
      if (current && sequence < state.currentThroughSeq) {
        return { ...state, stale: state.snapshot?.view_mode === "CURRENT", explanation: null,
          error: "收到较旧的执行图，请重新读取" };
      }
      return { ...state, snapshot: action.value, explanation: null, stale: false, error: "",
        currentThroughSeq: current ? sequence : state.currentThroughSeq,
        convergenceStale: state.convergenceStale || !!(current && state.convergence
          && state.convergence.through_seq < sequence) };
    }
    case "explanation":
      if (!state.snapshot || state.stale || state.snapshot.view_mode !== "CURRENT"
          || !sameToken(state.snapshot.read_token, action.value.read_token)) {
        return { ...state, stale: true, explanation: null, error: "状态已变化，请刷新后查看原因" };
      }
      return { ...state, explanation: action.value, error: "" };
    case "diff": return { ...state, diff: action.value, error: "" };
    case "convergence":
      if (action.value.through_seq < state.currentThroughSeq) {
        return { ...state, convergenceStale: true, error: "收到较旧的收敛状态，请重新读取" };
      }
      return { ...state, convergence: action.value, convergenceStale: false, error: "",
        currentThroughSeq: action.value.through_seq,
        stale: state.stale || !!(state.snapshot?.view_mode === "CURRENT"
          && state.snapshot.read_token.through_seq < action.value.through_seq),
        explanation: state.snapshot?.view_mode === "CURRENT"
          && state.snapshot.read_token.through_seq < action.value.through_seq ? null : state.explanation };
  }
}
