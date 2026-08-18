# Phase 0: Architecture Baseline Update

## 架构校准结果

### 1. 当前状态分析

**Commit 范围**: `1ec95c37` (上次校准) → `15333879` (HEAD)

**关键变更**:
- Commit `71f3a6e2`: 删除了旧 harness 代码 (62,349 行删除)
  - 删除了 `backend/deskpet/harness/bootstrap.py`
  - 删除了 `backend/deskpet/harness/drivers/react.py` (5,132行)
  - 删除了 `backend/deskpet/harness/drivers/react_loop.py` (1,190行)
  - 删除了 `backend/deskpet/harness/drivers/workflow.py` (294行)
  - 删除了 `backend/deskpet/harness/adapters/product_composition.py` (205行)
  - 删除了大量旧 harness 测试文件

- **backend/main.py 变更** (327行改动):
  - 删除了 311 行旧代码
  - 新增了 16 行，包括 NotImplementedError 和 TODO 注释
  - Lines 9301-9312: 新增了 NotImplementedError，明确说明 SDK Runtime 集成未完成
  - Lines 9314-9338: 保留了旧代码的注释版本（引用 ProductVenueRunResult）

### 2. 未完成的迁移识别

从 git 历史和当前代码分析，确认了 **THREE** 个未完成的 harness 组件：

#### 2.1 Core Coordinator (main.py:9301-9312)
```python
# TODO(CRITICAL): Implement SDK Runtime direct integration
raise NotImplementedError(
    "Agent execution path not yet migrated to SDK Runtime v0.1.x. "
    "The old Harness architecture was removed but the SDK integration "
    "was not completed. See commit 71f3a6e2."
)
```

**旧实现**: `ProductVenueRunAdapter.open()` → `KernelRunClient.start()` → `observe()`
**需要实现**: `_execute_sdk_run()` 函数，直接调用 SDK Runtime 的 start() 方法

#### 2.2 _DeliverySink (desktop_runtime.py:133-135)
```python
class _DeliverySink:
    async def deliver(self, payload, *, idempotency_key):
        del payload, idempotency_key  # Empty implementation
```

**状态**: 空实现，删除了参数但没有任何逻辑
**职责**: 接收 SDK DeliveryDispatcher 的事件并推送到产品层

#### 2.3 ProductDeliveryAdapter (delivery.py:14-31)
```python
class ProductDeliveryAdapter:
    """Adapter between SDK execution events and product UI delivery."""
    
    def __init__(self):
        # TODO T6.1: Initialize with product delivery system
        pass
```

**状态**: 只有占位符和 TODO 注释
**职责**: 
- Forward SDK execution events to RunPresenter
- Update SessionDB with messages and state
- Send WebSocket events to frontend
- Generate artifact cards for UI

### 3. 架构文档状态评估

**ARCHITECTURE.md** (Last calibrated: `1ec95c37`):
- 文档声称 "SDK v0.1.1 已经是唯一的生产执行 authority"
- 但实际上 main.py 中的 NotImplementedError 显示集成未完成
- 文档中的流程图显示 `_sdk_ingress.open_venue()` 是活跃入口，但实际会抛出 NotImplementedError

**差异**:
- 文档 (理想状态): "All ingress -> _sdk_ingress (SdkRuntimeIngress)"
- 现实 (当前代码): main.py 会抛出 NotImplementedError，Agent 无法执行

### 4. 需要更新的架构文档段落

#### ARCHITECTURE.md §1.1 (Lines 40-50)
当前描述了 "当前生产架构（使用 SDK v0.1.1）"，但应该添加：
- NotImplementedError 的存在说明迁移未完成
- 三个未实现的 harness 组件：_execute_sdk_run, _DeliverySink, ProductDeliveryAdapter
- 当前状态：SDK Runtime 已安装和初始化，但执行链未接通

#### ARCHITECTURE.md §2 (Lines 80-146)
"Current Request Lifecycle (SDK v0.1.1)" 应该更新为：
- 标记为 "目标状态" 而非 "当前状态"
- 添加 "实现状态：PENDING，三个核心组件未完成"

### 5. 新增架构文档需求

建议新增 `ARCHITECTURE/SDK_MIGRATION_STATUS.md` 记录：
- SDK Runtime v0.1.1 安装和初始化状态：✅ COMPLETE
- Event streaming model: Old (pull) → New (push): ⚠️ INCOMPLETE
- Core coordinator replacement: ProductVenueRunAdapter → _execute_sdk_run: ⚠️ INCOMPLETE
- Delivery sink implementation: _DeliverySink: ⚠️ INCOMPLETE
- Product delivery adapter: ProductDeliveryAdapter: ⚠️ INCOMPLETE

## 建议行动

1. **更新 ARCHITECTURE.md 校准锚点** 到 HEAD (`15333879`)
2. **添加迁移状态说明** 到 §1.1 和 §2
3. **创建 SDK_MIGRATION_STATUS.md** 详细记录三个未完成组件
4. **更新 PROJECT_STATUS.md** 标记 SDK Runtime migration 为 IN_PROGRESS

## Challenger 验证点

Phase 0 challenger subagent 应该验证：
1. ✅ NotImplementedError 位置和内容准确识别
2. ✅ 三个未完成组件的职责描述准确
3. ✅ 旧实现（ProductVenueRunAdapter）的删除确认
4. ✅ 新实现（SdkRuntimeIngress）的初始化状态
5. ⚠️ ARCHITECTURE.md 是否误导性地声称 SDK 已是生产 authority
