// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPetEngine, NullPetEngine, resolveBackendFromEnv } from '../index'

describe('createPetEngine factory', () => {
  it('returns NullPetEngine for backend="null"', () => {
    const eng = createPetEngine('null')
    expect(eng).toBeInstanceOf(NullPetEngine)
    expect(eng.backend).toBe('null')
  })

  it('throws if asked for live2d without supplying the loaded model', () => {
    expect(() => createPetEngine('live2d')).toThrow(/live2d backend requires/i)
  })

  it('wraps a provided live2d model when backend="live2d"', () => {
    const fakeCore = {
      getParameterIndex: () => 0,
      setParameterValueByIndex: () => {},
    }
    const fakeModel = { internalModel: { coreModel: fakeCore } }
    const eng = createPetEngine('live2d', { live2dModel: fakeModel })
    expect(eng.backend).toBe('live2d')
    expect(eng.getCoreModel()).toBe(fakeCore)
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

  it('defaults to live2d when env unset', () => {
    expect(resolveBackendFromEnv(undefined)).toBe('live2d')
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('accepts "live2d" explicitly', () => {
    expect(resolveBackendFromEnv('live2d')).toBe('live2d')
  })

  it('accepts "null"', () => {
    expect(resolveBackendFromEnv('null')).toBe('null')
  })

  it('falls back to live2d for unknown values + warns', () => {
    expect(resolveBackendFromEnv('deskpet-mesh' /* not yet S3 */)).toBe('live2d')
    expect(warnSpy).toHaveBeenCalledTimes(1)
    expect(warnSpy.mock.calls[0][0]).toMatch(/unknown VITE_PET_ENGINE/)
  })
})
