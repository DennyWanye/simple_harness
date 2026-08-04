// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useMemo } from "react";

import { useSessionsStore } from "./sessionsStore";

/**
 * Subscribe to the stable map snapshot first, then derive values in React.
 * Returning `Object.values(...)` directly from a Zustand selector creates a
 * new snapshot on every read and loops under React 19/useSyncExternalStore.
 */
export function useCompanionProvisionalValues(): string[] {
  const streams = useSessionsStore((state) =>
    state.companion_provisional_streams
  );
  return useMemo(() => Object.values(streams), [streams]);
}
