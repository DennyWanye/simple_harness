// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * One foreground turn's size bound, shared by the composer and the controller.
 *
 * Incident G: an 18 393-byte Chinese message was accepted by the composer and
 * then rejected by the Host under a generic identifier cap, whose only public
 * signal was an opaque protocol code. The bound is now named on both sides and
 * the composer states it in the user's own language before anything is sent.
 *
 * Mirrors `FOREGROUND_TURN_TEXT_MAX_BYTES` in
 * `backend/deskpet/memory/human_memory_service.py`; change both together.
 */
export const PRIMARY_TURN_TEXT_MAX_BYTES = 65_536;

/** Stable Host code for a turn above that bound. */
export const PRIMARY_TURN_TEXT_TOO_LARGE = "human_memory_turn_text_too_large";

const encoder = new TextEncoder();

/** UTF-8 byte length — the unit the Host actually bounds. */
export function turnTextByteLength(text: string): number {
  return encoder.encode(text).length;
}

/**
 * The message to show for an over-long draft, or "" when it is acceptable.
 * Never truncates and never sends: the draft stays the user's to edit.
 */
export function turnTextRejection(text: string): string {
  const bytes = turnTextByteLength(text);
  if (bytes <= PRIMARY_TURN_TEXT_MAX_BYTES) return "";
  return `消息过长，未发送：当前 ${bytes} 字节（约 ${text.length} 字），上限 ${PRIMARY_TURN_TEXT_MAX_BYTES} 字节。请拆分后分条发送；草稿已保留。`;
}
