# Task 0 绿色基线与执行边界

> 日期：2026-07-24  
> 锁定 HEAD：`cde016ad651295e60100c3275e72bf23724b3833`  
> 状态：Task 0 完成；SP-11 的正式 helper 完整重验被提升为 Task 6/15 的强制门。

## 1. 仓库与上游 worktree 审计

- 主 checkout 位于 `F:\projects\deskpet`；Task 0 开始时先核对真实 HEAD、dirty
  worktree 与所有 worktree，没有沿用 plan 调研快照。
- 用户现有且不属于本计划的改动保持原样：
  `ARCHITECTURE/DeepResearch.md`、`plans/2026-07-20-deepresearch-enumeration-quality/`
  与 `plans/2026-07-20-deepresearch-topn-quality-followup.md`。
- `deskpet-wt-cap-store` 为 patch-equivalent clean worktree；Capability/Godot
  聚焦回归为 `175 passed, 1 skipped`。
- `deskpet-wt-os-runtime` 中 10 个文件与 master byte-identical，15 个文件已被 master
  的后续统一执行链取代；brokered runtime/OS tools/ticket 聚焦回归为 `80 passed`。
- `deskpet-wt-ui-godot` 的提交已 patch-equivalent。
- 三个 worktree 均在逐文件和测试对账后移除并执行 `git worktree prune`；分支引用仍保留，
  因此不是不可恢复地丢弃代码。当前 `git worktree list` 只剩主 checkout。

## 2. 工具链与动态 schema 分配

| 项 | Task 0 实测 |
|---|---|
| Python | 3.11.9 |
| Node | v24.14.0 |
| pnpm | 11.9.0 |
| rustc / cargo | 1.97.0 / 1.97.0 |
| Git | 2.53.0.windows.3 |
| Workflow 当前版本 `N` | 16 |
| Capability 当前版本 `C` | 1 |
| SessionDB 当前版本 `S` | 20 |
| Memory migration 当前最大序号 | 012 |

按最终稳定 HEAD 锁定以下编号，禁止后续 Task 再硬编码调研期编号：

| Task | 分配 |
|---|---|
| Task 3 | Workflow 17；SessionDB 21；`013_companion_projection.sql` |
| Task 7 | Workflow 18；Capability 2 |
| Task 8 | Workflow 19（receipt）+ Workflow 20（exact immutable material；生产组合修正） |
| Task 10 | Workflow 21 |

## 3. Machine-readable 冻结物

- `baseline-package-limits.json`：冻结包大小、路径预算、Godot 当前样本与 baseline hash。
- `baseline-tool-effects.json`：冻结 72 个 V2 stable handler、1 个 legacy reminder、
  Task 6/11 的 approved additions、phase equation、schema hash 与 effect classification。
- `baseline-file-claims.json`：163 个生产文件 claim，`active_claim_intersections=0`、
  `dependency_cycles=0`、`missing_claim_paths=0`。
- 三个冻结物都记录本文件顶部的精确 HEAD，并以去掉自身 `baseline_hash` 后的 canonical
  JSON SHA-256 进行自校验。

## 4. 自动化绿色基线

| 范围 | 结果 |
|---|---:|
| Capability + Godot | `175 passed, 1 skipped` |
| Brokered driver / OS runtime / WI-5 / workflow task attempt | `80 passed` |
| Skill / preference / SessionDB v19-v20 | `110 passed` |
| Workflow 聚焦 | `53 passed` |
| Harness indices 0-11 | `129 passed, 4 xfailed` |
| Harness indices 12-23 | `122 passed` |
| Harness indices 24-25 | `11 passed` |
| R4.5 core budget（完整文件） | `20 passed` |
| R5.5 admission | `12 passed` |
| R5.5 cutover readiness | `6 passed` |
| Recovery lease | `5 passed` |
| Registry/RunKernel/runtime/evidence/single-session/subagent | `103 passed` |
| Harness indices 36-45 | `171 passed` |
| 主消息 session store | `40 passed` |

上游合并后曾暴露两类真实基线漂移，均先定位再单独修复：

1. authority/migration mapping 由生成器按真实 DML authority 重建，提交
   `f844a706`；
2. 最终统一执行链比 pre-integration LOC 估算增加了 durable failure/replan 与 brokered
   runtime 实现。没有删除真实功能或放宽大额余量，而是按最终实测重新锁定
   raw `70100`、adjusted `69600`、core `17400`、Kernel `1050`，完整预算门
   `20/20` 通过，提交 `cde016ad`。

测试期间观察到少量既有 `aiosqlite` worker 在 pytest event loop 已关闭后的 warning；
全部相关测试退出码为 0，未发现数据断言失败或残留测试进程。

## 5. 主消息页真人基线

Task 0 直接复用紧邻本计划、且生产代码之后未再变化的统一执行链 source-Tauri 证据；
两个后续提交仅更新测试冻结物/LOC ceiling。证据位于
`plans/2026-07-23-universal-action-and-capability-packs/manual-results-2026-07-24/`：

- 真实 DeskPet 主消息页，backend `18120`、Vite `15193`、隔离 user-data；
- 日志确认 `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`，运行的是当前源码而不是
  frozen backend；
- VS-1 root `4eeb23a0d3f05fb38a32e1d8e7727054`：模型在同一 root 查询目录、激活并调用
  `file_write`、`run_shell`，完成真实工具任务；
- VS-2 root `74e21a97b26852b3a22047b7fb2eb37a`：真实写入并由 PowerShell/Git Bash
  双重读回；首次校验失败后模型在同一 root 吸收失败并重规划成功；
- 全部动作由 Computer Use 在 UI 内真实点击/输入，不使用 WS/API 直注。

两轮隔离进程树都按 launcher/PID 精确清理；最终一轮 13/13 PID 消失，
`survivor=0`、18120/15193 listener=0，释放约 8533.9 MiB private memory；
用户主实例 backend 8100 保持监听。

## 6. current-HEAD spike 复测

### SP-02：真实提交图与 FULL-durable writer

在锁定 HEAD 上从 `react.py` 当前生产分支核对，并通过
`SqliteExecutionUnitOfWork` public methods 动态计数：

| 路径 | 当前 Driver 写 | 增加 invocation claim/outcome 后 |
|---|---:|---:|
| ReactFinal | 0 | 2 |
| ToolBatch(1)，新 goal | 5 | 7 |
| ToolBatch(1)，已有 goal | 4 | 6 |
| ToolBatch(3)，新 goal | 7 | 9 |
| ToolBatch(3)，已有 goal | 6 | 8 |

固定 provider await=50ms，每侧 3 warmup + 20 samples；真实 SQLite 三侧均
`journal_mode=wal`、`synchronous=2 (FULL)`，没有合并物理事务：

| 路径 | 当前 p95 | 新连接/事务 p95 与回退 | 长寿命串行 writer p95 与回退 |
|---|---:|---:|---:|
| Final | 63.067ms | 80.122ms / +27.043%（FAIL） | 63.914ms / +1.344%（PASS） |
| ToolBatch(1) | 111.970ms | 144.161ms / +28.750%（FAIL） | 79.174ms / -29.290%（PASS） |
| ToolBatch(3) | 145.948ms | 166.017ms / +13.751%（FAIL） | 80.961ms / -44.528%（PASS） |

因此 Task 3 必须先实现 `ExecutionWriteLane`：保持 WAL/FULL 和事务边界，只复用一条
由 lock 串行化的长寿命 writer connection。该 spike 的 transaction count 走真实 current
UoW API；延迟 A/B 使用同款 aiosqlite 事务 lifecycle 和一次性最小表，不冒充
ProductVenue/Kernel 完整组合根，后者仍由 Task 14 终测。

进程树为 PowerShell `31852` → venv launcher `30104` → real Python `31468`
（另有 conhost `4640`），命令行精确包含 `_task0_sp02_current_head.py` 与 HEAD；
峰值 private memory 859,217,920 bytes（819.414 MiB），退出码 0。所有观察 PID、
脚本绝对命令、临时文件与 runtime 复核 `survivor=0`，对应 private memory 已释放。

### SP-10：dispatch-start 适配器

current-HEAD 依赖为 httpx 0.28.1、MCP 1.28.1、anyio 4.13.0：

| transport | cancel-before-handoff | ACK / completion | post-ACK crash/timeout |
|---|---:|---:|---|
| httpx provider | physical=0 | 3.882ms / 265.779ms | unknown |
| MCP stdio | physical=0 | 5.947ms / 307.956ms | unknown |
| local worker | physical=0 | 0.018ms / ≈250.018ms | unknown |

MCP 在 `stdio_client()` 返回的公开 write stream 前插入 anyio relay，再构造
`ClientSession`；没有读取 SDK 私有字段。退出码 0，峰值 private memory
106,209,280 bytes（101.29 MiB）；记录的 root、MCP launcher/real/conhost 与 local
launcher/real 全部消失，绝对脚本命令复核 `survivor=0`，内存已释放。

### SP-11：Windows Job 生命周期

定稿 spike 的历史真机证据保持完整 PASS：`CREATE_SUSPENDED → AssignProcessToJobObject
(KILL_ON_JOB_CLOSE) → ResumeThread`，normal close/helper crash 两路 root/late child 全部
收敛，峰值约 71.26 MiB、survivor=0。

current-HEAD 一次性重建确认了下列不变事实：

- 真 `CreateProcess(CREATE_SUSPENDED)` 与 Job assign 成功；
- resume 前 marker 不存在；
- `ResumeThread` 返回 previous suspend count=1；
- helper 关闭 Job 后精确 root 消失，每轮及最终均 survivor=0。

但这次临时 helper 的 root 在 5 秒内没有运行到 user-code marker/late-child，因而不能把
本次重建写成完整 PASS。该差异属于一次性 helper 本身，而不是已存在生产实现回归：
Task 6 才会落正式 `ManagedProcessStartPort`，当前仓库没有可回归的正式 helper。执行决策为：

1. 保留历史完整 PASS 作为架构可行性证据；
2. 不把本次部分复现冒充 PASS；
3. 将“resume 后 user code + late child、normal close、helper crash”列为 Task 6 正式
   adapter 的阻断测试，并在 Task 15 lifecycle helper 上再跑真机；
4. 正式 helper 未全绿前不得激活任何 executable candidate。

所有 `_task0_sp10_*`、`_task0_sp11_*` 临时源码、TEMP roots 和后代均已删除；
最终 CIM 复核 survivor=0，未修改生产代码。
