# SDK Runtime vs Deskpet Harness 架构分析报告

**生成时间**: 2026-08-18  
**分析人员**: Claude (Opus 5)  
**问题背景**: commit 71f3a6e2 重构导致 Agent 执行路径失效

---

## 一、核心发现

### 1. SDK Runtime 是 deskpet/harness 的重构版本

**证据**：
- SDK 包位置：`.venv/lib/python3.12/site-packages/simple_harness/`
- SDK Runtime Kernel：1884行代码（`simple_harness/runtime/kernel.py`）
- Deskpet Harness Kernel：1291行代码（`deskpet/harness/kernel.py`）
- 两者**不共享代码**，SDK 完全独立，不依赖 deskpet 模块

### 2. 两套系统的架构差异

| 特性 | Deskpet Harness (旧) | SDK Runtime (新) |
|------|---------------------|------------------|
| 核心类 | `RunKernel` | `Runtime` |
| 客户端 | `KernelRunClient` | `RunClient` |
| 启动方法 | `start(RunRequest, host, prepared)` | `start(RunStart)` |
| 恢复方法 | `resume(request_id, turn_id, host)` | ❌ 不支持 |
| 事件流 | `observe(ref, actor)` → `AsyncIterator` | `client.start()` → `RunRecord` |
| 适配器 | `ProductVenueRunAdapter` | ❌ 无等价物 |

---

## 二、当前代码库的双重架构

### A. SDK Runtime（新架构，已集成）

**位置**: `.venv/lib/python3.12/site-packages/simple_harness/`

**核心组件**：
```python
simple_harness/
├── runtime/
│   ├── kernel.py          # Runtime 类（1884行）
│   ├── drivers/
│   │   ├── react_loop.py  # ReActDriver
│   │   └── workflow.py    # WorkflowRuntimeDriver
│   ├── orchestration.py   # 55798行，编排逻辑
│   └── ...
├── execution/             # 执行层抽象
├── contracts/             # 合约定义
├── providers/             # Provider 抽象
├── tools/                 # 工具执行
└── workflow/              # Workflow 引擎
```

**使用方式**（从 ingress.py 看到的）：
```python
from simple_harness.runtime.kernel import RunClient, RunStart
from simple_harness import Runtime, build_runtime

# 1. 构建 Runtime
runtime = build_runtime(ports, profile, services)

# 2. 获取 RunClient
client: RunClient = runtime  # Runtime 实现了 RunClient 协议

# 3. 启动 Run
start = RunStart(
    execution_session_id=ExecutionSessionId(session_id),
    run_id=run_id,
    request_id=RequestId(request_id),
    turn_id=turn_id,
    input=payload,  # 注意：叫 input，不是 prepared
    tool_catalog_generation=session_generation,
)
record: RunRecord = await client.start(start)
```

**关键差异**：
- ✅ 有 `start(RunStart)` 方法
- ❌ **没有** `resume()` 方法
- ❌ **没有** `open_venue()` 方法
- ❌ **没有** `ProductVenueRunAdapter` 等价物
- ✅ 有 `query(run_id)` 查询运行状态
- ✅ 有 `signal(run_id, payload)` 发送信号
- ✅ 有 `cancel(run_id)` 取消运行

### B. Deskpet Harness（旧架构，部分保留）

**位置**: `backend/deskpet/harness/`

**保留的核心组件**：
```python
deskpet/harness/
├── kernel.py                      # RunKernel 类（1291行）
├── adapters/
│   ├── venues.py                  # ProductVenueRunAdapter + KernelRunClient
│   └── product_turn_open.py      # ProductTurnIdentityResolver
├── contracts.py                   # HostContext, PreparedRunContextV1
├── ports.py                       # 抽象端口定义
├── profiles.py                    # ProfileRegistry
├── context.py                     # HostContextFactory
├── child_runs.py                  # ChildRunCoordinator
└── drivers/                       # Driver 注册表
    └── ...
```

**已删除的组件**（commit 71f3a6e2）：
- ❌ `bootstrap.py` - harness 构建和激活
- ❌ `adapters/product_composition.py` - 产品组合层
- ❌ `adapters/product_profiles.py` - 配置文件适配器
- ❌ `adapters/subagent_registry.py` - 子代理注册
- ❌ `adapters/team.py` - 团队协作
- ❌ `drivers/react.py` - ReAct Driver 实现
- ❌ `react_loop.py, react_recovery.py` - ReAct 循环和恢复
- ❌ 所有 harness 单元测试（~20个文件）

**为什么保留这些模块？**

提交说明提到：
> Keep 25 harness files (AC-8): contracts, ports, projector, context, profiles, kernel + engine modules
> Reason: companion/run_adapter.py → venues.py → kernel.py dependency chain

即：**Companion 后台任务系统还在使用旧的 `RunKernel` 架构**。

---

## 三、问题的根本原因

### commit 71f3a6e2 的重构不完整

**做了什么**：
1. ✅ 删除了旧 harness 的构建和激活代码
2. ✅ 保留了 SDK Runtime 作为"唯一入口"
3. ✅ 保留了 companion 需要的底层 harness 模块

**没做什么**（关键遗漏）：
1. ❌ **没有提供从 SDK Runtime 到旧 harness 的桥接层**
2. ❌ **没有更新 main.py 中的调用代码**
3. ❌ **没有迁移 Companion 系统到 SDK Runtime**

### main.py 中的错误调用链

**当前代码（错误）**：
```python
# main.py:9301-9318
venue_adapter = ProductVenueRunAdapter(
    preparer=ProductTurnPreparer(),
    run_client=_sdk_ingress.require_ready().client,  # ❌ 类型不匹配
    presenter=RunPresenter(),
)

outcome = await venue_adapter.open(...)  # ❌ 调用旧架构的方法
```

**问题**：
- `_sdk_ingress.require_ready().client` 返回 `simple_harness.runtime.kernel.RunClient`（SDK）
- `ProductVenueRunAdapter.__init__()` 期望 `deskpet.harness.adapters.venues.KernelRunClient`（旧架构）
- **两者完全不兼容**

**类型签名对比**：

```python
# SDK Runtime 的 RunClient
class RunClient(Protocol):
    def start(self, value: RunStart) -> RunRecord: ...
    def query(self, run_id: RunId) -> RunRecord | None: ...
    def signal(self, run_id: RunId, ...) -> ContinuationRecord: ...
    def cancel(self, run_id: RunId) -> RunRecord: ...

# Deskpet Harness 的 KernelRunClient  
class KernelRunClient:
    def __init__(self, kernel: RunKernel): ...
    def start(self, request: Mapping, host: HostContext, *, prepared: PreparedRunContextV1) -> VenueRunHandle: ...
    def resume(self, request_id: str, turn_id: str, host: HostContext) -> VenueRunHandle | None: ...
```

完全不同的接口！

---

## 四、Companion 系统的依赖

### Companion 为什么还能工作？

**Companion 的架构**：
```python
# deskpet/companion/run_adapter.py
class BackgroundJobVenueAdapter:
    def __init__(self, client: KernelRunClient, ...):
        self._client = client
    
    async def handle_job(self, ...):
        handle = await self._client.start(request, host, prepared=prepared)
        async for event in handle.events:
            # 处理事件
```

**关键点**：
1. Companion 系统有自己的 `RunKernel` 实例（不通过 SDK Runtime）
2. 这个 `RunKernel` 是在哪里构建的？（需要进一步调查）
3. Companion 可能使用了某个未删除的构建路径

---

## 五、修复方案

### 方案 A：完成 SDK Runtime 迁移（推荐，长期）

**需要做的事**：

1. **创建 SDK Runtime 适配器**
   ```python
   class SdkRuntimeVenueAdapter:
       """将 SDK Runtime 适配到产品层"""
       
       def __init__(self, ingress: SdkRuntimeIngress, presenter: RunPresenter):
           self._ingress = ingress
           self._presenter = presenter
       
       async def open(self, turn: TurnInput, host: HostContext, ...) -> ProductVenueRunResult:
           # 1. 准备 RunStart payload
           payload = self._prepare_payload(turn, ...)
           
           # 2. 调用 SDK Runtime
           receipt = await self._ingress.start(
               session_id=turn.session_id,
               request_id=turn.request_id,
               turn_id=turn.turn_id,
               payload=payload,
               session_generation=...,
           )
           
           # 3. 订阅事件流（需要新的事件订阅机制）
           # SDK Runtime 的 RunRecord 不提供事件流，需要通过其他方式获取
           
           # 4. 将事件转换为 WebSocket 消息
           await self._stream_events(receipt.run_id, ...)
   ```

2. **实现事件流订阅**
   - SDK Runtime 没有公开 `observe()` 方法
   - 需要查看 SDK 内部如何订阅事件
   - 可能需要使用 `Runtime.dispatch_deliveries_once()` 或其他机制

3. **迁移所有调用点**
   - `main.py` 的 chat_ingress
   - Voice pipeline（如果也在使用）
   - Background tasks（companion）

**工作量估计**: 8-16 小时

### 方案 B：重建 RunKernel 桥接层（临时，快速）

**思路**：在 SDK Runtime 和旧 harness 之间建立桥接

```python
class SdkToKernelBridge:
    """将 SDK Runtime 包装成 KernelRunClient 接口"""
    
    def __init__(self, sdk_ingress: SdkRuntimeIngress, sdk_runtime: Runtime):
        self._ingress = sdk_ingress
        self._runtime = sdk_runtime
    
    async def start(
        self,
        request: Mapping[str, object],
        host: HostContext,
        *,
        prepared: PreparedRunContextV1 | None = None,
    ) -> VenueRunHandle:
        # 转换参数格式
        payload = self._convert_request_to_payload(request, prepared)
        
        # 调用 SDK
        receipt = await self._ingress.start(
            session_id=host.session_id,
            request_id=request["request_id"],
            turn_id=request["turn_id"],
            payload=payload,
            session_generation=...,
        )
        
        # 包装成 VenueRunHandle
        return self._wrap_as_handle(receipt.run_id)
    
    async def resume(self, request_id: str, turn_id: str, host: HostContext):
        # SDK Runtime 不支持 resume，总是返回 None
        return None
```

**问题**：
- SDK Runtime 没有公开事件流，难以实现 `VenueRunHandle.events`
- 可能需要修改 SDK 包本身

**工作量估计**: 4-8 小时（但可能不完整）

### 方案 C：回退到 commit 71f3a6e2 之前（最快）

**操作**：
```bash
git revert 71f3a6e2
```

**优点**：
- 立即恢复功能
- 无需重写任何代码

**缺点**：
- 失去 SDK Runtime 的优势（如果有的话）
- 需要维护旧的 harness 架构

---

## 六、关键问题待调查

### 1. SDK Runtime 的事件流机制

**问题**：SDK `RunClient.start()` 返回 `RunRecord`（快照），不是事件流。产品层需要实时事件来：
- 发送 WebSocket 消息给前端
- 更新状态
- 处理工具调用

**需要查明**：
- SDK Runtime 内部如何发布事件？
- 是否有订阅机制？
- `Runtime.dispatch_deliveries_once()` 的作用？

### 2. Companion 的 RunKernel 来源

**问题**：Companion 系统还在使用 `KernelRunClient`，但构建代码已删除。

**需要查明**：
- Companion 的 `RunKernel` 是在哪里初始化的？
- 是否有隐藏的构建路径？
- 还是 Companion 也已经失效？

### 3. 重构的原始意图

**问题**：commit 71f3a6e2 的描述说"SDK v0.1.1 as sole ingress"，但实际上没有提供完整的集成。

**需要查明**：
- 重构是否只完成了一半？
- 是否有后续 commits 应该完成迁移？
- 原作者的计划是什么？

---

## 七、推荐行动方案

### 短期（今天）

1. **确认用户需求**：是否需要立即恢复功能？
2. **如果需要**：回退 commit 71f3a6e2（方案C）
3. **如果不急**：继续调查 SDK Runtime 的事件机制

### 中期（本周）

1. **深入研究 SDK Runtime**：
   - 阅读 `.venv/lib/python3.12/site-packages/simple_harness/runtime/` 源码
   - 找到事件订阅的正确方式
   - 查看 `orchestration.py`（55KB）的实现

2. **实现 SDK 适配器**（方案A）
   - 创建 `SdkRuntimeVenueAdapter`
   - 重写 `main.py` 的 Agent 入口
   - 完整测试

3. **迁移 Companion**：
   - 将 Companion 也迁移到 SDK Runtime
   - 删除所有旧 harness 代码

### 长期

- 统一架构，只使用 SDK Runtime
- 删除 `deskpet/harness/` 目录
- 更新文档和测试

---

## 八、结论

**SDK Runtime 确实包含了 deskpet/harness 的功能**，但是：

1. ✅ **功能等价性**：SDK 提供了运行 Agent 所需的所有核心功能
2. ❌ **接口不兼容**：API 设计完全不同，无法直接替换
3. ❌ **集成不完整**：commit 71f3a6e2 只删除了旧代码，没有完成新代码的集成
4. ❌ **文档缺失**：没有迁移指南或使用文档

**当前状态**：系统处于"半迁移"状态，既不能用旧架构（已删除构建代码），也不能用新架构（未完成集成）。

**建议**：如果需要快速恢复，回退重构；如果有时间，完成 SDK Runtime 迁移。
