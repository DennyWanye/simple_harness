// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CoreModelLike } from '../pet-anim'

export type { CoreModelLike }

export type PetEngineBackend = 'live2d' | 'null'

/**
 * Canonical runtime-accessible union of known backends. Kept here (not in
 * index.ts) so tests can import a value from this module — purely-typed
 * modules get stripped by esbuild and any RED test against them would pass
 * spuriously.
 */
export const PET_ENGINE_BACKENDS: readonly PetEngineBackend[] = ['live2d', 'null'] as const

/**
 * Abstraction over the underlying character runtime. pet-anim's
 * AnimationOverlay only ever talks to CoreModelLike, so swapping
 * engines is a one-line factory change.
 *
 * S1 introduces two backends:
 *  - 'live2d' — wraps pixi-live2d-display (legacy, copyright-laden)
 *  - 'null'   — placeholder sprite for the rewrite period
 *
 * S3 adds 'deskpet-mesh' (WebGL2 self-rendered). S5 deletes 'live2d'.
 */
export interface PetEngine {
  readonly backend: PetEngineBackend
  /**
   * Returns the runtime model the AnimationOverlay should drive each
   * frame, or `null` if not yet loaded. Same object MUST be returned
   * across calls for a stable reference.
   */
  getCoreModel(): CoreModelLike | null
  /** Trigger a named motion group; backends MAY no-op. */
  playMotion(group: string, index?: number): void
  /** Apply a named expression; backends MAY no-op. */
  setExpression(name: string): void
  /** Tear down all GPU/CPU resources; safe to call multiple times. */
  destroy(): void
}
