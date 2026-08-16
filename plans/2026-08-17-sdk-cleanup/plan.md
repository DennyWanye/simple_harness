# Plan：清理未使用的旧 Harness 代码

## 主要矛盾

**决定成败的核心问题**：如何在不破坏 SDK Runtime（已在使用中）的前提下，安全地移除大量（269 处导入引用、56 个测试文件）遗留的旧 harness 代码。

**调研深度倾斜点**：
1. **引用分析的完整性**：必须识别所有引用类型（生产代码、测试、类型提示、注释）
2. **测试策略**：56 个测试文件的迁移 vs 删除决策
3. **实用函数的提取**：`root_run_identity` 等简单函数如何处理

## 关联验收标准

- AC-1: 删除未使用的旧 harness 核心模块
- AC-2: 删除旧 harness 全局变量和构建逻辑
- AC-3: 清理旧 harness 导入
- AC-4: SDK Runtime 继续正常工作
- AC-5: 测试通过
- AC-6: 产品 adapters 保留且正常工作
- AC-7: 删除旧 composition 代码
- AC-8: 保留必要的 harness 契约

## 最佳实践调研

### 1. 大规模代码删除的安全策略

**业界实践**：
- Google's "Large Scale Changes"：分阶段删除，每阶段验证
- Stripe's "Code Archaeology"：先分析依赖图，再从叶子节点删除
- Uber's "Deprecation Strategy"：标记 → 迁移 → 删除，每步独立验证

**本项目适配**：
- ✅ 采用分阶段策略：先清理 main.py 引用（Phase 2）→ 再删除核心模块（Phase 3，drivers → adapters → kernel）
- ✅ 从叶子节点开始：drivers → bootstrap/runtime → kernel
- ❌ 不用"标记废弃期"：代码已不在使用，直接删除
- **权衡**：分阶段增加了提交次数，但降低了回滚成本

### 2. 实用函数的处理策略

**业界实践**：
- Martin Fowler "Extract Method"：小函数提取到独立模块
- "Utility Belt Anti-pattern"：避免创建大而全的 utils 模块

**本项目适配**：
- `root_run_identity` 仅 3 行代码，被 3 处使用
- ✅ 选择方案：**内联到调用处**（避免为单一函数创建新模块）
- ❌ 不创建 `backend/deskpet/utils/run_identity.py`（过度工程化）
- **权衡**：代码重复 vs 模块膨胀，选择前者（函数极简）

### 3. 测试迁移 vs 删除决策

**业界实践**：
- Kent Beck "Test Pyramid"：删除重复的低价值测试
- Google "Testing on the Toilet"：测试应验证行为而非实现

**本项目适配**：
- 56 个测试文件测试旧 harness 内部实现
- SDK 已有自己的测试套件（1181 passed）
- ✅ 选择方案：**删除旧 harness 单元测试**（测试实现而非行为）
- ✅ 保留：端到端测试、产品集成测试（测试行为）
- **权衡**：测试覆盖率下降 vs 维护成本，选择后者（SDK 已测试）

### 4. 解剖麻雀：典型删除模式

**选择的典型链路**：`main.py` 中构建旧 harness 的完整路径

```
main.py:_build_product_harness_stack()
  → deskpet.harness.adapters.product_composition.build_product_harness_composition()
    → deskpet.harness.bootstrap.build_harness_runtime()
      → deskpet.harness.kernel.RunKernel.__init__()
      → deskpet.harness.drivers.react.ReActDriver
      → deskpet.harness.drivers.workflow.WorkflowDriver
```

**通用删除模式**：
1. 删除叶子节点（drivers）
2. 删除中间层（bootstrap, runtime）
3. 删除根节点（kernel）
4. 删除调用链入口（_build_product_harness_stack）
5. 删除全局变量（_harness_runtime, _harness_venue）

**依赖：所有步骤前先处理被调用函数的引用**

## Assurance / 信任与失败边界

### Profile 与 Contract
- Profile: **standard**（来自 `assurance-contract.json`）
- 可信假设：SDK v0.1.1 已正常工作，旧 harness 确实未被生产路径使用
- 最大影响：误删导致应用无法启动（可 git revert 恢复）

### 入口链与 Trust Boundary
**生产入口链**（已验证，不改动）：
```
backend/main.py (所有 ingress)
  → _sdk_ingress.open_venue() [信任边界]
  → simple_harness.runtime.kernel.RunKernel [SDK 可信]
```

**待删除的非生产链**（已验证未被调用）：
```
backend/main.py:_build_product_harness_stack()
  → _harness_venue (被构建但未被任何 ingress 调用)
```

### 数据流、持久化与清理
- **不涉及数据迁移**：SDK 已在使用自己的执行数据库
- **不修改 schema**：删除的是代码，不是数据结构
- **无持久化副作用**：旧 harness 模块不持有任何持久化状态

### 范围内失败/对手
- FAIL-IMPORT: 误删被引用的模块（缓解：分阶段 + 每阶段 grep 验证）
- FAIL-LEFTOVER-IMPORT: 遗留导入引用（缓解：全仓 grep + pytest）
- FAIL-SDK-REGRESSION: 误删影响 SDK（缓解：SDK 独立于旧 harness）
- FAIL-TEST: 测试失败（缓解：接受删除旧测试，保留 E2E 测试）

### 明确停止追踪点
**保留边界**（不追踪进去）：
- `backend/deskpet/sdk_adapters/*` - 产品 adapters（已验证工作）
- `backend/deskpet/tools/*` - 产品工具（独立于 harness）
- `backend/deskpet/execution/*` - 执行契约（可能被 SDK 使用）
- `simple_harness.*` - SDK 代码（immutable，不改动）

**删除边界**（追踪到底）：
- `backend/deskpet/harness/kernel.py` 及其所有引用
- `backend/deskpet/harness/bootstrap.py` 及其所有引用
- `backend/deskpet/harness/drivers/*` 及其所有引用
- 所有指向已删除模块的导入语句

## 文件影响清单

| 文件 | 职责 | 本次改动 | 风险等级 |
|------|------|----------|---------|
| `backend/main.py` | 应用入口 | 删除 `_build_product_harness_stack()` 及其调用、删除 3 处 `root_run_identity` 导入并内联 | 高 |
| `backend/deskpet/harness/kernel.py` | 旧执行核心 | **保留（AC-8）** — `venues.py` 导入 `RunKernel`，删除会破坏 companion 链 | — |
| `backend/deskpet/harness/bootstrap.py` | 旧启动逻辑 | **删除整个文件** — 仅被测试引用 | 低 |
| `backend/deskpet/harness/runtime.py` | 旧运行时 | **保留（AC-8）** — kernel.py 导入它 | — |
| `backend/deskpet/harness/drivers/react_boundary.py` | ReAct 边界 | **保留（AC-8）** — `spawn_subagents_tool.py` 惰性导入 `ReactCommandBoundary` | — |
| `backend/deskpet/harness/drivers/react.py` 等其他 drivers | 旧 ReAct 引擎 | **删除**（react.py, react_loop.py, react_recovery.py, react_artifact_completion.py, workflow.py）— 仅测试引用 | 低 |
| `backend/deskpet/harness/adapters/product_composition.py` | 旧组装逻辑 | **删除整个文件** — 仅被 `_build_product_harness_stack` 调用 | 低 |
| `backend/deskpet/harness/adapters/product_profiles.py` | 旧 profile 构建 | **删除整个文件** | 低 |
| `backend/deskpet/harness/adapters/subagent_registry.py` | 旧子代理注册 | **删除整个文件** — 仅测试引用 | 低 |
| `backend/deskpet/harness/adapters/team.py` | 旧团队运行 | **删除整个文件** — 仅测试引用 | 低 |
| `backend/deskpet/harness/adapters/legacy_execution_migration.py` | 旧迁移 | **删除整个文件** — 无外部引用 | 低 |
| `backend/deskpet/harness/reconciler.py` 等引擎文件 | 旧协调器等 | **保留（AC-8）** — kernel.py 导入：admission_launch, attempts, child_runs, child_signal_runtime, execution_profiles, kernel_terminal, live_index, reconciler, router, start_snapshot, tool_executor, user_continuations | — |
| `backend/deskpet/harness/contracts.py` | 产品契约 | **保留（AC-8）** — 6 个产品文件导入 HostExtensionRefV1/HostContext/PreparedRunContextV1 | — |
| `backend/deskpet/harness/ports.py` | 端口定义 | **保留（AC-8）** — contracts.py + spawn_subagents_tool.py 导入 | — |
| `backend/deskpet/harness/projector.py` | 投影器 | **保留（AC-8）** — 3 个产品文件导入 DeliveryDiscarded/SinkRegistration | — |
| `backend/deskpet/harness/context.py` | 上下文工厂 | **保留（AC-8）** — personal_runtime.py 导入 HostContextFactory | — |
| `backend/deskpet/harness/profiles.py` | Profile 注册 | **保留（AC-8）** — orchestration_controls.py 导入 ProfileRegistry | — |
| `backend/deskpet/harness/skill_scope.py` | 技能范围 | **保留（AC-8）** — react_boundary.py 导入 | — |
| `backend/deskpet/harness/adapters/venues.py` | KernelRunClient | **保留（AC-8）** — companion/run_adapter.py 导入 KernelRunClient | — |
| `backend/deskpet/harness/adapters/product_turn_open.py` | 产品回合 | **保留（AC-8）** — venues.py 导入 | — |
| `backend/tests/harness_simplification/` | 旧 harness 测试 | **删除整个目录** | 低（接受测试删除） |
| `backend/tests/test_*harness*.py` 等 | 其他旧 harness 测试 | **删除**（见 Task 4.1） | 低 |
| `backend/deskpet/sdk_adapters/*` | SDK 产品适配器 | **不改动**（保留） | 无 |

## 任务清单（按依赖排序）

### Phase 1: 准备与分析

#### Task 1.1 — 完整引用分析（含分类） [覆盖 AC-3]
- **改动文件**: 无（纯分析）
- **现状**: 已知 269 处导入引用、56 个测试文件
- **修改方式**:
  ```bash
  # 1. 生成完整引用报告（包含字符串类型注解、TYPE_CHECKING 块）
  grep -rn "deskpet\.harness" backend/ --include="*.py" > /tmp/harness_refs.txt
  
  # 2. 分类统计（每类由哪个 Task 处理）
  grep "backend/main.py" /tmp/harness_refs.txt > /tmp/refs_main.txt          # → Phase 2 Tasks 2.1-2.3 处理
  grep "backend/tests/"  /tmp/harness_refs.txt > /tmp/refs_tests.txt          # → Phase 4 Tasks 4.1-4.2 处理
  grep "deskpet/harness/" /tmp/harness_refs.txt > /tmp/refs_internal.txt      # → 随模块删除自消失
  grep "deskpet/sdk_adapters/" /tmp/harness_refs.txt > /tmp/refs_sdk_adapters.txt  # → 需手动评估
  grep "deskpet/tools/" /tmp/harness_refs.txt > /tmp/refs_tools.txt           # → 需手动评估
  grep -v "backend/main.py\|backend/tests/\|deskpet/harness/\|deskpet/sdk_adapters/\|deskpet/tools/" \
       /tmp/harness_refs.txt > /tmp/refs_other.txt                            # → 识别并分配 Task
  
  # 3. 打印分类汇总
  echo "=== 引用分类汇总 ==="
  echo "main.py:          $(wc -l < /tmp/refs_main.txt) 处 → Phase 2 Tasks 2.1-2.3"
  echo "tests/:           $(wc -l < /tmp/refs_tests.txt) 处 → Phase 4 Tasks 4.1-4.2"
  echo "harness 内部:     $(wc -l < /tmp/refs_internal.txt) 处 → 随文件删除自消失"
  echo "sdk_adapters/:    $(wc -l < /tmp/refs_sdk_adapters.txt) 处 → Task 1.4 评估并修复"
  echo "tools/:           $(wc -l < /tmp/refs_tools.txt) 处 → Task 1.4 评估并修复"
  echo "其他:             $(wc -l < /tmp/refs_other.txt) 处 → 需分配 Task"
  ```
- **验证**:
  - `/tmp/refs_other.txt` 必须为空或每行都分配到对应 Task
  - `/tmp/refs_sdk_adapters.txt` 和 `/tmp/refs_tools.txt` 进入 Task 1.4 处理
- **依赖**: 无
- **输出**: `/tmp/harness_refs.txt`（完整引用清单）+ 分类汇总输出

#### Task 1.2 — 确认 SDK 等价功能 [覆盖 AC-4]
- **改动文件**: 无（纯验证）
- **现状**: `root_run_identity` 被 3 处使用，函数仅 3 行代码
- **修改方式**:
  1. 读取 `root_run_identity` 函数实现：
     ```python
     def root_run_identity(session_id: str, request_id: str, turn_id: str) -> tuple[str, RunRef]:
         key = root_idempotency_key(session_id, request_id, turn_id)
         return key, RunRef(uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{key}").hex, session_id)
     ```
  2. 确认 `root_idempotency_key` 和 `RunRef` 来自 `deskpet.execution.contracts`（不在 harness 内）
  3. 决策：**内联到 3 个调用处**（避免创建新模块）
- **验证**: 确认内联后功能等价
- **依赖**: Task 1.1
- **输出**: 内联策略决定

#### Task 1.3 — 测试文件分类 [覆盖 AC-5]
- **改动文件**: 无（纯分析）
- **现状**: 56 个测试文件引用旧 harness
- **修改方式**:
  ```bash
  # 分类测试文件
  find backend/tests -name "*.py" -exec grep -l "from deskpet.harness" {} \; > /tmp/test_files.txt
  
  # 区分单元测试 vs 集成测试
  grep "test_harness\|test_kernel\|test_bootstrap\|test_driver" /tmp/test_files.txt > /tmp/unit_tests.txt
  grep -v "test_harness\|test_kernel\|test_bootstrap\|test_driver" /tmp/test_files.txt > /tmp/integration_tests.txt
  ```
- **验证**: 确认分类准确
- **依赖**: Task 1.1
- **输出**: 
  - `/tmp/unit_tests.txt`（删除）
  - `/tmp/integration_tests.txt`（评估迁移）

#### Task 1.4 — 评估并修复 sdk_adapters / tools 中的 harness 引用 [覆盖 AC-3]
- **改动文件**: 视结果而定（可能修改 `backend/deskpet/sdk_adapters/*.py` 或 `backend/deskpet/tools/*.py`）
- **现状**: Task 1.1 分类后，`/tmp/refs_sdk_adapters.txt` 和 `/tmp/refs_tools.txt` 中可能有引用
- **修改方式**:
  ```bash
  # 查看具体引用
  cat /tmp/refs_sdk_adapters.txt
  cat /tmp/refs_tools.txt
  ```
  对每个引用，决策：
  - **类型注解 / HostContext / 契约类型**：检查 SDK 或 `deskpet.execution.contracts` 是否有等价类型 → 替换导入
  - **实现调用（调用旧 harness 内部函数）**：❌ 不应存在（sdk_adapters 应已切换到 SDK），需重新评估
  - **仅字符串引用 / 注释**：直接删除
- **验证**:
  ```bash
  # 执行后确认无遗留
  grep -r "deskpet\.harness" backend/deskpet/sdk_adapters/ backend/deskpet/tools/ \
    --include="*.py" | grep -v "\.pyc" | wc -l
  # 预期: 0
  ```
- **依赖**: Task 1.1（须有分类汇总输出）
- **风险**: 低（sdk_adapters 应已使用 SDK，引用应为类型注解级别）

### Phase 2: 清理 main.py 引用（必须先于删除模块）

#### Task 2.1 — 内联 root_run_identity 到调用处 [覆盖 AC-2, AC-3]
- **改动文件**: `backend/main.py`
- **现状**: 3 处导入并调用 `root_run_identity`
- **修改方式**:

**位置 1**（第一处使用，约 main.py:9142）:
```python
# 旧代码
from deskpet.harness.kernel import root_run_identity
# ... 
"root_run_id": root_run_identity(session_id, request_id, turn_id),

# 新代码（内联）
from deskpet.execution.contracts import root_idempotency_key, RunRef
import uuid
# ...
key = root_idempotency_key(session_id, request_id, turn_id)
root_ref = RunRef(uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{key}").hex, session_id)
"root_run_id": (key, root_ref),
```

**位置 2 和 3**（类似处理）

- **验证**:
  ```bash
  # 确认无 root_run_identity 导入
  grep "from deskpet.harness.kernel import root_run_identity" backend/main.py | wc -l
  # 预期: 0
  
  # 确认应用启动
  cd backend && python -c "import main; print('Import OK')"
  ```
- **依赖**: Task 1.2
- **风险**: 中等（修改 main.py 主入口）

#### Task 2.2 — 删除 _build_product_harness_stack 函数及导入 [覆盖 AC-2]
- **改动文件**: `backend/main.py`
- **现状**: `_build_product_harness_stack()` 函数及其调用仍存在
- **修改方式**:
  1. 先删除所有 harness adapters 导入（约 main.py 开头）:
     ```python
     # 删除这些导入行
     from deskpet.harness.adapters.product_composition import build_product_harness_composition
     from deskpet.harness.adapters.product_profiles import build_product_profile_registry
     from deskpet.harness.projector import SinkRegistration
     from deskpet.harness.contracts import HostContext
     from deskpet.harness.adapters.venues import ProductVenueRunResult
     from deskpet.harness.adapters.legacy_execution_migration import ...
     ```
  2. 删除 `async def _build_product_harness_stack(...)` 函数定义（约 main.py:7900，约 200 行）
  3. 删除调用处（约 main.py:9795-9809）:
     ```python
     # 删除整个调用块
     # stack = await _build_product_harness_stack(state.generation)
     # _harness_runtime, _harness_venue = stack
     # ...
     ```

- **验证**:
  ```bash
  # 确认函数已删除
  grep -n "def _build_product_harness_stack" backend/main.py | wc -l
  # 预期: 0
  
  # 确认导入已删除
  grep "from deskpet.harness" backend/main.py | wc -l
  # 预期: 0
  
  # 确认可导入
  cd backend && python -c "import main; print('Import OK')"
  ```
- **依赖**: Task 2.1
- **风险**: 中等

#### Task 2.3 — 删除 _harness_* 全局变量 [覆盖 AC-2]
- **改动文件**: `backend/main.py`
- **现状**: 全局变量 `_harness_runtime`, `_harness_venue`, `_harness_accepting` 已定义但未使用
- **修改方式**:
  1. 删除全局变量声明（约 main.py:7895-7896）:
     ```python
     # 删除这些行
     _harness_runtime = None
     _harness_venue = None
     ```
  2. 删除 `_harness_accepting` 相关代码（约 main.py:6372, 9759）
  3. 删除关闭逻辑中的 harness 清理（约 main.py:6440-6447）:
     ```python
     # 删除这段
     if _harness_runtime is not None:
         try:
             await _harness_runtime.close(timeout=5.0)
         except Exception:
             pass
         finally:
             _harness_runtime = None
             _harness_venue = None
     ```

- **验证**:
  ```bash
  # 确认变量已删除
  grep -n "_harness_runtime\|_harness_venue\|_harness_accepting" backend/main.py | wc -l
  # 预期: 0
  ```
- **依赖**: Task 2.2
- **风险**: 低（这些变量确实未被使用）

### Phase 3: 删除核心模块（从叶子到根）

#### Task 3.1 — 删除 drivers 目录 [覆盖 AC-1, AC-4]
- **改动文件**: 
  - `backend/deskpet/harness/drivers/react.py` **删除**
  - `backend/deskpet/harness/drivers/react_loop.py` **删除**
  - `backend/deskpet/harness/drivers/react_boundary.py` **删除**
  - `backend/deskpet/harness/drivers/react_recovery.py` **删除**
  - `backend/deskpet/harness/drivers/react_artifact_completion.py` **删除**
  - `backend/deskpet/harness/drivers/workflow.py` **删除**
  - `backend/deskpet/harness/drivers/__init__.py` **删除**
- **现状**: drivers 目录包含旧 ReAct 和 Workflow 实现，已被 SDK 替代
- **修改方式**:
  ```bash
  rm -rf backend/deskpet/harness/drivers/
  ```
- **验证**:
  ```bash
  # 确认删除
  test ! -d backend/deskpet/harness/drivers && echo "PASS" || echo "FAIL"
  
  # 确认无遗留导入（harness 内部交叉引用随文件删除自消失）
  grep -r "from deskpet.harness.drivers" backend/ --include="*.py" | grep -v "\.pyc" | wc -l
  # 预期输出: 0
  ```
- **依赖**: Task 2.2（确保 main.py 已清理相关导入）
- **风险**: 中等（需先确认 main.py 中的 harness 导入在 Task 2.2 已清除）

#### Task 3.2 — 删除旧 adapters [覆盖 AC-1, AC-7]
- **改动文件**:
  - `backend/deskpet/harness/adapters/product_composition.py` **删除**
  - `backend/deskpet/harness/adapters/product_profiles.py` **删除**
  - `backend/deskpet/harness/adapters/legacy_execution_migration.py` **删除**（如存在）
  - `backend/deskpet/harness/adapters/venues.py` **删除**
- **现状**: 这些 adapters 仅被 `_build_product_harness_stack()` 使用
- **修改方式**:
  ```bash
  rm -f backend/deskpet/harness/adapters/product_composition.py
  rm -f backend/deskpet/harness/adapters/product_profiles.py
  rm -f backend/deskpet/harness/adapters/legacy_execution_migration.py
  rm -f backend/deskpet/harness/adapters/venues.py
  ```
- **验证**:
  ```bash
  # 确认文件已删除
  for f in product_composition.py product_profiles.py venues.py; do
    test ! -f backend/deskpet/harness/adapters/$f && echo "$f: PASS" || echo "$f: FAIL"
  done
  ```
- **依赖**: Task 3.1
- **风险**: 低（仅被 main.py 的一个函数使用）

#### Task 3.3 — 删除 bootstrap 和 runtime [覆盖 AC-1]
- **改动文件**:
  - `backend/deskpet/harness/bootstrap.py` **删除**
  - `backend/deskpet/harness/runtime.py` **删除**
  - `backend/deskpet/harness/reconciler.py` **删除**
  - `backend/deskpet/harness/admission_launch.py` **删除**
  - `backend/deskpet/harness/live_index.py` **删除**
  - `backend/deskpet/harness/kernel_terminal.py` **删除**
  - `backend/deskpet/harness/child_runs.py` **删除**
  - `backend/deskpet/harness/child_signal_runtime.py` **删除**
  - `backend/deskpet/harness/context.py` **删除**
  - `backend/deskpet/harness/attempts.py` **删除**
  - `backend/deskpet/harness/execution_profiles.py` **删除**
  - `backend/deskpet/harness/profiles.py` **删除**
  - `backend/deskpet/harness/projector.py` **删除**
  - `backend/deskpet/harness/router.py` **删除**
  - `backend/deskpet/harness/tool_executor.py` **删除**
  - `backend/deskpet/harness/user_continuations.py` **删除**
  - `backend/deskpet/harness/start_snapshot.py` **删除**
  - `backend/deskpet/harness/skill_scope.py` **删除**
- **现状**: 这些模块组成旧 harness 的基础设施层
- **修改方式**:
  ```bash
  cd backend/deskpet/harness
  rm -f bootstrap.py runtime.py reconciler.py admission_launch.py live_index.py \
        kernel_terminal.py child_runs.py child_signal_runtime.py context.py \
        attempts.py execution_profiles.py profiles.py projector.py router.py \
        tool_executor.py user_continuations.py start_snapshot.py skill_scope.py
  ```
- **验证**:
  ```bash
  # 确认关键文件已删除
  for f in bootstrap.py runtime.py reconciler.py kernel_terminal.py; do
    test ! -f backend/deskpet/harness/$f && echo "$f: PASS" || echo "$f: FAIL"
  done
  ```
- **依赖**: Task 3.2
- **风险**: 中等（被 kernel.py 引用，需在 Task 3.4 一起处理）

#### Task 3.4 — 删除 kernel.py [覆盖 AC-1]
- **改动文件**:
  - `backend/deskpet/harness/kernel.py` **删除**
- **现状**: kernel.py 是旧 harness 的根节点，包含 RunKernel 和 root_run_identity
- **前置条件**: 必须先处理 main.py 中的 3 处 root_run_identity 引用（Task 2.1）
- **修改方式**:
  ```bash
  rm -f backend/deskpet/harness/kernel.py
  ```
- **验证**:
  ```bash
  test ! -f backend/deskpet/harness/kernel.py && echo "PASS" || echo "FAIL"
  ```
- **依赖**: Task 3.3, Task 2.1（必须先内联 root_run_identity）
- **风险**: 高（如果 root_run_identity 未内联会导致 ImportError）

#### Task 3.5 — 条件删除 contracts.py 和 ports.py [覆盖 AC-8]
- **改动文件**:
  - `backend/deskpet/harness/contracts.py` **条件删除**
  - `backend/deskpet/harness/ports.py` **条件删除**
- **现状**: 这两个文件可能被 SDK adapters 或其他产品代码引用
- **修改方式**:
  ```bash
  # 检查外部引用（排除 harness 内部）
  grep -r "from deskpet.harness.contracts" backend/ --include="*.py" | \
    grep -v "deskpet/harness/" | grep -v "\.pyc" > /tmp/contracts_external_refs.txt
  
  grep -r "from deskpet.harness.ports" backend/ --include="*.py" | \
    grep -v "deskpet/harness/" | grep -v "\.pyc" > /tmp/ports_external_refs.txt
  
  # 如果无外部引用，删除
  if [ ! -s /tmp/contracts_external_refs.txt ]; then
    rm -f backend/deskpet/harness/contracts.py
  fi
  
  if [ ! -s /tmp/ports_external_refs.txt ]; then
    rm -f backend/deskpet/harness/ports.py
  fi
  ```
- **验证**:
  ```bash
  # 如果删除了，验证无遗留引用
  if [ ! -f backend/deskpet/harness/contracts.py ]; then
    grep -r "from deskpet.harness.contracts" backend/ --include="*.py" | wc -l
    # 预期: 0
  fi
  ```
- **依赖**: Task 3.4
- **风险**: 低（条件删除，有引用则保留）

#### Task 3.6 — 删除空的 harness 目录 [覆盖 AC-1]
- **改动文件**:
  - `backend/deskpet/harness/` **删除目录**（如果为空）
- **现状**: 删除所有模块后，harness 目录可能只剩 __init__.py 和 __pycache__
- **修改方式**:
  ```bash
  # 删除 __pycache__
  rm -rf backend/deskpet/harness/__pycache__
  rm -rf backend/deskpet/harness/adapters/__pycache__
  
  # 如果只剩 __init__.py，删除目录
  cd backend/deskpet/harness
  if [ "$(ls -A | grep -v __pycache__)" = "__init__.py" ] || [ -z "$(ls -A | grep -v __pycache__)" ]; then
    cd ../..
    rm -rf backend/deskpet/harness
  fi
  ```
- **验证**:
  ```bash
  # 确认目录已删除或仅保留必要文件
  if [ ! -d backend/deskpet/harness ]; then
    echo "PASS: harness directory deleted"
  else
    echo "INFO: harness directory retained with:"
    ls -la backend/deskpet/harness/
  fi
  ```
- **依赖**: Task 3.5
- **风险**: 无

### Phase 4: 清理测试文件

#### Task 4.1 — 删除旧 harness 单元测试 [覆盖 AC-5]
- **改动文件**: 约 40-50 个测试文件
- **现状**: 56 个测试文件引用旧 harness，其中约 40+ 个是单元测试
- **修改方式**:
  ```bash
  # 删除明确测试旧 harness 内部实现的测试
  cd backend/tests
  rm -f test_harness_*.py
  rm -f test_run_kernel.py
  rm -f test_kernel_*.py
  rm -f test_bootstrap.py
  rm -f test_workflow_driver.py
  rm -f test_react_driver.py
  rm -f test_react_loop.py
  rm -f test_agent_loop_*.py
  rm -f harness_simplification/test_*.py
  ```

- **验证**:
  ```bash
  # 确认测试文件已删除
  find backend/tests -name "*harness*.py" -o -name "*kernel*.py" | wc -l
  # 预期: 0 或少量（如果是集成测试）
  
  # 运行剩余测试
  cd backend && python -m pytest -v
  # 预期: 通过或跳过，无 ImportError
  ```
- **依赖**: Task 3.4（先删除 kernel.py）
- **风险**: 低（接受删除旧测试）

#### Task 4.2 — 更新集成测试导入 [覆盖 AC-5]
- **改动文件**: 约 10-15 个集成测试文件
- **现状**: 部分集成测试可能引用旧 harness 的类型或实用函数
- **修改方式**:
  1. 识别集成测试中的 harness 导入
  2. 评估每个导入：
     - 如果是类型提示 → 删除或改用 SDK 类型
     - 如果是实用函数 → 内联或迁移到 SDK
     - 如果无法迁移 → 删除该测试或标记为 skip
  
  ```python
  # 示例：更新类型提示
  # 旧代码
  from deskpet.harness.contracts import RunRef
  
  # 新代码
  from deskpet.execution.contracts import RunRef
  # 或
  from simple_harness.contracts import RunRef  # 如果 SDK 有
  ```

- **验证**:
  ```bash
  # 运行更新后的测试
  cd backend && python -m pytest tests/ -v -k "not harness"
  # 预期: 全部通过
  ```
- **依赖**: Task 4.1
- **风险**: 中等（需要逐个评估）

### Phase 5: 最终验证

#### Task 5.1 — 全仓导入验证 [覆盖 AC-3]
- **改动文件**: 无（纯验证）
- **修改方式**:
  ```bash
  # 确认无遗留 harness 导入
  grep -r "from deskpet.harness" backend/ --include="*.py" | grep -v "\.pyc" | wc -l
  # 预期: 0
  
  grep -r "import deskpet.harness" backend/ --include="*.py" | grep -v "\.pyc" | wc -l
  # 预期: 0
  ```
- **验证**: 输出为 0
- **依赖**: Task 3.4, Task 4.2
- **风险**: 无

#### Task 5.2 — 应用启动测试 [覆盖 AC-4]
- **改动文件**: 无（纯验证）
- **修改方式**:
  ```bash
  # 启动应用
  cd /Users/denny/projects/simple_harness
  ./scripts/dev.sh
  
  # 手工验证：
  # 1. 应用窗口打开
  # 2. 无启动错误
  # 3. 日志显示 SDK Runtime 初始化成功
  ```
- **验证**: 应用正常启动，无 ModuleNotFoundError
- **依赖**: Task 5.1
- **风险**: 无

#### Task 5.3 — SDK Runtime 功能测试（全 ingress）[覆盖 AC-4]
- **改动文件**: 无（纯验证）
- **修改方式**:
  1. **Text ingress**（必须测试）：发送一条文本消息，确认正常响应
  2. **Tool call**（必须测试）：执行一个工具（如搜索/文件读取），确认 tool 调用完整
  3. **Workflow ingress**（必须测试，如果可手动触发）：创建一个 workflow，确认完整执行
  4. **Voice ingress**（如不可自动化，文档化为已知风险）：
     - 可测试 → 发送语音输入，确认处理
     - 不可测试 → 在 baseline.md 记录"voice ingress: 未验证，已知风险"
  5. **Background task ingress**（如不可自动化，文档化为已知风险）：
     - 可测试 → 触发后台任务，确认执行
     - 不可测试 → 在 baseline.md 记录"background ingress: 未验证，已知风险"
  
- **验证标准**:
  - ✅ Text + tool 功能正常（强制）
  - ✅ 或 ⚠️ Workflow / Voice / Background（可文档化风险代替测试）
  - ✅ 日志中无 `deskpet.harness` 相关 ImportError 或 AttributeError
- **依赖**: Task 5.2
- **风险**: 无（已切换到 SDK，此步骤是验证确认）

#### Task 5.4 — pytest 完整测试 [覆盖 AC-5]
- **改动文件**: 无（纯验证）
- **修改方式**:
  ```bash
  cd backend
  python -m pytest -v
  ```
- **验证**: 所有测试通过或合理跳过，无 ImportError
- **依赖**: Task 4.2
- **风险**: 无

#### Task 5.5 — SDK Adapters 验证 [覆盖 AC-6]
- **改动文件**: 无（纯验证）
- **修改方式**:
  ```bash
  # 确认 SDK adapters 仍存在
  ls -la backend/deskpet/sdk_adapters/
  
  # 运行 SDK adapters 测试
  cd backend
  python -m pytest tests/sdk_adapters/ -v
  ```
- **验证**: 
  - SDK adapters 文件完整
  - 测试通过（约 90 passed）
- **依赖**: Task 5.4
- **风险**: 无

## 依赖图

```
Phase 1 (准备)
  ├─ Task 1.1 (引用分析 + 分类输出)
  ├─ Task 1.2 (SDK 等价功能确认) ← 1.1
  ├─ Task 1.3 (测试文件分类) ← 1.1
  └─ Task 1.4 (评估并修复 sdk_adapters/tools 引用) ← 1.1

Phase 2 (清理 main.py — 必须先于删除模块)
  ├─ Task 2.1 (内联 root_run_identity) ← 1.2
  ├─ Task 2.2 (删除 _build_product_harness_stack 及其导入) ← 2.1
  └─ Task 2.3 (删除 _harness_* 全局变量) ← 2.2

Phase 3 (删除核心模块 — 必须后于 Phase 2)
  ├─ Task 3.1 (删除 drivers 目录) ← 2.2
  ├─ Task 3.2 (删除旧 adapters) ← 3.1
  ├─ Task 3.3 (删除 bootstrap/runtime/reconciler 等) ← 3.2
  ├─ Task 3.4 (删除 kernel.py) ← 3.3, 2.1
  ├─ Task 3.5 (条件删除 contracts.py/ports.py) ← 3.4
  └─ Task 3.6 (删除空 harness 目录) ← 3.5

Phase 4 (清理测试)
  ├─ Task 4.1 (删除旧 harness 单元测试) ← 3.4
  └─ Task 4.2 (更新集成测试导入) ← 4.1

Phase 5 (最终验证)
  ├─ Task 5.1 (全仓导入验证) ← 2.3, 1.4, 4.2
  ├─ Task 5.2 (应用启动测试) ← 5.1
  ├─ Task 5.3 (功能测试 — 全 ingress) ← 5.2
  ├─ Task 5.4 (pytest 完整测试) ← 4.2
  └─ Task 5.5 (SDK adapters 验证) ← 5.4
```

## 关键决策点

### 决策 1: root_run_identity 处理方式
- **备选方案**:
  1. 创建 `backend/deskpet/utils/run_identity.py`
  2. 内联到 3 个调用处
  3. 寻找 SDK 等价功能
- **选择**: 方案 2（内联）
- **理由**: 函数仅 3 行，创建新模块过度工程化；SDK 无直接等价物
- **权衡**: 代码重复（3 处） vs 模块复杂度，选择前者

### 决策 2: 测试策略
- **备选方案**:
  1. 迁移所有测试到 SDK
  2. 删除所有旧 harness 测试
  3. 保留集成测试，删除单元测试
- **选择**: 方案 3（保留集成测试）
- **理由**: SDK 已有完整测试（1181 passed），旧单元测试测试的是实现而非行为
- **权衡**: 测试覆盖率 vs 维护成本，选择后者

### 决策 3: 删除顺序
- **备选方案**:
  1. 自顶向下（先清理 main.py → 再删模块，叶子到根）
  2. 自底向上（先删叶子模块 → 再清理 main.py）
  3. 一次性删除所有
- **选择**: 方案 1（先清理 main.py / Phase 2，再删模块 / Phase 3）
- **理由**: 方案 2 会产生中间态不可编译状态（删除 adapters 后 main.py 仍导入它们）；方案 1 每步可独立验证，不出现编译失败
- **权衡**: 提交次数 vs 中间态安全，选择降低风险

## 回滚计划

每个 Phase 完成后提交一次 git commit，便于回滚：

- Phase 1 完成 → `git commit -m "refactor: analyze harness references"`
- Phase 2 完成 → `git commit -m "refactor: delete unused harness core modules"`
- Phase 3 完成 → `git commit -m "refactor: clean up main.py harness references"`
- Phase 4 完成 → `git commit -m "test: clean up harness tests"`
- Phase 5 完成 → `git commit -m "chore: verify harness cleanup complete"`

如需回滚：
```bash
git revert <commit-hash>
```

## 预期结果

完成后：
- ✅ `backend/deskpet/harness/` 目录已删除或仅保留必要契约文件
- ✅ `backend/main.py` 中无任何 `deskpet.harness` 导入
- ✅ 全仓搜索 `from deskpet.harness` 返回 0 结果
- ✅ 应用正常启动，SDK Runtime 正常工作
- ✅ pytest 测试通过（删除了约 40+ 个旧测试）
- ✅ SDK adapters 完整保留且工作正常

## 估算

- **代码行数**: 删除约 5000-7000 行代码（37 个文件 + 测试）
- **工时**: 约 2-3 小时（分阶段验证）
- **风险窗口**: 每个 Phase 约 20-30 分钟
