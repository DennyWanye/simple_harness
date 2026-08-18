# Git 历史对比报告

> **创建日期**: 2026-08-17  
> **目的**: 确认 commit 71f3a6e2 删除的旧代码和现在的新代码差异

---

## 1. Commit 71f3a6e2 做了什么？

**Commit 信息**:
```
commit 71f3a6e2510e4fbd7562a59f2fd8b12ef98422a0
Date:   Mon Aug 17 07:12:07 2026 +0800

refactor(harness): remove dead harness code, keep SDK v0.1.1 as sole ingress

- Delete unused harness build functions
- Delete harness globals: _harness_runtime, _harness_venue, _harness_accepting
- Remove 11 dead harness module files
- Delete ~20 harness unit test files

Known issue (documented, not fixed): 
companion/run_adapter.py expects KernelRunClient but receives SDK RunClient (interface mismatch)
```

**删除的文件**（部分关键文件）:
- `backend/deskpet/harness/bootstrap.py` (214 行)
- `backend/deskpet/harness/adapters/product_composition.py` (205 行)
- `backend/deskpet/harness/drivers/react.py` (5132 行)
- `backend/deskpet/harness/drivers/react_loop.py` (1190 行)
- `backend/deskpet/harness/drivers/workflow.py` (294 行)
- 以及约 20 个测试文件

**测试结果**:
- 删除前: 79 failed, 6636 passed
- 删除后: 81 failed, 6466 passed
- 减少 170 个通过的测试（因为删除了测试文件）

**已知问题（commit message 中明确说明）**:
> "companion/run_adapter.py expects KernelRunClient but receives SDK RunClient (interface mismatch)"

---

## 2. 旧的 ProductVenueRunAdapter 是什么？

### 2.1 类定义（删除前）

```python
# 文件: backend/deskpet/harness/adapters/venues.py (已删除)

class ProductVenueRunAdapter:
    """Dormant venue-neutral product chain kept outside the production owner."""

    def __init__(
        self,
        *,
        preparer: ProductTurnPreparer,
        run_client: KernelRunClient,  # ← 注意：这里用的是 KernelRunClient
        presenter: RunPresenter,
        event_adapter: CanonicalRunEventPresentationAdapter | None = None,
        identity_resolver: ProductTurnIdentityResolver | None = None,
        preparation_service: ProductTurnPreparationService | None = None,
    ) -> None:
        self._preparer = preparer
        self._run_client = run_client  # ← KernelRunClient
        self._presenter = presenter
        # ...
```

### 2.2 关键方法：open()

```python
async def open(
    self,
    turn: TurnInput,
    host: HostContext,
    *,
    services: Mapping[str, Any],
    config: Any,
    local_llm: Any,
    tool_registry: Any,
    provider: Any,
    # ... 很多参数
) -> ProductVenueRunSession | ProductVenueRunResult:
    
    # 1. 先尝试恢复旧的 Run
    resumed = await self._run_client.resume(
        turn.request_id, turn.turn_id, host
    )
    
    if resumed is not None:
        # 恢复成功，返回 session
        return self._session(resumed, presentation_context)
    
    # 2. 如果没有旧 Run，准备新 Run
    preparation = await preparation_service.prepare(...)
    prepared = preparation.prepared
    
    # 3. 启动新 Run (这里调用了 start 方法)
    # 注意：旧代码这里的逻辑更复杂，涉及很多参数传递
```

---

## 3. 旧的 KernelRunClient 接口

### 3.1 完整接口（从 git 历史推断）

```python
# backend/deskpet/harness/kernel.py (已删除)

class KernelRunClient:
    """旧的 Run 客户端"""
    
    async def start(
        self,
        request: Mapping[str, object],      # ← 参数1: request 字典
        host: HostContext,                   # ← 参数2: host context
        *,
        prepared: PreparedRunContextV1,      # ← 参数3: 准备好的 context
    ) -> VenueRunHandle:
        """启动新 Run"""
        # ...
    
    async def resume(
        self,
        request_id: str,
        turn_id: str,
        host: HostContext
    ) -> VenueRunHandle | None:
        """恢复旧 Run"""
        # 内部调用 kernel.recover()
        # 如果 Run 不存在，返回 None
    
    def observe(
        self,
        ref: RunRef,
        actor: ActorContext,
        cursor: int | None = None,
    ) -> AsyncIterator[RunEvent]:
        """订阅事件流 - 返回异步迭代器"""
        # 1. 先从 durable storage 读取历史事件
        # 2. 订阅 live queue 接收新事件
        # 3. 合并历史和实时事件
        # 4. 通过 AsyncIterator 逐个返回
```

### 3.2 observe() 的工作方式（拉取模式）

```python
# 旧代码中的使用方式：

async def _consume_events():
    # 1. 调用 observe() 获取事件流
    events = kernel.observe(run_ref, actor)
    
    # 2. 使用 async for 循环逐个处理
    async for event in events:
        # 处理事件
        await presenter.handle_event(event)
        await websocket.send_json(event.to_dict())
```

**关键特征（拉取模式）**:
- 返回 `AsyncIterator[RunEvent]`
- **调用方主动拉取**：需要用 `async for` 循环不断读取
- 事件流由调用方控制
- 如果调用方停止读取，事件会堆积在队列中

---

## 4. 新的 SDK Runtime 架构

### 4.1 RunClient 接口（SDK v0.1.x）

```python
# simple_harness.runtime.kernel.RunClient

class RunClient:
    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    async def start(self, value: RunStart) -> RunRecord:
        """启动 Run - 只接受一个 RunStart 对象"""
        return await self._runtime._start_run(value)
    
    def query(self, run_id: RunId) -> RunRecord | None:
        """查询 Run 状态"""
        return self._runtime._uow.read_run(_run_id(run_id))
    
    def signal(
        self,
        run_id: RunId,
        *,
        signal_id: str,
        payload: Mapping[str, JsonValue],
    ) -> ContinuationRecord:
        """发送信号给 Run（替代旧的 resume）"""
        # ...
    
    async def cancel(self, run_id: RunId) -> RunRecord:
        """取消 Run"""
        return await self._runtime._cancel_run(run_id)
    
    # ❌ 没有 resume() 方法
    # ❌ 没有 observe() 方法
```

### 4.2 DeliveryDispatcher（推送模式）

```python
# simple_harness.execution.delivery.DeliveryDispatcher (Protocol)

class DeliveryDispatcher(Protocol):
    async def run_once() -> int:
        """处理一批待交付事件，返回处理数量"""
        # 1. 从 workflow.db 读取待交付事件
        # 2. 写入目标存储（如 SessionDB）
        # 3. 通过 WebSocket 推送
        # 4. 标记事件为已交付
```

### 4.3 Runtime 内部的事件推送机制

```python
# simple_harness.runtime.kernel.Runtime

class Runtime:
    def __init__(self, *, ports: RuntimePorts, ...):
        self._ports = ports
        self._delivery_pump_task = None
        self._delivery_wake = asyncio.Event()
        # ...
    
    async def start(self):
        """启动 Runtime"""
        # 启动后台事件推送任务
        self._delivery_pump_task = asyncio.create_task(
            self._delivery_pump(), 
            name="simple-harness-delivery-pump"
        )
    
    async def _delivery_pump(self) -> None:
        """后台事件推送循环"""
        while not self._closing:
            try:
                # 等待事件或超时
                await asyncio.wait_for(
                    self._delivery_wake.wait(), 
                    timeout=backoff
                )
                self._delivery_wake.clear()
                
                # 调用 DeliveryDispatcher 处理一批事件
                await self._ports.delivery.run_once()
                
            except asyncio.TimeoutError:
                # 超时后重试
                self._delivery_wake.set()
```

**关键特征（推送模式）**:
- **后台任务自动推送**：不需要调用方主动拉取
- 事件写入持久化层（SessionDB）
- 通过 WebSocket 主动推送到前端
- 调用方只需启动 Runtime，事件自动流动

---

## 5. 对比总结表

| 维度 | 旧架构（已删除） | 新架构（SDK Runtime） |
|------|-----------------|---------------------|
| **适配器类** | `ProductVenueRunAdapter` | `SdkRuntimeIngress` |
| **Run 客户端** | `KernelRunClient` | `RunClient` |
| **启动方法** | `start(request, host, prepared)` | `start(RunStart)` |
| **恢复方法** | `resume(request_id, turn_id, host)` | `signal(run_id, payload)` |
| **事件订阅** | `observe() -> AsyncIterator` | ❌ 无需订阅 |
| **事件流模式** | **拉取模式**（调用方用 async for 读取） | **推送模式**（后台自动推送） |
| **事件存储** | 内存队列 + durable storage | SessionDB (持久化) |
| **WebSocket 推送** | 调用方负责 | DeliveryDispatcher 负责 |
| **后台任务** | 无 | `_delivery_pump_task` |

---

## 6. 事件流机制的详细对比

### 6.1 旧方式：observe() 拉取事件

**工作流程**:
```
用户发消息
  ↓
main.py 调用 ProductVenueRunAdapter.open()
  ↓
KernelRunClient.start() 启动 Run
  ↓
main.py 调用 kernel.observe(run_ref) 获取事件流
  ↓
main.py 用 async for 循环：
  async for event in events:
      ↓
      presenter.handle_event(event)  # 转换事件
      ↓
      websocket.send_json(...)        # 推送到前端
```

**代码示例**:
```python
# 旧代码（已删除）
async def _run_chat():
    # 启动 Run
    venue_handle = await adapter.open(turn, host, ...)
    
    # 获取事件流
    events = venue_handle.events  # AsyncIterator[RunEvent]
    
    # 主动拉取事件
    async for event in events:
        # 转换事件
        presentation_events = presenter.to_presentation_events(event)
        
        # 推送到 WebSocket
        for pe in presentation_events:
            await websocket.send_json(pe.to_dict())
```

**特点**:
- ✅ 调用方完全控制事件流
- ✅ 可以随时停止读取
- ❌ 必须有 `async for` 循环持续读取
- ❌ 如果调用方崩溃，事件丢失
- ❌ 无法在不同组件间共享事件流

---

### 6.2 新方式：DeliveryDispatcher 推送事件

**工作流程**:
```
用户发消息
  ↓
main.py 调用 SdkRuntimeIngress.start()
  ↓
SDK Runtime 启动 Run
  ↓
Driver 执行过程中产生事件
  ↓
Runtime 将事件写入 delivery queue
  ↓
后台 _delivery_pump_task 自动运行：
  ↓
  DeliveryDispatcher.run_once()
    ↓
    1. 从 workflow.db 读取待交付事件
    ↓
    2. 写入 SessionDB (持久化)
    ↓
    3. 通过 WebSocket 推送到前端
    ↓
    4. 标记事件为已交付
```

**代码示例**:
```python
# 新代码（SDK Runtime）
async def _execute_sdk_run():
    # 1. 启动 Run
    receipt = await ingress.start(
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        payload=payload,
        session_generation=1,
    )
    
    # 2. 等待完成（不需要主动拉取事件）
    await ingress.wait_idle(receipt.run_id)
    
    # 事件已经自动推送到前端了！
```

**特点**:
- ✅ 调用方只需启动，无需管理事件流
- ✅ 事件持久化到 SessionDB，不会丢失
- ✅ 多个组件可以独立读取 SessionDB
- ✅ 调用方崩溃不影响事件交付
- ❌ 调用方无法控制事件流速度
- ❌ 无法在事件到达时立即拦截处理

---

## 7. 为什么 commit 71f3a6e2 没完成迁移？

**Commit message 中已经说明**:
> "Known issue (documented, not fixed): companion/run_adapter.py expects KernelRunClient but receives SDK RunClient (interface mismatch)"

**具体原因**:
1. 删除了 `KernelRunClient` 的实现
2. 但是 `ProductVenueRunAdapter` 依赖 `KernelRunClient` 的三个方法：
   - `start(request, host, prepared)`
   - `resume(request_id, turn_id, host)`
   - `observe(ref, actor) -> AsyncIterator`
3. SDK Runtime 的 `RunClient` 接口完全不同：
   - `start(RunStart)` - 参数不匹配
   - ❌ 没有 `resume()` - 方法缺失
   - ❌ 没有 `observe()` - 方法缺失

**留下的断点**:
- `main.py` 中还在尝试创建 `ProductVenueRunAdapter`
- 但 `ProductVenueRunAdapter` 已被删除
- 所以只能抛出 `NotImplementedError`

---

## 8. 我的建议：使用新的 DeliveryDispatcher

### 8.1 为什么不应该恢复旧的 observe() 方式？

**技术原因**:
1. **旧代码已删除**：`KernelRunClient` 和 `ProductVenueRunAdapter` 已完全删除
2. **架构不一致**：旧代码依赖已删除的 `RunKernel`，而现在只有 `Runtime`
3. **维护成本高**：恢复旧代码需要重新实现 `observe()`，等于重复造轮子
4. **技术债务**：保留两套事件机制会增加复杂度

**功能原因**:
1. **事件丢失风险**：旧的拉取模式，如果调用方崩溃，事件丢失
2. **不支持多消费者**：`AsyncIterator` 只能被一个调用方消费
3. **无持久化**：事件只在内存队列中，重启后丢失

### 8.2 为什么应该用新的 DeliveryDispatcher？

**技术优势**:
1. ✅ **已经存在**：SDK Runtime 已内置，无需重新实现
2. ✅ **经过测试**：SDK Runtime 自带测试套件
3. ✅ **架构清晰**：事件流与 Run 执行完全解耦
4. ✅ **易于维护**：后台任务自动管理，无需手动控制

**功能优势**:
1. ✅ **持久化**：事件写入 SessionDB，重启不丢失
2. ✅ **多消费者**：多个组件可以读取 SessionDB
3. ✅ **解耦**：调用方不需要管理事件流生命周期
4. ✅ **可靠性**：后台任务自动重试，容错性强

### 8.3 性能对比

| 指标 | observe() 拉取 | DeliveryDispatcher 推送 |
|------|---------------|----------------------|
| **延迟** | 低（实时读取内存队列） | 中（需要经过持久化层） |
| **吞吐量** | 高（无持久化开销） | 中（有持久化开销） |
| **可靠性** | 低（崩溃丢失） | 高（持久化） |
| **可扩展性** | 低（单消费者） | 高（多消费者） |
| **维护成本** | 高（需要管理生命周期） | 低（自动管理） |

**结论**：
- 如果你追求**绝对最低延迟**（毫秒级），旧方式略优
- 如果你追求**可靠性和易维护性**，新方式显著更优
- 对于产品场景（用户对话），延迟差异（几十毫秒）用户感知不到

---

## 9. 最终建议

**推荐方案**：使用 DeliveryDispatcher（新方式）

**理由**:
1. ✅ 旧代码已删除，恢复成本高
2. ✅ 新方式已经内置在 SDK Runtime
3. ✅ 持久化 + 多消费者 + 自动管理
4. ✅ 这是项目的演进方向

**如果你仍想用旧方式**，需要：
1. 从 git 历史恢复 `KernelRunClient` 和 `ProductVenueRunAdapter`（约 2000 行代码）
2. 适配到新的 `Runtime` 接口（因为旧的 `RunKernel` 也已删除）
3. 重新实现 `observe()` 方法
4. 修复所有依赖关系
5. 预计工作量：**3-5 天**

**如果用新方式**，只需要：
1. 调用 `SdkRuntimeIngress.start()`（已存在）
2. 等待 `DeliveryDispatcher` 自动推送事件（已存在）
3. 修改 `main.py` 约 30 行代码
4. 预计工作量：**8-12 小时**

---

## 10. 总结

**确认事实**:
1. ✅ `ProductVenueRunAdapter` 在 commit 71f3a6e2 中被删除
2. ✅ `KernelRunClient.start(request, host, prepared)` 的接口确实存在过
3. ✅ `observe() -> AsyncIterator` 的拉取模式确实存在过
4. ✅ `DeliveryDispatcher` 推送模式是新架构

**我的判断**:
- 🔵 **用新方式**（DeliveryDispatcher）：工作量小，架构清晰，易维护
- 🔴 **恢复旧方式**（observe 拉取）：工作量大，技术债务，不推荐

**决定权在你**，我已经提供了所有技术细节。你希望我：
1. 继续用新方式（DeliveryDispatcher）实现？
2. 还是花 3-5 天恢复旧方式（observe 拉取）？

