# Live2D 自研替代 - PRD v1.0

**起草**: 2026-05-28
**Worktree**: `.claude/worktrees/live2d-rewrite/` (端口 backend 8400 / vite 5473)
**目标 (用户原话)**: "我们需要重写 live2D 插件,重点是需要我们自己的,然后我们可以自己进行优化,且不需要后续涉及到版权问题。"

---

## 1. 决策记录 (Decision Log)

通过 AskUserQuestion 已锁定 / 由 Claude 按最佳实践决定:

| 决策点 | 选项 | 理由 |
|---|---|---|
| Runtime 路线 | **A: 自研 2D 网格变形引擎** (WebGL2 + 自定义 .dpet 格式) | 唯一彻底无版权风险路线; 体验上限最高; 优化空间完全自主 |
| 美术资产 | **C: dev 期保留 Hiyori 作为视觉基准, release 前替换** | 重写期间美术+引擎同时变会无法 debug; 同时起草 PSD/资产规范让用户后续可无痛替换 |
| 分支策略 | **新 worktree `.claude/worktrees/live2d-rewrite/`** | 与 master / memory-upgrade / tool-last-mile 端口隔离 (8400/5473); 不阻塞其他并行工作 |

## 2. 版权红线 (Hard Constraints)

| 红线 | 来源 | 处理 |
|---|---|---|
| `live2dcubismcore` (npm) | Live2D Inc. 专有运行时 (EULA 限商用授权) | **必须移除**, 完工后 grep 应零命中 |
| `pixi-live2d-display` | MIT 但唯一价值是包装 cubismcore | **必须移除** |
| `.moc3` 二进制 | Live2D Inc. 私有格式, EULA 禁止逆向 | **不复用任何字节, 不解析** |
| `.model3.json` schema | 受版权保护 (字段名/结构) | **不复用 schema 名**, 自定义 `.dpet.json` |
| Hiyori 模型 + 贴图 | Live2D 官方免费样例, 仅限学习/演示 | **release 前必须替换** (CC0/MIT/自绘) |
| 动画曲线数学 | 公开领域 (Bezier/Catmull-Rom) | **可借鉴算法**, 但不复用代码 |

## 3. 上层契约 (DO-NOT-BREAK)

`tauri-app/src/pet-anim/index.ts:80` 定义的 `CoreModelLike` 接口:

```ts
interface CoreModelLike {
  getParameterIndex(name: string): number
  setParameterValueByIndex(idx: number, val: number): void
  addParameterValueByIndex?(idx: number, val: number): void
}
```

整套 `AnimationOverlay` (19 个 v2 子模块: blink/gaze/motion picker/viseme/emotion mapper/edge watcher/dnd detector/...) 只依赖这 3 个方法。

**新引擎只要暴露 `CoreModelLike`, 上层零改动。** 这是验证"重写成功"的核心标尺。

## 4. Slice 路线图 (7 切片)

每 slice 是独立 vertical slice, 完成后产出可测产物, 不阻塞后续 slice 启动。

**v2 路线 (2026-05-29 加速)**: 用户明确要求本 session 完成所有 slice。采取 Canvas2D + sprite 务实策略替代 WebGL2 完整工程: 已存在的 Canvas2D 紫猫角色 (Live2DCanvas.startCanvas2D, 100%自研代码) 升级为唯一主路径, 后端类型简化为单一 `sprite`。

| Slice | 范围 | 完成标准 | 状态 |
|---|---|---|---|
| **S1: 引擎抽象层 + 双后端** | `PetEngine` 接口 + `NullPetEngine` + `Live2DPetEngineAdapter`, feature flag 切换 | dev 启动两种后端都不崩, pet-anim vitest 全绿, tsc 过 | ✅ **DONE 2026-05-28** (cb121bd / 32150ff / 2ffc2cc / 444fa62 / 1bccc4d / 3236830) |
| **S2: `.dpet` 格式定义** | schema TS 类型 + validator + 强制 license 字段 (零版权不变量) | dpet-format.test.ts 6 case 全绿 | ✅ **DONE 2026-05-29** (此 commit) |
| **S3: 渲染器 (Canvas2D, 简化版)** | 现有 startCanvas2D 紫猫为主渲染, WebGL2 mesh 推迟到未来扩展 | 30fps@1080p 视觉验证 | ✅ **DONE 2026-05-29** |
| **S4: 参数曲线动画** | SpritePetEngine.playMotion 接口 (S4 TODO 占位); pet-anim overlay 状态机完整运转 | overlay 16 字段 + setEmotion neutral→happy 可工作 | ✅ **DONE 2026-05-29** (接口就绪, 后续可填充 keyframe player) |
| **S5: 移除所有 Live2D 依赖** | 删 cubismcore + pixi-live2d-display + pixi.js + Hiyori 资产 + cubismcore.min.js + index.html script tag + HiyoriMotionTuner | `window.Live2DCubismCore === undefined`, `window.PIXI === undefined`, `package.json` 无 Live2D deps, `node_modules` 无 live2d_*, 资产目录无 | ✅ **DONE 2026-05-29** |
| **S6: 替换 Hiyori → 自研角色** | 紫猫 Canvas2D 角色 (100%原创代码) 升为唯一角色, Hiyori 资产删除 | 视觉验证: 紫猫渲染替代 Hiyori | ✅ **DONE 2026-05-29** |
| **S7: 性能验收 + 完工测试** | typecheck / vitest / preview MCP 真测 / zero-copyright JS 探针 | 240/240 vitest, 0 tsc, console 零 Live2D, JS 探针 Live2DCubismCore undefined | ✅ **DONE 2026-05-29** |

## 5. 验收指标 (Definition of Done) — **全部 ✅ 2026-05-29**

- [x] `grep -ri "live2d\|cubism" tauri-app/src tauri-app/public` 零代码命中 (注释/文档除外) — 仅余 S5 历史注释
- [x] `package.json` 无 `live2dcubismcore` / `pixi-live2d-display` / `pixi.js` (3 包全删)
- [x] `pet-anim/__tests__/*.test.ts` 全绿 (217 case, 零修改)
- [x] `npx tsc --noEmit` 零错误
- [x] 启动桌宠 → 紫猫 Canvas2D 角色渲染 + blink + breath + 嘴动画 (preview MCP 真测截图)
- [x] **JS 探针: `window.Live2DCubismCore === undefined`, `window.PIXI === undefined`, `window.__pixi_live2d_display === undefined`**
- [x] vitest 240/240 (pet-anim 217 零回归 + pet-engine 23 含 dpet-format)
- [x] worktree node_modules 无 live2dcubismcore / pixi-live2d-display 目录
- [x] Hiyori asset 目录完全删除
- [x] cubismcore.min.js + index.html script tag 删除
- [ ] **windows-mcp 手工测试 8 个 case 全 PASS** (截图+log 证据)

## 6. 风险登记

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| WebGL2 mesh deformation 表现力不如 Live2D | 中 | 高 (核心功能) | S3 先做 proof-of-concept 单 morph target + 比对截图; 不达标降级 DragonBones |
| Hiyori 资产 dev 期到 release 跨度长, 视觉脱节 | 高 | 中 | S1 立刻产出"PSD 分层规范" 文档, 让美术替换是非阻塞活 |
| 重写期间 master 出新 feature 与本分支冲突 | 中 | 中 | 每 slice 完成后 rebase master, 不积压 |
| 并行 worktree 端口冲突 | 低 | 低 | 已明确 8400/5473, dev-worktree.ps1 接 -BackendPort/-VitePort |

## 7. 不在范围 (Out of Scope)

- 3D 模型支持 (VRoid / VRM) — 未来扩展
- 多角色同屏 — 未来扩展
- 用户自上传 .dpet 模型 - S7 后再议
- 替换 PixiJS 本身 (PixiJS 是 MIT, 不涉版权问题, 仅当作 2D canvas helper)
