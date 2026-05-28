// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { NullPetEngine } from '../NullPetEngine'

describe('NullPetEngine', () => {
  let warnSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    warnSpy.mockRestore()
  })

  it('reports backend="null"', () => {
    const eng = new NullPetEngine()
    expect(eng.backend).toBe('null')
  })

  it('returns a stable CoreModelLike from getCoreModel()', () => {
    const eng = new NullPetEngine()
    const m1 = eng.getCoreModel()
    const m2 = eng.getCoreModel()
    expect(m1).not.toBeNull()
    expect(m1).toBe(m2)
  })

  it('getParameterIndex returns -1 for any pet-anim param name', () => {
    const eng = new NullPetEngine()
    const m = eng.getCoreModel()!
    expect(m.getParameterIndex('ParamAngleX')).toBe(-1)
    expect(m.getParameterIndex('ParamMouthOpenY')).toBe(-1)
    expect(m.getParameterIndex('')).toBe(-1)
  })

  it('setParameterValueByIndex never throws even for -1 / out-of-range idx', () => {
    const eng = new NullPetEngine()
    const m = eng.getCoreModel()!
    expect(() => m.setParameterValueByIndex(-1, 0.5)).not.toThrow()
    expect(() => m.setParameterValueByIndex(999, 0.5)).not.toThrow()
    expect(() => m.setParameterValueByIndex(0, NaN)).not.toThrow()
  })

  it('addParameterValueByIndex is exposed (pet-anim fast-path)', () => {
    const eng = new NullPetEngine()
    const m = eng.getCoreModel()!
    expect(typeof m.addParameterValueByIndex).toBe('function')
    expect(() => m.addParameterValueByIndex!(-1, 0.5)).not.toThrow()
  })

  it('playMotion warns once then stays silent', () => {
    const eng = new NullPetEngine()
    eng.playMotion('Idle')
    eng.playMotion('TapBody')
    eng.playMotion('Idle', 2)
    expect(warnSpy).toHaveBeenCalledTimes(1)
    expect(warnSpy.mock.calls[0][0]).toMatch(/\[NullPetEngine\].*null backend/)
  })

  it('setExpression also goes through the warn-once budget', () => {
    const eng = new NullPetEngine()
    eng.setExpression('happy')
    eng.setExpression('sad')
    expect(warnSpy).toHaveBeenCalledTimes(1)
  })

  it('destroy() makes subsequent getCoreModel() return null', () => {
    const eng = new NullPetEngine()
    expect(eng.getCoreModel()).not.toBeNull()
    eng.destroy()
    expect(eng.getCoreModel()).toBeNull()
  })

  it('destroy() is idempotent', () => {
    const eng = new NullPetEngine()
    eng.destroy()
    expect(() => eng.destroy()).not.toThrow()
    expect(eng.getCoreModel()).toBeNull()
  })
})
