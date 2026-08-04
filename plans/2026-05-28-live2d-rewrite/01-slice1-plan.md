# Slice 1: 引擎抽象层 + 双后端 - 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 `PetEngine` 抽象层, 把 cubismcore 隔离到一个可替换的 adapter 后面; 引入 `NullPetEngine` 占位实现; feature flag 选择后端。完成后 pet-anim 上层零改动, 重写后续 slice 时可独立替换渲染后端。

**Architecture:**
- `tauri-app/src/pet-engine/` 新模块: 接口 + Null 实现 + Live2D adapter + 工厂
- `Live2DCanvas.tsx` 用工厂选后端, 不再直接 import `pixi-live2d-display`
- `import.meta.env.VITE_PET_ENGINE` (`live2d` | `null`) 默认 `live2d`, 保持当前行为零回归

**Tech Stack:** TypeScript / React 18 / Vite / Vitest / PixiJS 7

---

## File Structure

**Create:**
- `tauri-app/src/pet-engine/types.ts` — `PetEngine` 接口 + 重导出 `CoreModelLike`
- `tauri-app/src/pet-engine/NullPetEngine.ts` — Null 后端 (占位 sprite)
- `tauri-app/src/pet-engine/Live2DPetEngineAdapter.ts` — 包装 pixi-live2d-display
- `tauri-app/src/pet-engine/index.ts` — `createPetEngine(backend)` 工厂
- `tauri-app/src/pet-engine/__tests__/types.test.ts`
- `tauri-app/src/pet-engine/__tests__/NullPetEngine.test.ts`
- `tauri-app/src/pet-engine/__tests__/factory.test.ts`

**Modify:**
- `tauri-app/src/components/Live2DCanvas.tsx` — 移除直接 import `pixi-live2d-display/cubism4`, 改用 `createPetEngine`
- `tauri-app/.env.example` — 增加 `VITE_PET_ENGINE=live2d` 说明

**Do NOT touch (red lines):**
- `tauri-app/src/pet-anim/**` — 整个 AnimationOverlay 子系统零改动 (验证抽象成功的标尺)
- 所有 manual-results-*, plans/2026-05-2[34]-* 历史归档

---

## Task 1: PetEngine 接口定义

**Files:**
- Create: `tauri-app/src/pet-engine/types.ts`
- Test: `tauri-app/src/pet-engine/__tests__/types.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// tauri-app/src/pet-engine/__tests__/types.test.ts
import { describe, it, expect } from 'vitest'
import type { PetEngine, PetEngineBackend, CoreModelLike } from '../types'

describe('PetEngine type', () => {
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/types.test.ts`
Expected: FAIL — `Cannot find module '../types'`

- [ ] **Step 3: Write minimal implementation**

```ts
// tauri-app/src/pet-engine/types.ts
import type { CoreModelLike } from '../pet-anim'

export type { CoreModelLike }

export type PetEngineBackend = 'live2d' | 'null'

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/types.test.ts`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add tauri-app/src/pet-engine/types.ts tauri-app/src/pet-engine/__tests__/types.test.ts
git commit -m "feat(pet-engine): S1 task1 — PetEngine interface + CoreModelLike re-export"
```

---

## Task 2: NullPetEngine 实现

**Files:**
- Create: `tauri-app/src/pet-engine/NullPetEngine.ts`
- Test: `tauri-app/src/pet-engine/__tests__/NullPetEngine.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// tauri-app/src/pet-engine/__tests__/NullPetEngine.test.ts
import { describe, it, expect, vi } from 'vitest'
import { NullPetEngine } from '../NullPetEngine'

describe('NullPetEngine', () => {
  it('reports backend="null"', () => {
    const eng = new NullPetEngine()
    expect(eng.backend).toBe('null')
  })

  it('returns a stable CoreModelLike from getCoreModel()', () => {
    const eng = new NullPetEngine()
    const m1 = eng.getCoreModel()
    const m2 = eng.getCoreModel()
    expect(m1).not.toBeNull()
    expect(m1).toBe(m2) // same reference
  })

  it('getParameterIndex returns -1 for unknown params (pet-anim safe fallback)', () => {
    const eng = new NullPetEngine()
    const m = eng.getCoreModel()!
    expect(m.getParameterIndex('ParamAngleX')).toBe(-1)
    expect(m.getParameterIndex('ParamMouthOpenY')).toBe(-1)
  })

  it('setParameterValueByIndex never throws even for -1 / out-of-range idx', () => {
    const eng = new NullPetEngine()
    const m = eng.getCoreModel()!
    expect(() => m.setParameterValueByIndex(-1, 0.5)).not.toThrow()
    expect(() => m.setParameterValueByIndex(999, 0.5)).not.toThrow()
  })

  it('addParameterValueByIndex is exposed (pet-anim fast-path)', () => {
    const eng = new NullPetEngine()
    const m = eng.getCoreModel()!
    expect(typeof m.addParameterValueByIndex).toBe('function')
    expect(() => m.addParameterValueByIndex!(-1, 0.5)).not.toThrow()
  })

  it('playMotion / setExpression are no-ops (warn-once)', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const eng = new NullPetEngine()
    eng.playMotion('Idle')
    eng.setExpression('happy')
    // Just assert it didn't crash; warn budget is implementation detail.
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('destroy() makes subsequent getCoreModel() return null', () => {
    const eng = new NullPetEngine()
    expect(eng.getCoreModel()).not.toBeNull()
    eng.destroy()
    expect(eng.getCoreModel()).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/NullPetEngine.test.ts`
Expected: FAIL — `Cannot find module '../NullPetEngine'`

- [ ] **Step 3: Write minimal implementation**

```ts
// tauri-app/src/pet-engine/NullPetEngine.ts
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
 * Placeholder engine used during the Live2D-replacement rewrite.
 *
 * Renders nothing on its own — the host Canvas paints a static
 * "DeskPet (preview)" sprite while the new mesh renderer (S3) is
 * built. AnimationOverlay still runs against this engine: parameter
 * writes are accepted but discarded. This lets us validate that
 * pet-anim genuinely only touches CoreModelLike — if anything outside
 * pet-anim breaks under VITE_PET_ENGINE=null, that's a leaky
 * abstraction we want to flush out now.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/NullPetEngine.test.ts`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add tauri-app/src/pet-engine/NullPetEngine.ts tauri-app/src/pet-engine/__tests__/NullPetEngine.test.ts
git commit -m "feat(pet-engine): S1 task2 — NullPetEngine placeholder backend"
```

---

## Task 3: Live2DPetEngineAdapter

**Files:**
- Create: `tauri-app/src/pet-engine/Live2DPetEngineAdapter.ts`
- Test: 无独立单测 (依赖 PixiJS + cubismcore, 在 Live2DCanvas 集成测试覆盖)

- [ ] **Step 1: Write implementation**

```ts
// tauri-app/src/pet-engine/Live2DPetEngineAdapter.ts
import type { CoreModelLike, PetEngine, PetEngineBackend } from './types'

/**
 * Wraps pixi-live2d-display's loaded model into the PetEngine
 * interface. This is the LEGACY backend — S5 will delete this file
 * together with the live2dcubismcore / pixi-live2d-display deps.
 *
 * Caller is responsible for `Live2DModel.from(path)` and passing the
 * resolved model; this adapter just exposes the CoreModelLike that
 * pet-anim wants and forwards motion/expression calls.
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
```

- [ ] **Step 2: Verify typecheck**

Run: `cd tauri-app && npx tsc --noEmit`
Expected: PASS (no errors in pet-engine/)

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src/pet-engine/Live2DPetEngineAdapter.ts
git commit -m "feat(pet-engine): S1 task3 — Live2DPetEngineAdapter wraps legacy backend"
```

---

## Task 4: 工厂 + index.ts

**Files:**
- Create: `tauri-app/src/pet-engine/index.ts`
- Test: `tauri-app/src/pet-engine/__tests__/factory.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// tauri-app/src/pet-engine/__tests__/factory.test.ts
import { describe, it, expect } from 'vitest'
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
    const fakeModel = { internalModel: { coreModel: { getParameterIndex: () => 0, setParameterValueByIndex: () => {} } } }
    const eng = createPetEngine('live2d', { live2dModel: fakeModel })
    expect(eng.backend).toBe('live2d')
    expect(eng.getCoreModel()).toBe(fakeModel.internalModel.coreModel)
  })
})

describe('resolveBackendFromEnv', () => {
  it('defaults to live2d when env unset', () => {
    expect(resolveBackendFromEnv(undefined)).toBe('live2d')
  })
  it('accepts "null"', () => {
    expect(resolveBackendFromEnv('null')).toBe('null')
  })
  it('falls back to live2d for unknown values + warns', () => {
    expect(resolveBackendFromEnv('deskpet-mesh' /* not yet S3 */)).toBe('live2d')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/factory.test.ts`
Expected: FAIL — `Cannot find module '../index'`

- [ ] **Step 3: Write minimal implementation**

```ts
// tauri-app/src/pet-engine/index.ts
import type { PetEngine, PetEngineBackend, CoreModelLike } from './types'
import { NullPetEngine } from './NullPetEngine'
import { Live2DPetEngineAdapter } from './Live2DPetEngineAdapter'

export type { PetEngine, PetEngineBackend, CoreModelLike }
export { NullPetEngine, Live2DPetEngineAdapter }

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

const KNOWN_BACKENDS: ReadonlySet<string> = new Set<PetEngineBackend>(['live2d', 'null'])

/**
 * Resolve VITE_PET_ENGINE (or any string source) into a known backend.
 * Defaults to 'live2d' (zero-regression). Unknown values warn-and-fallback.
 */
export function resolveBackendFromEnv(raw: string | undefined): PetEngineBackend {
  if (!raw) return 'live2d'
  if (KNOWN_BACKENDS.has(raw)) return raw as PetEngineBackend
  console.warn(`[pet-engine] unknown VITE_PET_ENGINE="${raw}", falling back to live2d`)
  return 'live2d'
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/factory.test.ts`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tauri-app/src/pet-engine/index.ts tauri-app/src/pet-engine/__tests__/factory.test.ts
git commit -m "feat(pet-engine): S1 task4 — createPetEngine factory + env resolver"
```

---

## Task 5: Live2DCanvas 切换到工厂

**Files:**
- Modify: `tauri-app/src/components/Live2DCanvas.tsx`

- [ ] **Step 1: Read current init flow**

Re-read `tauri-app/src/components/Live2DCanvas.tsx:497-665` (the `init()` function). Note the points where:
- `Live2DModel.from(modelPath)` loads the model (~line 542)
- `model.internalModel.coreModel` is reached by `overlayRef.current.applyTo` (~line 617)
- `modelRef.current = model` is set (line 560)
- `overlayRef.current?.setMotionPlayer((g,i) => model.motion(g,i,2))` is wired (line 564)

We want to keep all that, just funnel it through `createPetEngine`.

- [ ] **Step 2: Add engine ref and resolve backend**

Near the other refs in Live2DCanvas (around line 199-216), add:

```tsx
import { createPetEngine, resolveBackendFromEnv, type PetEngine } from '../pet-engine'

// ... inside the component, near modelRef:
const engineRef = useRef<PetEngine | null>(null)
const backend = resolveBackendFromEnv(
  (import.meta as any).env?.VITE_PET_ENGINE as string | undefined,
)
```

- [ ] **Step 3: Branch the init() function**

In `useEffect` "Main init" (around line 490), wrap the Live2D init in a backend check:

```tsx
async function init() {
  if (backend === 'null') {
    // Null backend path — no PixiJS / no Live2D, just wire pet-anim
    // against the NullPetEngine so AnimationOverlay still runs and the
    // canvas falls through to its Canvas2D fallback character.
    const engine = createPetEngine('null')
    engineRef.current = engine
    const coreModel = engine.getCoreModel()
    if (coreModel && overlayRef.current) {
      overlayRef.current.setMotionPlayer((g, i) => engine.playMotion(g, i))
    }
    modeRef.current = 'canvas2d'
    startCanvas2D()
    return
  }
  try {
    // ... existing PixiJS + Live2DModel.from(...) code unchanged ...
    // After model loads, wrap it:
    const engine = createPetEngine('live2d', { live2dModel: model })
    engineRef.current = engine
    // overlay.setMotionPlayer becomes:
    overlayRef.current?.setMotionPlayer((group, idx) => engine.playMotion(group, idx))
    // (rest of init unchanged)
  } catch (err) {
    // ... existing fallback unchanged ...
  }
}
```

- [ ] **Step 4: Update destroy path**

In `cleanupRef.current` (around line 777), call engine.destroy() before pixi destroy:

```tsx
cleanupRef.current = () => {
  destroyed = true
  cancelAnimationFrame(rafId)
  modelRef.current = null
  engineRef.current?.destroy()
  engineRef.current = null
  overlayRef.current?.dispose()
  overlayRef.current = null
  // ... rest unchanged ...
}
```

- [ ] **Step 5: typecheck + vitest pet-anim regression**

Run:
```bash
cd tauri-app
npx tsc --noEmit
npx vitest run src/pet-anim/__tests__/
```

Expected: typecheck PASS (zero new errors); pet-anim tests all PASS (zero regressions — proves abstraction is leak-free).

- [ ] **Step 6: Commit**

```bash
git add tauri-app/src/components/Live2DCanvas.tsx
git commit -m "feat(pet-engine): S1 task5 — Live2DCanvas drives backend via createPetEngine"
```

---

## Task 6: `.env.example` + 文档

**Files:**
- Modify: `tauri-app/.env.example` (create if missing)
- Modify: `.claude/worktrees/live2d-rewrite/plans/2026-05-28-live2d-rewrite/00-PRD.md` (mark S1 ✅)

- [ ] **Step 1: Document env flag**

Append to `tauri-app/.env.example`:

```
# Pet rendering backend selector (S1 — Live2D rewrite)
#   live2d (default) — legacy pixi-live2d-display path
#   null             — placeholder backend (Canvas2D fallback character + stub CoreModel)
# Future: deskpet-mesh (S3 — self-rendered WebGL2)
VITE_PET_ENGINE=live2d
```

- [ ] **Step 2: Update PRD slice table**

Edit row "S1: 引擎抽象层 + 双后端" — change "本 session" column from `✅ 本 session` to `✅ DONE 2026-05-28`.

- [ ] **Step 3: Commit**

```bash
git add tauri-app/.env.example plans/2026-05-28-live2d-rewrite/00-PRD.md
git commit -m "docs(pet-engine): S1 task6 — VITE_PET_ENGINE env flag + PRD status"
```

---

## Task 7: 集成验收 (windows-mcp 手工测试钩子)

延伸至独立文档 [`02-manual-test.md`](02-manual-test.md) — 共 4 个 windows-mcp case (MR-S1-01 ~ MR-S1-04)。本任务的工作是:

- [ ] **Step 1: 启动 backend (worktree 隔离端口)**

```powershell
cd G:\projects\deskpet\.claude\worktrees\live2d-rewrite
powershell -File scripts/dev-worktree.ps1 -BackendPort 8400 -VitePort 5473
```

Expected: backend on :8400, vite on :5473, Tauri 窗口出现桌宠主界面 (Hiyori 渲染正常)

- [ ] **Step 2: 用 VITE_PET_ENGINE=null 跑第二次**

按 Ctrl+C 停 dev → 用环境变量重启:

```powershell
$env:VITE_PET_ENGINE = "null"
powershell -File scripts/dev-worktree.ps1 -BackendPort 8400 -VitePort 5473
```

Expected: Canvas2D 紫猫 fallback 角色显示 + 控制台 `[NullPetEngine] playMotion(Idle) — null backend, no animation will play` 警告各一次

- [ ] **Step 3: 跑 02-manual-test.md 全部 4 case**

按 02-manual-test.md 规定的 windows-mcp 协议: 真坐标点击 + 截图存盘 + log 证据。

- [ ] **Step 4: Final commit**

```bash
git add plans/2026-05-28-live2d-rewrite/manual-results-2026-05-28/
git commit -m "test(pet-engine): S1 task7 — windows-mcp manual test results (4/4 PASS)"
```

---

## Acceptance Checklist (S1 DONE 条件)

- [ ] 7 个新 commit 在 `live2d-rewrite` 分支
- [ ] `npx tsc --noEmit` 在 tauri-app 内零错误
- [ ] `npx vitest run` 全绿, 含 pet-engine 新增 16 个 case + pet-anim 既有 case 零回归
- [ ] `VITE_PET_ENGINE=live2d` (默认) dev 启动 → Hiyori 正常 (zero-regression 锚)
- [ ] `VITE_PET_ENGINE=null` dev 启动 → Canvas2D 紫猫 fallback + 单次 warn
- [ ] 4 个 windows-mcp 手工 case 全 PASS (截图+log 入 `manual-results-2026-05-28/`)
- [ ] PRD 的 Slice 1 行 marked ✅ DONE
