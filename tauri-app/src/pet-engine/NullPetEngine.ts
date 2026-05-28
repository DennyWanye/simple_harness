// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CoreModelLike, PetEngine, PetEngineBackend } from './types'

class NullCoreModel implements CoreModelLike {
  getParameterIndex(_name: string): number {
    return -1
  }
  setParameterValueByIndex(_idx: number, _val: number): void {
    /* no-op */
  }
  addParameterValueByIndex(_idx: number, _val: number): void {
    /* no-op */
  }
}

/**
 * Placeholder engine used during the Live2D-replacement rewrite (Slice 1).
 *
 * Renders nothing on its own — the host Canvas falls through to its
 * Canvas2D fallback character. AnimationOverlay still runs against this
 * engine: parameter writes are accepted but discarded. This lets us
 * validate that pet-anim genuinely only touches CoreModelLike — anything
 * outside pet-anim breaking under VITE_PET_ENGINE=null is a leaky
 * abstraction we want to flush out now, before S3 swaps in the real
 * WebGL2 renderer.
 */
export class NullPetEngine implements PetEngine {
  readonly backend: PetEngineBackend = 'null'
  private model: CoreModelLike | null = new NullCoreModel()
  private warned = false

  getCoreModel(): CoreModelLike | null {
    return this.model
  }

  playMotion(group: string, _index?: number): void {
    this.warnOnce('playMotion', group)
  }

  setExpression(name: string): void {
    this.warnOnce('setExpression', name)
  }

  destroy(): void {
    this.model = null
  }

  private warnOnce(method: string, arg: string): void {
    if (this.warned) return
    this.warned = true
    console.warn(`[NullPetEngine] ${method}(${arg}) — null backend, no animation will play`)
  }
}
