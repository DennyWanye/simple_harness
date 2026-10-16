// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/** Minimum note length before packaging is allowed. */
export const MIN_NOTE_CHARS = 10;

/**
 * Pure: is the feedback note long enough to allow packaging?
 * Trimmed length must be ≥ :data:`MIN_NOTE_CHARS`. Exported for
 * unit-testing without a DOM.
 */
export function isFeedbackNoteValid(note: string): boolean {
  return note.trim().length >= MIN_NOTE_CHARS;
}
