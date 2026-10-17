// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { BACKEND_PORT } from "../backendPort";
import { type SlashCommand } from "./SlashDropdown";

// 输入历史 — module-scope，跨 InputBar 实例共享 (max 50 entries)
export const _slashInputHistory: string[] = [];

export const HISTORY_MAX = 50;

// 输入框与唯一主操作按钮严格等高。
export function pushHistory(entry: string) {
  if (!entry.startsWith("/")) return;
  if (_slashInputHistory[_slashInputHistory.length - 1] === entry) return;
  _slashInputHistory.push(entry);
  while (_slashInputHistory.length > HISTORY_MAX) _slashInputHistory.shift();
}

// commands 缓存 (页面级；输入一个新的 "/" 时强制从本机全局 catalog 刷新)
// 2026-06-26 修复"输入 /g 无候选"：原实现把**失败/空**结果也缓存进
// _cachedCommands/_cachedCommandsPromise → boot 早期后端没起来 fetch 空一次后，
// 模块级缓存永久为空、再也不重试。改为**只缓存非空成功结果**，空/失败时返回空但
// 不污染缓存 → 下次（首次输入 "/"）可重拉。
export let _cachedCommands: SlashCommand[] | null = null;

export async function fetchCommands(forceRefresh = false): Promise<SlashCommand[]> {
  if (!forceRefresh && _cachedCommands !== null && _cachedCommands.length > 0)
    return _cachedCommands;
  try {
    // backend 绝对 URL，复用 backendPort.ts 单一源.
    // 相对路径在 Tauri WebView2 (tauri://) 或 vite dev 跨 5473→8400 都失效；
    // 必须显式 http://127.0.0.1:${BACKEND_PORT}/api/... 走 CORS.
    const resp = await fetch(`http://127.0.0.1:${BACKEND_PORT}/api/commands/help`);
    if (!resp.ok) return [];
    const data = await resp.json();
    const out: SlashCommand[] = Array.isArray(data.commands) ? data.commands : [];
    if (out.length > 0) _cachedCommands = out;
    return out;
  } catch {
    return [];
  }
}

export function filterCommands(all: SlashCommand[], q: string): SlashCommand[] {
  if (!q) return all;
  const lower = q.toLowerCase();
  // 排序：prefix-match 优先，substring-match 次之
  const prefix = all.filter((c) => c.name.toLowerCase().startsWith(lower));
  const substr = all.filter(
    (c) => !c.name.toLowerCase().startsWith(lower) && c.name.toLowerCase().includes(lower),
  );
  return [...prefix, ...substr];
}

// Exports for test
export const _testing = {
  pushHistory,
  getHistory: () => [..._slashInputHistory],
  clearHistory: () => {
    _slashInputHistory.length = 0;
  },
  resetCache: () => {
    _cachedCommands = null;
  },
  fetchCommands,
  filterCommands,
  HISTORY_MAX,
};
