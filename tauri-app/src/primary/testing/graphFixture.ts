/** Protocol unit-test values only. Real SDK/store/API fixture is generated separately.
 *
 * 2026-09-10：认知记忆关系图夹具（`graph`）随 simple-harness-memory-sdk 移除；
 * 本文件只剩通用的 PrimaryPort 线缆桩 `wire()` 与 `flush()`，仍被 task scope /
 * chat view 的协议单测使用。
 */
import type { PrimaryPort } from "../controller";
import type { PrimaryWireRequest } from "../requests";

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
