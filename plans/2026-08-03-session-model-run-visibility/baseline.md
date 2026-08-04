# Baseline — 2026-08-03

- 基线提交：`9b7fd640ccaae9cd75b79505e5d2c840246200d3`
- 工作树：仅本计划、acceptance 与架构事实源有未提交修改；未混入业务实现。

## 已通过

- 后端聚焦测试：64 passed，17.99s。
  - provider resolution
  - context usage actual attempt
  - execution projector
  - public tool projection
  - workflow progress
- 前端聚焦测试：4 files / 33 tests passed，15.70s。
  - HarnessInspectorPanel
  - AgentActivityMessage
  - ContextRing
  - WorkflowProgressGroup
- `pnpm run build`：通过；只有现有 Vite chunk size warning。

## 既有红项与时间边界

- `pnpm run lint`：既有全库红基线，277 problems（267 errors、10 warnings）；其中包含
  `harnessInspectorModel.ts:603` 的 `_order` unused。实现不得把全库 lint 当成本切片新增失败，但必须对
  所有受影响文件执行定向 lint/typecheck，并保证不新增问题。
- 全量 backend pytest：普通收集因重复 `test_fault_matrix.py` module 失败；改用
  `PYTHONPATH=backend --import-mode=importlib` 后运行 15 分钟仍无终态，按时间边界终止。原进程树
  PID 2508/28220/26668 已逐一确认退出。

## plan-task 进入实现前的处理

1. 以当前 64 个后端测试、33 个前端测试和 build 作为绿色基线门槛。
2. 将全量 backend tests 按目录/文件分片，并给每片记录耗时与终态；先定位重复模块和超时分片，不再用
   单个 15 分钟静默命令充当通过证据。
3. 受影响文件必须通过定向 lint/typecheck；最终报告把既有全库 lint 红项与新增回归分开列出。

## plan-task 现场复验（2026-08-03 16:16～16:30 +08:00）

基线 HEAD 仍为 `9b7fd640ccaae9cd75b79505e5d2c840246200d3`；业务代码尚未修改。

### 计划相关绿色门

- 后端聚焦：64 passed，15.66s。
- 前端聚焦：4 files / 33 tests passed，2.35s。
- `pnpm run build`：通过，只有既有 dynamic-import/chunk-size warning。
- 全库 ESLint：仍为 277 problems（267 errors、10 warnings），与 plan-bs 记录完全一致；不是本轮新增。

### 后端全量分片结果

为避免单个 15 分钟静默命令，把根目录 485 个 test 文件按稳定文件名 modulo 8 分片，并把三个子目录
独立运行。通过证据包括：

- root shard 0：642 passed / 5 skipped。
- root shard 1：603 passed；1 个既有 stale `execution_build_manifest.json` 失败。
- root shard 2：977 passed；1 个 DeepResearch 并发峰值断言在高并行下得到 3 而非 4，隔离复跑
  `1 passed in 2.33s`，记录为既有 flaky 信号。
- root shard 3：831 passed / 1 skipped；8 个既有 DeepResearch skill 路径 / workflow-eval fixture 漂移失败。
- root shard 4：660 passed / 1 skipped。
- root shard 5：706 passed / 7 skipped。
- root shard 6：560 passed；有两个既有 unclosed Windows transport warning。
- root shard 7：675 passed / 2 skipped；5 个既有 memory chunker / builtin skill inventory 失败。
- `tests/capabilities`：260 passed。
- `tests/companion`：650 passed；8 个既有 v19 migration/table inventory 断言失败。
- `tests/harness_simplification` 整目录在 5 分钟超时；精确终止并确认 PID 27432/20928 已退出。按文件
  modulo 4 拆分后，四片都在 4m30s 内终止：合计 804 passed / 4 xfailed，6 个既有失败，分别属于
  migration mapping、Harness LOC/budget authority、durable-task fixture、TurnInput 字段清单、authority
  manifest 与 React driver LOC 上限。

### 基线判定

- 没有发现“测试进程永不终止”的单一文件；先前 15 分钟问题属于全量单命令可视性差和测试规模过大。
- 本计划相关的 provider resolution、Context Usage、Inspector、tool projection、workflow progress 与前端
  build 均有绿色基线。
- 上述既有红项不得误算为本次回归；但本计划会触及 migration/Inspector/Harness 的测试必须以
  change-impact 方式更新并转绿，且任何新增失败都必须修复。
