// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { create } from "zustand";
import { arrayMove } from "@dnd-kit/sortable";

// ---- Domain types ---------------------------------------------------------

export interface Provider {
  id: string;
  /** 2026-08-09：relay 来源已移除，provider 一律用户自建。 */
  source?: "user";
  account_ref?: string;
  name: string;
  base_url: string;
  /** canonical model list (a provider can serve multiple models). */
  models: string[];
  /** which model in `models` is used by default for chain calls. */
  default_model?: string | null;
  /** Back-compat scalar — server includes it derived from models[0]/default_model. */
  model?: string;
  /** Always `"********"` when sourced from backend list (sanitized). */
  api_key: string;
  priority: number;
  enabled: boolean;
  incarnation_id?: string;
  config_revision?: number;
}

// ---- Pure helpers (exported for tests) ------------------------------------

/** Backend redaction sentinel for api_key field in list responses. */
export const REDACTED_API_KEY = "********";

/** Is the api_key string the redaction sentinel (never plaintext)? */
export function isRedactedApiKey(v: unknown): boolean {
  return typeof v === "string" && v === REDACTED_API_KEY;
}

/** Display string for api_key cell — always 8 stars when not editing. */
export function displayApiKey(_p: Pick<Provider, "api_key">): string {
  // Spec: list_providers SHALL return api_key="********".
  // Regardless of what the backend sent we render the sentinel to make
  // an accidental plaintext leak in the UI impossible.
  return REDACTED_API_KEY;
}

/** Build the ws message frontend sends to reorder providers. */
export function buildReorderMessage(
  ordered_ids: string[],
  providers?: Provider[],
): {
  type: "settings_providers_reorder";
  payload: Record<string, unknown>;
} {
  const expected_versions = providers
    ? Object.fromEntries(
        providers
          .filter((p) => p.incarnation_id && p.config_revision != null)
          .map((p) => [
            p.id,
            {
              incarnation_id: p.incarnation_id,
              config_revision: p.config_revision,
            },
          ]),
      )
    : undefined;
  return {
    type: "settings_providers_reorder",
    payload: { ordered_ids, ...(expected_versions ? { expected_versions } : {}) },
  };
}

/** Build the ws message frontend sends to toggle enabled. */
export function buildToggleEnabledMessage(
  id: string,
  enabled: boolean,
  provider?: Provider,
): { type: "settings_providers_update"; payload: Record<string, unknown> } {
  return {
    type: "settings_providers_update",
    payload: {
      id,
      patch: { enabled },
      ...(provider?.incarnation_id
        ? {
            expected_incarnation_id: provider.incarnation_id,
            expected_config_revision: provider.config_revision,
          }
        : {}),
    },
  };
}

/**
 * 把某个提供方的默认模型改成 model。后台只接受模型列表里的默认模型，所以 model 不在
 * 列表里时补丁先带追加后的 models，再带 default_model（后台按键顺序处理）。带版本号防并发覆盖。
 */
export function buildSetDefaultModelMessage(
  provider: { id: string; models?: string[]; incarnation_id?: string; config_revision?: number },
  model: string,
): { type: "settings_providers_update"; payload: Record<string, unknown> } {
  const models = Array.isArray(provider.models) ? provider.models : [];
  const patch: Record<string, unknown> = models.includes(model)
    ? { default_model: model }
    : { models: [...models, model], default_model: model };
  return {
    type: "settings_providers_update",
    payload: {
      id: provider.id,
      patch,
      ...(provider.incarnation_id
        ? {
            expected_incarnation_id: provider.incarnation_id,
            expected_config_revision: provider.config_revision,
          }
        : {}),
    },
  };
}

/** Build the ws message frontend sends to remove a provider. */
export function buildRemoveMessage(
  id: string,
  provider?: Provider,
): {
  type: "settings_providers_remove";
  payload: Record<string, unknown>;
} {
  return {
    type: "settings_providers_remove",
    payload: {
      id,
      ...(provider?.incarnation_id
        ? {
            expected_incarnation_id: provider.incarnation_id,
            expected_config_revision: provider.config_revision,
          }
        : {}),
    },
  };
}

/** Build the ws message frontend sends to request the provider list. */
export function buildListRequestMessage(): { type: "settings_providers_list_request" } {
  return { type: "settings_providers_list_request" };
}

/**
 * Apply a keyboard-driven reorder (↑ / ↓ on a focused row).
 * Pure helper so vitest can verify without a real DOM.
 * Returns the new ordered ids; if the move is a no-op (already at the
 * edge), returns the input unchanged.
 */
export function applyKeyboardReorder(
  ordered_ids: string[],
  focused_id: string,
  direction: "up" | "down",
): string[] {
  const idx = ordered_ids.indexOf(focused_id);
  if (idx < 0) return ordered_ids;
  const target = direction === "up" ? idx - 1 : idx + 1;
  if (target < 0 || target >= ordered_ids.length) return ordered_ids;
  return arrayMove(ordered_ids, idx, target);
}

/** Sort providers for display: priority asc, then name asc as tie-break. */
export function sortProvidersForDisplay(providers: Provider[]): Provider[] {
  return [...providers].sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority;
    return a.name.localeCompare(b.name);
  });
}

// ---- Providers store + ws dispatcher (used by code-panel/ws.ts) -----------
//
// Providers are app-global state, but per the Phase 4 file-whitelist we
// keep the store co-located with the component that owns it. ws.ts
// imports `dispatchProviderEvent` to route the 4 new wire events here.
export interface ProvidersStore {
  providers: Provider[];
  error: string | null;
}

export const useProvidersStore = create<ProvidersStore>(() => ({
  providers: [],
  error: null,
}));

/**
 * Route a backend provider-related ws message into useProvidersStore.
 * Pure side-effect on the store (no React), so vitest can drive it
 * straight from the test file without needing a DOM.
 *
 * Recognised event types:
 *   - settings_providers_list_response   { providers: [...] }
 *   - providers_changed                  { providers: [...] }
 *   - settings_providers_reordered       { providers: [...] }
 *   - settings_providers_added           { provider }
 *   - settings_providers_updated         { provider }
 *   - settings_providers_removed         { id }
 *   - settings_providers_error           { reason, detail }
 *
 * Unknown types are ignored so adding new server-side events later
 * doesn't accidentally clobber the store.
 */
/** 服务商相关控制消息（载荷按 type 各带其中一部分字段）。 */
export interface ProviderEventMessage {
  type?: string;
  payload?: {
    providers?: Provider[];
    provider?: Provider;
    id?: string;
    reason?: unknown;
    detail?: unknown;
    ok?: boolean;
    models?: string[];
  };
}

export function dispatchProviderEvent(msg: ProviderEventMessage): void {
  switch (msg.type) {
    case "settings_providers_list_response":
    case "providers_changed":
    case "settings_providers_reordered": {
      const list: Provider[] = Array.isArray(msg.payload?.providers) ? msg.payload.providers : [];
      useProvidersStore.setState({ providers: list, error: null });
      break;
    }
    case "settings_providers_added": {
      const p: Provider | undefined = msg.payload?.provider;
      if (!p || !p.id) break;
      useProvidersStore.setState((state) => {
        const exists = state.providers.some((x) => x.id === p.id);
        const next = exists
          ? state.providers.map((x) => (x.id === p.id ? p : x))
          : [...state.providers, p];
        return { providers: next, error: null };
      });
      break;
    }
    case "settings_providers_updated": {
      const p: Provider | undefined = msg.payload?.provider;
      if (!p || !p.id) break;
      useProvidersStore.setState((state) => ({
        providers: state.providers.map((x) => (x.id === p.id ? p : x)),
        error: null,
      }));
      break;
    }
    case "settings_providers_removed": {
      const id: string | undefined = msg.payload?.id;
      if (!id) break;
      useProvidersStore.setState((state) => ({
        providers: state.providers.filter((x) => x.id !== id),
        error: null,
      }));
      break;
    }
    case "settings_providers_error": {
      const reason = String(msg.payload?.reason ?? "");
      const detail = String(msg.payload?.detail ?? "");
      const text = [reason, detail].filter(Boolean).join(": ");
      useProvidersStore.setState({ error: text || "未知错误" });
      break;
    }
    default:
      break;
  }
}

/** 只给测试用的别名。 */
export const __test_dispatch_provider_event = dispatchProviderEvent;
