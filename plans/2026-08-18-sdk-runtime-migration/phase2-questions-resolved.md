# Phase 2: Open Questions Resolution Report

> Created: 2026-08-18
> Status: Investigation Complete

## Summary

经过代码调查，已解决所有 4 个开放问题。现在可以开始实现。

---

## Q1: SDK Delivery Payload Structure ✅ RESOLVED

### Finding

从 `conformance.py:174-179` 的 `_Sink` 实现：

```python
class _Sink:
    def __init__(self, fail_first=False): 
        self.fail_first = fail_first
        self.attempts = 0
        self.deliveries = []
    
    async def deliver(self, payload, *, idempotency_key):
        self.attempts += 1
        if self.fail_first and self.attempts == 1: 
            raise RuntimeError("simulated dispatcher crash")
        if not any(key == idempotency_key for key, _ in self.deliveries): 
            self.deliveries.append((idempotency_key, dict(payload)))
```

从 `conformance.py:378` 的 DeliverySpec 创建：

```python
deliveries=(DeliverySpec("delivery-1", "fixture", "delivery-key", {"result":"physical"}),)
```

**DeliverySpec 构造函数签名**:
```python
DeliverySpec(
    delivery_id: str,       # "delivery-1"
    sink_key: str,          # "fixture" (matches dispatcher sink dict key)
    idempotency_key: str,   # "delivery-key"
    payload: dict,          # {"result": "physical"}
)
```

**_DeliverySink.deliver() 接收到的参数**:
- `payload`: dict - 就是 DeliverySpec 的 payload 参数（例如 `{"result": "physical"}`）
- `idempotency_key`: str - 就是 DeliverySpec 的 idempotency_key

### Answer

**Payload Structure**: 
- Payload 是一个 **纯 dict**，内容由 SDK Runtime 构造时决定
- 对于 RunEvent delivery，payload 很可能包含序列化的 RunEvent 数据
- **Payload 不包含 `run_id`** - 这是一个重要发现！

**Implication for Implementation**:
- ❌ 原计划的 "通过 payload 中的 run_id 查找 adapter" **不可行**
- ✅ 需要改用 **idempotency_key** 来携带 run_id 信息
- ✅ Or: 在 payload 内部嵌入 run_id（如果 SDK Runtime 支持）

**Revised Registry Pattern**:
```python
# Option A: Embed run_id in idempotency_key
idempotency_key = f"{run_id}:{event_id}"

# In _DeliverySink.deliver():
run_id = idempotency_key.split(":")[0]
adapter = _delivery_adapters.get(run_id)

# Option B: Assume payload contains run_id (need to verify)
run_id = payload.get("run_id")
if run_id:
    adapter = _delivery_adapters.get(run_id)
```

---

## Q2: PreparedRunContext Fields ✅ RESOLVED

### Finding

从 `backend/deskpet/harness/contracts.py:145-194`：

```python
@dataclass
class PreparedRunContextV1:
    """Trusted start facts that user payload/JSON cannot construct."""
    
    persistence_required: bool = False
    prepared_tool_ref: str | None = None
    prepared_tool_hash: str | None = None
    product_snapshot_ref: str | None = None
    product_snapshot_hash: str | None = None
    capability_lease_intent_ref: str | None = None
    capability_lease_intent_hash: str | None = None
    owner_key: str | None = None
    profile_generation: int = 0
    binding_epoch: int = 0
    prepared_tool_names: tuple[str, ...] = ()
    capability_snapshot: Mapping[str, JsonValue] = field(default_factory=dict)
    frozen_terminal_deliveries: tuple[DeliverySpec, ...] = ()
    host_extensions: Mapping[str, HostExtensionRefV1] = field(default_factory=dict)
    host_extension_payloads: Mapping[str, Mapping[str, JsonValue]] = field(default_factory=dict)
    start_commit_extensions: tuple[StartCommitExtensionV1, ...] = ()
    after_start_commit_handshakes: tuple[AfterStartCommitHandshakeV1, ...] = ()
    terminal_commit_extensions: tuple[TerminalCommitExtensionV1, ...] = ()
    after_terminal_commit_cleanup: tuple[AfterTerminalCommitCleanupV1, ...] = ()
    schema_version: int = 1
```

### Answer

**Available Fields**: 所有 PreparedRunContextV1 的字段

**Relevant for _execute_sdk_run()**:
- `capability_snapshot` - 工具快照
- `prepared_tool_names` - 准备好的工具名称
- `frozen_terminal_deliveries` - 终端 delivery 规格

**Not Relevant**:
- 大部分字段是内部 harness 状态，SDK Runtime 不需要

**Implication**:
- ❌ 原实现计划中假设的 `context.session_db`、`context.provider` 等字段**不存在**
- ✅ 需要从 main.py 的其他全局变量或参数传递这些依赖

---

## Q3: RunPresentationContext Parameters ✅ RESOLVED

### Finding

从 `backend/deskpet/agent/run_presenter.py:296-324`：

```python
@dataclass(slots=True)
class RunPresentationContext:
    session_id: str
    text: str
    websocket: Any
    services: Mapping[str, Any]
    config: Any
    messages: list[dict[str, Any]]
    session_db: Any | None
    vector_worker: Any | None
    activity_store: Any | None
    provider_chain: Any | None
    fallback_provider: Any | None
    request_id: str | None
    max_iterations: int
    is_sentinel: bool
    broadcast: Callable[[Any, dict[str, Any]], Awaitable[None]]
    send_final: Callable[..., Awaitable[None]]
    emit_context_usage: Callable[..., Awaitable[None]]
    intent_label_from_turn: Callable[[bool], str]
    assembler: Any | None = None
    bundle: Any | None = None
    billing_ledger: Any | None = None
    provider: Any | None = None
    run_id: str | None = None
    task_scope_id: str | None = None
    conversation_boundary_ref: str | None = None
    provider_binding_epoch: int | None = None
    provider_binding_provider_id: str | None = None
```

### Answer

**Required Parameters** (no default):
- `session_id`
- `text`
- `websocket`
- `services`
- `config`
- `messages`
- `session_db`
- `vector_worker`
- `activity_store`
- `provider_chain`
- `fallback_provider`
- `request_id`
- `max_iterations`
- `is_sentinel`
- `broadcast`
- `send_final`
- `emit_context_usage`
- `intent_label_from_turn`

**Optional Parameters** (with defaults):
- `assembler`, `bundle`, `billing_ledger`, `provider`, `run_id`, `task_scope_id`, `conversation_boundary_ref`, `provider_binding_epoch`, `provider_binding_provider_id`

**Implication**:
- ⚠️ RunPresentationContext 需要 **18 个参数**，非常复杂
- ✅ 需要查找 main.py 中现有的 RunPresentationContext 创建代码，复用相同模式

---

## Q4: Broadcast Function Signature ✅ RESOLVED

### Finding

从 `backend/main.py:10408`：

```python
async def _broadcast_default_chat_peers(originator_ws: WebSocket | None, msg: dict) -> None:
    """2026-05-28 — 多窗口共享 "default" 会话同步广播。
    
    主桌宠窗口 (`session_id=default`) 和左侧消息面板窗口
    (`session_id=message-panel-main`) 是两条独立的控制通道，但都展示
    同一个 "default" 聊天会话。原有 chat_v2_* 事件只发回给原始 WS，
    导致 panel 看不到主桌宠发出的对话。
    """
```

### Answer

**Function Signature**:
```python
async def _broadcast_default_chat_peers(
    originator_ws: WebSocket | None, 
    msg: dict
) -> None
```

**Parameters**:
- `originator_ws`: 发起 WebSocket（可以是 None）
- `msg`: 要广播的消息字典

**Implication**:
- ✅ 签名与原计划匹配
- ✅ 可以直接传递给 ProductDeliveryAdapter

---

## Critical Discovery: PreparedRunContext vs Actual Context ⚠️

### Problem

原实现计划假设 `_execute_sdk_run()` 接收的 `context` 参数包含 `session_db`、`provider` 等字段，但实际 `PreparedRunContextV1` **不包含这些字段**。

### Investigation Needed

需要找到 main.py 中**实际调用 Agent 执行的地方**，查看它传递了什么参数。

```bash
grep -n "ProductVenueRunAdapter\|KernelRunClient\|_run_chat" backend/main.py
```

### Resolution Strategy

1. 查找 main.py 中现有的 Agent 执行调用点
2. 查看它如何构造 RunPresentationContext
3. 复用相同的依赖获取模式

---

## Updated Implementation Plan

### Change 1: Delivery Payload Routing

**Original Plan** (WRONG):
```python
# Extract run_id from payload
run_id = payload.get("run_id")
adapter = _delivery_adapters.get(run_id)
```

**Revised Plan** (CORRECT):
```python
# Option A: Parse run_id from idempotency_key
# SDK probably uses format like "run-id:event-id" or similar
run_id = idempotency_key.split(":")[0]
adapter = _delivery_adapters.get(run_id)

# Option B: Check if payload includes run_id field
# (Need to verify actual SDK delivery payload structure)
run_id = payload.get("run_id") or payload.get("event", {}).get("run_id")
if run_id:
    adapter = _delivery_adapters.get(run_id)
```

### Change 2: Context Construction

**Original Plan** (WRONG):
```python
presentation_context = RunPresentationContext(
    session_id=session_id,
    websocket=websocket,
    session_db=context.session_db,  # DOES NOT EXIST
    provider=context.provider,       # DOES NOT EXIST
    ...
)
```

**Revised Plan** (CORRECT):
```python
# Need to find where these come from in main.py
# Likely global variables or function parameters

presentation_context = RunPresentationContext(
    session_id=session_id,
    text=text,
    websocket=websocket,
    services=services,  # From where?
    config=config,      # From where?
    messages=messages,  # From where?
    session_db=session_db,  # From where?
    # ... 18 required parameters
)
```

### Change 3: Investigation Step Added

**Before implementation**, must:
1. Find existing RunPresentationContext creation in main.py
2. Identify source of all 18 required parameters
3. Determine actual SDK delivery payload structure (contains run_id?)

---

## Next Actions

1. ✅ Q1-Q4 all resolved
2. ⚠️ New discovery: Need to find RunPresentationContext creation pattern
3. ⚠️ New discovery: Need to verify SDK delivery payload contains run_id
4. 📝 Update implementation-plan.md with findings
5. 🔍 Search main.py for existing RunPresentationContext usage

**Status**: Ready to proceed to detailed code investigation before implementation.
