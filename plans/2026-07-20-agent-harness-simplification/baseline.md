# Agent Harness 简化：执行基线

> 采集时间：2026-07-20（Asia/Shanghai）
> Git：`master@961c7d34`
> 范围：实现 Harness 业务代码前的主工作树状态。

## 工作区状态

- 采集期间，DeepResearch v7 已以 `961c7d34` 合入 `master`。全部 Harness 实现 worktree 都以该提交为基线，因此不会覆盖最新生产链路。
- 主工作树当时只增加计划文档；业务实现放在隔离的 `codex/harness-*` worktree。
- `tauri-app/pnpm-lock.yaml` 和 `pnpm-workspace.yaml` 在采集前已经是未跟踪文件。

## 初始绿色基线

| 门禁 | 结果 | 备注 |
|---|---:|---|
| Harness 契约 | `30 passed` | 4.45 秒 |
| AgentLoop 组 | `36 passed` | 3.83 秒 |
| Tool / Registry 组 | `86 passed` | 2.91 秒 |
| Workflow 组 | `650 passed` | 177.55 秒；1 个既有弃用警告 |
| 已知 vector-worker 抖动用例 | `1 passed` | 仍记录为时间相关 flaky |
| 后端全量 | 未完成 | 904 秒后无汇总；既有 worker hang，不计绿色 |
| 前端 Vitest | `79 files / 832 tests passed` | 有既有 Tauri/window 测试环境噪声 |
| TypeScript + Vite | 通过 | 有既有 chunk 警告 |
| ESLint | 基线红：265 个问题 | 255 error、10 warning，不允许新增 |
| Rust | `73 passed` | 有既有 dead-code/linker 警告 |

## 环境说明

- 环境中的 `npm` 不可用，因此直接使用仓库已安装的 `node_modules` 入口和捆绑 Node。
- 从 `backend/` 目录运行 pytest 会导致 4 个 `scripts.*` import 收集失败；标准命令从仓库根目录运行 `backend/tests`。
- ESLint 和后端全量超时是基线缺陷，不构成新增失败的豁免。

## 回退检查点

第一次 WI-12 生产切换因丢失 ContextAssembler、历史、persona、memory、附件、问题管线状态、Skill Codify 和多个 UI 事件而被拒绝，随后完整回退。

- 安全提交：`4d38979e`
- Harness：`150 passed, 9 xfailed`
- 当时 orchestration LOC：`33,228`
- 结论：仅减少 owner 或 LOC 不足以证明简化成功，必须保持完整产品能力。

## R0 机械基线

- Phase-0 LOC：`21,563`
- 回退态 LOC：`33,228`
- 未分类：`0`
- 10,000 个真实 `RunKernel.start -> UoW.finalize -> RunKernel.close` 生命周期：
  - start、终态行、final event、close 均为 `10,000`
  - 已完成运行强引用：`0`
  - RSS 增量：`1,470,464` 字节
- 产品调用点：`141`，未映射 `0`
- 集成门：`176 passed, 9 xfailed`

R0 只改测试、脚本、manifest 和文档，不改变生产 owner。

## R4.5 批准后的结构基线

> 采集时间：2026-07-21。生产仍为 `legacy/0`。

| 指标 | 基线 | R4.5 目标 |
|---|---:|---:|
| 统计 orchestration LOC | `33,618` | `<=33,618` |
| 审计核心 LOC | `5,725` | `<=5,500` |
| Kernel LOC | `820` | `<=850` 且公开操作恰好为 6 |
| 公开事务 starter | `33` | `<=23` |
| execution-table DML authority | `2` | `1` |
| fault window | `34` | `39` |
| run map / Supervisor / Presenter authority | 多个 | 各 `1` |
| legacy survivor | `15` | R6 前不增长 |

临时迁移峰值门为总量 `<=34,300`、核心 `<=5,725`，只用于施工阶段，不放宽最终目标。

## R4.5 收敛结果

各切片完成后：

- typed transaction starter：`33 -> 21`
- execution DML authority：`2 -> 1`
- 删除 `ProductVenueOpenResult` 冗余包装，`venues.py 406 -> 386`
- 三套活动运行 map 合并为一个 `BoundedLiveIndex`
- 多个常驻 scheduler / recovery coordinator 合并为一个 `HarnessSupervisor`
- 删除 `UnifiedToolExecutor` 转发层
- 删除 `KernelChildLauncher` 和 `ChildLauncher`
- 最终：raw / adjusted / core / Kernel 为 `33,925 / 33,416 / 5,498 / 767`
- Kernel 公开操作：`6`
- fault window：`39`
- run map / Supervisor / Presenter：`1 / 1 / 1`
- Harness：`424 passed, 8 xfailed`

生产在这一阶段仍保持 `legacy/0`，等待 R6 原子切换。

## R5.5 基线与 spike 结论

原 R6 方案在 durable admission 上存在缺口：产品层会在 `Kernel.start()` 前等待审批，无法满足原子 owner 切换。一次可丢弃 spike 证明完整 admission 不是几行补丁：

- 基线：`33,925 / 33,416 / 5,498 / 767`
- 不完整 spike：`34,210 / 33,701 / 5,685 / 857`
- 增量：`+285 / +285 / +187 / +90`

因此用户批准 R5.5 施工门：

- raw `<=34,800`
- adjusted `<=34,250`
- core `<=5,950`
- Kernel `<=925`

R6 永久目标保持严格，不允许用压行、任意删除或审计排除隐藏复杂度。

spike 后按精确 worktree / 命令行范围清理了相关孤儿进程，并复核剩余为 0。
