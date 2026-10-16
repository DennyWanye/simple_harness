// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { v4 as uuidv4 } from "uuid";
import type { Provider } from "./providersStore";

export interface ProviderDraft {
  id: string;
  /** 2026-08-09：relay 来源已移除，provider 一律用户自建。 */
  source?: "user";
  account_ref?: string;
  name: string;
  base_url: string;
  models: string[];
  default_model: string;
  enabled?: boolean;
  /** Plaintext from the input field. Empty string means "don't touch
   * the existing keychain entry" when editing. */
  api_key: string;
}

// ---- Pure validation helpers ---------------------------------------------
export const KEBAB_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export const PROVIDER_ID_MAX_LENGTH = 32;

/**
 * Generate an opaque provider id that satisfies the backend registry contract:
 * kebab-case and no longer than 32 characters. A raw UUID is 36 characters and
 * therefore cannot be sent directly.
 */
export function createProviderId(rawUuid: string = uuidv4()): string {
  const compact = rawUuid.toLowerCase().replace(/[^a-z0-9]/g, "");
  return `provider-${compact.slice(0, 23)}`;
}

export interface ValidationResult {
  ok: boolean;
  /** Field-keyed error messages; empty when ok=true. */
  errors: Partial<Record<keyof ProviderDraft, string>>;
}

/**
 * Validate a draft client-side. `editing=true` (i.e. provider already
 * exists) skips id format check and allows empty api_key (means "leave
 * keychain alone"). When adding, api_key is required. IDs are auto-generated,
 * but still validated here so frontend/backend contract drift fails locally.
 */
export function validateProviderDraft(
  draft: ProviderDraft,
  opts: { editing: boolean },
): ValidationResult {
  const errors: ValidationResult["errors"] = {};
  if (!opts.editing) {
    const id = draft.id.trim();
    if (!id) {
      errors.id = "id 不能为空";
    } else if (!KEBAB_RE.test(id) || id.length > PROVIDER_ID_MAX_LENGTH) {
      errors.id = "id 必须是 kebab-case，且不超过 32 个字符";
    }
  }
  if (!draft.name || !draft.name.trim()) {
    errors.name = "name 不能为空";
  }
  if (!draft.base_url || !draft.base_url.trim()) {
    errors.base_url = "base_url 不能为空";
  } else if (!/^https?:\/\//.test(draft.base_url.trim())) {
    errors.base_url = "base_url 必须 http:// 或 https:// 开头";
  }
  if (!draft.models || draft.models.length === 0) {
    errors.models = "至少配置一个 model";
  }
  if (!opts.editing) {
    if (!draft.api_key || !draft.api_key.trim()) {
      errors.api_key = "新 provider 必须填 api_key";
    }
  }
  return { ok: Object.keys(errors).length === 0, errors };
}

/**
 * Pre-fill an edit modal from an existing provider. api_key is
 * deliberately blanked: the backend only ever returned `********`,
 * and forcing the user to retype if they want to change it means
 * we never accidentally re-save the redaction sentinel as a key.
 */
export function prefillFromProvider(p: Provider): ProviderDraft {
  const models =
    Array.isArray(p.models) && p.models.length > 0 ? p.models : p.model ? [p.model] : [];
  const default_model =
    p.default_model && models.includes(p.default_model) ? p.default_model : models[0] || "";
  return {
    id: p.id,
    source: p.source,
    account_ref: p.account_ref,
    name: p.name,
    base_url: p.base_url,
    models,
    default_model,
    enabled: p.enabled,
    api_key: "",
  };
}

/**
 * Build the outbound `settings_providers_add` ws message from a draft.
 * Pure function so vitest can verify the shape.
 */
export function buildAddProviderMessage(draft: ProviderDraft): {
  type: "settings_providers_add";
  payload: {
    id: string;
    name: string;
    base_url: string;
    models: string[];
    default_model: string;
    api_key: string;
    enabled: boolean;
  };
} {
  return {
    type: "settings_providers_add",
    payload: {
      id: draft.id.trim(),
      name: draft.name.trim(),
      base_url: draft.base_url.trim(),
      models: draft.models.map((m) => m.trim()).filter(Boolean),
      default_model: draft.default_model.trim(),
      api_key: draft.api_key,
      enabled: true,
    },
  };
}

/**
 * Build the outbound `settings_providers_update` ws message from a draft.
 * Empty `api_key` is excluded from the patch so the backend keeps the
 * existing keychain entry.
 */
export function buildUpdateProviderMessage(
  id: string,
  draft: ProviderDraft,
): {
  type: "settings_providers_update";
  payload: { id: string; patch: Record<string, unknown> };
} {
  const patch: Record<string, unknown> = {
    name: draft.name.trim(),
    base_url: draft.base_url.trim(),
    models: draft.models.map((m) => m.trim()).filter(Boolean),
    default_model: draft.default_model.trim(),
  };
  if (draft.api_key && draft.api_key.trim().length > 0) {
    patch.api_key = draft.api_key.trim();
  }
  return {
    type: "settings_providers_update",
    payload: { id, patch },
  };
}

/**
 * Build the outbound `settings_providers_probe_models` ws message. The
 * backend GETs `<base_url>/models` and replies with
 * `settings_providers_probe_models_response`.
 */
export function buildProbeModelsMessage(
  base_url: string,
  api_key: string,
  provider_id?: string,
): {
  type: "settings_providers_probe_models";
  payload: { base_url: string; api_key: string; provider_id?: string };
} {
  return {
    type: "settings_providers_probe_models",
    payload: {
      base_url: base_url.trim(),
      api_key,
      ...(provider_id ? { provider_id } : {}),
    },
  };
}
