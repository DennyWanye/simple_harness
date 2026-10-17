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
import { useMemo, useState } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";
import type { Provider } from "./providersStore";
import { dark } from "../theme/components";
import { createProviderId, validateProviderDraft, prefillFromProvider } from "./providerDraft";
import type { ProviderDraft } from "./providerDraft";
import "./AddProviderModal.styles.css";

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
  // 渲染期按上一次比对（不在 effect 里 setState）。
  if (useDepsChanged([editing])) {
    setDraft(editing ? prefillFromProvider(editing) : createBlankDraft());
    setSubmitted(false);
    setNewModel("");
  }

  // Merge probed models into the draft (idempotent — only adds new ones).
  // 首次渲染也要并入（与原 effect 挂载即执行一致）。
  if (useDepsChanged([probedModels], { onMount: true })) {
    if (probedModels && probedModels.length > 0)
      setDraft((cur) => {
        const merged = [...cur.models];
        for (const m of probedModels) {
          if (m && !merged.includes(m)) merged.push(m);
        }
        const default_model =
          cur.default_model && merged.includes(cur.default_model)
            ? cur.default_model
            : merged[0] || "";
        return { ...cur, models: merged, default_model };
      });
  }

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
    const default_model = draft.default_model === m ? models[0] || "" : draft.default_model;
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
      className="sh-AddProviderModal-1"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form className="sh-AddProviderModal-2" onSubmit={handleSubmit}>
        <header className="sh-AddProviderModal-3">
          <h3 className="sh-AddProviderModal-4">
            {isEditing ? `编辑 provider: ${editing!.id}` : "添加 LLM Provider"}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="sh-AddProviderModal-5"
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        {/* ID field hidden for new providers (auto-generated UUID) */}
        {isEditing && (
          <label className="sh-AddProviderModal-6">
            <span>id</span>
            <input
              data-testid="provider-id-input"
              disabled={true}
              value={draft.id}
              className="sh-AddProviderModal-7"
            />
          </label>
        )}

        <label className="sh-AddProviderModal-6">
          <span>name</span>
          <input
            data-testid="provider-name-input"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="DeepSeek via My Relay"
            className="sh-AddProviderModal-8"
          />
          {submitted && validation.errors.name && (
            <span className="sh-AddProviderModal-9">{validation.errors.name}</span>
          )}
        </label>

        <label className="sh-AddProviderModal-6">
          <span>base_url</span>
          <input
            data-testid="provider-base-url-input"
            value={draft.base_url}
            onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
            placeholder="https://your-llm-relay.example.com/v1"
            className="sh-AddProviderModal-8"
          />
          {submitted && validation.errors.base_url && (
            <span className="sh-AddProviderModal-9">{validation.errors.base_url}</span>
          )}
        </label>

        <label className="sh-AddProviderModal-6">
          <span>
            api_key {isEditing && <em className="sh-AddProviderModal-10">(留空保留已存的 key)</em>}
          </span>
          <input
            data-testid="provider-api-key-input"
            type="password"
            value={draft.api_key}
            onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}
            placeholder={isEditing ? "(已配置)" : "sk-..."}
            className="sh-AddProviderModal-8"
            autoComplete="off"
          />
          {submitted && validation.errors.api_key && (
            <span className="sh-AddProviderModal-9">{validation.errors.api_key}</span>
          )}
        </label>

        <div className="sh-AddProviderModal-6">
          <div className="sh-AddProviderModal-11">
            <button
              type="button"
              onClick={() => setShowModels((v) => !v)}
              className="sh-AddProviderModal-12"
              aria-expanded={showModels}
              aria-label={showModels ? "收起 models" : "展开 models"}
            >
              {showModels ? "▼" : "▶"}
            </button>
            <span className="sh-AddProviderModal-13">
              models <em className="sh-AddProviderModal-14">(可配置多个；默认 model 用 ◉ 标记)</em>
            </span>
            <button
              type="button"
              onClick={() => {
                if (onProbeModels && canProbe) {
                  onProbeModels(draft.base_url, draft.api_key, editing?.id);
                }
              }}
              disabled={!canProbe || probing}
              className="sh-AddProviderModal-15"
              title="向 base_url/models 拉取支持的模型列表"
              data-testid="provider-probe-models-button"
            >
              {probing ? "获取中…" : "🔍 自动获取"}
            </button>
          </div>
          {showModels && (
            <div className="sh-AddProviderModal-16">
              <div className="sh-AddProviderModal-17">
                {draft.models.length === 0 && (
                  <div className="sh-AddProviderModal-18">
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
                      className="sh-AddProviderModal-19"
                      title={draft.default_model === m ? "默认 model" : "点击设为默认"}
                    >
                      <input
                        type="radio"
                        name="default_model"
                        checked={draft.default_model === m}
                        onChange={() => setDraft({ ...draft, default_model: m })}
                        aria-label={`设 ${m} 为默认`}
                      />
                      <span className="sh-AddProviderModal-20">{m}</span>
                    </label>
                    <button
                      type="button"
                      onClick={() => removeModel(m)}
                      className="sh-AddProviderModal-21"
                      aria-label={`删除 ${m}`}
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
              <div className="sh-AddProviderModal-22">
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
                  className="sh-AddProviderModal-23"
                  data-testid="provider-new-model-input"
                />
                <button
                  type="button"
                  onClick={addModel}
                  disabled={!newModel.trim()}
                  className="sh-AddProviderModal-24"
                  data-testid="provider-add-model-button"
                >
                  + 添加
                </button>
              </div>
              {probeError && (
                <div className="sh-AddProviderModal-9">
                  自动获取失败：
                  {/all connection attempts failed|connection refused|connecterror/i.test(
                    probeError,
                  )
                    ? "连不上这个服务地址，请检查 base_url 是否正确、服务是否在运行。"
                    : probeError}
                </div>
              )}
            </div>
          )}
          {saveError && (
            <span role="alert" className="sh-AddProviderModal-9">
              {saveError}
            </span>
          )}
          {submitted && validation.errors.models && (
            <span className="sh-AddProviderModal-9">{validation.errors.models}</span>
          )}
        </div>

        {/* 2026-08-09：「启用」开关原先只对 relay provider 显示，relay 移除后
            改为编辑既有 provider 时一律可见（新建的默认就是启用）。 */}
        {isEditing && (
          <label className="sh-AddProviderModal-25">
            <input
              type="checkbox"
              checked={draft.enabled ?? true}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
              data-testid="provider-enabled-input"
            />
            <span>启用</span>
          </label>
        )}

        <footer className="sh-AddProviderModal-26">
          <button type="button" onClick={onClose} className="sh-AddProviderModal-27">
            取消
          </button>
          <button
            type="submit"
            disabled={saving}
            data-testid="provider-save-button"
            className="sh-AddProviderModal-28"
          >
            {saving ? "保存中…" : isEditing ? "保存" : "添加"}
          </button>
        </footer>
      </form>
    </div>
  );
}
// ---- inline styles -------------------------------------------------------
