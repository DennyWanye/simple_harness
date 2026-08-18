# SDK Runtime Migration Implementation Plan

> Created: 2026-08-18
> Status: Phase 1 - Planning
> Baseline: acceptance.md (2026-08-18) + SDK_MIGRATION_STATUS.md

## 1. Executive Summary

**Goal**: 完成 SDK Runtime v0.1.1 迁移，接通 Agent 执行链，使 Agent 能够通过 SDK Runtime 执行用户请求。

**Current Blocker**: `backend/main.py:9308` 抛出 `NotImplementedError`，三个核心 harness 组件未实现。

**Implementation Order**:
1. **Component 1**: ProductDeliveryAdapter (foundation)
2. **Component 2**: _DeliverySink (bridge)
3. **Component 3**: _execute_sdk_run() (orchestrator)

**Estimated Complexity**:
- ProductDeliveryAdapter: ~150-200 lines (medium complexity)
- _DeliverySink: ~20-30 lines (low complexity)
- _execute_sdk_run(): ~80-120 lines (medium complexity)
- Total: ~250-350 lines of new code

---

## 2. Architecture Context

### 2.1 Event Flow (Old vs New)

**Old Architecture (DELETED in 71f3a6e2)**:
```
main.py
  → ProductVenueRunAdapter.open()
       → TurnPreparer.prepare()
       → KernelRunClient.start()
       → observe() AsyncIterator[RunEvent]  # Pull model
  → async for event in session.events:  # Manual event loop
       → RunPresenter.present()
       → SessionDB.append_message()
       → websocket.send_json()
```

**New Architecture (Target)**:
```
main.py
  → _execute_sdk_run()  ❌ TO IMPLEMENT
       → SdkRuntimeIngress.start()  ✅ EXISTS
            → SDK Runtime (background execution)
                 → DeliveryDispatcher._delivery_pump  ⚠️ EXISTS (background task)
                      → _DeliverySink.deliver()  ❌ TO IMPLEMENT
                           → ProductDeliveryAdapter.handle_event()  ❌ TO IMPLEMENT
                                → RunPresenter.present()  ✅ EXISTS
                                → SessionDB.append_message()  ✅ EXISTS
                                → websocket.send_json()  ✅ EXISTS
  → await _sdk_ingress.wait_idle(run_id)  ✅ EXISTS
  → _sdk_ingress.query(run_id)  ✅ EXISTS
```

**Key Difference**: 
- Old: **Pull model** (caller owns event loop)
- New: **Push model** (SDK owns event loop via background task)

### 2.2 Existing Infrastructure (Already Implemented ✅)

1. **SDK Runtime Initialization** (`main.py:9583`)
   - `_activate_product_sdk_runtime()` creates global `_sdk_ingress`
   - Logs "product_sdk_runtime_ready"

2. **SdkRuntimeIngress** (`sdk_adapters/ingress.py`)
   - Methods: `.start()`, `.signal()`, `.cancel()`, `.query()`, `.wait_idle()`
   - `.open()` called at `main.py:9650` to accept new Runs

3. **DeliveryDispatcher** (SDK Runtime internal)
   - Background task `_delivery_pump` already runs
   - Polls UoW for pending deliveries
   - Calls sink's `.deliver()` method

4. **RunPresenter** (`deskpet/agent/run_presenter.py:327`)
   - `build_product_run_presenter()` creates configured presenter
   - Handlers registered for: AssistantDeltaEvent, ToolCallEvent, ToolResultEvent, FinalEvent, ErrorEvent, etc.
   - Methods: `.present()`, `.present_run_event()`, `.finish_turn()`

5. **SessionDB** (product database)
   - Methods: `.append_message()`, `.update_message()`, etc.

6. **WebSocket Connection** (main.py)
   - `websocket.send_json()` for real-time delivery

### 2.3 Event Types (SDK → Product)

SDK Runtime emits **RunEvent** with following event kinds:
- `transcript` → AssistantDeltaEvent (token streaming)
- `tool.call_started` → ToolCallEvent
- `tool.call_completed` → ToolResultEvent
- `run.completed` → FinalEvent
- `run.failed` → ErrorEvent
- `workflow.*` → PipelineEvent (workflow-specific)

**RunEventPresentationAdapter** already exists (`run_presenter.py:31-100`):
- `CanonicalRunEventPresentationAdapter.to_presentation_events()`
- Converts SDK `RunEvent` → tuple of `AgentEvent`

---

## 3. Implementation Plan

### Component 1: ProductDeliveryAdapter ⭐ FOUNDATION

**File**: `backend/deskpet/sdk_adapters/delivery.py`

**Current State** (lines 14-31):
```python
class ProductDeliveryAdapter:
    def __init__(self):
        # TODO T6.1: Initialize with product delivery system
        pass
```

**Required Implementation**:

```python
from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import Awaitable, Callable

import structlog
from starlette.websockets import WebSocket

from deskpet.agent.run_presenter import (
    RunPresenter,
    RunEventPresentationAdapter,
    CanonicalRunEventPresentationAdapter,
    RunPresentationContext,
    PresentationState,
)
from deskpet.execution.contracts import RunEvent
from deskpet.memory.session_db import SessionDB

logger = structlog.get_logger(__name__)


class ProductDeliveryAdapter:
    """Adapter between SDK execution events and product UI delivery.
    
    Converts SDK RunEvents → AgentEvents → WebSocket messages.
    Persists to SessionDB and broadcasts to peer connections.
    """
    
    def __init__(
        self,
        *,
        session_id: str,
        request_id: str,
        turn_id: int,
        presenter: RunPresenter,
        adapter: RunEventPresentationAdapter,
        context: RunPresentationContext,
        state: PresentationState,
        session_db: SessionDB,
        websocket: WebSocket,
        broadcast: Callable[[WebSocket, dict], Awaitable[None]],
    ):
        """Initialize delivery adapter with product services.
        
        Args:
            session_id: Session identifier
            request_id: Request identifier
            turn_id: Turn number
            presenter: RunPresenter for event formatting
            adapter: RunEvent → AgentEvent converter
            context: Presentation context (provider, max_iterations, etc.)
            state: Mutable presentation state (tracks tool calls, final text)
            session_db: SessionDB for persistence
            websocket: WebSocket for real-time delivery
            broadcast: Broadcast function for peer connections
        """
        self._session_id = session_id
        self._request_id = request_id
        self._turn_id = turn_id
        self._presenter = presenter
        self._adapter = adapter
        self._context = context
        self._state = state
        self._session_db = session_db
        self._websocket = websocket
        self._broadcast = broadcast
        self._idempotency_seen: set[str] = set()
    
    async def handle_event(
        self,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        """Handle one SDK delivery event.
        
        Args:
            payload: SDK delivery payload (contains RunEvent data)
            idempotency_key: Unique key for exactly-once semantics
        """
        # Idempotency: skip if already seen
        if idempotency_key in self._idempotency_seen:
            logger.debug(
                "delivery_idempotency_skip",
                key=idempotency_key,
                session_id=self._session_id,
            )
            return
        
        try:
            # Extract RunEvent from payload
            # (Payload structure TBD - need to check SDK delivery format)
            run_event = self._deserialize_run_event(payload)
            
            # Convert SDK RunEvent → AgentEvent(s) via adapter
            await self._presenter.present_run_event(
                event=run_event,
                adapter=self._adapter,
                context=self._context,
                state=self._state,
            )
            
            # Mark as delivered
            self._idempotency_seen.add(idempotency_key)
            
            logger.debug(
                "delivery_handled",
                key=idempotency_key,
                event_kind=run_event.candidate.kind,
                session_id=self._session_id,
            )
        
        except Exception as exc:
            logger.error(
                "delivery_handler_failed",
                key=idempotency_key,
                error=str(exc),
                session_id=self._session_id,
                exc_info=True,
            )
            # Don't re-raise - delivery failure should not crash SDK Runtime
    
    def _deserialize_run_event(self, payload: dict[str, Any]) -> RunEvent:
        """Deserialize SDK delivery payload → RunEvent.
        
        TODO: Verify actual SDK delivery payload structure.
        May need to import from simple_harness.execution.
        """
        # Placeholder - actual structure TBD
        from deskpet.execution.contracts import RunEvent
        return RunEvent(**payload)
    
    async def finish(self) -> None:
        """Called when Run completes - finalize presentation."""
        await self._presenter.finish_turn(self._context, self._state)
```

**Design Questions**:
1. ❓ What is the exact structure of SDK `DeliveryDispatcher` payload?
2. ❓ Does SDK delivery already include serialized `RunEvent`, or do we reconstruct it?
3. ❓ Should we batch WebSocket sends, or send immediately per event?

**Resolution Strategy**:
- Read SDK source code for `DeliveryDispatcher` payload format
- Check existing `conformance.py` for actual delivery structure
- Start with immediate send, optimize later if needed

---

### Component 2: _DeliverySink ⭐ BRIDGE

**File**: `backend/deskpet/sdk_adapters/desktop_runtime.py`

**Current State** (lines 133-135):
```python
class _DeliverySink:
    async def deliver(self, payload, *, idempotency_key):
        del payload, idempotency_key  # Empty
```

**Required Implementation**:

```python
class _DeliverySink:
    """Bridge between SDK DeliveryDispatcher and ProductDeliveryAdapter."""
    
    def __init__(self, adapter: ProductDeliveryAdapter):
        self._adapter = adapter
    
    async def deliver(self, payload: dict, *, idempotency_key: str) -> None:
        """Deliver one SDK event to product adapter.
        
        Called by SDK DeliveryDispatcher background task.
        
        Args:
            payload: SDK delivery payload (RunEvent data)
            idempotency_key: Unique delivery key from SDK
        """
        await self._adapter.handle_event(
            payload=payload,
            idempotency_key=idempotency_key,
        )
```

**Dependencies**:
- Requires `ProductDeliveryAdapter` to exist
- Must be instantiated in `desktop_runtime.py:267` (where `DeliveryDispatcher` is created)

**Note**: This is a thin shim. The real logic lives in `ProductDeliveryAdapter`.

---

### Component 3: _execute_sdk_run() ⭐ ORCHESTRATOR

**File**: `backend/main.py`

**Location**: Replace lines 9301-9338 (NotImplementedError + old commented code)

**Required Implementation**:

```python
async def _execute_sdk_run(
    *,
    session_id: str,
    request_id: str,
    turn_id: int,
    task_scope_id: str | None,
    text: str,
    context: PreparedRunContext,
    websocket: WebSocket,
) -> None:
    """Execute Agent via SDK Runtime with event delivery to WebSocket.
    
    Replaces old ProductVenueRunAdapter.open() flow.
    
    Args:
        session_id: Session identifier
        request_id: Request identifier
        turn_id: Turn number
        task_scope_id: Optional task scope
        text: User input text
        context: Prepared run context (from TurnPreparer)
        websocket: WebSocket connection for real-time delivery
    """
    # 1. Build payload for SDK Runtime
    payload = {
        "input": {"text": text},
        "context": {
            "session_id": session_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "conversation_boundary_ref": context.conversation_boundary_ref,
        },
    }
    
    # 2. Create ProductDeliveryAdapter
    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    
    presentation_context = RunPresentationContext(
        session_id=session_id,
        websocket=websocket,
        session_db=context.session_db,
        provider=context.provider,
        max_iterations=context.max_iterations,
        assembler=context.assembler,
        bundle=context.bundle,
        billing_ledger=context.billing_ledger,
        activity_store=context.activity_store,
    )
    
    presentation_state = PresentationState()
    
    delivery_adapter = ProductDeliveryAdapter(
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        presenter=presenter,
        adapter=adapter,
        context=presentation_context,
        state=presentation_state,
        session_db=context.session_db,
        websocket=websocket,
        broadcast=_broadcast_default_chat_peers,
    )
    
    # 3. Inject delivery adapter into SDK Runtime
    # TODO: Need to figure out how to pass adapter to existing DeliveryDispatcher
    # Option A: Modify composition.py to accept adapter factory
    # Option B: Use thread-local or context var
    # Option C: Restart SDK Runtime with new sink per request (EXPENSIVE)
    
    # 4. Start SDK Runtime execution
    receipt = await _sdk_ingress.start(
        session_id=session_id,
        request_id=request_id,
        turn_id=str(turn_id),
        payload=payload,
        session_generation=context.session_generation,
    )
    
    # 5. Send run_started event to WebSocket
    started = {
        "type": "chat_v2_run_started",
        "payload": {
            "session_id": session_id,
            "run_id": receipt.run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "conversation_boundary_ref": context.conversation_boundary_ref,
            "conversation_boundary_version": 1,
            "projection_version": 0,
        },
    }
    await websocket.send_json(started)
    await _broadcast_default_chat_peers(websocket, started)
    
    # 6. Wait for SDK Runtime to complete
    # Events are pushed via DeliveryDispatcher → _DeliverySink → ProductDeliveryAdapter
    await _sdk_ingress.wait_idle(receipt.run_id)
    
    # 7. Query final state
    final_state = _sdk_ingress.query(receipt.run_id)
    
    # 8. Finalize presentation
    await delivery_adapter.finish()
    
    logger.info(
        "sdk_run_completed",
        session_id=session_id,
        request_id=request_id,
        run_id=receipt.run_id,
        state=final_state.value,
    )
```

**Design Questions**:
1. ❓ **CRITICAL**: How to inject per-request `ProductDeliveryAdapter` into SDK Runtime?
   - SDK Runtime initialized once at startup with single `DeliveryDispatcher`
   - `DeliveryDispatcher` created with fixed sink dict: `{"desktop": _DeliverySink()}`
   - But we need **different adapter per request** (different websocket, session_id, etc.)

2. ❓ What fields does `PreparedRunContext` actually have?

3. ❓ Should we send `run_started` before or after `_sdk_ingress.start()`?

**Resolution Strategy for Question 1** (CRITICAL):

**Option A: Per-Request Sink Registration** ⭐ RECOMMENDED
- Modify `_DeliverySink` to use a **registry** of adapters keyed by `run_id`
- `_execute_sdk_run()` registers adapter before `.start()`, unregisters after `.wait_idle()`
- `_DeliverySink.deliver()` looks up adapter by `run_id` from payload

```python
# Global registry
_delivery_adapters: dict[str, ProductDeliveryAdapter] = {}

class _DeliverySink:
    async def deliver(self, payload: dict, *, idempotency_key: str) -> None:
        run_id = payload.get("run_id")  # Extract from payload
        adapter = _delivery_adapters.get(run_id)
        if adapter is None:
            logger.warning("delivery_adapter_not_found", run_id=run_id)
            return
        await adapter.handle_event(payload, idempotency_key)

# In _execute_sdk_run():
_delivery_adapters[receipt.run_id] = delivery_adapter
try:
    await _sdk_ingress.wait_idle(receipt.run_id)
finally:
    _delivery_adapters.pop(receipt.run_id, None)
```

**Option B: Context Variable** (thread-local alternative)
- Use `contextvars.ContextVar` to pass adapter through async context
- Less explicit, harder to debug

**Option C: Rebuild SDK Runtime per request** ❌ REJECTED
- Too expensive (startup cost ~100-500ms)
- Loses state between requests

---

## 4. Implementation Sequence

### Step 1: Investigate SDK Delivery Format 🔍

**Before coding**, read SDK source to answer:
1. What is the structure of `DeliveryDispatcher` payload?
2. Does payload include `run_id`?
3. How to reconstruct `RunEvent` from payload?

**Files to read**:
- `simple_harness.execution.delivery.DeliveryDispatcher`
- `backend/deskpet/sdk_adapters/conformance.py` (lines 378-400, shows real usage)

### Step 2: Implement ProductDeliveryAdapter 📝

1. Create full implementation in `delivery.py`
2. Add imports for `RunPresenter`, `RunEventPresentationAdapter`, etc.
3. Implement `__init__()` and `handle_event()`
4. Add `_deserialize_run_event()` based on Step 1 findings
5. Add `finish()` method

### Step 3: Implement _DeliverySink with Registry 📝

1. Add global registry `_delivery_adapters: dict[str, ProductDeliveryAdapter]`
2. Update `_DeliverySink.__init__()` (remove adapter parameter)
3. Update `_DeliverySink.deliver()` to lookup adapter by run_id
4. Add logging for missing adapters

### Step 4: Implement _execute_sdk_run() 📝

1. Replace `NotImplementedError` block (lines 9301-9312)
2. Build payload for SDK Runtime
3. Create presenter/adapter/context/state
4. Create `ProductDeliveryAdapter`
5. Register adapter in global registry
6. Call `_sdk_ingress.start()`
7. Send `run_started` WebSocket event
8. Wait for completion via `wait_idle()`
9. Query final state
10. Unregister adapter
11. Call `delivery_adapter.finish()`

### Step 5: Unit Tests 🧪

1. **test_product_delivery_adapter.py**
   - Test event deserialization
   - Test idempotency (duplicate keys skipped)
   - Test presenter integration
   - Mock WebSocket/SessionDB

2. **test_delivery_sink.py**
   - Test registry lookup by run_id
   - Test missing adapter handling
   - Test multiple concurrent runs

3. **test_execute_sdk_run.py**
   - Test full execution flow
   - Test adapter registration/unregistration
   - Test error handling
   - Mock SdkRuntimeIngress

### Step 6: Integration Test 🧪

1. Create `test_sdk_execution_integration.py`
2. Start full SDK Runtime stack
3. Send real WebSocket message
4. Verify events stream back
5. Verify SessionDB persistence
6. Verify run completes successfully

### Step 7: Manual E2E Test 🎯

1. Start app via `./scripts/dev.sh`
2. Open UI, send message: "Hello, what can you do?"
3. Verify Agent responds
4. Try tool call: "List running processes"
5. Verify tool result displayed
6. Check backend logs for delivery events

---

## 5. Risk Analysis

### High Risk ⚠️

1. **SDK Delivery Payload Structure Unknown**
   - Impact: Cannot deserialize RunEvent correctly
   - Mitigation: Read SDK source first (Step 1)

2. **Per-Request Adapter Injection**
   - Impact: Cannot route events to correct WebSocket
   - Mitigation: Use registry pattern (Option A)

3. **RunPresentationContext Field Mismatch**
   - Impact: Cannot create context, compilation fails
   - Mitigation: Read actual PreparedRunContext definition

### Medium Risk ⚠️

1. **Event Ordering Guarantees**
   - Impact: Events might arrive out-of-order
   - Mitigation: Rely on SDK's sequential delivery guarantees

2. **WebSocket Disconnection During Execution**
   - Impact: Events lost if WebSocket closes
   - Mitigation: Handle WebSocketDisconnect exception, continue execution

3. **Idempotency Key Collisions**
   - Impact: Duplicate events if keys not unique
   - Mitigation: Trust SDK's idempotency_key generation

### Low Risk ℹ️

1. **Performance Overhead of Registry Lookup**
   - Impact: Minor latency per event (~1-10μs)
   - Mitigation: Acceptable, optimize later if needed

2. **Memory Leak from Registry**
   - Impact: Adapters not cleaned up if `.wait_idle()` never completes
   - Mitigation: Add timeout + periodic cleanup task

---

## 6. Testing Strategy

### Unit Tests (Backend)

```bash
cd backend
python -m pytest tests/sdk_adapters/test_product_delivery_adapter.py -v
python -m pytest tests/sdk_adapters/test_delivery_sink.py -v
python -m pytest tests/test_execute_sdk_run.py -v
```

### Integration Tests (Backend)

```bash
cd backend
python -m pytest tests/sdk_adapters/test_sdk_execution_integration.py -v
```

### Manual E2E Tests

1. **Basic Chat**
   - User: "Hello"
   - Expected: Agent responds with greeting

2. **Tool Call**
   - User: "What processes are running?"
   - Expected: `process_list` tool called, results displayed

3. **Multi-Turn**
   - User: "Create a presentation about cats"
   - Expected: PPT tool called, artifact card shown

4. **Error Handling**
   - Simulate SDK error (modify code temporarily)
   - Expected: Error displayed in UI, doesn't crash

### Acceptance Criteria (from acceptance.md)

- **AC-1**: ✅ Agent execution starts without `NotImplementedError`
- **AC-2**: ✅ ReAct loop executes (tool calls work)
- **AC-3**: ✅ Events stream to WebSocket
- **AC-4**: ✅ Errors handled gracefully
- **AC-5**: ✅ Run state queryable via `_sdk_ingress.query()`
- **AC-6**: ✅ Historical SessionDB data compatible

---

## 7. Rollout Plan

### Phase A: Implementation (Estimated: 4-6 hours)

1. Step 1: SDK investigation (30 min)
2. Step 2: ProductDeliveryAdapter (90 min)
3. Step 3: _DeliverySink (30 min)
4. Step 4: _execute_sdk_run() (60 min)
5. Step 5: Unit tests (90 min)
6. Step 6: Integration test (60 min)

### Phase B: Testing (Estimated: 2-3 hours)

1. Run all unit tests
2. Run integration test
3. Manual E2E testing (all 4 scenarios)
4. Fix any issues found

### Phase C: Documentation Update (Estimated: 30 min)

1. Update ARCHITECTURE.md
   - Remove ⚠️/❌ markers
   - Change "目标状态" to "当前状态"
2. Update SDK_MIGRATION_STATUS.md
   - Mark all three components as ✅ COMPLETE
3. Update PROJECT_STATUS.md
   - Mark SDK Runtime migration as COMPLETE

---

## 8. Open Questions (To Resolve Before Implementation)

### Q1: SDK Delivery Payload Structure ❓
**Question**: What fields does SDK `DeliveryDispatcher` payload contain?
**Answer**: TBD (read SDK source in Step 1)
**Blocking**: ProductDeliveryAdapter._deserialize_run_event()

### Q2: PreparedRunContext Fields ❓
**Question**: What fields are available on `context` parameter in main.py?
**Answer**: TBD (read main.py TurnPreparer output)
**Blocking**: _execute_sdk_run() compilation

### Q3: RunPresentationContext Required Fields ❓
**Question**: What parameters does RunPresentationContext.__init__() accept?
**Answer**: TBD (read run_presenter.py)
**Blocking**: _execute_sdk_run() compilation

### Q4: Broadcast Function Signature ❓
**Question**: What is the signature of `_broadcast_default_chat_peers()`?
**Answer**: TBD (search main.py)
**Blocking**: ProductDeliveryAdapter.__init__()

---

## 9. Success Criteria

### Code Quality
- [ ] All type hints present
- [ ] All functions documented with docstrings
- [ ] Error handling for all external calls
- [ ] Logging at debug/info/error levels

### Testing
- [ ] Unit tests: 100% coverage of new code
- [ ] Integration test: Full execution path tested
- [ ] Manual E2E: All 4 scenarios pass

### Performance
- [ ] Agent response time < 2s for simple queries
- [ ] No memory leaks (registry cleanup verified)
- [ ] No CPU spikes during execution

### Documentation
- [ ] ARCHITECTURE.md updated
- [ ] SDK_MIGRATION_STATUS.md updated
- [ ] Inline comments for complex logic
- [ ] Acceptance.md verified

---

## 10. Next Steps

1. **Get user approval** on this plan
2. **Resolve open questions** (Q1-Q4) via code investigation
3. **Iterate on plan** based on findings
4. **Proceed to Phase 2**: Implementation

---

## Appendix A: File Inventory

### Files to Modify
- `backend/deskpet/sdk_adapters/delivery.py` (ProductDeliveryAdapter)
- `backend/deskpet/sdk_adapters/desktop_runtime.py` (_DeliverySink)
- `backend/main.py` (_execute_sdk_run)

### Files to Create
- `backend/tests/sdk_adapters/test_product_delivery_adapter.py`
- `backend/tests/sdk_adapters/test_delivery_sink.py`
- `backend/tests/test_execute_sdk_run.py`
- `backend/tests/sdk_adapters/test_sdk_execution_integration.py`

### Files to Read (Investigation)
- `simple_harness/execution/delivery.py` (SDK source)
- `backend/deskpet/sdk_adapters/conformance.py` (real usage example)
- `backend/deskpet/agent/run_presenter.py` (RunPresentationContext)
- `backend/main.py` (TurnPreparer, PreparedRunContext, _broadcast_default_chat_peers)

---

## Appendix B: Code Size Estimate

| Component | Lines | Complexity |
|-----------|-------|------------|
| ProductDeliveryAdapter | ~180 | Medium |
| _DeliverySink | ~25 | Low |
| _execute_sdk_run() | ~100 | Medium |
| Unit tests | ~400 | Medium |
| Integration test | ~150 | Medium |
| **Total** | **~855** | **Medium** |
