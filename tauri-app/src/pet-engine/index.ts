// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { PetEngine, PetEngineBackend, CoreModelLike } from './types'
import { PET_ENGINE_BACKENDS } from './types'
import { NullPetEngine } from './NullPetEngine'
import { Live2DPetEngineAdapter } from './Live2DPetEngineAdapter'

export type { PetEngine, PetEngineBackend, CoreModelLike }
export { PET_ENGINE_BACKENDS, NullPetEngine, Live2DPetEngineAdapter }

export interface PetEngineFactoryOpts {
  /** Required when backend === 'live2d': the already-loaded pixi-live2d-display model. */
  live2dModel?: any
}

export function createPetEngine(
  backend: PetEngineBackend,
  opts: PetEngineFactoryOpts = {},
): PetEngine {
  switch (backend) {
    case 'null':
      return new NullPetEngine()
    case 'live2d':
      if (!opts.live2dModel) {
        throw new Error('[pet-engine] live2d backend requires opts.live2dModel')
      }
      return new Live2DPetEngineAdapter(opts.live2dModel)
    default: {
      const _exhaustive: never = backend
      throw new Error(`[pet-engine] unknown backend: ${String(_exhaustive)}`)
    }
  }
}

const KNOWN: ReadonlySet<string> = new Set<PetEngineBackend>(PET_ENGINE_BACKENDS)

/**
 * Resolve VITE_PET_ENGINE (or any string source) into a known backend.
 * Defaults to 'live2d' (zero-regression). Unknown values warn-and-fallback.
 */
export function resolveBackendFromEnv(raw: string | undefined): PetEngineBackend {
  if (!raw) return 'live2d'
  if (KNOWN.has(raw)) return raw as PetEngineBackend
  console.warn(`[pet-engine] unknown VITE_PET_ENGINE="${raw}", falling back to live2d`)
  return 'live2d'
}
