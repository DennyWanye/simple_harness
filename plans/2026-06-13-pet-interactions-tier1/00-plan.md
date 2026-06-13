# 桌宠互动 Tier-1（整体变换 + 覆盖 UI）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已经写好但视觉断链的 pet-anim 互动（点击 / 拖拽 / 努力工作）接到 sprite 立绘渲染上，实现可见的整体变换反馈 + 覆盖式 UI（星星特效、思考气泡）。

**Architecture:** pet-anim 的 `AnimationOverlay` 已经在每次交互时更新内部状态并能通过 `applyTo(coreModel, t)` 写出 Cubism 参数（`ParamAngleZ`/`ParamBodyAngleZ`/`ParamBustY`/`ParamBodyAngleX` 等）。Tier-1 做三件事：(1) 把 `SpriteCoreModel` 从空壳改成真参数字典，让写入可被读回；(2) 在 sprite 渲染循环 `draw()` 里每帧调 `applyTo` 然后用纯函数 `deriveTransform()` 把这些参数合成成对**整张立绘的整体变换**（绕脚底旋转/缩放/位移）；(3) 叠加两个覆盖层 UI——点击星星特效 + 努力工作思考气泡，并补上 agent 工作期间持续的 working 状态信号。**不碰部件级变形**（眨眼/嘴型/换表情需立绘分层，属 Tier-2，不在本计划）。

**Tech Stack:** TypeScript / React 18 / Canvas2D / Vitest / 现有 pet-anim `AnimationOverlay`。

**分支策略:** 在主 worktree（`G:\projects\deskpet`）切 `feat/pet-interactions-tier1` 分支开发（master 已 push beta，不直接改）。完成后合并。

---

## 设计决策（参数 → 整体变换映射）

pet-anim 写的关键参数 → 立绘整体变换（绕**脚底中心**为 pivot，模拟"站着晃"而非绕中心飘）：

| Cubism 参数 | 来源互动 | 映射到立绘变换 |
|---|---|---|
| `ParamAngleZ`（头倾，度） | 点击/hover transient、dizzy、edge 吸附 | rotate 合成 |
| `ParamBodyAngleZ`（身倾，度） | 拖拽 wobble、spring-back | rotate 合成 |
| `ParamBodyAngleX`（身侧，度，±18） | cursor lean、倾身 | offsetX 水平位移 |
| `ParamBustY`（胸 squash，±1） | 拖拽 squash/stretch、long-press | scaleY/scaleX 挤压 |

**合成公式**（`deriveTransform`）：
- `rotateDeg = clamp(AngleZ + BodyAngleZ, -25, 25)`
- `offsetX = clamp(BodyAngleX, -30, 30) * 0.5`（度 → px）
- `scaleY = 1 + clamp(BustY, -1, 1) * 0.12`；`scaleX = 1 - clamp(BustY, -1, 1) * 0.06`（体积守恒近似）
- `offsetY = 0`（Tier-1 弹跳由 rotate transient + 星星特效表达，不引入新 bounce 参数）

**不映射的参数**（立绘做不了部件变形，Tier-1 忽略，留给 Tier-2）：`ParamEyeLOpen/ROpen`（眨眼）、`ParamMouthOpenY/Form`（嘴型）、`ParamBrowLY/RY`（眉）、`ParamEyeBallX/Y`（眼球）、`ParamHairFront`（发丝）、`ParamCheek`（脸红）。

**覆盖层 UI**（与立绘变换正交，独立 DOM/canvas）：
- 点击 → 星星粒子特效（在点击坐标爆 5 个星星，600ms 淡出上飘）
- 努力工作 → 思考气泡（角色头顶 💭 + 三点跳动），working 状态期间常驻

**working 状态信号**（断点 B5）：App 在 `tool_use_event` 时置 working=true，`chat_v2_final`/`chat_v2_error`/所有工具结束时置 false——让 agent 执行任务全程有视觉。

---

## File Structure

**Create:**
- `tauri-app/src/components/petTransform.ts` — `deriveTransform(coreModel)` 纯函数 + `PARAM_NAMES` 常量
- `tauri-app/src/components/petTransform.test.ts` — deriveTransform 单测
- `tauri-app/src/components/PetTapBurst.tsx` — 点击星星粒子特效组件
- `tauri-app/src/components/PetWorkingBubble.tsx` — 努力工作思考气泡组件
- `tauri-app/src/pet-engine/__tests__/SpriteCoreModel.test.ts` — 参数字典单测

**Modify:**
- `tauri-app/src/pet-engine/SpritePetEngine.ts` — `SpriteCoreModel` 空壳 → 真参数字典（实现全 4 方法）
- `tauri-app/src/components/petCharacter.ts` — `CharacterFrame` 加 transform 字段 + `drawSpriteCharacter` 应用变换
- `tauri-app/src/components/Live2DCanvas.tsx` — `draw()` 接 `applyTo` + `deriveTransform` + 挂 PetTapBurst；hit-zone onClick 触发星星
- `tauri-app/src/App.tsx` — working 状态信号（tool_use_event 维持）+ 渲染 PetWorkingBubble

**Do NOT touch:** `pet-anim/**`（互动逻辑层已完整，零改动——这是 Tier-1 成功的标尺）。

---

## Task 1: SpriteCoreModel 改成真参数字典

**Files:**
- Modify: `tauri-app/src/pet-engine/SpritePetEngine.ts`
- Test: `tauri-app/src/pet-engine/__tests__/SpriteCoreModel.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// tauri-app/src/pet-engine/__tests__/SpriteCoreModel.test.ts
// SPDX-License-Identifier: BUSL-1.1
import { describe, it, expect } from 'vitest'
import { SpritePetEngine } from '../SpritePetEngine'

describe('SpriteCoreModel as real param dict', () => {
  it('assigns a stable index per param name', () => {
    const m = new SpritePetEngine().getCoreModel()!
    const a = m.getParameterIndex('ParamAngleZ')
    const b = m.getParameterIndex('ParamAngleZ')
    const c = m.getParameterIndex('ParamBodyAngleZ')
    expect(a).toBeGreaterThanOrEqual(0)
    expect(a).toBe(b)        // 同名稳定
    expect(c).not.toBe(a)    // 异名不同
  })

  it('set then get round-trips the value', () => {
    const m = new SpritePetEngine().getCoreModel()!
    const i = m.getParameterIndex('ParamAngleZ')
    m.setParameterValueByIndex(i, 12.5)
    expect(m.getParameterValueByIndex(i)).toBe(12.5)
  })

  it('add accumulates onto current value', () => {
    const m = new SpritePetEngine().getCoreModel()!
    const i = m.getParameterIndex('ParamBustY')
    m.setParameterValueByIndex(i, 0.2)
    m.addParameterValueByIndex!(i, 0.3)
    expect(m.getParameterValueByIndex(i)).toBeCloseTo(0.5)
  })

  it('getParameterValueByIndex returns 0 for an untouched / unknown index', () => {
    const m = new SpritePetEngine().getCoreModel()!
    expect(m.getParameterValueByIndex(999)).toBe(0)
  })

  it('never throws on out-of-range / NaN writes', () => {
    const m = new SpritePetEngine().getCoreModel()!
    expect(() => m.setParameterValueByIndex(-1, 0.5)).not.toThrow()
    expect(() => m.addParameterValueByIndex!(999, NaN)).not.toThrow()
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/SpriteCoreModel.test.ts`
Expected: FAIL（`getParameterValueByIndex is not a function` / round-trip 拿不到值，因为现在是 no-op）

- [ ] **Step 3: 实现真参数字典**

替换 `tauri-app/src/pet-engine/SpritePetEngine.ts` 的 `SpriteCoreModel` 类：

```ts
/**
 * Sprite CoreModel — a real parameter dictionary (Tier-1 interactions).
 *
 * pet-anim's AnimationOverlay.applyTo() writes Cubism params here every
 * frame; the sprite renderer reads them back via getParameterValueByIndex
 * and turns the relevant ones into a whole-figure transform
 * (see petTransform.deriveTransform). Stable name→index mapping; writes
 * never throw. Zero copyright — plain Map, no Live2D SDK.
 */
class SpriteCoreModel implements CoreModelLike {
  private nameToIdx = new Map<string, number>()
  private values: number[] = []

  getParameterIndex(name: string): number {
    let idx = this.nameToIdx.get(name)
    if (idx === undefined) {
      idx = this.values.length
      this.nameToIdx.set(name, idx)
      this.values.push(0)
    }
    return idx
  }

  setParameterValueByIndex(idx: number, val: number): void {
    if (idx < 0 || !Number.isFinite(val)) return
    this.values[idx] = val
  }

  addParameterValueByIndex(idx: number, val: number): void {
    if (idx < 0 || !Number.isFinite(val)) return
    this.values[idx] = (this.values[idx] ?? 0) + val
  }

  getParameterValueByIndex(idx: number): number {
    return this.values[idx] ?? 0
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd tauri-app && npx vitest run src/pet-engine/__tests__/SpriteCoreModel.test.ts`
Expected: PASS（5 tests）

- [ ] **Step 5: 回归 — 既有 pet-engine + pet-anim 测试不破**

Run: `cd tauri-app && npx vitest run src/pet-engine src/pet-anim && npx tsc --noEmit`
Expected: 全 PASS + tsc 0 errors（这也修了 SpriteCoreModel 之前缺 `getParameterValueByIndex` 的潜在 tsc 问题）

- [ ] **Step 6: Commit**

```bash
git add tauri-app/src/pet-engine/SpritePetEngine.ts tauri-app/src/pet-engine/__tests__/SpriteCoreModel.test.ts
git commit -m "feat(pet-tier1): SpriteCoreModel 改真参数字典 — pet-anim 写入可读回"
```

---

## Task 2: deriveTransform 参数→整体变换纯函数

**Files:**
- Create: `tauri-app/src/components/petTransform.ts`
- Test: `tauri-app/src/components/petTransform.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// tauri-app/src/components/petTransform.test.ts
// SPDX-License-Identifier: BUSL-1.1
import { describe, it, expect } from 'vitest'
import { deriveTransform, type PetTransform } from './petTransform'
import type { CoreModelLike } from '../pet-engine'

// 极简 fake CoreModel：name→value
function fakeModel(params: Record<string, number>): CoreModelLike {
  const names = Object.keys(params)
  return {
    getParameterIndex: (n: string) => names.indexOf(n),
    setParameterValueByIndex: () => {},
    addParameterValueByIndex: () => {},
    getParameterValueByIndex: (i: number) => params[names[i]] ?? 0,
  }
}

describe('deriveTransform', () => {
  it('neutral model → identity transform', () => {
    const tf = deriveTransform(fakeModel({}))
    expect(tf).toEqual<PetTransform>({ rotateDeg: 0, offsetX: 0, offsetY: 0, scaleX: 1, scaleY: 1 })
  })

  it('AngleZ + BodyAngleZ 合成 rotate', () => {
    const tf = deriveTransform(fakeModel({ ParamAngleZ: 5, ParamBodyAngleZ: 8 }))
    expect(tf.rotateDeg).toBeCloseTo(13)
  })

  it('rotate 被 clamp 到 ±25', () => {
    const tf = deriveTransform(fakeModel({ ParamAngleZ: 40, ParamBodyAngleZ: 40 }))
    expect(tf.rotateDeg).toBe(25)
  })

  it('BodyAngleX → offsetX (度*0.5)', () => {
    const tf = deriveTransform(fakeModel({ ParamBodyAngleX: 18 }))
    expect(tf.offsetX).toBeCloseTo(9)
  })

  it('BustY>0 拉伸: scaleY>1, scaleX<1 (体积守恒近似)', () => {
    const tf = deriveTransform(fakeModel({ ParamBustY: 1 }))
    expect(tf.scaleY).toBeCloseTo(1.12)
    expect(tf.scaleX).toBeCloseTo(0.94)
  })

  it('BustY<0 挤压: scaleY<1, scaleX>1', () => {
    const tf = deriveTransform(fakeModel({ ParamBustY: -1 }))
    expect(tf.scaleY).toBeCloseTo(0.88)
    expect(tf.scaleX).toBeCloseTo(1.06)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd tauri-app && npx vitest run src/components/petTransform.test.ts`
Expected: FAIL（`Cannot find module './petTransform'`）

- [ ] **Step 3: 实现 deriveTransform**

```ts
// tauri-app/src/components/petTransform.ts
// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import type { CoreModelLike } from '../pet-engine'

/** Whole-figure transform applied to the sprite portrait (pivot = foot center). */
export interface PetTransform {
  rotateDeg: number
  offsetX: number
  offsetY: number
  scaleX: number
  scaleY: number
}

const clamp = (v: number, lo: number, hi: number): number =>
  v < lo ? lo : v > hi ? hi : v

function read(model: CoreModelLike, name: string): number {
  const idx = model.getParameterIndex(name)
  if (idx < 0) return 0
  const v = model.getParameterValueByIndex(idx)
  return Number.isFinite(v) ? v : 0
}

/**
 * Map pet-anim's per-part Cubism params into one whole-figure transform.
 * Tier-1: rotate (head+body tilt), offsetX (body lean), squash (bust Y).
 * Part-level params (eyes/mouth/brows/hair) are intentionally ignored —
 * a single static portrait can't deform them (that's Tier-2).
 */
export function deriveTransform(model: CoreModelLike): PetTransform {
  const angleZ = read(model, 'ParamAngleZ')
  const bodyAngleZ = read(model, 'ParamBodyAngleZ')
  const bodyAngleX = read(model, 'ParamBodyAngleX')
  const bustY = clamp(read(model, 'ParamBustY'), -1, 1)
  return {
    rotateDeg: clamp(angleZ + bodyAngleZ, -25, 25),
    offsetX: clamp(bodyAngleX, -30, 30) * 0.5,
    offsetY: 0,
    scaleY: 1 + bustY * 0.12,
    scaleX: 1 - bustY * 0.06,
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd tauri-app && npx vitest run src/components/petTransform.test.ts`
Expected: PASS（6 tests）

- [ ] **Step 5: Commit**

```bash
git add tauri-app/src/components/petTransform.ts tauri-app/src/components/petTransform.test.ts
git commit -m "feat(pet-tier1): deriveTransform — Cubism 参数合成立绘整体变换"
```

---

## Task 3: CharacterFrame 扩展 + drawSpriteCharacter 应用变换

**Files:**
- Modify: `tauri-app/src/components/petCharacter.ts`

- [ ] **Step 1: 扩展 CharacterFrame 接口**

在 `tauri-app/src/components/petCharacter.ts` 的 `CharacterFrame` 接口加 5 个可选字段（可选→零回归，draw 不传时退化为静态）：

```ts
export interface CharacterFrame {
  readonly w: number
  readonly h: number
  readonly t: number
  readonly mouthOpen: number
  readonly blink: number
  /** Tier-1 互动整体变换（缺省=单位变换）。pivot = 脚底中心。 */
  readonly rotateDeg?: number
  readonly offsetX?: number
  readonly offsetY?: number
  readonly scaleX?: number
  readonly scaleY?: number
}
```

- [ ] **Step 2: drawSpriteCharacter 应用变换（绕脚底）**

把 `drawSpriteCharacter` 的绘制体替换为带 transform 版（pivot 在脚底中心，让旋转/挤压像"站着晃"）：

```ts
export function drawSpriteCharacter(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  f: CharacterFrame,
): void {
  const { cx, cy, scale } = applyAliveTransform(ctx, f)
  const maxW = f.w * 0.9
  const maxH = f.h * 0.92
  const iw = img.naturalWidth || img.width || 1
  const ih = img.naturalHeight || img.height || 1
  const fit = Math.min(maxW / iw, maxH / ih)
  const drawW = iw * fit * scale
  const drawH = ih * fit * scale

  // Tier-1 互动变换（缺省 = 单位）。pivot = 脚底中心。
  const rotateRad = ((f.rotateDeg ?? 0) * Math.PI) / 180
  const sx = f.scaleX ?? 1
  const sy = f.scaleY ?? 1
  const ox = f.offsetX ?? 0
  const oy = f.offsetY ?? 0
  const footX = cx + ox
  const footY = cy + drawH / 2 + oy // 立绘底边

  ctx.save()
  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
  ctx.translate(footX, footY)
  ctx.rotate(rotateRad)
  ctx.scale(sx, sy)
  // 以脚底为原点向上画整张图
  ctx.drawImage(img, -drawW / 2, -drawH, drawW, drawH)
  ctx.restore()
}
```

- [ ] **Step 3: typecheck**

Run: `cd tauri-app && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src/components/petCharacter.ts
git commit -m "feat(pet-tier1): CharacterFrame 加 transform 字段 + drawSpriteCharacter 绕脚底应用"
```

---

## Task 4: draw() 接通 applyTo + deriveTransform（点击/拖拽视觉上线）

**Files:**
- Modify: `tauri-app/src/components/Live2DCanvas.tsx`（`draw()` 约 547-588）

- [ ] **Step 1: 加 import**

`Live2DCanvas.tsx` 顶部 import 区（`./petCharacter` import 之后）加：

```tsx
import { deriveTransform } from "./petTransform";
```

- [ ] **Step 2: draw() 每帧调 applyTo + deriveTransform**

在 `startCanvas2D` 的 `draw(ts)` 里，构造 `frame` 之前插入 applyTo + 读变换。把现有的 `const frame: CharacterFrame = { w, h, t: ts, mouthOpen, blink }` 替换为：

```tsx
        // Tier-1: 让 pet-anim 每帧写参数到 engine 的 CoreModel，再读回合成整体变换。
        const engine = engineRef.current;
        const core = engine?.getCoreModel();
        let tf = { rotateDeg: 0, offsetX: 0, offsetY: 0, scaleX: 1, scaleY: 1 };
        if (core && overlayRef.current) {
          overlayRef.current.setMouthOpenY(mouthRef.current);
          overlayRef.current.applyTo(core, ts);
          tf = deriveTransform(core);
        }

        const frame: CharacterFrame = {
          w,
          h,
          t: ts,
          mouthOpen: mouthRef.current,
          blink: isBlinking ? 1 : 0,
          rotateDeg: tf.rotateDeg,
          offsetX: tf.offsetX,
          offsetY: tf.offsetY,
          scaleX: tf.scaleX,
          scaleY: tf.scaleY,
        };
```

> 注意：`engineRef` / `overlayRef` 已在组件内存在（imperative handle 用的就是它们）。`applyTo` 在 `pet-anim/index.ts:766`，签名 `applyTo(model, now_t)`。

- [ ] **Step 3: typecheck + vitest 回归**

Run: `cd tauri-app && npx tsc --noEmit && npx vitest run src/pet-anim src/pet-engine src/components/petTransform.test.ts`
Expected: 0 errors + 全 PASS

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src/components/Live2DCanvas.tsx
git commit -m "feat(pet-tier1): draw() 接 applyTo+deriveTransform — 点击/拖拽 wobble 视觉上线"
```

- [ ] **Step 5: preview 真测（点击/拖拽变换可见）**

启动 preview（端口避开占用），DevTools console 跑：
```js
const ov = window.__deskpet_anim_overlay
ov.setDragState('being_held', performance.now())   // 模拟拖拽
// 观察立绘是否开始 wobble 摇摆
setTimeout(() => ov.setDragState('idle', performance.now()), 1500)  // spring back
```
Expected: 立绘可见地左右摇摆后回正（pet-anim 的 ParamBodyAngleZ wobble → rotate）。截图存证。

---

## Task 5: 点击星星粒子特效（覆盖 UI）

**Files:**
- Create: `tauri-app/src/components/PetTapBurst.tsx`
- Modify: `tauri-app/src/components/Live2DCanvas.tsx`（hit-zone onClick）

- [ ] **Step 1: 写 PetTapBurst 组件**

```tsx
// tauri-app/src/components/PetTapBurst.tsx
// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useState } from "react";

interface Burst { id: number; x: number; y: number }

/**
 * Tier-1 点击星星特效。父组件每次点击调 spawn(x, y)，在该坐标爆 5 颗
 * 星星，600ms 上飘 + 淡出后自动清除。纯覆盖层，z-index 高于立绘但
 * pointer-events:none 不挡交互。
 */
export function usePetTapBurst() {
  const [bursts, setBursts] = useState<Burst[]>([]);
  const spawn = (x: number, y: number) => {
    const id = performance.now();
    setBursts((b) => [...b, { id, x, y }]);
    window.setTimeout(() => setBursts((b) => b.filter((it) => it.id !== id)), 650);
  };
  return { bursts, spawn };
}

export function PetTapBurst({ bursts }: { bursts: Burst[] }) {
  return (
    <>
      {bursts.map((b) =>
        Array.from({ length: 5 }).map((_, i) => {
          const ang = (i / 5) * Math.PI * 2;
          const dx = Math.cos(ang) * 26;
          const dy = Math.sin(ang) * 26 - 14;
          return (
            <span
              key={`${b.id}-${i}`}
              style={{
                position: "fixed",
                left: b.x,
                top: b.y,
                pointerEvents: "none",
                zIndex: 40,
                fontSize: 16,
                // CSS 变量驱动 keyframe（见下方 <style>）
                ["--dx" as string]: `${dx}px`,
                ["--dy" as string]: `${dy}px`,
                animation: "pet-star 600ms ease-out forwards",
              }}
            >
              ✨
            </span>
          );
        }),
      )}
      <style>{`
        @keyframes pet-star {
          0%   { transform: translate(0,0) scale(0.4); opacity: 0; }
          25%  { opacity: 1; }
          100% { transform: translate(var(--dx), var(--dy)) scale(1.1); opacity: 0; }
        }
      `}</style>
    </>
  );
}
```

- [ ] **Step 2: Live2DCanvas 挂 tap burst + onClick 触发**

在 `Live2DCanvas` 组件里：
1. 顶部 import：`import { usePetTapBurst, PetTapBurst } from "./PetTapBurst";`
2. 组件体加：`const { bursts, spawn: spawnBurst } = usePetTapBurst();`
3. hit-zone 的 `onClick` 回调里，在现有 `overlay.pulseInteraction("click", ts)` 之后加：`spawnBurst(e.clientX, e.clientY);`
4. 组件 return 的 `<>` 里（hit-zone div 之后）加：`<PetTapBurst bursts={bursts} />`

- [ ] **Step 3: typecheck**

Run: `cd tauri-app && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src/components/PetTapBurst.tsx tauri-app/src/components/Live2DCanvas.tsx
git commit -m "feat(pet-tier1): 点击星星粒子特效"
```

---

## Task 6: 努力工作思考气泡 + working 状态信号

**Files:**
- Create: `tauri-app/src/components/PetWorkingBubble.tsx`
- Modify: `tauri-app/src/App.tsx`（working 状态 + tool_use_event 维持 + 渲染气泡）

- [ ] **Step 1: 写 PetWorkingBubble 组件**

```tsx
// tauri-app/src/components/PetWorkingBubble.tsx
// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Tier-1「努力工作」气泡。working=true 时显示在角色头顶：💭 + 三点
 * 跳动 + 文案。覆盖层，pointer-events:none。位置参考 PetCelebrationBubble
 * （bottom:220px right:32px），略高一点避免重叠。
 */
export function PetWorkingBubble({ active, label = "努力工作中" }: { active: boolean; label?: string }) {
  if (!active) return null;
  return (
    <div
      style={{
        position: "fixed",
        bottom: 250,
        right: 28,
        zIndex: 35,
        pointerEvents: "none",
        background: "rgba(30,27,46,0.82)",
        backdropFilter: "blur(8px)",
        color: "#e9e4ff",
        borderRadius: 14,
        padding: "7px 12px",
        fontSize: 12,
        display: "flex",
        alignItems: "center",
        gap: 6,
        boxShadow: "0 4px 16px rgba(0,0,0,0.3)",
      }}
    >
      <span style={{ fontSize: 14 }}>💭</span>
      <span>{label}</span>
      <span className="pet-dots">
        <i></i><i></i><i></i>
      </span>
      <style>{`
        .pet-dots i {
          display: inline-block; width: 4px; height: 4px; margin: 0 1px;
          background: #c4b5fd; border-radius: 50%;
          animation: pet-dot 1s infinite ease-in-out;
        }
        .pet-dots i:nth-child(2) { animation-delay: 0.18s; }
        .pet-dots i:nth-child(3) { animation-delay: 0.36s; }
        @keyframes pet-dot { 0%,80%,100% { opacity:0.3; transform:translateY(0) } 40% { opacity:1; transform:translateY(-3px) } }
      `}</style>
    </div>
  );
}
```

- [ ] **Step 2: App.tsx 加 working 状态 + tool_use_event 维持**

在 `App.tsx`：
1. import：`import { PetWorkingBubble } from "./components/PetWorkingBubble";`
2. 加 state：`const [working, setWorking] = useState(false);`
3. 在 ws 消息处理里：
   - 收到 `tool_use_event` 时 → `setWorking(true)`（agent 开始调工具）
   - 收到 `chat_v2_final` / `chat_v2_error` / 用户 interrupt 时 → `setWorking(false)`
   - （`handleSend` 发消息时已有 `thinkingObsRef.notifyStart()`，可顺带 `setWorking(true)`；收到第一个 chunk 不关 working——working 表示"整个任务执行期"，只在 final/error 关）
4. 在 App 的 JSX 顶层（与桌宠同层）渲染：`<PetWorkingBubble active={working} />`

> 精确触发点参考调研：`App.tsx:731` tool_use_event 处理、`App.tsx:775` chat_v2_final、`App.tsx:824` chat_v2_error、`App.tsx:1338` interrupt。

- [ ] **Step 3: typecheck + 前端测试**

Run: `cd tauri-app && npx tsc --noEmit && npx vitest run`
Expected: 0 errors + 全 PASS

- [ ] **Step 4: Commit**

```bash
git add tauri-app/src/components/PetWorkingBubble.tsx tauri-app/src/App.tsx
git commit -m "feat(pet-tier1): 努力工作思考气泡 + tool_use 期间持续 working 状态"
```

---

## Task 7: 真机验收（windows-mcp / Tauri 桌面版）

> 遵守项目手工测试纪律：每 case 真坐标操作 + 截图 + 判定。

- [ ] **Step 1: 启动 Tauri dev（主 worktree）**

先杀残留 `taskkill /F /IM deskpet.exe`，确认 8100/5173 释放，`cd tauri-app && npx tauri dev`（后台）。等 deskpet.exe + sprite loaded 日志。

- [ ] **Step 2: TC-1 点击反应**

declare: `坐标=桌宠脸部 | 动作=单击 | 期望=立绘轻歪(AngleZ transient)+爆星星✨`
windows-mcp 截图前后对比。判定 PASS/FAIL。

- [ ] **Step 3: TC-2 拖拽 wobble**

declare: `动作=按住桌宠拖动>5px再松开 | 期望=拖动时 wobble 摇摆，松手 spring-back 回正`
截图（拖动中 + 松手后）。判定。

- [ ] **Step 4: TC-3 努力工作**

declare: `动作=对话框发"帮我生成一个PPT"等会调工具的任务 | 期望=agent 执行期间头顶常驻💭努力工作气泡，任务结束消失`
截图（执行中气泡 + 结束后无气泡）。判定。

- [ ] **Step 5: 写真测报告**

存 `plans/2026-06-13-pet-interactions-tier1/manual-results/REPORT.md`，含 3 个 case 截图 + 判定。Commit。

---

## Acceptance Checklist（Tier-1 DONE）

- [ ] `npx tsc --noEmit` 0 errors
- [ ] `npx vitest run` 全绿（新增 SpriteCoreModel 5 + petTransform 6 + pet-anim 零回归）
- [ ] pet-anim/** 零改动（互动逻辑层未动）
- [ ] 真机：点击→歪+星星 ✅；拖拽→wobble+回弹 ✅；发任务→努力工作气泡 ✅（3 截图）
- [ ] 立绘静止时无异常变形（neutral → 单位变换）

## 不在本计划（Tier-2/3，按需另立）

- 眨眼 / 嘴型 lip-sync / 表情换脸（需立绘分层 — Tier-2）
- 完整 Cubism 参数驱动的网格变形（WebGL2 mesh backend — Tier-3）
- 部件级参数（眉/眼球/发丝/脸红）的视觉
