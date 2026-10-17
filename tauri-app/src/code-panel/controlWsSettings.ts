// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ControlWireMessage } from "./controlWsTypes";
import type { SessionsStore } from "../stores/sessionTypes";
import { type ProviderEntry, useProvidersStore } from "./providersStore";
import { type ProviderEventMessage, dispatchProviderEvent } from "../components/providersStore";
import { useSessionModelsStore } from "./sessionModelsStore";

/** 控制通道消息处理：服务商与模型设置。由 controlWs 的处理函数表按消息类型分发（9G 从 dispatch 的 switch 拆出，分支体原样）。 */
export function onSettingsProvidersListResponse(
  msg: ControlWireMessage,
  _store: SessionsStore,
  _sid: string,
): void {
  // Initial provider list (frontend asks on panel mount). Same shape as
  // the broadcast — populate the store without binding reconciliation
  // (no UI state to reconcile yet on first load).
  const incoming = Array.isArray(msg.payload?.providers) ? msg.payload.providers : [];
  useProvidersStore.getState().set_providers(incoming as ProviderEntry[]);
  // Mirror into Phase 4 settings store.
  dispatchProviderEvent(msg as ProviderEventMessage);
}

// P5-S2 Phase 4 — individual provider mutation events route to
// SettingsProviders' internal store only (no per-session reconciliation
// needed — the upstream `providers_changed` broadcast handles that).
export function onSettingsProvidersReordered(
  msg: ControlWireMessage,
  _store: SessionsStore,
  _sid: string,
): void {
  dispatchProviderEvent(msg as ProviderEventMessage);
}

export function onSessionProviderBinding(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  // Ack from backend for a session_set_provider request. Mirror the
  // authoritative provider_id + preferred_model back into the store.
  // session_provider_binding is also the cold-start/history hydration
  // path, so a persisted Kimi selection cannot visually fall back to the
  // provider default after a restart.
  const p = msg.payload || {};
  const target = p.session_id || sid;
  store.ensure(target);
  store.upsert(target, {
    provider_id: p.provider_id === undefined ? null : p.provider_id,
    preferred_model: p.preferred_model === undefined ? null : p.preferred_model,
    // Backend's set_provider path preserves + echoes model_params.
    ...(p.model_params !== undefined ? { model_params: p.model_params } : {}),
    provider_incarnation_id: p.provider_incarnation_id ?? null,
    provider_config_revision: p.provider_config_revision ?? null,
    binding_epoch: Number(p.binding_epoch ?? 0),
  });
}

export function onModelsListResponse(
  msg: ControlWireMessage,
  _store: SessionsStore,
  _sid: string,
): void {
  // Live model catalog + per-model capabilities.
  const p = msg.payload || {};
  useSessionModelsStore
    .getState()
    .set_catalog(
      Array.isArray(p.models) ? p.models : [],
      typeof p.source === "string" ? p.source : "none",
      typeof p.default_model === "string" ? p.default_model : "",
    );
}

export function onModelContextSetAck(
  msg: ControlWireMessage,
  _store: SessionsStore,
  _sid: string,
): void {
  // 上下文覆盖保存 ack(p4_ipc 完整版)。catalog 刷新由 modal 保存时
  // 紧随的 models_list 请求完成,这里只记失败。
  const p = msg.payload || {};
  if (!p.ok) {
    console.warn("[ws] model_context_set rejected:", p.reason);
  }
}

export function onSessionModelSet(
  msg: ControlWireMessage,
  store: SessionsStore,
  sid: string,
): void {
  // Ack from backend for session_set_model. Same merge as above —
  // backend echoes provider_id + preferred_model + model_params so we
  // keep all three in sync even if the user only changed the model.
  const p = msg.payload || {};
  const target = p.session_id || sid;
  store.ensure(target);
  store.upsert(target, {
    provider_id: p.provider_id === undefined ? null : p.provider_id,
    preferred_model: p.preferred_model === undefined ? null : p.preferred_model,
    // code-session-model-params S2: dict ⇒ active params; null ⇒
    // binding cleared. undefined (legacy backend) ⇒ leave untouched.
    ...(p.model_params !== undefined ? { model_params: p.model_params } : {}),
    provider_incarnation_id: p.provider_incarnation_id ?? null,
    provider_config_revision: p.provider_config_revision ?? null,
    binding_epoch: Number(p.binding_epoch ?? 0),
  });
}
