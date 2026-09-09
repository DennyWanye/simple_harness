// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S2 Phase 4 — Add / Edit Provider modal.
 *
 * UI is dumb: parent owns the editing target, parent handles save
 * (sending ws). The modal collects + validates the draft client-side
 * before letting parent commit it.
 *
 * v2 (multi-model support): `model: string` → `models: string[]` +
 * `default_model: string`. Adds inline UI for adding/removing models
 * + a "🔍 自动获取" button that asks the backend to probe `<base_url>/models`
 * and merges the result into the draft's models list.
 *
 * Pure helpers exported for vitest — matches the no-DOM testing
 * convention.
 */
import { useEffect, useMemo, useState } from "react";
import { v4 as uuidv4 } from "uuid";

import type { Provider } from "./SettingsProviders";
import { dark } from "../theme/components";
import { tokens } from "../theme/tokens";

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

const KEBAB_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const PROVIDER_ID_MAX_LENGTH = 32;

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
  const models = Array.isArray(p.models) && p.models.length > 0
    ? p.models
    : (p.model ? [p.model] : []);
  const default_model = (p.default_model && models.includes(p.default_model))
    ? p.default_model
    : (models[0] || "");
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

// ---- Component -----------------------------------------------------------

interface AddProviderModalProps {
  editing: Provider | null;
  onClose(): void;
  onSave(draft: ProviderDraft): void;
  /** Send a ws probe request. Parent owns the channel. */
  onProbeModels?(base_url: string, api_key: string, provider_id?: string): void;
  /** Latest probe result from backend (managed by parent). */
  probedModels?: string[];
  /** Backend probe error (if any). */
  probeError?: string | null;
  /** Probe in-flight indicator. */
  probing?: boolean;
  saveError?: string | null;
  saving?: boolean;
}

function createBlankDraft(): ProviderDraft {
  return {
    id: createProviderId(),
    source: "user",
    name: "",
    base_url: "",
    models: [],
    default_model: "",
    enabled: true,
    api_key: "",
  };
}

export function AddProviderModal({
  editing,
  onClose,
  onSave,
  onProbeModels,
  probedModels,
  probeError,
  probing,
  saveError,
  saving = false,
}: AddProviderModalProps) {
  const [draft, setDraft] = useState<ProviderDraft>(() =>
    editing ? prefillFromProvider(editing) : createBlankDraft(),
  );
  const [submitted, setSubmitted] = useState(false);
  const [newModel, setNewModel] = useState("");
  const [showModels, setShowModels] = useState(true);

  // If the editing target changes (rare; UI usually re-mounts), reset draft.
  useEffect(() => {
    setDraft(editing ? prefillFromProvider(editing) : createBlankDraft());
    setSubmitted(false);
    setNewModel("");
  }, [editing]);

  // Merge probed models into the draft (idempotent — only adds new ones).
  useEffect(() => {
    if (!probedModels || probedModels.length === 0) return;
    setDraft((cur) => {
      const merged = [...cur.models];
      for (const m of probedModels) {
        if (m && !merged.includes(m)) merged.push(m);
      }
      const default_model = cur.default_model && merged.includes(cur.default_model)
        ? cur.default_model
        : (merged[0] || "");
      return { ...cur, models: merged, default_model };
    });
  }, [probedModels]);

  const isEditing = editing !== null;
  const validation = useMemo(
    () => validateProviderDraft(draft, { editing: isEditing }),
    [draft, isEditing],
  );

  const addModel = () => {
    const m = newModel.trim();
    if (!m || draft.models.includes(m)) return;
    const models = [...draft.models, m];
    setDraft({
      ...draft,
      models,
      default_model: draft.default_model || m,
    });
    setNewModel("");
  };

  const removeModel = (m: string) => {
    const models = draft.models.filter((x) => x !== m);
    const default_model = draft.default_model === m
      ? (models[0] || "")
      : draft.default_model;
    setDraft({ ...draft, models, default_model });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!validation.ok) return;
    onSave(draft);
  };

  const canProbe = !!onProbeModels && !!draft.base_url.trim();

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isEditing ? "编辑 provider" : "添加 provider"}
      style={overlayStyle}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form style={modalStyle} onSubmit={handleSubmit}>
        <header style={{ display: "flex", justifyContent: "space-between" }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>
            {isEditing ? `编辑 provider: ${editing!.id}` : "添加 LLM Provider"}
          </h3>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              fontSize: 14,
              cursor: "pointer",
            }}
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        {/* ID field hidden for new providers (auto-generated UUID) */}
        {isEditing && (
          <label style={fieldStyle}>
            <span>id</span>
            <input
              data-testid="provider-id-input"
              disabled={true}
              value={draft.id}
              style={{ ...inputStyle, opacity: 0.6 }}
            />
          </label>
        )}

        <label style={fieldStyle}>
          <span>name</span>
          <input
            data-testid="provider-name-input"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="DeepSeek via My Relay"
            style={inputStyle}
          />
          {submitted && validation.errors.name && (
            <span style={errStyle}>{validation.errors.name}</span>
          )}
        </label>

        <label style={fieldStyle}>
          <span>base_url</span>
          <input
            data-testid="provider-base-url-input"
            value={draft.base_url}
            onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
            placeholder="https://your-llm-relay.example.com/v1"
            style={inputStyle}
          />
          {submitted && validation.errors.base_url && (
            <span style={errStyle}>{validation.errors.base_url}</span>
          )}
        </label>

        <label style={fieldStyle}>
          <span>api_key {isEditing && <em style={{ fontSize: 10, color: dark.textMuted }}>(留空保留已存的 key)</em>}</span>
          <input
            data-testid="provider-api-key-input"
            type="password"
            value={draft.api_key}
            onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}
            placeholder={isEditing ? "(已配置)" : "sk-..."}
            style={inputStyle}
            autoComplete="off"
          />
          {submitted && validation.errors.api_key && (
            <span style={errStyle}>{validation.errors.api_key}</span>
          )}
        </label>

        <div style={fieldStyle}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button
              type="button"
              onClick={() => setShowModels((v) => !v)}
              style={{
                background: "transparent",
                border: "none",
                cursor: "pointer",
                padding: 0,
                fontSize: 12,
                color: dark.textMuted,
              }}
              aria-expanded={showModels}
              aria-label={showModels ? "收起 models" : "展开 models"}
            >
              {showModels ? "▼" : "▶"}
            </button>
            <span style={{ flex: 1 }}>
              models <em style={{ color: dark.textMuted, fontSize: 10 }}>(可配置多个；默认 model 用 ◉ 标记)</em>
            </span>
            <button
              type="button"
              onClick={() => {
                if (onProbeModels && canProbe) {
                  onProbeModels(draft.base_url, draft.api_key, editing?.id);
                }
              }}
              disabled={!canProbe || probing}
              style={probeBtn}
              title="向 base_url/models 拉取支持的模型列表"
              data-testid="provider-probe-models-button"
            >
              {probing ? "获取中…" : "🔍 自动获取"}
            </button>
          </div>
          {showModels && (
            <div style={{ display: "grid", gap: 4, marginTop: 4 }}>
            <div
              style={{
                display: "grid",
                gap: 4,
                // Cap height for long lists (100+ models from probe), with
                // its own scroll so the modal's footer (取消/保存) stays
                // visible without endless outer scrolling.
                maxHeight: 220,
                overflowY: "auto",
                paddingRight: 2,
              }}
            >
              {draft.models.length === 0 && (
                <div style={{ fontSize: 11, color: dark.textFaint, padding: "4px 6px" }}>
                  尚未配置 model（手动添加或点 🔍 自动获取）
                </div>
              )}
              {draft.models.map((m) => (
                <div
                  key={m}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "3px 6px",
                    border: `1px solid ${dark.border}`,
                    borderRadius: 4,
                    background: draft.default_model === m ? dark.accentSoft : dark.card,
                  }}
                >
                  <label
                    style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", flex: 1 }}
                    title={draft.default_model === m ? "默认 model" : "点击设为默认"}
                  >
                    <input
                      type="radio"
                      name="default_model"
                      checked={draft.default_model === m}
                      onChange={() => setDraft({ ...draft, default_model: m })}
                      aria-label={`设 ${m} 为默认`}
                    />
                    <span style={{ overflowWrap: "anywhere", fontFamily: "monospace", fontSize: 11 }}>{m}</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => removeModel(m)}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: dark.danger,
                      cursor: "pointer",
                      fontSize: 11,
                    }}
                    aria-label={`删除 ${m}`}
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
              <div style={{ display: "flex", gap: 4 }}>
                <input
                  value={newModel}
                  onChange={(e) => setNewModel(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addModel();
                    }
                  }}
                  placeholder="新 model 名（例：claude-sonnet-4-5）"
                  style={{ ...inputStyle, flex: 1 }}
                  data-testid="provider-new-model-input"
                />
                <button
                  type="button"
                  onClick={addModel}
                  disabled={!newModel.trim()}
                  style={smallAddBtn}
                  data-testid="provider-add-model-button"
                >
                  + 添加
                </button>
              </div>
              {probeError && (
                <div style={errStyle}>自动获取失败: {probeError}</div>
              )}
            </div>
          )}
          {saveError && <span role="alert" style={errStyle}>{saveError}</span>}
          {submitted && validation.errors.models && (
            <span style={errStyle}>{validation.errors.models}</span>
          )}
        </div>

        {/* 2026-08-09：「启用」开关原先只对 relay provider 显示，relay 移除后
            改为编辑既有 provider 时一律可见（新建的默认就是启用）。 */}
        {isEditing && (
          <label style={{ ...fieldStyle, display: "flex", flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={draft.enabled ?? true}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
              data-testid="provider-enabled-input"
            />
            <span>启用</span>
          </label>
        )}

        <footer style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button type="button" onClick={onClose} style={cancelBtn}>
            取消
          </button>
          <button
            type="submit"
            disabled={saving}
            data-testid="provider-save-button"
            style={saveBtn}
          >
            {saving ? "保存中…" : (isEditing ? "保存" : "添加")}
          </button>
        </footer>
      </form>
    </div>
  );
}

// ---- inline styles -------------------------------------------------------

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: dark.scrim,
  display: "grid",
  placeItems: "center",
  padding: 8,
  zIndex: 1100,
};

const modalStyle: React.CSSProperties = {
  background: dark.bg,
  border: `1px solid ${dark.borderStrong}`,
  padding: 16,
  borderRadius: 8,
  width: "min(94vw, 440px)",
  maxHeight: "92vh",
  overflowY: "auto",
  overflowX: "hidden",
  color: dark.text,
  boxShadow: tokens.shadow.overlay,
  display: "grid",
  gap: 8,
};

const fieldStyle: React.CSSProperties = {
  display: "grid",
  gap: 4,
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  padding: "5px 8px",
  borderRadius: 4,
  border: `1px solid ${dark.border}`,
  background: dark.card,
  color: dark.text,
  fontSize: 12,
  fontFamily: "inherit",
  outline: "none",
  minWidth: 0,
};

const errStyle: React.CSSProperties = {
  fontSize: 11,
  color: dark.danger,
};

const saveBtn: React.CSSProperties = {
  padding: "5px 12px",
  borderRadius: 4,
  border: "1px solid transparent",
  background: dark.accent,
  color: "white",
  fontSize: 12,
  cursor: "pointer",
};

const cancelBtn: React.CSSProperties = {
  padding: "5px 12px",
  borderRadius: 4,
  border: `1px solid ${dark.border}`,
  background: dark.card,
  color: dark.text,
  fontSize: 12,
  cursor: "pointer",
};

const smallAddBtn: React.CSSProperties = {
  padding: "3px 10px",
  borderRadius: 4,
  border: `1px solid ${dark.accentBorder}`,
  background: dark.card,
  color: dark.accentText,
  fontSize: 11,
  cursor: "pointer",
};

const probeBtn: React.CSSProperties = {
  padding: "3px 8px",
  borderRadius: 4,
  border: `1px solid ${dark.border}`,
  background: dark.card,
  color: dark.textMuted,
  fontSize: 11,
  cursor: "pointer",
};
