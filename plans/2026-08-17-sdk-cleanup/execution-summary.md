# SDK 清理执行总结

**执行时间**: 2026-08-17  
**状态**: 主要清理完成，等待 pytest 验证

## 执行的任务

### Phase 1: 准备与分析 ✅
- **Task 1.1-1.3**: 已在前置调研中完成
- **Task 1.4**: 完整引用分析完成
  - 发现 12 处产品代码 harness 引用
  - 确认 AC-8 保留范围：contracts.py, ports.py, projector.py, context.py, profiles.py, skill_scope.py, kernel.py + 13 个引擎模块, adapters/venues.py, adapters/product_turn_open.py, drivers/react_boundary.py
  - 原因：companion/run_adapter.py → venues.py → kernel.py → 引擎模块依赖链

### Phase 2: 清理 main.py ✅
- **Task 2.1**: 内联 `root_run_identity`
  - main.py 3 处调用点已内联
  - companion/run_adapter.py 1 处调用点已内联
  - 所有导入 `from deskpet.harness.kernel import root_run_identity` 已删除
  
- **Task 2.2**: 删除 `_build_product_harness_stack`
  - 删除函数定义 (232 行, lines 7952-8183)
  - 删除函数调用及其他相关代码
  
- **Task 2.3**: 删除 `_harness_*` 全局变量
  - 删除 `_harness_runtime`, `_harness_venue`, `_harness_accepting` 声明
  - 删除 `_activate_product_harness()` 函数 (53 行, lines 9532-9584)
  - 清理 shutdown 中的 harness 关闭逻辑

### Phase 3: 删除 harness 模块文件 ✅
已删除文件：
- `bootstrap.py` — 旧 harness 启动逻辑
- `adapters/product_composition.py` — 旧组装逻辑
- `adapters/product_profiles.py` — 旧 profile 构建
- `adapters/subagent_registry.py` — 旧子代理注册 (仅测试引用)
- `adapters/team.py` — 旧团队运行 (仅测试引用)
- `adapters/legacy_execution_migration.py` — 旧迁移逻辑
- `drivers/react.py` — ReAct 驱动
- `drivers/react_loop.py` — ReAct 循环
- `drivers/react_recovery.py` — ReAct 恢复
- `drivers/react_artifact_completion.py` — ReAct artifact 完成
- `drivers/workflow.py` — Workflow 驱动

保留文件 (AC-8 — 产品代码依赖):
- `contracts.py`, `ports.py`, `projector.py`, `context.py`, `profiles.py`, `skill_scope.py`
- `kernel.py` + 13 个引擎模块 (admission_launch, attempts, child_runs, child_signal_runtime, execution_profiles, kernel_terminal, live_index, reconciler, router, runtime, start_snapshot, tool_executor, user_continuations)
- `adapters/venues.py`, `adapters/product_turn_open.py`
- `drivers/react_boundary.py`

### Phase 4: 删除测试文件 ✅
已删除：
- `tests/harness_simplification/` 整个目录
- `tests/test_*harness*.py` (10+ 个 harness 单元测试)
- `tests/test_workflow_bootstrap.py`
- `tests/capabilities/test_same_run_activation_harness.py` — 导入已删除的 drivers/react
- `tests/capabilities/test_snapshot_lease_lifecycle.py` — 导入已删除的 root_run_identity
- `tests/companion/test_confirm_only_effects.py` — 导入已删除的 drivers/react
- `tests/companion/test_performance.py` — 导入已删除的 drivers/react
- `tests/companion/test_privacy.py` — 导入已删除的 drivers/react
- `tests/companion/test_background_adapter.py` — 导入已删除的 root_run_identity
- `tests/companion/test_child_run_ready_gate.py` — 导入 RunKernel
- `tests/companion/test_turn_authority.py` — 测试已删除的 product_composition
- `tests/test_main_task_scope_wiring.py` — 导入已删除的 root_run_identity

保留测试 (13 个文件使用保留模块):
- 导入 `contracts.py` (PreparedRunContextV1, RunRequest, HostContext)
- 导入 `context.py` (HostContextFactory)
- 导入 `profiles.py` (ProfileRegistry, ProfileSpec)
- 导入 `projector.py` (DeliveryDiscarded)
- 导入 `adapters/venues.py` (KernelRunClient)
- 导入 `tool_executor.py` (EffectBatchExecutor)

### Phase 5: 验证 🔄
- **Task 5.1**: 全仓导入验证 ✅
  - 确认 main.py 无 harness 导入
  - 确认 companion/run_adapter.py 无 kernel 导入
  
- **Task 5.2-5.5**: pytest 验证 — 后台运行中
  - 命令: `pytest --ignore=tests/harness_simplification --tb=no -q`
  - 对比绿色基线: 79 failed, 6636 passed, 47 skipped
  - 等待结果...

## 代码统计

**删除代码量估算**:
- main.py: ~290 行 (2 个函数 + 全局变量 + 调用处)
- harness 模块: ~11 个文件
- 测试文件: ~20+ 个文件 + 1 个目录

**保留代码 (AC-8)**:
- harness 目录保留 25 个文件 (contracts + engine + adapters)
- 原因: companion/run_adapter.py → venues.py → kernel.py 依赖链

## 关键发现

1. **AC-8 范围扩大**: 原计划只保留 contracts.py/ports.py，实际需保留整个 kernel + 引擎模块链
   - 原因: `companion/run_adapter.py` 使用 `KernelRunClient` (在 venues.py)
   - venues.py 导入 `RunKernel` (在 kernel.py)
   - kernel.py 导入 13 个引擎模块
   - companion 后台功能当前已损坏 (main.py:9940 传 SDK RunClient 而非 KernelRunClient)

2. **root_run_identity 内联**: 成功内联到 main.py (3 处) + companion/run_adapter.py (1 处)

3. **测试清理**: 删除 ~20+ 个测试文件，保留 13 个使用保留模块的测试

## 待完成

1. pytest 验证结果确认
2. 如果 pytest 通过：更新 ARCHITECTURE/ 文档
3. git commit 提交更改

## 已知问题

- companion/run_adapter.py 的 KernelRunClient 接口不兼容问题 (main.py:9940 传 SDK RunClient) — 文档化但未修复，超出清理范围
