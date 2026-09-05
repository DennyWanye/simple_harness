// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import type { ControlWS } from "../code-panel/controlWs";
import { record } from "./requests";
import type { PrimaryRun } from "./controller";

type RunIdentity = Pick<PrimaryRun, "execution_session_ref" | "sdk_run_ref" | "run_ref">;

export function belongsToPrimaryRun(payload: unknown, run: RunIdentity): boolean {
  const p = record(payload);
  return Boolean(run.execution_session_ref && run.sdk_run_ref &&
    p.session_id === run.execution_session_ref &&
    (p.run_id === run.sdk_run_ref || p.run_id === run.run_ref));
}
/** Preserve the backend-issued permission identity; never substitute primary_ref. */
export function primaryRunChannel(port: ControlWS, run: RunIdentity) {
  return {
    state: port.state,
    on_state_change: port.on_state_change,
    send(message: { type: string; payload?: Record<string, unknown> }) {
      if (message.type === "chat_v2_interrupt") return false;
      const payload = message.type === "permissions_pending_list"
        ? { ...message.payload, session_id: run.execution_session_ref } : message.payload ?? {};
      return port.send_command({ ...message, request_id: crypto.randomUUID(), payload });
    },
    on_message(listener: (message: unknown) => void) {
      return port.on_message((raw: unknown) => {
        const message = record(raw);
        const payload = record(message.payload);
        if (message.type === "permissions_pending_list_response") {
          const pending = Array.isArray(payload.pending) ? payload.pending.filter((p) => belongsToPrimaryRun(p, run)) : [];
          listener({ ...message, payload: { ...payload, pending } });
        } else if (["permission_response_applied", "project_directory_error"].includes(String(message.type)) || belongsToPrimaryRun(payload, run)) {
          listener(raw);
        }
      });
    },
  };
}
