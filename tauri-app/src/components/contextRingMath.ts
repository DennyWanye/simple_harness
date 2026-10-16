// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { ContextUsageSnapshot } from "../stores/sessionsStore";
import { contextDisplayState } from "../context/contextAuthority";

/** Pure helper — % filled of the *practical* window. Exposed for vitest. */
export function ringPercent(snap: ContextUsageSnapshot | null | undefined): number {
  if (!snap || contextDisplayState(snap) !== "measured") return 0;
  return Math.max(0, Math.min(100, (snap.prompt_tokens / snap.effective_ceiling) * 100));
}

/** Pure helper — colour for a given fill %. Exposed for vitest. */
export function ringColor(pct: number): string {
  if (pct >= 95) return "#dc2626";
  if (pct >= 80) return "#ea580c";
  if (pct >= 50) return "#f59e0b";
  return "#10b981";
}
