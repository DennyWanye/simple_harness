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

| Slice | 范围 | 完成标准 | 本 session 是否做 |
|---|---|---|---|
| **S1: 引擎抽象层 + 双后端** | `PetEngine` 接口 + `NullPetEngine` + `Live2DPetEngineAdapter`, feature flag 切换 | dev 启动两种后端都不崩, pet-anim vitest 全绿, tsc 过 | ✅ **DONE 2026-05-28** (commits cb121bd / 32150ff / 2ffc2cc / 444fa62, 237/237 vitest, 0 tsc errors) |
| S2: `.dpet` 格式 + loader | schema 定义 + fixture 工具 + parser/validator | 单元测试覆盖, schema 文档完整 | 后续 session |
| S3: WebGL2 网格变形渲染器 | DeskPetMeshRenderer (mesh+skin+morph) | 30fps@1080p, drawcall<20 | 后续 session |
| S4: 参数曲线动画系统 | MotionPlayer 替换 pixi 的 motion() | 10 个 Hiyori motion 在新格式下回放 | 后续 session |
| S5: 移除所有 Live2D 依赖 | 删 cubismcore / pixi-live2d-display / Hiyori 资产 | grep 零命中, CI license-scan 加入 | 后续 session |
| S6: 替换 Hiyori → CC0 角色 | 美术资产替换 (用户提供/AI 生成) | release-ready 资产入仓 | 用户决策后 |
| S7: 性能验收 | 性能基线对比 + 内存/CPU profiling | 不劣于原 Live2D, 优化点列表 | 收尾 session |

## 5. 验收指标 (Definition of Done)

S1-S5 全部完成后, 整体重写视为完成:

- [ ] `grep -ri "live2d\|cubism" tauri-app/src tauri-app/public` 零代码命中 (注释/文档除外)
- [ ] `package.json` 无 `live2dcubismcore` / `pixi-live2d-display`
- [ ] `pet-anim/__tests__/*.test.ts` 全绿 (零修改)
- [ ] `npm run typecheck` 零错误
- [ ] 启动桌宠, 角色渲染、blink、gaze、motion、lip-sync 全部可见可工作
- [ ] FPS ≥ 30 @ 1080p
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
