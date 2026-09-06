import type { PrimaryPort } from "./controller";
import { PrimaryRequests, record } from "./requests";

export interface GraphSource {
  evidence_ref_hash: string; span_ref_hash: string; source_kind: string; quote_hash: string;
}
export interface GraphNode {
  node_id: string; memory_id: string; revision: number; memory_type: string;
  label: string; tooltip: string; status: string; lifecycle_state: string;
  epistemic_status: string; conflict_status: string; verification_state: string;
  confidence: number; confidence_basis: string[]; content_hash: string; source_node_hash: string;
  source_refs: GraphSource[]; source_refs_truncated: boolean; can_correct: boolean; can_forget: boolean;
}
export interface GraphEdge {
  edge_id: string; source_node_id: string; target_node_id: string;
  relation_kind: string; label: string; relation_hash: string; edge_hash: string;
}
export interface MemoryGraphView {
  primary_ref: string; view_ref: string; generated_at: number; source_payload_hash: string;
  nodes: GraphNode[]; edges: GraphEdge[]; truncated: { nodes: boolean; edges: boolean };
}
const hash = (v: unknown): v is string => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const text = (v: unknown, max: number): v is string => typeof v === "string" && Array.from(v).length <= max && !v.includes("\0");
const id = (v: unknown): v is string => text(v, 1024) && v.length > 0 && v.trim() === v;
const invalid = () => new Error("记忆关系读取无效，请刷新重试。");

/** Validate a server-filtered view; never invent dangling endpoints or relation nodes. */
export function parseMemoryGraph(raw: unknown, primary: string): MemoryGraphView {
  const v = record(raw), cut = record(v.truncated);
  if (v.primary_ref !== primary || !id(v.view_ref) || !hash(v.source_payload_hash) ||
      typeof v.generated_at !== "number" || !Number.isFinite(v.generated_at) || v.generated_at < 0 ||
      !Array.isArray(v.nodes) || v.nodes.length > 200 || !Array.isArray(v.edges) || v.edges.length > 400 ||
      typeof cut.nodes !== "boolean" || typeof cut.edges !== "boolean") throw invalid();
  const ids = new Set<string>(), edges = new Set<string>();
  for (const value of v.nodes) {
    const n = record(value);
    if (!id(n.node_id) || ids.has(n.node_id) || !id(n.memory_id) || !Number.isSafeInteger(n.revision) || Number(n.revision) < 1 ||
        !["episode", "semantic", "procedure", "prospective"].includes(String(n.memory_type)) ||
        !text(n.label, 512) || !text(n.tooltip, 2048) ||
        ![n.status, n.lifecycle_state, n.epistemic_status, n.conflict_status, n.verification_state].every((value) => text(value, 128)) ||
        typeof n.confidence !== "number" || !Number.isFinite(n.confidence) || n.confidence < 0 || n.confidence > 1 ||
        !Array.isArray(n.confidence_basis) || n.confidence_basis.length > 16 || !n.confidence_basis.every((v) => text(v, 128)) ||
        !hash(n.content_hash) || !hash(n.source_node_hash) ||
        !Array.isArray(n.source_refs) || n.source_refs.length > 8 ||
        ![n.source_refs_truncated, n.can_correct, n.can_forget].every((v) => typeof v === "boolean")) throw invalid();
    for (const value of n.source_refs) {
      const source = record(value);
      if (![source.evidence_ref_hash, source.span_ref_hash, source.quote_hash].every(hash) || !text(source.source_kind, 128)) throw invalid();
    }
    ids.add(n.node_id);
  }
  for (const value of v.edges) {
    const e = record(value);
    if (!id(e.edge_id) || edges.has(e.edge_id) || !id(e.source_node_id) || !id(e.target_node_id) ||
        !ids.has(e.source_node_id) || !ids.has(e.target_node_id) || e.source_node_id === e.target_node_id ||
        !text(e.label, 128) || !text(e.relation_kind, 128) || e.label !== e.relation_kind ||
        !hash(e.relation_hash) || !hash(e.edge_hash)) throw invalid();
    edges.add(e.edge_id);
  }
  return v as unknown as MemoryGraphView;
}
interface GraphState { ready: boolean; loading: boolean; graph: MemoryGraphView | null; error: string }

/** Ephemeral human display state. No localStorage, Agent port, mutations or polling. */
export class GraphRequests {
  private value: GraphState = { ready: false, loading: false, graph: null, error: "" };
  private listeners = new Set<() => void>();
  private reader?: PrimaryRequests;
  private epoch = 0;
  private primary = "";
  getSnapshot = () => this.value;
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  private update(patch: Partial<GraphState>) { this.value = { ...this.value, ...patch }; this.listeners.forEach((fn) => fn()); }
  private clear() { ++this.epoch; this.reader?.invalidate(); this.update({ graph: null, loading: false, error: "" }); }
  connect(port: PrimaryPort, primary: string, owner: string | null, ready: boolean) {
    this.clear(); this.primary = primary; this.reader = new PrimaryRequests(port);
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
        this.clear(); if (this.value.ready) void this.refresh();
      }
    });
    const focus = () => { if (this.value.ready) void this.refresh(); };
    window.addEventListener("focus", focus);
    if (this.value.ready) void this.refresh();
    return () => { revoke(); offState(); off(); window.removeEventListener("focus", focus); this.reader?.dispose(); };
  }
  refresh = async () => {
    if (!this.value.ready || !this.reader) return;
    this.clear(); const epoch = this.epoch;
    this.update({ loading: true });
    try {
      const value = await this.reader.request("primary.memory.graph", { primary_ref: this.primary, node_limit: 80, edge_limit: 160 });
      if (epoch === this.epoch) this.update({ graph: parseMemoryGraph(value, this.primary) });
    } catch { if (epoch === this.epoch) this.update({ error: "记忆关系暂时无法读取，请刷新重试。" }); }
    finally { if (epoch === this.epoch) this.update({ loading: false }); }
  };
}
