// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 相对时间显示（"14h 前"）。
 *
 * 原本是 MessageStreamPanel.tsx 里的私有 `format_relative`，消息气泡用它。
 * WB-5 给会话列表行补时间显示时出现第二个同语义调用点，遂抽到这里共用，
 * 避免两份实现漂移（消息气泡 "14h 前" 和会话行 "14h 前" 必须是同一套口径）。
 */

/** @param ts 毫秒 epoch。后端给的是**秒**，调用方自己 ×1000。 */
export function formatRelativeMs(ts: number, now: number = Date.now()): string {
  const delta_s = Math.max(0, Math.round((now - ts) / 1000));
  if (delta_s < 60) return `${delta_s}s 前`;
  if (delta_s < 3600) return `${Math.round(delta_s / 60)}m 前`;
  if (delta_s < 86400) return `${Math.round(delta_s / 3600)}h 前`;
  return `${Math.round(delta_s / 86400)}d 前`;
}

/**
 * 秒级 epoch 版（后端 `last_message_at` / `created_at` 都是秒）。
 * 缺失或非法（0 / NaN / 负）返回空串，让调用方整块不渲染而不是显示 "56y 前"。
 */
export function formatRelativeSec(tsSec: number, now: number = Date.now()): string {
  if (!Number.isFinite(tsSec) || tsSec <= 0) return "";
  return formatRelativeMs(tsSec * 1000, now);
}
