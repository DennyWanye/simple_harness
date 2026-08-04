// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Product-level voice availability.
 *
 * The legacy audio path is intentionally off.  Realtime will replace this
 * boundary later; keeping the decision in one module prevents either window
 * from accidentally reconnecting the old WebSocket or requesting the mic.
 */
export const VOICE_INPUT_ENABLED = false;
export const VOICE_UNAVAILABLE_MESSAGE =
  "语音功能暂时关闭，后续将接入 Realtime";
