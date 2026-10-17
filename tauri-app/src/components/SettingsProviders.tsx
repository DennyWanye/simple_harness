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
import { AddProviderModal } from "./AddProviderModal";
import { type ProviderDraft } from "./providerDraft";
import { ConfirmDialog } from "../code-panel/ConfirmDialog";
import type { ControlChannel } from "../ws/ControlChannel";
import { dark } from "../theme/components";
import {
  displayApiKey,
  buildReorderMessage,
  buildToggleEnabledMessage,
  buildRemoveMessage,
  buildListRequestMessage,
  sortProvidersForDisplay,
} from "./providersStore";
import type { Provider, ProviderEventMessage } from "./providersStore";
import "./SettingsProviders.styles.css";

// ---- Component ------------------------------------------------------------

interface SettingsProvidersProps {
  getChannel: () => ControlChannel | null;
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
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: provider.id,
    disabled: false,
  });
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
        <div className="sh-SettingsProviders-1">
          <div className="sh-SettingsProviders-2">
            <span className="sh-SettingsProviders-3">{provider.name}</span>
          </div>
          <div className="sh-SettingsProviders-4">
            {provider.default_model ||
              provider.model ||
              (provider.models && provider.models[0]) ||
              "(no model)"}
            {provider.models && provider.models.length > 1
              ? ` (+${provider.models.length - 1})`
              : ""}
          </div>
          <div className="sh-SettingsProviders-5">{provider.base_url}</div>
          <div className="sh-SettingsProviders-6">API Key: {displayApiKey(provider)}</div>
        </div>
      </div>
      <div style={actionsRow}>
        {provider.models.length > 0 && (
          <label className="sh-SettingsProviders-7">
            默认
            <select
              value={provider.default_model || provider.model || provider.models[0] || ""}
              onChange={(e) => onDefaultModelChange(provider.id, e.target.value)}
              className="sh-SettingsProviders-8"
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
        <label className="sh-SettingsProviders-7">
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
          className="sh-SettingsProviders-9"
          data-testid={`provider-edit-btn-${provider.id}`}
        >
          编辑
        </button>
        <button
          type="button"
          onClick={() => onDelete(provider.id)}
          className="sh-SettingsProviders-10"
          data-testid={`provider-delete-btn-${provider.id}`}
        >
          删除
        </button>
      </div>
    </li>
  );
}
export function SettingsProviders({ getChannel }: SettingsProvidersProps) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Provider | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Provider | null>(null);
  const pendingSaveIdRef = useRef<string | null>(null);
  const [saving, setSaving] = useState(false);
  const observeSaveResult = useCallback((msg: ProviderEventMessage) => {
    const pendingId = pendingSaveIdRef.current;
    if (!pendingId) return;
    if (msg.type === "settings_providers_error") {
      pendingSaveIdRef.current = null;
      setSaving(false);
      return;
    }
    const acknowledgedId = String(msg.payload?.provider?.id || "");
    const list = Array.isArray(msg.payload?.providers) ? msg.payload.providers : [];
    if (
      acknowledgedId === pendingId ||
      list.some((provider: Provider) => provider.id === pendingId)
    ) {
      pendingSaveIdRef.current = null;
      setSaving(false);
      setAddOpen(false);
      setEditTarget(null);
    }
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
  // 服务商事件只走这条订阅（不再另读 lastMessage，同一条消息只处理一次）。
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
      const msg = incoming as unknown as ProviderEventMessage;
      observeSaveResult(msg);
      if (msg.type === "settings_providers_error") {
        setError(String(msg.payload?.detail || msg.payload?.reason || "未知错误"));
        return;
      }
      if (
        msg.type !== "settings_providers_list_response" &&
        msg.type !== "providers_changed" &&
        msg.type !== "settings_providers_reordered"
      ) {
        return;
      }
      setProviders(Array.isArray(msg.payload?.providers) ? msg.payload.providers : []);
      setError(null);
    });
    return () => {
      offState?.();
      offMsg?.();
    };
  }, [getChannel, observeSaveResult]);

  // 2026-08-09: relay 虚拟项已移除 — 列表只来自 backend registry。
  const ordered = useMemo(() => sortProvidersForDisplay(providers), [providers]);
  const ordered_ids = useMemo(() => ordered.map((p) => p.id), [ordered]);

  const send = useCallback(
    (msg: { type: string; payload?: Record<string, unknown> }) => {
      const ch = getChannel();
      if (!ch || ch.state !== "connected") {
        setError("控制通道未连接");
        return false;
      }
      if (!ch.send(msg)) {
        setError("控制通道发送失败，请等待重连后重试");
        return false;
      }
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
      pendingSaveIdRef.current = editing?.id || draft.id;
      setSaving(true);
      setError(null);
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
        if (
          !send({
            type: "settings_providers_update",
            payload: {
              id: editing.id,
              patch,
              expected_incarnation_id: editing.incarnation_id,
              expected_config_revision: editing.config_revision,
            },
          })
        ) {
          pendingSaveIdRef.current = null;
          setSaving(false);
          return;
        }
      } else {
        if (
          !send({
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
          })
        ) {
          pendingSaveIdRef.current = null;
          setSaving(false);
          return;
        }
      }
    },
    [send],
  );

  // ---- Probe models flow (auto-fetch /models from base_url) ----------
  const [probedModels, setProbedModels] = useState<string[]>([]);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const probeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearProbeTimeout = useCallback(() => {
    if (probeTimeoutRef.current) clearTimeout(probeTimeoutRef.current);
    probeTimeoutRef.current = null;
  }, []);

  useEffect(() => clearProbeTimeout, [clearProbeTimeout]);

  // Watch for the backend's probe response on the control channel.
  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    return ch.onMessage?.((incoming) => {
      const msg = incoming as unknown as ProviderEventMessage;
      if (msg.type !== "settings_providers_probe_models_response") return;
      clearProbeTimeout();
      setProbing(false);
      if (msg.payload?.ok) {
        const list: string[] = Array.isArray(msg.payload.models) ? msg.payload.models : [];
        setProbedModels(list);
        setProbeError(null);
      } else {
        setProbedModels([]);
        setProbeError(String(msg.payload?.detail || "未知错误"));
      }
    });
  }, [getChannel, clearProbeTimeout]);

  const handleProbeModels = useCallback(
    (base_url: string, api_key: string, provider_id?: string) => {
      clearProbeTimeout();
      setProbing(true);
      setProbeError(null);
      setProbedModels([]);
      const sent = send({
        type: "settings_providers_probe_models",
        payload: { base_url, api_key, ...(provider_id ? { provider_id } : {}) },
      });
      if (!sent) {
        setProbing(false);
        setProbeError("控制通道未连接，请等待重连后重试");
        return;
      }
      probeTimeoutRef.current = setTimeout(() => {
        setProbing(false);
        setProbeError("获取模型列表超时，请检查网络或 Provider 地址后重试");
        probeTimeoutRef.current = null;
      }, 20_000);
    },
    [clearProbeTimeout, send],
  );

  return (
    <div data-testid="settings-providers">
      <div className="sh-SettingsProviders-11">
        <p className="sh-SettingsProviders-12">
          按优先级排列；拖拽 ⠿ 改顺序，第一个失败时自动落到下一个。
        </p>
        <button
          type="button"
          onClick={() => {
            setError(null);
            setEditTarget(null);
            setAddOpen(true);
          }}
          className="sh-SettingsProviders-13"
          data-testid="provider-add-button"
        >
          + 添加
        </button>
      </div>

      {error && (
        <div role="alert" className="sh-SettingsProviders-14">
          {error}
        </div>
      )}

      {ordered.length === 0 ? (
        <div className="sh-SettingsProviders-15">请添加你的第一个 LLM provider</div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={ordered_ids} strategy={verticalListSortingStrategy}>
            <ul className="sh-SettingsProviders-16" data-testid="provider-list">
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
            pendingSaveIdRef.current = null;
            setSaving(false);
          }}
          onSave={(draft) => handleSaveDraft(draft, editTarget)}
          onProbeModels={handleProbeModels}
          probedModels={probedModels}
          probeError={probeError}
          probing={probing}
          saveError={error}
          saving={saving}
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
