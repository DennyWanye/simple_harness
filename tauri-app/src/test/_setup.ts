// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 全局 per-test setup（原 pet-anim/__tests__/_setup.ts，T4 桌宠删除后迁此）。
 *
 * Wipes localStorage between tests so feature-flag and motion-label
 * fixtures don't leak across cases. `try/catch` is defensive: in
 * environments where localStorage is disabled (Safari private mode,
 * sandboxed iframes) the access can throw — we never want a setup
 * step to take the whole suite down.
 */
import { beforeEach } from 'vitest'

// Node ≥23 ships an experimental global `localStorage` whose methods are
// incomplete without `--localstorage-file`; inside vitest workers it can
// shadow jsdom's Storage (first seen on Node 25: `localStorage.clear is
// not a function`). Detect that and swap in a plain in-memory Storage so
// tests behave the same across Node versions.
function localStorageIsBroken(): boolean {
  try {
    const ls = globalThis.localStorage
    if (!ls || typeof ls.clear !== 'function') return true
    ls.setItem('__probe__', '1')
    ls.removeItem('__probe__')
    return false
  } catch {
    return true
  }
}

if (localStorageIsBroken()) {
  const store = new Map<string, string>()
  const memoryStorage: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => {
      store.delete(k)
    },
    setItem: (k: string, v: string) => {
      store.set(k, String(v))
    },
  }
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: memoryStorage,
  })
}

beforeEach(() => {
  try {
    localStorage.clear()
  } catch {
    /* localStorage unavailable — tests must tolerate this anyway */
  }
})
