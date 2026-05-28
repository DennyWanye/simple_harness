// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CoreModelLike, PetEngine, PetEngineBackend } from './types'

/**
 * Sprite CoreModel — accepts pet-anim parameter writes but does not
 * propagate them to a renderer (the Canvas2D character in Live2DCanvas
 * is currently self-driven). Future S2/S3 work will replace this with
 * a model that exposes parameters the Canvas2D / WebGL2 renderer reads.
 *
 * For pet-anim correctness it MUST: return -1 from getParameterIndex
 * for any name (so pet-anim writes are no-ops), and never throw.
 */
class SpriteCoreModel implements CoreModelLike {
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
 * The sprite backend — production rendering path post-S5.
 *
 * Live2DCanvas.startCanvas2D() paints a 100%-original Canvas2D
 * character (purple cat); this engine provides the CoreModelLike
 * surface pet-anim wants. Parameter writes are accepted but currently
 * have no visual effect — the Canvas2D character runs its own self-
 * driven blink/breath/mouth animation. A future slice can wire
 * setParameterValueByIndex into a parameter dict the renderer reads.
 *
 * Zero copyright risk: every byte of this file + the Canvas2D drawing
 * code is original work; no Live2D Cubism SDK, no Hiyori assets, no
 * proprietary mesh format.
 */
export class SpritePetEngine implements PetEngine {
  readonly backend: PetEngineBackend = 'sprite'
  private model: CoreModelLike | null = new SpriteCoreModel()

  getCoreModel(): CoreModelLike | null {
    return this.model
  }

  playMotion(_group: string, _index?: number): void {
    /* TODO S4: motion keyframe player — for now, the Canvas2D
       character runs a continuous idle loop and ignores group calls. */
  }

  setExpression(_name: string): void {
    /* TODO S4: expression sprite-swap — for now, the Canvas2D
       character has a single neutral expression. */
  }

  destroy(): void {
    this.model = null
  }
}
