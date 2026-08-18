# 验收标准：SDK Runtime 迁移 - Agent 执行链修复

## 范围

### 包含：
- 修复 `backend/main.py` 中的 NotImplementedError（第 9308 行）
- **实现核心协调逻辑**（替代旧的 ProductVenueRunAdapter）：
  - 在 main.py 中实现 `_execute_sdk_run()` 函数
  - 协调 TurnPreparer → SdkRuntimeIngress.start() → RunPresenter 的完整流程
  - 处理 Run 状态查询和事件推送
- **完成两个未完成的 Harness 适配器**：
  - 实现 `_DeliverySink.deliver()` 方法（desktop_runtime.py:133，当前为空实现）
  - 实现 `ProductDeliveryAdapter` 类（delivery.py，标记为 TODO T6.1，完全未实现）
- 使用 ReActDriver 模式执行 Agent 任务
- 保持与前端 WebSocket 协议的兼容性
- 保持与历史 Run 数据的向后兼容

**架构说明**：
旧架构：`ProductVenueRunAdapter.open()` → `KernelRunClient.start()` → `observe()` 事件流
新架构：`_execute_sdk_run()` → `SdkRuntimeIngress.start()` → `DeliveryDispatcher` 推送

### 明确不包含：
- Workflow 模式的注册和配置（后续任务）
- 旧 ProductVenueRunAdapter 的恢复（已删除，不回退）
- UI 层面的改动（纯后端修复）
- 新功能添加（仅修复已有执行链）

---

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-1 | Agent 执行链启动 + 两个 Harness 适配器实现 | 用户通过 WebSocket 发送消息时，Agent 能够通过 SDK Runtime 启动 Run，不再抛出 NotImplementedError。实现核心协调逻辑（替代旧 ProductVenueRunAdapter），完成 _DeliverySink 和 ProductDeliveryAdapter 两个适配器，使事件能够正确推送到 SessionDB 和前端 | 必须 |
| AC-2 | ReAct 循环执行 | Agent 能够执行完整的 ReAct 循环（思考→行动→观察），调用工具并返回结果 | 必须 |
| AC-3 | 事件流推送 | Agent 执行过程中的事件（RUN_STARTED, RUN_PROGRESS, RUN_COMPLETED）能够通过 DeliveryDispatcher 推送到前端 | 必须 |
| AC-4 | 错误处理 | Agent 执行失败时，能够正确返回错误状态（RUN_FAILED）并推送错误信息到前端 | 必须 |
| AC-5 | Run 状态查询 | 能够通过 `ingress.query(run_id)` 查询 Run 的最终状态（COMPLETED/FAILED/CANCELLED） | 必须 |
| AC-6 | 历史数据兼容 | 修复后的代码能够读取历史 Session 数据，不会因为数据格式变化导致崩溃 | 必须 |

---

## 非功能 / 边界

### 错误态：
- SDK Runtime 未就绪时，返回明确的错误信息（SdkRuntimeNotReady）
- Run 执行超时（默认 900 秒），返回超时错误
- LLM 调用失败时，Agent 执行链应正确失败并记录错误

### 幂等：
- 同一 (session_id, request_id, turn_id) 生成相同的 RunId（SHA-256 哈希）
- 重复调用 `ingress.start()` 对同一 RunId 是幂等的

### 性能：
- Agent 启动延迟 < 500ms（从 WebSocket 接收到调用 ingress.start()）
- Run 完成后状态查询延迟 < 100ms

### 兼容：
- 与前端 WebSocket 协议保持兼容（消息格式不变）
- 与 SessionDB 数据格式保持兼容
- 与现有工具注册系统保持兼容

---

## Assurance Contract 摘要

- **Profile**: standard
- **受保护资产**:
  - ASSET-1: 用户 Session 数据完整性
  - ASSET-2: Agent 执行状态一致性
  - ASSET-3: 前端 WebSocket 连接可用性
- **可信假设**:
  - TRUST-1: SdkRuntimeIngress 已正确初始化并 open
  - TRUST-2: DeliveryDispatcher 后台任务正常运行
  - TRUST-3: SessionDB 数据库连接正常
  - TRUST-4: LLM provider 配置正确
- **范围内失败**:
  - FAIL-1: Agent 执行链抛出 NotImplementedError（当前 bug）
  - FAIL-2: SDK Runtime 调用参数错误
  - FAIL-3: Run 状态无法查询
  - FAIL-4: 事件流推送失败导致前端无响应
  - FAIL-5: 历史数据读取失败导致系统崩溃
- **范围内对手**: 无（标准开发环境，非对抗场景）
- **明确范围外条件**:
  - OOS-1: SDK Runtime 本身的 bug（属于 SDK 层问题）
  - OOS-2: LLM provider 服务不可用（外部依赖）
  - OOS-3: 数据库磁盘空间耗尽
  - OOS-4: 网络连接中断
- **最大可接受影响**: Agent 执行链单次请求失败，不影响其他 Session，系统可恢复

---

## 测试场景矩阵

**判定：此功能为确定性后端 API 修复，不涉及输入语义敏感（非 LLM 对话内容验证），也不涉及 LLM 载荷驱动 UI 状态机。**

因此：
- `input_sensitive`: false（修复执行链基础设施，不验证 Agent 对话质量）
- `llm_payload_driven`: false（事件推送到前端，但前端状态机不在本次范围内）
- `stateful_init`: false（SDK Runtime 已在应用启动时初始化，不涉及冷启动场景）

删除测试场景矩阵（仅适用于输入语义敏感功能）。

---

## 测试义务矩阵（Test Obligation Matrix）

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---------------|------|-------|------|-------------------|-----------------|
| TO-A1 | delivery | AC-1 | — | 发送 WebSocket 消息，验证 Agent 启动不抛出 NotImplementedError，且 _DeliverySink.deliver() 被正确调用 | 直接证明核心 bug 和 Delivery Sink 已完成 |
| TO-A2 | delivery | AC-2 | — | 调用搜索工具，验证 ReAct 循环执行完成 | 证明 Agent 能够执行工具调用 |
| TO-A3 | delivery | AC-3 | — | 监听 WebSocket 事件，验证收到 RUN_STARTED/RUN_PROGRESS/RUN_COMPLETED | 证明事件流正常推送 |
| TO-A4 | delivery | AC-4 | — | 触发执行错误（如工具调用失败），验证返回 RUN_FAILED | 证明错误处理正确 |
| TO-A5 | delivery | AC-5 | — | 调用 `ingress.query(run_id)`，验证返回正确的 RunState | 证明状态查询功能 |
| TO-A6 | delivery | AC-6 | — | 读取历史 Session，验证不崩溃 | 证明向后兼容性 |
| TO-R1 | change-risk | — | FAIL-ROUTE | 验证所有 WebSocket 端点可达（/control, /chat） | 本次改动涉及核心执行链入口 |
| TO-R2 | change-risk | — | FAIL-REGRESSION | 验证历史功能（Session 列表、技能列表、设置页）正常 | 修改共享执行链可能影响全局 |

**类型说明**：
- `delivery`：直接证明 MUST AC，required
- `change-risk`：防范本次改动的受影响范围内风险，有明确风险时 required
- `exploratory`：探索性测试，不 required

**风险适用性判断**：
- 并发/幂等：本次改动不涉及新的共享可变状态
- 边界值：AC 中未声明需要边界值测试
- LLM 对抗：`llm_payload_driven=false`
- 冷启动：`stateful_init=false`
- 性能：AC 中已包含性能要求（TO-A1 隐含启动延迟）

---

## 完成的定义（DoD 摘要）

- ✅ 全部 6 条 MUST 验收条款通过测试
- ✅ 所有 delivery 类型的 test obligation（TO-A1 ~ TO-A6）都有对应的 PASS testcase
- ✅ 所有 change-risk 类型的 test obligation（TO-R1, TO-R2）都有对应的 PASS testcase
- ✅ 无回归（历史功能正常）
- ✅ 代码已提交（git status 干净）
- ✅ ARCHITECTURE 文档已更新（记录 SDK Runtime 迁移完成状态）
