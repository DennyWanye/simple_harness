// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Pure helpers for the session-list "rename topic" feature. Kept in their own
 * module (no React / ws / tauri imports) so vitest can exercise the rename
 * edge cases without pulling in the heavy MessagePanelRoot module graph.
 */

/** Max length for a user-set topic title (mirrors backend MAX_TITLE_LEN). */
export const MAX_TITLE_LEN = 80;

/** Trim + clamp raw rename input to the canonical stored form. Empty result
 * means "clear the custom title" (the row falls back to the auto preview). */
export function normalizeTopicTitle(raw: string): string {
  return (raw || "").trim().slice(0, MAX_TITLE_LEN);
}

/** Display label for a session row: a non-empty custom title wins, else the
 * auto preview, else the raw session id.
 *
 * 2026-08-09：原先还有一个 `isDefault` 分支给保留会话显示「默认话题」。保留会话
 * 已移除（会话一律用户新建），所有会话取名规则同构。 */
export function topicDisplayLabel(args: {
  title?: string;
  preview?: string;
  session_id: string;
}): string {
  const custom = (args.title || "").trim();
  return custom || args.preview || args.session_id;
}
