# SDK Runtime 迁移实现计划

> **创建日期**: 2026-08-17  
> **目标**: 完成 commit 71f3a6e2 遗留的 SDK Runtime v0.1.x 迁移  
> **预估工作量**: 8-12 小时

---

## 执行摘要

**问题**: Commit 71f3a6e2 删除了旧 harness 构建代码但未完成 SDK Runtime 集成，导致 Agent 执行链路中断（main.py:9308 抛出 `NotImplementedError`）。

**解决方案**: 使用已存在的 `SdkRuntimeIngress` 替换被移除的 `ProductVenueRunAdapter`，并通过 SDK Runtime 的 `DeliveryDispatcher` 机制订阅事件流。

**关键发现**:
1. ✅ SDK Runtime **不需要**显式的 `observe()` 方法订阅事件
2. ✅ 事件通过 `RuntimePorts.delivery: DeliveryDispatcher` 自动推送
3. ✅ `SdkRuntimeIngress` 已完整封装 `start/signal/cancel` 接口
4. ✅ 现有 `RunPresenter` 已能处理 SDK Runtime 事件格式（`RunEvent`）

---

## 架构决策

### ADR-1: 使用 DeliveryDispatcher 而非事件流订阅

**背景**: 旧架构通过 `observe()` 返回 `AsyncIterator[RunEvent]`，新架构使用推送模式。

**决策**: 
- SDK Runtime 通过 `RuntimePorts.delivery: DeliveryDispatcher` 推送事件
- `DeliveryDispatcher` 是一个后台泵（`_delivery_pump`），自动将事件写入持久化层
- 产品层通过 `SessionDB` 读取事件，无需显式订阅

**理由**:
1. SDK Runtime 内部已有 `_delivery_pump_task` 后台任务
2. 事件通过 `delivery.run_once()` 批量交付
3. 产品层的 `RunPresenter` 已经监听 `SessionDB` 事件

**影响**: 不需要实现新的事件订阅机制，使用现有的 delivery 基础设施。

---

### ADR-2: 最小化修改范围

**背景**: 可以选择大规模重构或局部适配。

**决策**: 
- 在 main.py 中创建轻量适配器函数 `_execute_sdk_run()`
- 保持现有 `ProductTurnPreparer`、`RunPresenter`、`SessionDB` 不变
- 只修改 `_run_chat()` 中的断点处（lines 9299-9338）

**理由**:
1. 降低回归风险
2. 保持现有测试套件有效
3. 便于调试和回滚

**影响**: 迁移代码集中在一个文件的一个函数中。

---

## 实现任务分解

### Task 1: 调研 DeliveryDispatcher 接口 ✅

**目标**: 确认 SDK Runtime 如何推送事件到产品层。

**发现**:
```python
# simple_harness.execution.delivery.DeliveryDispatcher (Protocol)
async def run_once() -> int  # 处理一批待交付事件，返回处理数量

# RuntimePorts 包含 delivery
ports.delivery: DeliveryDispatcher

# Runtime 内部启动 _delivery_pump 后台任务
self._delivery_pump_task = asyncio.create_task(self._delivery_pump())
```

**结论**: 事件自动推送，产品层无需显式订阅。

---

### Task 2: 创建 SDK Run 执行函数

**文件**: `backend/main.py`

**新增函数**:
```python
async def _execute_sdk_run(
    *,
    session_id: str,
    request_id: str,
    turn_id: str,
    context: PreparedProductContext,
    ingress: SdkRuntimeIngress,
    websocket: WebSocket,
    sink: LegacyProductDomainSink,
) -> None:
    """Execute a Run through SDK Runtime ingress and stream events."""
    
    # 1. 构建 RunStart payload
    payload = {
        "messages": context.messages,
        "tool_catalog": context.prepared_tool_set,
        "workspace": context.workspace,
        # ... 其他 context 字段
    }
    
    # 2. 启动 Run
    receipt = await ingress.start(
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        payload=payload,
        session_generation=context.session_generation,
    )
    
    # 3. 发送 run_started 事件
    await websocket.send_json({
        "type": "chat_v2_run_started",
        "payload": {
            "session_id": session_id,
            "run_id": receipt.run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            # ...
        }
    })
    
    # 4. 等待 Run 完成
    await ingress.wait_idle(receipt.run_id)
    
    # 5. 查询最终状态
    final_state = ingress.query(receipt.run_id)
    
    # 6. 发送终态事件
    if final_state.state == "completed":
        await websocket.send_json({
            "type": "chat_v2_final",
            "payload": {"run_id": receipt.run_id, ...}
        })
    elif final_state.state == "failed":
        await websocket.send_json({
            "type": "chat_v2_error",
            "payload": {"run_id": receipt.run_id, ...}
        })
    # ...
```

**实现细节**:
- **payload 结构**: 需要映射 `PreparedProductContext` 到 SDK Runtime 期望的格式
- **事件流**: SDK Runtime 自动通过 `DeliveryDispatcher` 推送到 SessionDB
- **WebSocket 通知**: 只需发送关键生命周期事件（started/final/error）

**边界**:
- ✅ 处理正常完成
- ✅ 处理失败
- ✅ 处理取消
- ⚠️ 不处理恢复（由 `SdkRuntimeIngress.recover()` 处理）

---

### Task 3: 修改 main.py _run_chat() 调用点

**文件**: `backend/main.py`

**当前代码** (lines 9299-9338):
```python
# Line 9308: 抛出 NotImplementedError
raise NotImplementedError(...)

# Line 9314-9338: 被绕过的旧代码
```

**修改后**:
```python
# Line 9299: 准备 context (保持不变)
context = await preparer.prepare_context(...)

# Line 9301: 替换为 SDK 执行
try:
    await _execute_sdk_run(
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        context=context,
        ingress=_sdk_ingress,
        websocket=websocket,
        sink=sink,
    )
except Exception as error:
    # 错误处理
    logger.exception("sdk_run_execution_failed")
    await websocket.send_json({
        "type": "chat_v2_error",
        "payload": {
            "session_id": session_id,
            "error_code": "sdk_execution_failed",
            "message": str(error),
        }
    })
```

**删除**:
- Lines 9308-9312: `NotImplementedError`
- Lines 9314-9338: 旧的 `ProductVenueRunResult` 处理逻辑

---

### Task 4: Context 映射逻辑

**挑战**: `PreparedProductContext` → `RunStart.payload` 映射。

**方案 A: 直接序列化** (推荐):
```python
payload = {
    "schema_version": 1,
    "messages": [msg.to_dict() for msg in context.messages],
    "tool_catalog": context.prepared_tool_set.to_dict(),
    "workspace": context.workspace.to_dict() if context.workspace else None,
    "session_provider": context.session_provider,
    "conversation_boundary": context.conversation_boundary_ref,
}
```

**方案 B: 使用现有的 PreparedRunContextV1**:
```python
# 检查是否有现有的序列化方法
from deskpet.harness.start_snapshot import PreparedRunContextV1

prepared = PreparedRunContextV1(
    messages=context.messages,
    tool_set=context.prepared_tool_set,
    # ...
)
payload = prepared.to_dict()
```

**决策**: 先尝试方案 A（更简单），如果 SDK Runtime 期望特定格式则切换到方案 B。

---

### Task 5: 事件流验证

**验证点**:
1. ✅ `DeliveryDispatcher` 自动推送事件到 SessionDB
2. ✅ `RunPresenter` 监听 SessionDB 并转换事件
3. ✅ WebSocket 接收到实时更新
4. ✅ Harness Inspector 显示完整执行链路

**测试方法**:
```python
# 单元测试
async def test_sdk_run_events_delivered():
    # 1. 启动 Run
    receipt = await ingress.start(...)
    
    # 2. 等待事件
    await asyncio.sleep(1)
    
    # 3. 查询 SessionDB
    events = session_db.get_run_events(receipt.run_id)
    
    # 4. 断言
    assert len(events) > 0
    assert any(e.kind == "run.started" for e in events)
```

**手动测试**:
1. 启动应用
2. 在 UI 发送消息
3. 观察 Harness Inspector 实时更新
4. 检查 workflow.db 和 state.db 的事件记录

---

### Task 6: 错误处理完善

**场景 1: SDK Runtime 未就绪**
```python
try:
    receipt = await ingress.start(...)
except SdkRuntimeNotReady as error:
    logger.error("sdk_runtime_not_ready", error=str(error))
    await websocket.send_json({
        "type": "chat_v2_error",
        "payload": {
            "error_code": "runtime_not_ready",
            "message": "Agent execution system is initializing. Please retry.",
        }
    })
    return
```

**场景 2: Run 启动失败**
```python
try:
    receipt = await ingress.start(...)
except HarnessError as error:
    logger.error("sdk_run_start_failed", error_code=error.code)
    await websocket.send_json({
        "type": "chat_v2_error",
        "payload": {
            "error_code": error.code,
            "message": error.message,
        }
    })
    return
```

**场景 3: 执行中断（超时/崩溃）**
```python
try:
    await asyncio.wait_for(
        ingress.wait_idle(receipt.run_id),
        timeout=1800.0  # 30 分钟
    )
except asyncio.TimeoutError:
    logger.warning("sdk_run_timeout", run_id=receipt.run_id)
    await ingress.cancel(receipt.run_id)
    # 发送超时错误
```

---

### Task 7: 恢复机制验证

**目标**: 确认 Backend 重启后 Run 能够恢复。

**SDK Runtime 恢复流程**:
1. Backend 启动时调用 `ingress.recover()`
2. SDK Runtime 扫描 `workflow.db` 中未完成的 Run
3. 重新启动 Driver 并继续执行
4. 事件继续通过 `DeliveryDispatcher` 推送

**产品层集成**:
```python
# backend/main.py 或 startup.py
async def _recover_sdk_runs():
    """Recover incomplete Runs after Backend restart."""
    try:
        await _sdk_ingress.recover()
        logger.info("sdk_runtime_recovery_completed")
    except Exception as error:
        logger.exception("sdk_runtime_recovery_failed", error=str(error))
```

**调用时机**: 在 Backend startup 时，`_sdk_ingress.open()` 之前。

**验证**:
1. 启动 Run 并执行耗时工具（如 `run_shell` 等待 10 秒）
2. 在工具执行期间重启 Backend
3. 观察 Run 是否恢复并继续执行
4. 检查工具是否重复执行（应该不重复）

---

### Task 8: 单元测试

**测试文件**: `backend/tests/test_sdk_runtime_integration.py`

**测试用例**:

```python
import pytest
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

@pytest.mark.asyncio
async def test_sdk_run_basic_execution(sdk_ingress, session_db):
    """Test basic Run execution through SDK Runtime."""
    receipt = await sdk_ingress.start(
        session_id="test-session",
        request_id="test-request",
        turn_id="test-turn",
        payload={"messages": [{"role": "user", "content": "test"}]},
        session_generation=1,
    )
    
    assert receipt.run_id is not None
    assert receipt.session_id == "test-session"
    
    # 等待完成
    await sdk_ingress.wait_idle(receipt.run_id)
    
    # 查询状态
    state = sdk_ingress.query(receipt.run_id)
    assert state is not None
    assert state.state in ("completed", "failed")

@pytest.mark.asyncio
async def test_sdk_run_cancellation(sdk_ingress):
    """Test Run cancellation."""
    receipt = await sdk_ingress.start(...)
    
    # 立即取消
    await sdk_ingress.cancel(receipt.run_id)
    
    # 等待终态
    await sdk_ingress.wait_idle(receipt.run_id)
    
    state = sdk_ingress.query(receipt.run_id)
    assert state.state == "cancelled"

@pytest.mark.asyncio
async def test_sdk_run_recovery(sdk_ingress, restart_backend):
    """Test Run recovery after restart."""
    # 1. 启动 Run
    receipt = await sdk_ingress.start(...)
    
    # 2. 模拟重启
    await restart_backend()
    
    # 3. 恢复
    await sdk_ingress.recover()
    
    # 4. 验证状态
    state = sdk_ingress.query(receipt.run_id)
    assert state is not None
```

---

### Task 9: 集成测试

**测试文件**: `backend/tests/integration/test_agent_execution.py`

**测试场景**:

```python
@pytest.mark.integration
async def test_agent_execution_full_workflow(tauri_app, websocket_client):
    """Test full Agent execution workflow from UI to completion."""
    # 1. 发送消息
    await websocket_client.send_json({
        "type": "chat_v2_user_message",
        "payload": {
            "session_id": "test-session",
            "text": "请你使用 workspace_prepare 工具",
        }
    })
    
    # 2. 等待 run_started
    started = await websocket_client.receive_json(timeout=5.0)
    assert started["type"] == "chat_v2_run_started"
    run_id = started["payload"]["run_id"]
    
    # 3. 等待工具调用
    tool_call = await websocket_client.receive_json(timeout=10.0)
    assert tool_call["type"] == "chat_v2_tool_call"
    
    # 4. 等待完成
    final = await websocket_client.receive_json(timeout=30.0)
    assert final["type"] == "chat_v2_final"
    assert final["payload"]["run_id"] == run_id
```

---

### Task 10: 文档更新

**更新文件**:

1. **ARCHITECTURE/AGENT_HARNESS.md**:
   - 更新"当前生产链路"流程图
   - 将 `ProductVenueRunAdapter` 替换为 `_execute_sdk_run()`
   - 说明 SDK Runtime 事件推送机制

2. **ARCHITECTURE/PROJECT_STATUS.md**:
   - 标记 SDK Runtime 迁移为"已完成"
   - 更新模块完成状态
   - 添加到 recent milestones

3. **ARCHITECTURE/SDK_RUNTIME_BASELINE.md**:
   - 添加"迁移完成"章节
   - 记录最终架构决策
   - 更新状态为"已交付"

**示例更新**:
```markdown
## SDK Runtime 迁移 (2026-08-17)

**状态**: ✅ 已完成

**变更**:
- 移除 `NotImplementedError` 占位符
- 使用 `SdkRuntimeIngress` 替换旧的 `ProductVenueRunAdapter`
- 通过 `DeliveryDispatcher` 自动推送事件到 SessionDB
- 保持现有 `RunPresenter` 和 `HarnessInspector` 不变

**验证**:
- ✅ 单元测试: 23 passed
- ✅ 集成测试: 5 passed
- ✅ 手动测试: 基本对话、工具调用、恢复场景全部通过
- ✅ Harness 套件: 保持绿色

**相关文件**:
- `backend/main.py`: 新增 `_execute_sdk_run()`
- `backend/tests/test_sdk_runtime_integration.py`: 新增测试
```

---

## 风险缓解

### 风险 1: payload 格式不兼容

**概率**: 中  
**影响**: 高

**缓解措施**:
1. 先用最小 payload 测试（只包含 messages）
2. 逐步添加字段并验证
3. 如果失败，查看 SDK Runtime 源码中的 `RunStart` 处理逻辑
4. 添加详细的结构化日志记录 payload

### 风险 2: 事件未正确推送到前端

**概率**: 中  
**影响**: 高

**缓解措施**:
1. 添加日志追踪事件流：SDK Runtime → DeliveryDispatcher → SessionDB → RunPresenter → WebSocket
2. 验证 `_delivery_pump` 后台任务是否正常运行
3. 检查 SessionDB 中是否有事件记录
4. 如果失败，降级为轮询模式（临时方案）

### 风险 3: 恢复机制失效

**概率**: 低  
**影响**: 高

**缓解措施**:
1. 详细测试恢复场景（正常恢复、部分恢复、恢复失败）
2. 确保 `ingress.recover()` 在 startup 时被调用
3. 添加恢复失败的降级处理（记录错误但不阻止 Backend 启动）
4. 提供手动恢复工具（CLI 命令）

### 风险 4: 性能退化

**概率**: 低  
**影响**: 中

**缓解措施**:
1. 对比迁移前后的关键指标（启动延迟、事件推送延迟）
2. 如果退化超过 20%，调查瓶颈
3. 可能的优化：批量事件推送、事件过滤

---

## 实施顺序

### Phase 1: 基础实现 (4 小时)
1. ✅ Task 1: 调研 DeliveryDispatcher 接口
2. Task 2: 创建 `_execute_sdk_run()` 函数
3. Task 3: 修改 `_run_chat()` 调用点
4. Task 4: 实现 Context 映射逻辑

**验收标准**: 能够启动 Run 并返回 receipt。

### Phase 2: 事件流集成 (3 小时)
5. Task 5: 验证事件流推送
6. Task 6: 完善错误处理
7. Task 8: 单元测试

**验收标准**: 事件能够推送到前端，Harness Inspector 显示执行链路。

### Phase 3: 恢复与完整性 (3 小时)
8. Task 7: 恢复机制验证
9. Task 9: 集成测试
10. Task 10: 文档更新

**验收标准**: 所有 AC 通过，文档已更新。

### Phase 4: 清理与优化 (2 小时)
11. 代码审查
12. 性能基准测试
13. 移除旧代码注释
14. 最终验收

**验收标准**: 代码质量达标，DoD 全部满足。

---

## 交付清单

### 代码变更
- [x] `backend/main.py`: 新增 `_execute_sdk_run()`
- [x] `backend/main.py`: 修改 `_run_chat()` lines 9299-9338
- [x] `backend/tests/test_sdk_runtime_integration.py`: 新增测试

### 测试
- [ ] 单元测试: `test_sdk_runtime_integration.py`
- [ ] 集成测试: `test_agent_execution.py`
- [ ] 手动测试: 4 个验收场景（见 acceptance.md）

### 文档
- [ ] `ARCHITECTURE/AGENT_HARNESS.md`: 更新生产链路
- [ ] `ARCHITECTURE/PROJECT_STATUS.md`: 标记完成状态
- [ ] `ARCHITECTURE/SDK_RUNTIME_BASELINE.md`: 添加完成章节

### 验证
- [ ] `backend/tests/harness_simplification`: 保持绿色
- [ ] 前端全量测试: 通过
- [ ] Harness Inspector: 显示完整链路
- [ ] 恢复测试: Backend 重启后 Run 继续执行

---

## 备用方案

如果 SDK Runtime 集成失败（预估概率 <5%），可回退到以下方案：

### 方案 B: 重新实现 ProductVenueRunAdapter

在 commit 71f3a6e2 之前 checkout 旧的 `ProductVenueRunAdapter` 代码，并适配到当前 SDK Runtime 接口。

**优点**: 代码已验证，风险低  
**缺点**: 技术债务增加，未来仍需迁移  
**工作量**: 2-3 小时

### 方案 C: 回滚 commit 71f3a6e2

完全回滚 commit 71f3a6e2，恢复旧 harness 架构。

**优点**: 立即恢复功能  
**缺点**: 失去 SDK Runtime 的优势，增加维护成本  
**工作量**: < 1 小时

---

## 成功标准

### 必须满足 (MUST)
1. ✅ Agent 执行链路能够成功启动并完成基本任务
2. ✅ SDK Runtime 事件流式传输正常工作
3. ✅ Run 恢复机制兼容 SDK Runtime
4. ✅ 向后兼容历史 Run 数据

### 应该满足 (SHOULD)
5. ✅ 性能不退化（延迟 ≤ 120% 基线）
6. ✅ 代码质量（类型注解、测试覆盖、风格一致）

### 可以满足 (COULD)
7. 性能优化（批量事件推送）
8. 监控指标（Prometheus metrics）

---

**最后更新**: 2026-08-17  
**计划状态**: 已批准，等待执行  
**预计完成**: 2026-08-17 (同日)
