# 验收标准：清理未使用的旧 Harness 代码

## 范围

**当前状态（关键）**：
- SDK v0.1.1 **已经是唯一的生产执行 authority**
- 所有 ingress（text/voice/background）**已经路由到** `_sdk_ingress`（代码证据：`backend/main.py:9544`）
- 旧 `backend/deskpet/harness/` 核心模块仍存在但**未被使用**（`_harness_venue` 被构建但无调用）

**包含：**
- 删除未被使用的旧 harness 核心模块（kernel, bootstrap, runtime, drivers）
- 删除 `main.py` 中未使用的 `_harness_runtime`, `_harness_venue`, `_harness_accepting` 全局变量
- 删除 `_build_product_harness_stack()` 及相关旧 composition 代码
- 清理任何指向已删除模块的导入（如果存在于测试中）
- 保留产品特定的 adapters、tools、skills

**明确不包含：**
- 修改 SDK 本身（SDK v0.1.1 是 immutable）
- 修改 ingress 路由（已经在使用 SDK，不需要切换）
- 创建新功能
- 修改产品特定的 tools/skills/adapters（`backend/deskpet/tools/`, `backend/deskpet/skills/`, `backend/deskpet/sdk_adapters/`）
- 删除仍被测试或其他代码引用的 harness 模块（需先 grep 确认）
- 修改测试框架本身
- 重构非 harness 相关代码

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-1 | 删除未使用的旧 harness 核心模块 | `backend/deskpet/harness/` 下的 `kernel.py`, `bootstrap.py`, `runtime.py`, `drivers/` 目录已删除；代码可编译 | 必须 |
| AC-2 | 删除旧 harness 全局变量和构建逻辑 | `main.py` 中的 `_harness_runtime`, `_harness_venue`, `_harness_accepting` 变量、`_build_product_harness_stack()` 函数及其调用已删除 | 必须 |
| AC-3 | 清理旧 harness 导入 | 所有 `from deskpet.harness.kernel import`, `from deskpet.harness.bootstrap import`, `from deskpet.harness.drivers import` 等对已删除模块的导入已清理；grep 确认无遗留 | 必须 |
| AC-4 | SDK Runtime 继续正常工作 | 应用启动成功，`_sdk_ingress` 正常接收所有 ingress 请求；发送消息能正常处理 | 必须 |
| AC-5 | 测试通过 | 所有现有的 pytest 测试通过（受影响的测试已更新或删除）；`backend/tests/` 下无对已删除模块的引用 | 必须 |
| AC-6 | 产品 adapters 保留且正常工作 | `backend/deskpet/sdk_adapters/` 下的所有 product adapters 保留；它们正确桥接产品服务到 SDK ports | 必须 |
| AC-7 | 删除旧 composition 代码 | `backend/deskpet/harness/adapters/product_composition.py` 等旧 adapter 代码已删除（如果不再被引用） | 必须 |
| AC-8 | 保留必要的 harness 契约 | 如果 `backend/deskpet/harness/contracts.py` 或 `ports.py` 仍被 SDK adapters 或测试引用，则保留；否则删除 | 必须 |

## 非功能 / 边界

**错误态：**
- 应用启动成功，无 ModuleNotFoundError 或 ImportError
- SDK Runtime 继续正常工作（已经在使用中）

**兼容性：**
- 现有的 session 数据库 schema 保持兼容
- 现有的 workflow definitions 继续工作
- 现有的 tool registrations 继续工作
- SDK execution database (`execution-v1.sqlite3`) 继续正常使用

**性能：**
- 应用启动时间不应受影响（删除未使用代码可能略微加快）
- 首次消息响应时间不应受影响（SDK 已在使用中）

**清理范围：**
- 只删除确认未被引用的旧 harness 模块
- 删除前通过 grep 验证无导入引用
- 保留所有被测试或其他代码实际使用的模块

## Assurance contract 摘要

- **Profile**: standard
- **受保护资产**: 
  - 用户会话数据和执行历史
  - 已配置的 LLM provider credentials
  - 现有的 workflow 状态
  - SDK Runtime 的正常运行（已在使用中）
- **可信假设**:
  - SDK v0.1.1 已正确安装且正常工作（当前生产状态）
  - 开发者账户和文件系统可信
  - Python runtime 和系统路径程序可信
  - 旧 harness 模块确实未被任何生产路径使用
- **范围内失败/对手**:
  - 误删仍被测试或其他代码引用的模块
  - 遗留的导入引用导致 ModuleNotFoundError
  - 清理不完整导致死代码残留
- **明确范围外条件**:
  - SDK v0.1.1 本身的 bugs（SDK 被视为可信依赖）
  - 网络故障或 provider API 错误
  - 用户输入的恶意 payload
  - 并发竞态条件（本次是代码删除，不改并发模型）
- **最大可接受影响**: 
  - 误删导致应用无法启动 → 可通过 git revert 恢复
  - 部分测试失败 → 可通过补丁修复或更新测试，不影响生产功能

## 测试义务矩阵（Test Obligation Matrix）

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---------------|------|-------|------|-------------------|-----------------|
| TO-A1 | delivery | AC-1 | — | 确认 `backend/deskpet/harness/{kernel,bootstrap,runtime,drivers/}.py` 已删除 | 直接证明 AC-1 |
| TO-A2 | delivery | AC-2 | — | 确认 `main.py` 中 `_harness_runtime`, `_harness_venue`, `_build_product_harness_stack()` 已删除 | 直接证明 AC-2 |
| TO-A3 | delivery | AC-3 | — | `grep -r "from deskpet.harness.kernel\|from deskpet.harness.bootstrap\|from deskpet.harness.drivers" backend/` 返回空或仅注释 | 直接证明 AC-3 |
| TO-A4 | delivery | AC-4 | — | 启动应用，发送一条文本消息，确认 SDK Runtime 正常处理 | 直接证明 AC-4 主流程 |
| TO-A5 | delivery | AC-5 | — | `cd backend && python -m pytest` 全部通过或仅删除相关测试 | 直接证明 AC-5 |
| TO-A6 | delivery | AC-6 | — | `backend/deskpet/sdk_adapters/*.py` 文件存在；启动日志显示 SDK Runtime 和 adapters 成功初始化 | 直接证明 AC-6 |
| TO-A7 | delivery | AC-7 | — | 确认 `backend/deskpet/harness/adapters/product_composition.py` 已删除（如果无引用） | 直接证明 AC-7 |
| TO-A8 | delivery | AC-8 | — | 确认 `backend/deskpet/harness/contracts.py` 等文件按引用情况保留或删除 | 直接证明 AC-8 |
| TO-R1 | change-risk | — | FAIL-IMPORT | 启动应用并导入所有模块，确认无 ModuleNotFoundError | 本次改动删除模块，可能遗留导入 |
| TO-R2 | change-risk | — | FAIL-REGRESSION | 现有核心功能冒烟测试（发消息、执行 tool、创建 workflow） | 确保 SDK Runtime 继续正常工作 |
| TO-R3 | change-risk | — | FAIL-TEST | 运行完整测试套件，确认无对已删除模块的引用 | 测试可能引用旧模块 |

**类型说明**：
- `delivery`：直接证明 MUST AC，required
- `change-risk`：防范本次改动的受影响范围内风险，有明确风险时 required

**风险适用性判断**：
- ✅ 导入错误：本次删除模块，可能遗留导入引用，required
- ✅ 功能回归：确保 SDK Runtime 继续工作，required
- ✅ 测试失败：测试可能引用已删除模块，required
- ❌ 并发/幂等：本次不改并发模型，不 required
- ❌ LLM 对抗：本次是确定性代码删除，`llm_payload_driven=false`，不 required
- ❌ 冷启动：本次不改初始化依赖，`stateful_init=false`，不 required
- ❌ 路由切换：已经在使用 SDK，本次只删除死代码，不 required

## 完成的定义（DoD 摘要）

- 全部 8 条"必须"条款通过测试
- 所有 delivery 类型的 test obligation (TO-A1 through TO-A8) 都有对应的 PASS testcase
- 所有 change-risk 类型的 test obligation (TO-R1 through TO-R3) 都有对应的 PASS testcase
- 应用能正常启动并处理用户请求（SDK Runtime 继续正常工作）
- 无导入错误或运行时错误
- 所有 pytest 测试通过（或已合理删除/更新对已删除模块的引用）
- 旧 harness 核心模块已完全删除（kernel, bootstrap, runtime, drivers）
- `_harness_runtime`, `_harness_venue` 等未使用的全局变量已删除
- 受影响的 ARCHITECTURE 文档已更新（已在 Phase 0 完成）
