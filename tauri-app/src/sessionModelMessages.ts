// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { SessionModelParams } from "./stores/sessionsStore";

export interface SessionBindingAuthority {
  expected_binding_epoch: number;
  expected_provider_incarnation_id?: string | null;
  expected_provider_config_revision?: number | null;
}

export function buildSetProviderMessage(
  session_id: string,
  provider_id: string | null,
  authority?: SessionBindingAuthority,
): { type: string; payload: Record<string, unknown> } {
  return {
    type: "session_set_provider",
    payload: { session_id, provider_id, ...(authority ?? {}) },
  };
}

export function buildSetModelMessage(
  session_id: string,
  model: string | null,
  params?: SessionModelParams | null,
  authority?: SessionBindingAuthority,
): { type: string; payload: Record<string, unknown> } {
  const normalized = model && model.trim() !== "" ? model : null;
  const payload: Record<string, unknown> = { session_id, model: normalized };
  if (params != null) payload.params = params;
  if (authority != null) Object.assign(payload, authority);
  return { type: "session_set_model", payload };
}
