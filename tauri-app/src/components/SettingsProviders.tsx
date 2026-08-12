// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S2 Phase 4 — LLM Provider settings UI (drag-drop reorderable list).
 *
 * Pairs with `LLMProviderRegistry` (backend phase 1) + the
 * `settings_providers_*` ws messages (backend phase 2). The component
 * lives inside `SettingsPanel` and talks to the control WS via
 * `getChannel()` (same pattern as the rest of SettingsPanel).
 *
 * Pure helpers below are exported so vitest can test them without
 * needing a DOM — matches the project's existing test convention
 * (see AutoResumeBanner.test.tsx / SettingsToggle.test.tsx).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import { AddProviderModal, type ProviderDraft } from "./AddProviderModal";
import { ConfirmDialog } from "../code-panel/ConfirmDialog";
import type { ControlChannel } from "../ws/ControlChannel";
import type { IncomingMessage } from "../types/messages";
import { dark } from "../theme/components";

// ---- Domain types ---------------------------------------------------------

export interface Provider {
  id: string;
  /** 2026-08-09：relay 来源已移除，provider 一律用户自建。 */
  source?: "user";
  account_ref?: string;
  name: string;
  base_url: string;
  /** P5-S2 v2: canonical model list (a provider can serve multiple models). */
  models: string[];
  /** P5-S2 v2: which model in `models` is used by default for chain calls. */
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
export function buildReorderMessage(ordered_ids: string[], providers?: Provider[]): {
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

/** Build the ws message frontend sends to remove a provider. */
export function buildRemoveMessage(id: string, provider?: Provider): {
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

interface ProvidersStore {
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
export function dispatchProviderEvent(msg: {
  type: string;
  payload?: any;
}): void {
  switch (msg.type) {
    case "settings_providers_list_response":
    case "providers_changed":
    case "settings_providers_reordered": {
      const list: Provider[] = Array.isArray(msg.payload?.providers)
        ? msg.payload.providers
        : [];
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

/** Test-only alias mirroring AutoResumeBanner's `__test_dispatch` convention. */
export const __test_dispatch_provider_event = dispatchProviderEvent;

// ---- Component ------------------------------------------------------------

interface SettingsProvidersProps {
  getChannel: () => ControlChannel | null;
  lastMessage: IncomingMessage | null;
}

interface SortableRowProps {
  provider: Provider;
  onToggle(id: string, next_enabled: boolean): void;
  onDefaultModelChange(id: string, default_model: string): void;
  onDelete(id: string): void;
  onEdit(provider: Provider): void;
}

function SortableRow({
  provider,
  onToggle,
  onDefaultModelChange,
  onDelete,
  onEdit,
}: SortableRowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: provider.id, disabled: false });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    border: `1px solid ${dark.border}`,
    borderRadius: 4,
    padding: "8px 10px",
    display: "flex",
    flexDirection: "column",
    gap: 6,
    background: provider.enabled ? dark.card : dark.inset,
    fontSize: 12,
    minWidth: 0,
  };
  const headerRow: React.CSSProperties = {
    display: "flex",
    alignItems: "flex-start",
    gap: 8,
    minWidth: 0,
  };
  const actionsRow: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
    justifyContent: "flex-end",
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      data-testid={`provider-row-${provider.id}`}
      aria-label={`provider ${provider.name}`}
    >
      <div style={headerRow}>
        <span
          {...attributes}
          {...listeners}
          aria-label={`拖拽 ${provider.name}`}
          style={{
            cursor: "grab",
            color: dark.textFaint,
            userSelect: "none",
            flexShrink: 0,
            lineHeight: "16px",
          }}
        >
          ⠿
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{provider.name}</span>
          </div>
          <div style={{ color: dark.textMuted, fontSize: 11, overflowWrap: "anywhere" }}>
            {provider.default_model || provider.model || (provider.models && provider.models[0]) || "(no model)"}
            {provider.models && provider.models.length > 1 ? ` (+${provider.models.length - 1})` : ""}
          </div>
          <div style={{ color: dark.textFaint, fontSize: 11, overflowWrap: "anywhere" }}>
            {provider.base_url}
          </div>
          <div style={{ color: dark.textFaint, fontSize: 11 }}>
            API Key: {displayApiKey(provider)}
          </div>
        </div>
      </div>
      <div style={actionsRow}>
        {provider.models.length > 0 && (
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
            默认
            <select
              value={provider.default_model || provider.model || provider.models[0] || ""}
              onChange={(e) => onDefaultModelChange(provider.id, e.target.value)}
              style={selectStyle}
              data-testid={`provider-default-model-select-${provider.id}`}
              aria-label={`默认模型 ${provider.name}`}
            >
              {provider.models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        )}
        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
          <input
            type="checkbox"
            checked={provider.enabled}
            onChange={(e) => onToggle(provider.id, e.target.checked)}
            aria-label={`启用 ${provider.name}`}
          />
          启用
        </label>
        <button
          type="button"
          onClick={() => onEdit(provider)}
          style={{ ...rowBtn }}
          data-testid={`provider-edit-btn-${provider.id}`}
        >
          编辑
        </button>
        <button
          type="button"
          onClick={() => onDelete(provider.id)}
          style={{ ...rowBtn, color: "#b91c1c" }}
          data-testid={`provider-delete-btn-${provider.id}`}
        >
          删除
        </button>
      </div>
    </li>
  );
}

export function SettingsProviders({
  getChannel,
  lastMessage,
}: SettingsProvidersProps) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Provider | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Provider | null>(null);
  // Re-entrancy guard independent of React state (button-disable already
  // prevents double-clicks; this also blocks programmatic / racy re-entry).
  // Auto-clear timers per provider; cleared on unmount so we never setState
  // after the component is gone.
  const resetTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const mountedRef = useRef(true);
  useEffect(() => {
    return () => {
      mountedRef.current = false;
      Object.values(resetTimersRef.current).forEach((t) => clearTimeout(t));
    };
  }, []);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // Hydrate from backend. WBUI-DEF-S08-01: a mount-only request left the
  // list permanently empty whenever the socket was not yet connected at
  // mount (and the `lastMessage` single slot can drop the reply when other
  // control traffic lands in the same React batch). Request on mount AND on
  // every (re)connect, and read the reply off the channel directly.
  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const request = () => ch.send(buildListRequestMessage());
    if (ch.state === "connected") request();
    // Older/stubbed channels may not expose the subscription API; degrade to
    // the mount-only request rather than throwing inside the effect.
    const offState = ch.onStateChange?.((s) => {
      if (s === "connected") request();
    });
    const offMsg = ch.onMessage?.((incoming) => {
      const msg = incoming as unknown as { type?: string; payload?: any };
      if (
        msg.type !== "settings_providers_list_response" &&
        msg.type !== "providers_changed" &&
        msg.type !== "settings_providers_reordered"
      ) {
        return;
      }
      if (!Array.isArray(msg.payload?.providers)) return;
      setProviders(msg.payload.providers as Provider[]);
      setError(null);
    });
    return () => {
      offState?.();
      offMsg?.();
    };
  }, [getChannel]);


  // Listen for inbound provider events on the shared lastMessage prop.
  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage as { type: string; payload?: any };
    switch (msg.type) {
      case "settings_providers_list_response":
      case "providers_changed":
      case "settings_providers_reordered": {
        const list: Provider[] = Array.isArray(msg.payload?.providers)
          ? msg.payload.providers
          : [];
        setProviders(list);
        setError(null);
        break;
      }
      case "settings_providers_error": {
        setError(String(msg.payload?.detail || msg.payload?.reason || "未知错误"));
        break;
      }
      default:
        break;
    }
  }, [lastMessage]);

  // 2026-08-09: relay 虚拟项已移除 — 列表只来自 backend registry。
  const ordered = useMemo(
    () => sortProvidersForDisplay(providers),
    [providers],
  );
  const ordered_ids = useMemo(() => ordered.map((p) => p.id), [ordered]);

  const send = useCallback(
    (msg: { type: string; payload?: Record<string, unknown> }) => {
      const ch = getChannel();
      if (!ch || ch.state !== "connected") {
        setError("控制通道未连接");
        return false;
      }
      ch.send(msg);
      return true;
    },
    [getChannel],
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const old_idx = ordered_ids.indexOf(String(active.id));
      const new_idx = ordered_ids.indexOf(String(over.id));
      if (old_idx < 0 || new_idx < 0) return;
      const next = arrayMove(ordered_ids, old_idx, new_idx);
      send(buildReorderMessage(next, providers));
    },
    [ordered_ids, providers, send],
  );

  const handleToggle = useCallback(
    (id: string, next_enabled: boolean) => {
      send(
        buildToggleEnabledMessage(
          id,
          next_enabled,
          providers.find((provider) => provider.id === id),
        ),
      );
    },
    [providers, send],
  );

  const handleDefaultModelChange = useCallback(
    (id: string, default_model: string) => {
      const provider = providers.find((item) => item.id === id);
      send({
        type: "settings_providers_update",
        payload: {
          id,
          patch: { default_model },
          expected_incarnation_id: provider?.incarnation_id,
          expected_config_revision: provider?.config_revision,
        },
      });
    },
    [providers, send],
  );

  const handleDelete = useCallback(
    (id: string) => {
      const provider = providers.find((item) => item.id === id);
      if (provider) setDeleteTarget(provider);
    },
    [providers],
  );

  const confirmDelete = useCallback(() => {
    if (!deleteTarget) return;
    send(buildRemoveMessage(deleteTarget.id, deleteTarget));
    setDeleteTarget(null);
  }, [deleteTarget, send]);


  const handleSaveDraft = useCallback(
    (draft: ProviderDraft, editing: Provider | null) => {
      const models = draft.models.map((m) => m.trim()).filter(Boolean);
      const default_model = draft.default_model.trim() || models[0] || "";
      if (editing) {
        // 2026-08-09: 所有 provider 一视同仁 —— baseUrl / apiKey / 默认模型 / 启用 全可改。
        const patch: Record<string, unknown> = {
          name: draft.name,
          base_url: draft.base_url,
          models: draft.models,
          default_model: draft.default_model,
        };
        if (draft.api_key && draft.api_key.trim().length > 0) {
          patch.api_key = draft.api_key.trim();
        }
        if (typeof draft.enabled === "boolean") {
          patch.enabled = draft.enabled;
        }
        send({
          type: "settings_providers_update",
          payload: {
            id: editing.id,
            patch,
            expected_incarnation_id: editing.incarnation_id,
            expected_config_revision: editing.config_revision,
          },
        });
      } else {
        send({
          type: "settings_providers_add",
          payload: {
            id: draft.id,
            name: draft.name,
            base_url: draft.base_url,
            models,
            default_model,
            api_key: draft.api_key,
            enabled: true,
          },
        });
      }
      setAddOpen(false);
      setEditTarget(null);
    },
    [send],
  );

  // ---- Probe models flow (auto-fetch /models from base_url) ----------
  const [probedModels, setProbedModels] = useState<string[]>([]);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);

  // Watch for the backend's probe response on the shared ws inbox.
  useEffect(() => {
    if (!lastMessage) return;
    const msg = lastMessage as { type: string; payload?: any };
    if (msg.type !== "settings_providers_probe_models_response") return;
    setProbing(false);
    if (msg.payload?.ok) {
      const list: string[] = Array.isArray(msg.payload.models) ? msg.payload.models : [];
      setProbedModels(list);
      setProbeError(null);
    } else {
      setProbedModels([]);
      setProbeError(String(msg.payload?.detail || "未知错误"));
    }
  }, [lastMessage]);

  const handleProbeModels = useCallback(
    (base_url: string, api_key: string) => {
      setProbing(true);
      setProbeError(null);
      setProbedModels([]);
      send({
        type: "settings_providers_probe_models",
        payload: { base_url, api_key },
      });
    },
    [send],
  );

  return (
    <div data-testid="settings-providers">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 6,
        }}
      >
        <p style={{ fontSize: 11, color: dark.textMuted, margin: 0 }}>
          按优先级排列；拖拽 ⠿ 改顺序，第一个失败时自动落到下一个。
        </p>
        <button
          type="button"
          onClick={() => {
            setEditTarget(null);
            setAddOpen(true);
          }}
          style={addBtnStyle}
          data-testid="provider-add-button"
        >
          + 添加
        </button>
      </div>

      {error && (
        <div role="alert" style={errorStyle}>
          {error}
        </div>
      )}

      {ordered.length === 0 ? (
        <div style={emptyStyle}>请添加你的第一个 LLM provider</div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext items={ordered_ids} strategy={verticalListSortingStrategy}>
            <ul style={listStyle} data-testid="provider-list">
              {ordered.map((p) => (
                <SortableRow
                  key={p.id}
                  provider={p}
                  onToggle={handleToggle}
                  onDefaultModelChange={handleDefaultModelChange}
                  onDelete={handleDelete}
                  onEdit={(prov) => {
                    setEditTarget(prov);
                    setAddOpen(true);
                  }}
                />
              ))}
            </ul>
          </SortableContext>
        </DndContext>
      )}

      {addOpen && (
        <AddProviderModal
          editing={editTarget}
          onClose={() => {
            setAddOpen(false);
            setEditTarget(null);
            setProbedModels([]);
            setProbeError(null);
          }}
          onSave={(draft) => handleSaveDraft(draft, editTarget)}
          onProbeModels={handleProbeModels}
          probedModels={probedModels}
          probeError={probeError}
          probing={probing}
        />
      )}
      {deleteTarget && (
        <ConfirmDialog
          title="删除 Provider"
          message={
            <>
              确定要删除 Provider <strong>{deleteTarget.name}</strong>（{deleteTarget.id}）吗？
              此操作只删除该 Provider，不会修改其它 Provider 的顺序、模型或凭据。
            </>
          }
          confirm_label="删除"
          variant="danger"
          onConfirm={confirmDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

// ---- inline styles --------------------------------------------------------

const listStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "grid",
  gap: 6,
};

const rowBtn: React.CSSProperties = {
  padding: "3px 8px",
  borderRadius: 4,
  border: `1px solid ${dark.border}`,
  background: dark.card,
  color: dark.text,
  fontSize: 11,
  cursor: "pointer",
};

const selectStyle: React.CSSProperties = {
  maxWidth: 180,
  padding: "2px 6px",
  borderRadius: 4,
  border: `1px solid ${dark.border}`,
  background: dark.card,
  color: dark.text,
  fontSize: 11,
};

const addBtnStyle: React.CSSProperties = {
  padding: "4px 10px",
  borderRadius: 4,
  border: "1px solid #2563eb",
  background: "#2563eb",
  color: "white",
  fontSize: 12,
  cursor: "pointer",
};

const errorStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "5px 8px",
  background: "rgba(127,29,29,0.18)",
  color: "#fca5a5",
  border: "1px solid rgba(248,113,113,0.28)",
  borderRadius: 4,
  marginBottom: 6,
};

const emptyStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "12px 8px",
  background: dark.inset,
  color: dark.textMuted,
  borderRadius: 4,
  textAlign: "center",
};
