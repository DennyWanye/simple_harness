# Plan Iteration Round 1 - Findings

## Challenger 发现的问题

### P0 - 必须修复

#### F-P0-1: 中间态不可编译
- **位置**: Task 2.2 → Task 3.2 顺序
- **问题**: Task 2.2 删除 `product_composition.py` 等 adapters，但 main.py 中 `_build_product_harness_stack()` 还在导入它们（Task 3.2 才删除）
- **失败场景**: 执行 Task 2.2 后，main.py 导入失败 → 应用无法启动
- **修复方案**: 重新排序 Phase 2 和 Phase 3
  - **新 Phase 2**: 先清理 main.py（删除 `_build_product_harness_stack` 及其导入，内联 `root_run_identity`）
  - **新 Phase 3**: 再删除核心模块（drivers → adapters → bootstrap/runtime → kernel）
- **状态**: ✅ 已修复（已重排序 Phase 2/3）

### P1 - 应该修复

#### F-P1-1: Phase 顺序与依赖图不一致
- **位置**: Task 2.4 依赖 Task 3.1
- **问题**: Task 2.4（删除 kernel.py，Phase 2）依赖 Task 3.1（内联 root_run_identity，Phase 3），但 Phase 编号暗示 Phase 2 先执行
- **修复方案**: 将 Task 3.1 移入 Phase 2（现已完成）
- **状态**: ✅ 已修复（Task 3.1 现为 Task 2.1）

#### F-P1-2: 269 处引用未分类
- **位置**: Task 1.1 输出
- **问题**: Task 1.1 提到 269 处导入引用，但未明确分类各引用由哪个 task 处理
- **修复方案**: 增强 Task 1.1，输出分类统计：
  - main.py 引用 → Task 2.2 处理
  - test 引用 → Task 4.1/4.2 处理
  - 内部 harness 交叉引用 → 随文件删除消失
  - sdk_adapters 引用 → 需评估
  - tools 引用 → 需评估
  - 其他 → 需识别
- **状态**: ⚠️ 待修复

#### F-P1-3: 验证范围不完整
- **位置**: Task 5.3
- **问题**: AC-4 要求验证所有 ingress（text/voice/background），但 Task 5.3 只测试 text
- **修复方案**: 扩展 Task 5.3，明确测试：
  1. Text message ingress
  2. Voice input ingress（如果可测试）
  3. Background task ingress（如果可测试）
  4. 如不可测试，文档化为已知风险
- **状态**: ⚠️ 待修复

### P2 - 建议修复

#### F-P2-1: 测试删除缺少行为覆盖分析
- **位置**: Task 4.1
- **问题**: 按文件名模式删除测试，未分析删除的测试是否覆盖产品特定行为
- **修复方案**: Task 1.3 增加子步骤：提取每个待删除测试的主要断言，交叉检查 SDK 测试覆盖
- **状态**: ⚠️ 待修复（时间允许）

#### F-P2-2: grep 可能遗漏字符串类型注解
- **位置**: Task 1.1
- **问题**: `grep "from deskpet.harness"` 遗漏字符串形式的类型注解、TYPE_CHECKING 导入
- **修复方案**: 增强 Task 1.1 grep：
  - `grep -rn "deskpet\.harness"` （捕获字符串引用）
  - `grep TYPE_CHECKING` + 二次 grep
  - Task 5.1 后运行 mypy 检查前向引用
- **状态**: ⚠️ 待修复（时间允许）

#### F-P2-3: 内联 root_run_identity 维护负担（advisory）
- **位置**: Task 2.1（原 Task 3.1）
- **问题**: 内联到 3 处导致代码重复，后续修改需同步 3 个位置
- **备选方案**: 添加到 `backend/deskpet/execution/contracts.py` 或创建 `run_utils.py`
- **决策**: 接受内联方案（避免为单一函数创建模块），在 Task 2.1 文档化同步要求
- **状态**: ✅ 接受（advisory，不阻断）

## 修正后的任务顺序

### Phase 1: 准备与分析（不变）
- Task 1.1: 完整引用分析 → **需增强分类**
- Task 1.2: 确认 SDK 等价功能
- Task 1.3: 测试文件分类 → **需增强行为分析**

### Phase 2: 清理 main.py 引用（**新顺序，先于删除模块**）
- Task 2.1: 内联 root_run_identity 到调用处（原 Task 3.1）
- Task 2.2: 删除 _build_product_harness_stack 函数及其导入（原 Task 3.2）
- Task 2.3: 删除 _harness_* 全局变量（原 Task 3.3）

### Phase 3: 删除核心模块（**新顺序，后于清理 main.py**）
- Task 3.1: 删除 drivers 目录（原 Task 2.1）
- Task 3.2: 删除旧 adapters（原 Task 2.2）
- Task 3.3: 删除 bootstrap 和 runtime（原 Task 2.3）
- Task 3.4: 删除 kernel.py（原 Task 2.4）
- Task 3.5: 条件删除 contracts.py 和 ports.py（原 Task 2.5）
- Task 3.6: 删除空的 harness 目录（原 Task 2.6）

### Phase 4: 清理测试文件（不变）
- Task 4.1: 删除旧 harness 单元测试
- Task 4.2: 更新集成测试导入

### Phase 5: 最终验证（不变）
- Task 5.1: 全仓导入验证
- Task 5.2: 应用启动测试
- Task 5.3: SDK Runtime 功能测试 → **需扩展覆盖所有 ingress**
- Task 5.4: pytest 完整测试
- Task 5.5: SDK Adapters 验证

## 依赖图（修正后）

```
Phase 1 (准备)
  ├─ Task 1.1 (引用分析 + 分类)
  ├─ Task 1.2 (SDK 等价功能) ← 1.1
  └─ Task 1.3 (测试分类 + 行为分析) ← 1.1

Phase 2 (清理 main.py - 必须先执行)
  ├─ Task 2.1 (内联 root_run_identity) ← 1.2
  ├─ Task 2.2 (删除 _build_product_harness_stack 及导入) ← 2.1
  └─ Task 2.3 (删除全局变量) ← 2.2

Phase 3 (删除核心 - 必须后执行)
  ├─ Task 3.1 (删除 drivers) ← 2.2
  ├─ Task 3.2 (删除 adapters) ← 3.1
  ├─ Task 3.3 (删除基础设施) ← 3.2
  ├─ Task 3.4 (删除 kernel) ← 3.3, 2.1
  ├─ Task 3.5 (条件删除 contracts/ports) ← 3.4
  └─ Task 3.6 (删除空目录) ← 3.5

Phase 4 (清理测试)
  ├─ Task 4.1 (删除单元测试) ← 3.4
  └─ Task 4.2 (更新集成测试) ← 4.1

Phase 5 (最终验证)
  ├─ Task 5.1 (全仓导入验证) ← 2.3, 4.2
  ├─ Task 5.2 (应用启动) ← 5.1
  ├─ Task 5.3 (功能测试 - 所有 ingress) ← 5.2
  ├─ Task 5.4 (pytest) ← 4.2
  └─ Task 5.5 (SDK adapters) ← 5.4
```

## 关键修复点总结

1. **✅ P0 已修复**: Phase 2/3 重排序，main.py 清理先于模块删除
2. **⚠️ P1 待修复**: Task 1.1 需输出分类统计，Task 5.3 需测试所有 ingress
3. **⚠️ P2 可选修复**: 测试行为分析、增强 grep、文档化同步要求

## 下一步行动

1. 更新 plan.md 中的 Task 1.1，添加分类输出要求
2. 更新 plan.md 中的 Task 5.3，添加 voice/background ingress 测试
3. 验证修正后的依赖图没有循环依赖
4. 用户 review 修正后的 plan
