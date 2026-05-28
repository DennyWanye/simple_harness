// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CoreModelLike, PetEngine, PetEngineBackend } from './types'

/**
 * Wraps pixi-live2d-display's loaded model into the PetEngine interface.
 *
 * This is the LEGACY backend — S5 will delete this file together with
 * the live2dcubismcore / pixi-live2d-display deps. The caller is
 * responsible for `Live2DModel.from(path)` and passes the resolved
 * model in; this adapter just surfaces the CoreModelLike that pet-anim
 * wants and forwards motion/expression calls.
 *
 * No unit tests on this adapter directly: it's a thin proxy over
 * pixi-live2d-display, which requires PixiJS + cubismcore + a real
 * .moc3 asset to instantiate. Coverage comes from the windows-mcp
 * manual test MR-S1-01 (live2d backend zero-regression).
 */
export class Live2DPetEngineAdapter implements PetEngine {
  readonly backend: PetEngineBackend = 'live2d'

  constructor(private model: any | null) {}

  getCoreModel(): CoreModelLike | null {
    if (!this.model) return null
    const core = this.model.internalModel?.coreModel
    return (core as CoreModelLike) ?? null
  }

  playMotion(group: string, index?: number): void {
    try {
      this.model?.motion?.(group, index, 2)
    } catch (err) {
      console.warn('[Live2DAdapter] playMotion failed:', group, index, err)
    }
  }

  setExpression(name: string): void {
    try {
      this.model?.expression?.(name)
    } catch (err) {
      console.warn('[Live2DAdapter] setExpression failed:', name, err)
    }
  }

  destroy(): void {
    try {
      this.model?.destroy?.()
    } catch {
      /* ignore */
    }
    this.model = null
  }
}
