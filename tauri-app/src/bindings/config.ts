// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P2-1: push cloud config changes to a running backend.
 *
 * 走 Rust IPC 命令代理到后端，不直接 `fetch("http://127.0.0.1:8100/config/cloud")`：
 * 正式版 webview 的源是 `https://tauri.localhost`，浏览器拒绝 https→http 请求（混合内容）。
 * SHARED_SECRET 由 Rust 持有，前端不用碰。
 */

import { invoke } from "@tauri-apps/api/core";

export interface CloudConfigUpdate {
  base_url: string;
  model: string;
  api_key?: string; // empty/absent = keep current
  strategy?: string; // empty/absent = keep current
  /** WI-R2: false → api_key applied to the live provider but NOT
   *  persisted to llm_runtime.json (relay rotating `tsk_xxx` key).
   *  Absent/true → existing behaviour (key persisted). */
  persist_key?: boolean;
}

export interface CloudConfigResult {
  ok: boolean;
  cloud_configured: boolean;
  base_url: string;
  model: string;
  has_api_key: boolean;
  strategy: string;
}

/**
 * Push cloud config to the running backend via Rust IPC. Throws on
 * Rust-side error (the underlying HTTP error message comes through as
 * the rejection string).
 */
export async function updateCloudConfig(
  _secret: string, // kept for legacy callers; ignored — Rust knows the secret
  update: CloudConfigUpdate,
): Promise<CloudConfigResult> {
  return invoke<CloudConfigResult>("update_cloud_config", { update });
}
