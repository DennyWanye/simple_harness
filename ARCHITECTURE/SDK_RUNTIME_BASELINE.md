最后更新：2026-09-07。C08-01/06/11/18保留旧USER+assistant摘要实际main及wrongassistant共5新控首批5PASS38.78s：原job APPLIED/IDLE，公开suppression后两history隐藏，重开生产authority后下一physical请求无旧内容。PG84053五child自然清空，非真实模型/原生/rolling-summary；正式dispatcher待接。本批也确认d60异步诊断两SDK来源实际写出且无未await警告。[结果](../plans/2026-09-07-corpus-c01-scoring/C08-RETAINED-RESULTS.md)。

最后更新：2026-09-07。Host诊断异步消费修复d60a94f4：真实installed Memory SQLite快照/timeout-cancel两个新控及三个受影响同步控制首批5PASS1.04s，PG83640清空。main改显式await，尚待下一新组合观测；SDK诊断版本硬编码原0.6.0另待，不冒称完整审计或改制品。[结果](../plans/2026-09-07-sdk-async-snapshot/RESULTS.md)。

# SDK Runtime 架构基线

> **创建日期**: 2026-08-17  
> **目的**: 为 SDK 迁移建立架构基线，记录当前状态和目标状态

---

## 1. 当前状态 (Broken)

### 1.1 核心问题

**Commit 71f3a6e2 执行了不完整的重构**：
- ✅ 删除了旧的 deskpet/harness 构建代码
- ❌ 未完成到 SDK Runtime v0.1.x 的迁移
- ❌ 留下了 `NotImplementedError` 占位符

**具体症状**：
```python
# backend/main.py:9301-9312
raise NotImplementedError(
    "Agent execution path not yet migrated to SDK Runtime v0.1.x. "
    "The old Harness architecture was removed but the SDK integration "
    "was not completed. See commit 71f3a6e2."
)
```

**历史错误** (已在分析中记录，当前不再出现因为有 NotImplementedError):
1. `AttributeError: 'RunClient' object has no attribute 'resume'`
2. `TypeError: RunClient.start() got an unexpected keyword argument 'prepared'`

### 1.2 架构不兼容根因

#### 旧架构 (已删除)
- **类**: `KernelRunClient` (deskpet/harness)
- **接口**:
  ```python
  async def start(
      request: Mapping[str, Any],
      host: HostContext,
      prepared: PreparedRunContextV1
  ) -> None
  
  async def resume(
      request_id: str,
      turn_id: str,
      host: HostContext
  ) -> ResumedRun
  
  def observe() -> AsyncIterator[RunEvent]
  ```

#### 新架构 (SDK Runtime v0.1.x)
- **类**: `RunClient` (simple_harness.runtime.kernel)
- **接口**:
  ```python
  async def start(value: RunStart) -> RunRecord
  
  async def signal(run_id: RunId, payload: dict) -> SignalDelivery
  
  async def cancel(run_id: RunId) -> None
  
  def query(run_id: RunId) -> RunState
  ```

**关键差异**：
1. **start()**: 旧接口接受 3 个参数 (request, host, prepared)，新接口只接受 1 个 RunStart 对象
2. **resume()**: 新接口完全没有 resume() 方法，用 signal() 替代
3. **事件流**: 旧接口通过 observe() 返回 AsyncIterator，新接口的事件机制未在当前分析中明确

---

## 2. SDK Runtime v0.1.x 能力清单

### 2.1 核心组件

**已确认存在** (通过 `backend/.venv/lib/python3.12/site-packages/simple_harness/`):

```
simple_harness/
├── __init__.py           # 公共 API 入口
├── contracts/            # 数据契约 (RunId, EventEnvelope, etc.)
├── execution/            # 执行层 (SqliteExecutionUnitOfWork)
├── providers/            # Provider 适配层
├── runtime/              # 运行时核心
│   ├── kernel.py         # Runtime, RunClient 定义
│   ├── drivers/          # ReActDriver, WorkflowDriver
│   ├── admission.py
│   ├── child_runs.py
│   ├── context.py
│   └── ...
├── tools/                # 工具系统
├── workflow/             # Workflow 引擎
└── workflows/            # 内置工作流
```

### 2.2 公共 API (从 `__init__.py` 确认)

**Runtime 核心**:
- `Runtime` - 主运行时类
- `RunClient` - Run 客户端接口
- `build_runtime()` - 构建运行时的工厂函数
- `RunStart` - 启动参数封装
- `RuntimeDriver` - Driver 抽象基类
- `RuntimePorts` - 运行时端口集合
- `RuntimeProfile` - Profile 定义
- `RuntimeServices` - 服务注册
- `RuntimeUnitOfWork` - 工作单元抽象

**Drivers**:
- `ReActDriver` - ReAct 循环驱动器
- `WorkflowRuntimeDriver` - Workflow 驱动器
- `build_react_driver()` - 构建 ReAct driver
- `build_workflow_runtime_driver()` - 构建 Workflow driver
- `AgentLoopCollaborator` - Agent 循环协作器
- `EffectBatchExecutor` - 批量效果执行器

**Context & Admission**:
- `ContextPort` - Context 端口接口
- `ContextSnapshot` - Context 快照
- `SqliteContextPort` - SQLite Context 实现
- `AdmissionPort` - 准入控制端口
- `AllowAllAdmission` - 允许所有准入策略

**Child Runs & Signals**:
- `ChildRunHandle` - 子 Run 句柄
- `ChildRunUnitOfWork` - 子 Run 工作单元
- `ChildSignalRuntime` - 子 Run 信号运行时
- `UserContinuationRuntime` - 用户继续运行时

**Recovery & Reconciliation**:
- `StartupReconciler` - 启动恢复器
- `ReconciliationPhase` - 恢复阶段枚举
- `TerminalCoordinator` - 终态协调器

**Constants**:
- `ROOT_PROFILE_KEY` - 根 Profile 键名

### 2.3 与旧架构的功能对应关系

| 旧 deskpet/harness | SDK Runtime v0.1.x | 状态 |
|-------------------|-------------------|------|
| `RunKernel` | `Runtime` | ✅ 完整替代 |
| `KernelRunClient` | `RunClient` | ⚠️ 接口不兼容 |
| `ReActDriver` | `ReActDriver` | ✅ 已迁移至 SDK |
| `WorkflowDriver` | `WorkflowRuntimeDriver` | ✅ 已迁移至 SDK |
| `AgentLoop` | `AgentLoopCollaborator` | ✅ 已迁移至 SDK |
| `EffectBatchExecutor` | `EffectBatchExecutor` | ✅ 已迁移至 SDK |
| `SqliteExecutionUnitOfWork` | 通过 `execution.sqlite` 提供 | ✅ 已迁移至 SDK |
| `observe()` 事件流 | ❓ 未明确 | ⚠️ 需调查 |
| `resume()` | `signal()` | ⚠️ 语义不同 |

---

## 3. 当前产品集成架构

### 3.1 SDK Ingress 层 (已存在)

**文件**: `backend/deskpet/sdk_adapters/ingress.py`

**核心类**: `SdkRuntimeIngress`

**已实现方法**:
```python
async def start(
    session_id: str,
    request_id: str,
    turn_id: str,
    payload: dict[str, Any],
    session_generation: int
) -> IngressStartReceipt

async def signal(
    run_id: str,
    payload: dict[str, Any]
) -> IngressSignalReceipt

async def cancel(run_id: str) -> None

def query(run_id: str) -> RunState

async def reconcile() -> None

async def recover() -> None

async def wait_idle(run_id: str) -> None
```

**关键特性**:
1. ✅ 已实现确定性 RunId 生成 (基于 session_id, request_id, turn_id 的 SHA-256)
2. ✅ 已封装 SDK Runtime 的 `ready.client.start()`
3. ✅ 使用 `RunStart` 对象构造
4. ✅ 提供了 signal/cancel/query 等完整接口

### 3.2 Product Stack 层 (已存在)

**文件**: `backend/deskpet/sdk_adapters/composition.py`

**核心类**: `ProductSdkRuntimeStack`

**职责**:
- 管理 SDK Runtime 生命周期 (startup/ready/close)
- 提供 `require_ready()` 获取 `SdkRuntimeReady`
- 管理 Database 和 UoW 生命周期
- 处理 Workflow factory 构建

**已存在的依赖注入机制**:
- `dependency_loader: DependencyLoader` - 延迟加载依赖
- `ready_publisher: Callable` - 发布 ready 状态

### 3.3 断点：main.py 未完成集成

**文件**: `backend/main.py`

**位置**: Lines 9299-9338

**当前状态**:
```python
# Line 9301: 注释说明问题
# TODO(CRITICAL): Implement SDK Runtime direct integration

# Line 9308: 抛出 NotImplementedError
raise NotImplementedError(...)

# Line 9314-9338: 被绕过的旧代码残留
# - ProductVenueRunResult 的处理逻辑
# - chat_v2_run_started 事件发送
# - session.events 迭代
```

**问题**:
1. `ProductVenueRunAdapter` 已被移除，但调用点未替换
2. 缺少从 `SdkRuntimeIngress.start()` 到事件流的桥接
3. 事件流订阅机制不明确

---

## 4. 迁移目标架构

### 4.1 预期调用链

```
main.py: _run_chat()
  ↓
SdkRuntimeIngress.start()
  ↓
SDK Runtime: RunClient.start(RunStart)
  ↓
Runtime 内部执行 (ReActDriver / WorkflowDriver)
  ↓
事件流 → RunPresenter (现有)
  ↓
WebSocket → 前端
```

### 4.2 需要回答的关键问题

1. **事件流订阅**:
   - ❓ SDK Runtime 如何暴露事件流？
   - ❓ 是否需要调用 `Runtime.observe()` 或类似方法？
   - ❓ 事件格式是否与旧 `RunEvent` 兼容？

2. **RunPresenter 集成**:
   - ❓ 现有的 `RunPresenter` 是否需要适配？
   - ❓ 事件转换逻辑在哪一层？

3. **恢复机制**:
   - ❓ `SdkRuntimeIngress.recover()` 已存在，是否足够？
   - ❓ 恢复时如何重建事件流订阅？

4. **Context 准备**:
   - ❓ `ProductTurnPreparer` 的输出如何转换为 `RunStart.payload`？
   - ❓ Host Context、工具快照如何传递？

5. **终态处理**:
   - ❓ Run 完成/失败/取消如何通知到产品层？
   - ❓ Session 投影一致性门如何集成？

---

## 5. 已存在的相关组件

### 5.1 ProductTurnPreparer (现有)

**文件**: `backend/deskpet/harness/product_turn_preparer.py` (推测)

**职责**:
- 组装 Session 历史
- 加载记忆
- 准备工具快照
- 构建 Context OS

**输出**: 某种形式的 `PreparedContext` 或 `PreparedRunContext`

### 5.2 RunPresenter (现有)

**描述** (从 AGENT_HARNESS.md):
- 将统一事件转换为产品消息
- 投递到 SessionDB 和 WebSocket
- 处理工具轨迹、workflow 进度、终态

**问题**: 是否兼容 SDK Runtime 的事件格式？

### 5.3 HarnessReconciler (现有)

**描述**:
- 启动时清空 durable 积压
- 事件驱动的恢复
- child run、晚到 effect 处理

**集成点**: 应该调用 `SdkRuntimeIngress.recover()`

---

## 6. 迁移风险与约束

### 6.1 硬约束 (来自 CLAUDE.md)

1. **ARCHITECTURE 更新规则**:
   - ✅ 必须在交付时同时更新 `ARCHITECTURE/` 文档
   - ✅ 更新 `PROJECT_STATUS.md` 模块完成状态

2. **测试要求**:
   - ✅ 必须保持 `backend/tests/harness_simplification` 绿色
   - ✅ E2E 验证：真人在 UI 发送消息并观察完整生命周期

3. **单一执行 authority**:
   - ✅ 不能引入第二个 RunKernel 或 execution writer
   - ✅ 保持 `SqliteExecutionUnitOfWork` 为唯一 DML authority

4. **向后兼容**:
   - ✅ 历史 Run 数据必须仍可读取
   - ✅ 不能破坏现有的 Inspector / 消息历史展示

### 6.2 技术风险

**高风险**:
1. **事件流机制不明确** - 可能需要深入 SDK Runtime 源码
2. **Context 传递复杂** - 需要映射旧的 PreparedRunContext 到新的 RunStart.payload
3. **恢复逻辑验证** - 涉及进程重启，难以自动化测试

**中等风险**:
1. **RunPresenter 适配** - 可能需要修改事件处理逻辑
2. **错误处理映射** - 旧错误码到新错误码的转换
3. **性能回归** - 新架构可能引入额外开销

**低风险**:
1. **基本启动流程** - `SdkRuntimeIngress` 已封装良好
2. **取消/信号机制** - 接口已明确
3. **单元测试覆盖** - SDK Runtime 本身应该有完整测试

---

## 7. 下一步行动 (Phase 1 输入)

### 7.1 必须调研的问题

1. **事件流机制**:
   - 阅读 SDK Runtime 源码中的事件订阅接口
   - 确认事件格式和订阅生命周期
   - 确定是否需要显式调用 observe() 或类似方法

2. **RunStart.payload 结构**:
   - 确认 payload 的 schema
   - 确定如何编码 Context、工具快照、权限等

3. **现有 RunPresenter 兼容性**:
   - 检查 RunPresenter 期望的事件格式
   - 确定是否需要适配器层

### 7.2 实现策略建议

**保守方案** (推荐):
1. 创建适配器层 `ProductSdkRunAdapter`，封装复杂映射逻辑
2. 保持 main.py 中的调用点尽量简单
3. 分阶段验证：启动 → 事件流 → 恢复 → 完整集成

**激进方案** (不推荐):
1. 直接在 main.py 中内联所有逻辑
2. 一次性完成所有映射
3. 风险：调试困难，回滚代价大

---

## 8. 参考资料

- **SDK 分析文档**: `SDK_VS_DESKPET_ANALYSIS.md`
- **架构文档**: `ARCHITECTURE/AGENT_HARNESS.md`
- **Ingress 实现**: `backend/deskpet/sdk_adapters/ingress.py`
- **Stack 实现**: `backend/deskpet/sdk_adapters/composition.py`
- **SDK 包位置**: `backend/.venv/lib/python3.12/site-packages/simple_harness/`

---

**最后更新**: 2026-08-17  
**状态**: 架构基线已建立，等待 Phase 1 实现计划
