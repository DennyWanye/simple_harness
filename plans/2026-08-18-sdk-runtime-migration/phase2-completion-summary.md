# Phase 2 完成：关键发现总结

> 完成时间：2026-08-18
> 状态：所有技术问题已解决，可以开始实现

## 关键发现

### 1. RunPresentationContext 创建模式 ✅

**位置**: `backend/main.py:9258-9290`

**所有依赖都已经存在于该函数作用域中**：

```python
context = RunPresentationContext(
    session_id=session_id,                    # ✅ 函数参数
    text=text,                                # ✅ 函数参数
    websocket=websocket,                      # ✅ 函数参数
    services=service_context,                 # ✅ 局部变量
    config=config,                            # ✅ 全局变量
    messages=[],                              # ✅ 空列表
    session_db=session_db,                    # ✅ 局部变量
    vector_worker=vector_worker,              # ✅ 局部变量
    activity_store=activity,                  # ✅ 局部变量 (None)
    provider_chain=provider_chain,            # ✅ 局部变量
    fallback_provider=provider,               # ✅ 局部变量
    request_id=request_id,                    # ✅ 函数参数
    max_iterations=50,                        # ✅ 常量
    is_sentinel=is_sentinel,                  # ✅ 局部变量
    broadcast=_broadcast_default_chat_peers,  # ✅ 函数引用
    send_final=_send_chat_final,              # ✅ 函数引用
    emit_context_usage=_emit_context_usage,   # ✅ 函数引用
    intent_label_from_turn=_intent_label_from_turn,  # ✅ 函数引用
    billing_ledger=billing_ledger,            # ✅ 局部变量
    provider=provider,                        # ✅ 局部变量
    run_id=root_ref.run_id,                   # ✅ 局部变量
    task_scope_id=task_scope_id,              # ✅ 函数参数
    provider_binding_epoch=int(               # ✅ 从 frozen_session_binding 提取
        frozen_session_binding.get("binding_epoch") or 0
    ),
    provider_binding_provider_id=(            # ✅ 从 frozen_session_binding 提取
        str(frozen_session_binding.get("provider_id") or "") or None
    ),
    provider_binding_model_id=(               # ✅ 从 frozen_session_binding 提取
        str(frozen_session_binding.get("preferred_model") or "") or None
    ),
)
```

**结论**: 所有需要的变量都已经在 `main.py:9301` (NotImplementedError 位置) 的作用域中可用！

### 2. SDK Delivery Payload 不包含 run_id ⚠️

**证据**: `conformance.py:378`
```python
DeliverySpec("delivery-1", "fixture", "delivery-key", {"result":"physical"})
```

Payload 只包含业务数据，不包含 run_id。

**解决方案**: 需要在 idempotency_key 中编码 run_id，或者检查 SDK 是否在实际 RunEvent delivery 时会在 payload 中包含 run_id。

### 3. 实现位置精确定位 ✅

**NotImplementedError 位置**: `backend/main.py:9301-9312`

**需要替换的代码块**: `lines 9301-9338` (包括旧的注释代码)

**可用上下文**:
- 前面 (lines 9228-9298): 所有依赖变量的初始化
- 后面 (lines 9339+): 错误处理逻辑

### 4. 现有的 LegacyProductDomainSink ✅

**位置**: `backend/main.py:9291-9298`

```python
sink = LegacyProductDomainSink(
    websocket=websocket,
    services=service_context,
    session_db=session_db,
    broadcast=_broadcast_default_chat_peers,
    plan_waiters={},
    tool_registry=deskpet_tool_registry_v2,
)
```

这是旧的 domain sink，但我们可能不需要它（SDK Runtime 有自己的 delivery 机制）。

## 修订后的实现计划

### 简化的 ProductDeliveryAdapter

**关键洞察**: RunPresenter 已经处理了大部分逻辑，我们只需要：
1. 接收 SDK delivery payload
2. 反序列化为 RunEvent
3. 调用 `presenter.present_run_event()`

**不需要**:
- ❌ 手动调用 `websocket.send_json()` (RunPresenter 内部处理)
- ❌ 手动调用 `session_db.append_message()` (RunPresenter 内部处理)
- ❌ 复杂的事件格式化 (RunPresenter 内部处理)

### 简化的 _execute_sdk_run()

**核心流程**:
1. 复用现有的 `context` 和 `sink` 初始化 (lines 9258-9298)
2. 创建 ProductDeliveryAdapter，传入 `context`
3. 注册到全局 registry
4. 调用 `_sdk_ingress.start()`
5. 发送 `run_started` 事件
6. 等待完成
7. 清理 registry

### run_id 路由问题的解决方案

**Option A**: 假设 SDK 在实际 RunEvent delivery 时会在 payload 中包含 run_id
- 检查 `payload.get("run_id")` 或 `payload.get("event", {}).get("run_id")`
- 如果不存在，记录警告并跳过

**Option B**: 修改 SDK Runtime 的 DeliverySpec 创建，在 idempotency_key 中编码 run_id
- 格式: `f"{run_id}:{event_id}"`
- 在 _DeliverySink 中解析

**推荐**: 先尝试 Option A (检查 payload)，如果不work再考虑 Option B。

## 下一步行动

现在所有技术细节都已清楚，可以开始实现：

1. ✅ 所有开放问题已解决
2. ✅ RunPresentationContext 创建模式已找到
3. ✅ 所有依赖变量已定位
4. ✅ 实现位置已精确定位
5. ⚠️ run_id 路由仍需运行时验证（两个备选方案）

**状态**: 准备进入 Phase 3 - 代码实现
