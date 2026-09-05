// SPDX-License-Identifier: BUSL-1.1
import type { ControlWS } from "../code-panel/controlWs";
import { PrimaryRequests, record } from "./requests";
import type { PrimaryRun, PrimaryPort } from "./controller";

type RunIdentity = Pick<PrimaryRun, "execution_session_ref" | "sdk_run_ref" | "run_ref">;
type DecisionTarget = RunIdentity & { primary_ref: string; generation: number };
export function belongsToPrimaryRun(payload: unknown, run: RunIdentity): boolean {
  const p = record(payload);
  return Boolean(run.execution_session_ref && run.sdk_run_ref &&
    p.session_id === run.execution_session_ref &&
    (p.run_id === run.sdk_run_ref || p.run_id === run.run_ref));
}
/** Only an authenticated exact-Run read may introduce a Primary permission card. */
export function primaryRunChannel(port: PrimaryPort, run: DecisionTarget, events?: ControlWS) {
  let requests: PrimaryRequests;
  let off = () => {}, offEvents = () => {}, offState = () => {};
  const listeners = new Set<(message: unknown) => void>();
  let lifetime = 0;
  let disposed = true, reading = false, readAgain = false, readSequence = 0;
  const target = { primary_ref: run.primary_ref, expected_run_ref: run.run_ref, expected_generation: run.generation };
  const emit = (message: unknown) => { if (!disposed) for (const listener of listeners) listener(message); };
  const exact = (result: Record<string, unknown>) => result.primary_ref === run.primary_ref && result.run_ref === run.run_ref &&
    result.generation === run.generation && result.sdk_run_ref === run.sdk_run_ref;
  const read = async () => {
    if (disposed) return;
    if (reading) { readAgain = true; return; }
    reading = true;
    const life = lifetime;
    const sequence = ++readSequence;
    try {
      const result = await requests.request("primary.decisions.list", target);
      if (!exact(result) || !Array.isArray(result.pending) || result.truncated !== false || result.pending.length > 32 ||
          !result.pending.every((p) => belongsToPrimaryRun(p, run) && record(p).sdk_run_id === run.sdk_run_ref)) {
        throw new Error("授权读取目标不匹配");
      }
      if (sequence === readSequence) {
        emit({ type: "permissions_pending_list_response", payload: { pending: result.pending } });
        emit({ type: "primary_decision_status", payload: { state: result.sdk_state, error: "" } });
      }
    } catch {
      if (sequence === readSequence) emit({ type: "primary_decision_status", payload: { error: "授权状态读取未确认，请刷新状态重试。" } });
    } finally {
      if (life === lifetime) {
        reading = false;
        if (readAgain) { readAgain = false; void read(); }
      }
    }
  };
  const handle = (raw: unknown) => {
    const message = record(raw), payload = record(message.payload);
    if (message.type === "human_memory_changed") { void read(); return; }
    // Live permission frames and legacy ACKs are not Primary read authority.
    if (["permission_request", "permission_response_applied", "permissions_pending_list_response"].includes(String(message.type))) return;
    if (belongsToPrimaryRun(payload, run)) emit(raw);
  };
  const handleEvent = (raw: unknown) => {
    const message = record(raw);
    if (["permission_request", "permission_response_applied", "permissions_pending_list_response"].includes(String(message.type))) return;
    if (belongsToPrimaryRun(message.payload, run)) emit(raw);
  };
  const onFocus = () => void read();
  const start = () => {
    if (!disposed) return;
    disposed = false; ++lifetime; reading = false; readAgain = false;
    requests = new PrimaryRequests(port);
    off = port.on_message(handle);
    offEvents = events?.on_message(handleEvent) ?? (() => {});
    offState = port.on_state_change((state) => {
      if (state !== "connected") {
        ++readSequence;
        requests.invalidate();
        emit({ type: "permissions_pending_list_response", payload: { pending: [] } });
      }
    });
    window.addEventListener("focus", onFocus);
  };
  return {
    start,
    state: port.state,
    on_state_change: port.on_state_change,
    dispose() { if (disposed) return; disposed = true; ++lifetime; readAgain = false; ++readSequence; requests.dispose(); off(); offEvents(); offState(); listeners.clear(); window.removeEventListener("focus", onFocus); },
    send(message: { type: string; payload?: Record<string, unknown> }) {
      if (disposed) return false;
      if (message.type === "permissions_pending_list") { void read(); return true; }
      if (message.type === "permission_response") {
        const p = message.payload ?? {};
        if (!belongsToPrimaryRun(p, run) || !["allow", "deny"].includes(String(p.decision))) return false;
        // Exact SDK-issued nonce/version remain in this request's memory only.
        const request = { ...target, decision_id: p.decision_id, nonce: p.nonce, version: p.version, decision: p.decision };
        const life = lifetime;
        ++readSequence; // An older snapshot must not dismiss a submitted card.
        void requests.request("primary.decisions.respond", request).then((result) => {
          if (disposed || life !== lifetime) return;
          if (!exact(result) || result.decision_id !== p.decision_id || result.version !== p.version ||
              !["allowed", "denied", "expired"].includes(String(result.outcome)) ||
              (result.outcome === "allowed" && p.decision !== "allow") || (result.outcome === "denied" && p.decision !== "deny")) {
            throw new Error("授权回执目标不匹配");
          }
          emit({ type: "permission_response_applied", payload: { ok: true, decision_id: p.decision_id } });
          emit({ type: "primary_decision_status", payload: { outcome: result.outcome, error: "" } });
          void read();
        }).catch(() => {
          if (disposed || life !== lifetime) return;
          emit({ type: "permission_response_applied", payload: { ok: false, decision_id: p.decision_id,
            error: { message: "授权结果未确认，请补读状态后重试；不会自动批准。" } } });
        });
        return true;
      }
      if (message.type === "chat_v2_interrupt") return false;
      return events?.send_command({ ...message, request_id: crypto.randomUUID(), payload: message.payload ?? {} }) ?? false;
    },
    on_message(listener: (message: unknown) => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
  };
}
