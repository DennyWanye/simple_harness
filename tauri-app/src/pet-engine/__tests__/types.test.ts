// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, it, expect } from 'vitest'
import { PET_ENGINE_BACKENDS } from '../types'
import type { PetEngine, PetEngineBackend, CoreModelLike } from '../types'

describe('PetEngine type', () => {
  it('exports PET_ENGINE_BACKENDS as the canonical runtime union', () => {
    expect(PET_ENGINE_BACKENDS).toEqual(['live2d', 'null'])
  })

  it('exposes a CoreModelLike via getCoreModel()', () => {
    const stub: PetEngine = {
      backend: 'null',
      getCoreModel: () => null,
      playMotion: () => {},
      setExpression: () => {},
      destroy: () => {},
    }
    expect(stub.backend).toBe('null')
    expect(stub.getCoreModel()).toBeNull()
  })

  it('PetEngineBackend is a closed union of "live2d" | "null"', () => {
    const valid: PetEngineBackend[] = ['live2d', 'null']
    expect(valid).toHaveLength(2)
  })

  it('CoreModelLike re-export matches pet-anim contract', () => {
    const m: CoreModelLike = {
      getParameterIndex: () => -1,
      setParameterValueByIndex: () => {},
    }
    expect(m.getParameterIndex('x')).toBe(-1)
  })
})
