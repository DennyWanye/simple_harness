/** Protocol unit-test values only. Real SDK/store/API fixture is generated separately. */
import type { MemoryGraphView } from "../graphRequests";
import type { PrimaryPort } from "../controller";
import type { PrimaryWireRequest } from "../requests";
export const graph: MemoryGraphView = {
  primary_ref: "p", view_ref: "view", generated_at: 1, source_payload_hash: "a".repeat(64),
  nodes: [{ node_id: "n", memory_id: "memory", revision: 1, memory_type: "semantic", label: "秋天偏好", tooltip: "",
    status: "active", lifecycle_state: "active", epistemic_status: "explicit_user", conflict_status: "uncontested",
    verification_state: "source_bound", confidence: 0.8, confidence_basis: [], content_hash: "b".repeat(64),
    source_node_hash: "c".repeat(64), source_refs: [], source_refs_truncated: false, can_correct: true, can_forget: true }],
  edges: [], truncated: { nodes: false, edges: false },
};
export function wire() {
  const listeners = new Set<(v: unknown) => void>(), states = new Set<(s: "connected" | "disconnected") => void>();
  const sent: PrimaryWireRequest[] = [];
  const port: PrimaryPort = { state: () => "connected", send_command: (r) => { sent.push(r); return true; },
    on_message: (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    on_state_change: (fn) => { states.add(fn); return () => { states.delete(fn); }; } };
  const emit = (v: unknown) => [...listeners].forEach((fn) => fn(v));
  const reply = (i: number, result: object) => emit({ type: "human_memory_response", request_id: sent[i].request_id,
    payload: { ok: true, operation: sent[i].operation, result } });
  return { port, sent, reply, emit, states };
}
export const flush = async () => { for (let i = 0; i < 8; ++i) await Promise.resolve(); };
