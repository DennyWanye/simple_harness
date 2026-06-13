// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPetEngine, SpritePetEngine, resolveBackendFromEnv } from '../index'

describe('createPetEngine factory', () => {
  it('returns SpritePetEngine for backend="sprite"', () => {
    const eng = createPetEngine('sprite')
    expect(eng).toBeInstanceOf(SpritePetEngine)
    expect(eng.backend).toBe('sprite')
  })

  it('produces a usable CoreModelLike out of the box', () => {
    const eng = createPetEngine('sprite')
    const m = eng.getCoreModel()
    expect(m).not.toBeNull()
    expect(m!.getParameterIndex('ParamAngleX')).toBe(-1)
  })
})

describe('resolveBackendFromEnv', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    warnSpy.mockRestore()
  })

  it('defaults to sprite when env unset', () => {
    expect(resolveBackendFromEnv(undefined)).toBe('sprite')
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('accepts "sprite" explicitly', () => {
    expect(resolveBackendFromEnv('sprite')).toBe('sprite')
  })

  it('falls back to sprite for legacy "live2d" or unknown values + warns', () => {
    expect(resolveBackendFromEnv('live2d' /* removed in S5 */)).toBe('sprite')
    expect(resolveBackendFromEnv('null' /* old S1 name */)).toBe('sprite')
    expect(resolveBackendFromEnv('mesh' /* future S3 */)).toBe('sprite')
    expect(warnSpy).toHaveBeenCalledTimes(3)
  })
})
