# 第三轮测试设计方案

## 🎯 测试目标

第三轮要测试的是**极限场景**和**深层集成**，找到系统的边界和潜在问题。

---

## 📋 测试类别设计

### 类别 1: 状态机压力测试 (StateMachineStressTests)

#### 测试点：
1. **无效状态转换** - idle → idle（重复发送 final 事件）
2. **快速状态翻转** - 100次 idle ↔ running 快速切换
3. **乱序事件** - final 事件在 tool_call 之前到达
4. **重复 final 事件** - 同一个 run_id 发送多次 final
5. **并发状态修改** - 多个线程同时修改状态

---

### 类别 2: 资源管理和极限测试 (ResourceManagementTests)

#### 测试点：
1. **超大 payload** - 10MB JSON 数据
2. **极限并发** - 100 个工具同时执行
3. **内存压力** - 创建 1000 个 mock 对象
4. **文件描述符耗尽模拟** - OSError: Too many open files
5. **线程池耗尽** - asyncio 任务数量极限

---

### 类别 3: 异常类型全覆盖 (ExceptionTypeTests)

#### 测试点：
1. **网络异常** - ConnectionError, TimeoutError, OSError
2. **内存异常** - MemoryError（模拟）
3. **类型异常** - TypeError, AttributeError, KeyError
4. **JSON 异常** - JSONDecodeError, 不可序列化对象
5. **系统异常** - SystemExit, KeyboardInterrupt
6. **自定义异常** - 业务逻辑异常

---

### 类别 4: 复杂集成场景 (ComplexIntegrationTests)

#### 测试点：
1. **多步骤错误恢复** - 步骤1成功 → 步骤2失败 → 回滚 → 重试
2. **嵌套事件处理** - error 事件处理中又抛出 error
3. **长事件链** - 10 个连续的工具调用
4. **混合同步/异步** - 同步代码中调用异步工具
5. **跨模块依赖** - run_presenter → tool_executor → memory

---

### 类别 5: 边缘案例探索 (EdgeCaseTests)

#### 测试点：
1. **极端时间值** - timeout=0, timeout=999999999
2. **特殊对象** - None, {}, [], float('inf'), float('nan')
3. **循环引用** - A引用B，B引用A
4. **不可序列化对象** - 包含 lambda、文件句柄等
5. **超深嵌套** - 1000 层嵌套的字典

---

### 类别 6: 真实 Bug 模拟 (RealWorldBugSimulation)

#### 测试点：
1. **卡住的原始 bug** - 工具执行完成但 UI 不更新
2. **WebSocket 断开重连** - 中途断开，恢复后的状态同步
3. **健康检查误触发** - 正常执行但被误判为卡住
4. **竞态条件** - final 和 error 同时到达
5. **内存泄漏检测** - 长时间运行后的内存增长

---

## 🔧 技术实现策略

### 策略 1: 使用 pytest fixtures 管理复杂环境

```python
@pytest.fixture
async def mock_context_with_state():
    """创建带状态跟踪的 mock context"""
    context = MagicMock(spec=RunPresentationContext)
    context.state_history = []  # 记录状态变化历史
    # ...
    return context
```

### 策略 2: 使用 pytest.mark.parametrize 批量测试

```python
@pytest.mark.parametrize("exception_type,expected_handling", [
    (ConnectionError, "retry"),
    (TimeoutError, "timeout_fallback"),
    (MemoryError, "emergency_shutdown"),
])
async def test_exception_handling(exception_type, expected_handling):
    # ...
```

### 策略 3: 使用 pytest-timeout 防止测试挂死

```python
@pytest.mark.timeout(10)  # 10秒超时
async def test_infinite_loop_protection():
    # ...
```

### 策略 4: 使用 pytest-benchmark 进行性能回归测试

```python
def test_performance_regression(benchmark):
    result = benchmark(some_function)
    assert result < baseline * 1.1  # 不超过基准的110%
```

### 策略 5: 使用 memory_profiler 检测内存泄漏

```python
@pytest.mark.memory_leak_check
async def test_long_running_memory():
    import tracemalloc
    tracemalloc.start()
    # ... 执行 1000 次
    current, peak = tracemalloc.get_traced_memory()
    assert current < 100 * 1024 * 1024  # < 100MB
```

---

## 📊 预期测试规模

| 类别 | 测试数量 | 预计耗时 |
|------|---------|---------|
| 状态机压力 | 5 | 2s |
| 资源管理 | 5 | 5s |
| 异常类型 | 6 | 1s |
| 复杂集成 | 5 | 3s |
| 边缘案例 | 5 | 2s |
| 真实Bug模拟 | 5 | 3s |
| **总计** | **31** | **~16s** |

---

## 🎓 测试金字塔层级

```
           /\
          /  \    E2E Tests (真实Bug模拟)
         /____\
        /      \   Integration Tests (复杂集成)
       /________\
      /          \  Unit Tests (其他4类)
     /____________\
```

---

## 🚨 风险和注意事项

### 风险 1: 测试运行时间过长
**缓解:** 使用 pytest -m "not slow" 标记慢测试

### 风险 2: 测试不稳定（flaky tests）
**缓解:** 重试机制 @pytest.mark.flaky(reruns=3)

### 风险 3: 资源耗尽影响其他测试
**缓解:** 使用 fixtures 的 teardown 清理资源

### 风险 4: Mock 过度导致测试不真实
**缓解:** 混合使用真实对象和 mock

---

## 📝 实现优先级

### P0 (必须实现)
- 状态机压力测试
- 异常类型覆盖
- 真实Bug模拟

### P1 (建议实现)
- 资源管理测试
- 边缘案例探索

### P2 (可选实现)
- 复杂集成场景（需要更多时间）

---

## 🎯 成功标准

1. ✅ 所有测试通过率 ≥ 95%
2. ✅ 发现至少 1-2 个潜在问题
3. ✅ 测试执行时间 < 20 秒
4. ✅ 代码覆盖率提升 5-10%
5. ✅ 无内存泄漏
6. ✅ 无测试污染（tests影响彼此）

---

**下一步:** 开始实现测试代码
