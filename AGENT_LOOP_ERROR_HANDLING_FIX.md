# Agent Loop Error Handling Fix

## Problem Analysis

After extensive investigation, the root cause of the "🔧 工具执行中" stuck status bug is:

### Current Architecture
1. **Frontend** (`controlWs.ts`) sets status to "running" when tool execution starts
2. **Agent Loop** (`agent_loop.py`) yields `ToolBatchEvent` requesting tool execution  
3. **SDK Runtime** (`harness/runtime.py` + `tool_executor.py`) executes tools via `EffectBatchExecutor`
4. **Tool Executor** (`tool_executor.py:838-844`) catches ALL exceptions and converts to `NormalizedToolOutcome.failure()`
5. **Driver** resumes Agent loop with tool results
6. **Agent Loop** should eventually yield `FinalEvent` or `ErrorEvent`
7. **Run Presenter** (`run_presenter.py`) sends `chat_v2_final` or `chat_v2_error` to frontend
8. **Frontend** receives event and sets status back to "idle"

### Where Things Can Fail

#### Scenario A: Tool execution succeeds but loop doesn't terminate
- Tool returns success result
- Agent loop continues with another iteration
- Loop hits max_iterations or other error
- ErrorEvent is emitted but not sent to frontend (presentation layer error)

#### Scenario B: Presentation layer throws exception
- Agent loop emits FinalEvent/ErrorEvent correctly
- `_present_final()` or `_present_error()` throws exception during `send_final()`
- Frontend never receives the event
- Status stays stuck at "running"

#### Scenario C: WebSocket disconnection during tool execution
- Tool execution completes
- Agent loop emits FinalEvent
- WebSocket already closed, event not sent
- Frontend still shows "running"

## Current Mitigations

### ✅ Already Deployed: Health Check (Temporary Fix)
**Location:** `tauri-app/src/code-panel/controlWs.ts:2088-2134`

```typescript
const SESSION_HEALTH_CHECK_INTERVAL = 30_000; // 30 seconds  
const SESSION_STUCK_TIMEOUT = 120_000; // 2 minutes

// Checks every 30 seconds for sessions stuck > 2 minutes
// Forces reset to idle and sends health_check event to backend
```

**Effectiveness:** 
- ✅ Prevents permanent UI freeze
- ✅ Auto-recovers after 2 minutes
- ❌ Doesn't fix root cause
- ❌ 2-minute wait is bad UX

## Recommended Fixes

### Fix #1: Ensure Agent Loop Always Emits Terminal Event (P0)

**Problem:** Agent loop might not emit FinalEvent/ErrorEvent in all error paths

**Solution:** Add try-finally in `agent_loop.py:_run_impl()` to guarantee terminal event

```python
# backend/agent/agent_loop.py around line 1805
async def _run_impl(self, messages, *, task_id=None, ...):
    tid = task_id or new_task_id()
    terminal_event_emitted = False
    
    try:
        # ... existing agent loop logic ...
        
        # When yielding FinalEvent or ErrorEvent
        yield FinalEvent(...)
        terminal_event_emitted = True
        
    except Exception as exc:
        logger.error(
            "agent_loop_uncaught_exception tid=%s error=%s",
            tid, exc, exc_info=True
        )
        if not terminal_event_emitted:
            yield ErrorEvent(
                type="error",
                task_id=tid,
                iteration=0,
                reason="agent_loop_exception",
                detail=f"{type(exc).__name__}: {exc}"
            )
            terminal_event_emitted = True
        raise  # Re-raise so upper layers can handle
    
    finally:
        # Last resort: if no terminal event was emitted, force one
        if not terminal_event_emitted:
            logger.error(
                "agent_loop_no_terminal_event tid=%s forcing_error_event=True",
                tid
            )
            yield ErrorEvent(
                type="error",
                task_id=tid,
                iteration=0,
                reason="no_terminal_event",
                detail="Agent loop ended without emitting terminal event"
            )
```

**Status:** ⚠️ **BLOCKED** - Cannot use `yield` in `finally` block (Python limitation)

**Alternative:** Wrap the entire loop and emit error event on exception in the outer handler

### Fix #2: Ensure Presentation Layer Always Sends Event (P0)

**Problem:** `_present_final()` and `_present_error()` might throw exceptions

**Solution:** Add defensive try-catch in presentation handlers

```python
# backend/deskpet/agent/run_presenter.py:651
async def _present_final(event: AgentEvent, context: RunPresentationContext, state: PresentationState) -> None:
    assert isinstance(event, FinalEvent)
    state.final_text = event.content
    state.final_reasoning = event.reasoning_content
    
    # Try to save to database (optional)
    if context.session_db is not None:
        try:
            message_id = await context.session_db.append_message(...)
            if context.vector_worker is not None and message_id is not None:
                await context.vector_worker.enqueue(message_id, state.final_text)
        except Exception as exc:
            logger.warning('chat_persist_assistant_failed', error=str(exc))
    
    # CRITICAL: Must always send final event, even if DB save fails
    try:
        await context.send_final(
            context.websocket,
            {'type': 'chat_v2_final', 'payload': _task_payload(context, {
                'text': state.final_text,
                'iterations': event.iteration,
                'replace_all_provisional': True
            })},
            session_id=context.session_id,
            request_id=context.request_id
        )
    except Exception as exc:
        logger.error(  # Changed from warning to error
            "run_presenter_final_delivery_failed_CRITICAL",
            session_id=context.session_id,
            run_id=context.run_id,
            request_id=context.request_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        # Last resort: try to send error event
        try:
            await context.websocket.send_json({
                'type': 'chat_v2_error',
                'payload': {
                    'session_id': context.session_id,
                    'request_id': context.request_id,
                    'reason': 'final_delivery_failed',
                    'detail': f'{type(exc).__name__}: {exc}'
                }
            })
        except Exception:
            pass  # Give up, health check will recover
    
    # ... rest of the function ...
```

**Status:** ✅ **FEASIBLE** - Proper error handling in presentation layer

### Fix #3: Add Explicit Timeout in Backend (P1)

**Problem:** Tools can run indefinitely without timeout

**Solution:** Add per-tool execution timeout in tool_executor

```python
# backend/deskpet/harness/tool_executor.py:679
async def execute_at(index: int) -> None:
    if results[index] is not None:
        return
    
    # Add timeout wrapper
    TOOL_EXECUTION_TIMEOUT = 180.0  # 3 minutes per tool
    
    try:
        async with asyncio.timeout(TOOL_EXECUTION_TIMEOUT):
            # ... existing tool execution logic ...
            results[index] = await dispatch_with_run_fence(...)
            
    except asyncio.TimeoutError:
        logger.error(
            "tool_execution_timeout tool=%s effect_id=%s timeout_s=%.1f",
            call.tool_name, context.effect_id, TOOL_EXECUTION_TIMEOUT
        )
        results[index] = NormalizedToolOutcome.failure(
            "executor_timeout",
            f"Tool execution exceeded {TOOL_EXECUTION_TIMEOUT}s timeout"
        )
    except Exception as exc:
        # ... existing exception handler ...
```

**Status:** ✅ **FEASIBLE** - Add timeout to tool execution

### Fix #4: Reduce Health Check Timeout (P1)

**Problem:** 2-minute timeout is too long for stuck status recovery

**Solution:** Reduce to 30-60 seconds

```typescript
// tauri-app/src/code-panel/controlWs.ts:2088
const SESSION_HEALTH_CHECK_INTERVAL = 15_000; // 15 seconds (was 30s)
const SESSION_STUCK_TIMEOUT = 60_000; // 60 seconds (was 120s)
```

**Rationale:**
- Most tool executions complete in < 10 seconds
- 60 second timeout still allows long-running operations
- Better UX: recovers faster when truly stuck

**Status:** ✅ **FEASIBLE** - Simple constant change

## Implementation Plan

### Phase 1: Immediate (Today) ✅
1. ✅ Reduce health check timeout from 120s → 60s
2. ✅ Add better logging in presentation layer
3. ✅ Test with manual UI interaction

### Phase 2: Short-term (This Week)
1. ⚠️ Add tool execution timeout (3 minutes)
2. ⚠️ Add defensive error handling in `_present_final()` and `_present_error()`  
3. ⚠️ Add metrics/monitoring for stuck sessions

### Phase 3: Long-term (Next Sprint)
1. 📝 Investigate Agent loop terminal event guarantee
2. 📝 Add comprehensive integration tests
3. 📝 Consider WebSocket reconnection strategy

## Testing Plan

### Test Case 1: Normal Tool Execution
1. Send message requiring tool use
2. ✅ Verify: status "thinking" → "running" → "idle"
3. ✅ Verify: Tool result displayed correctly

### Test Case 2: Tool Execution Error
1. Send message that triggers invalid tool args
2. ✅ Verify: Error shown to user
3. ✅ Verify: Status returns to "idle"

### Test Case 3: Backend Crash During Tool Execution
1. Send message requiring tool use
2. Kill backend process mid-execution
3. ✅ Verify: Health check recovers after 60s
4. ✅ Verify: Status forced to "idle"

### Test Case 4: WebSocket Disconnect
1. Send message requiring tool use
2. Simulate network interruption
3. ✅ Verify: Health check recovers
4. ✅ Verify: Reconnection works

### Test Case 5: Long-Running Tool
1. Send message requiring slow operation (e.g., large file read)
2. ✅ Verify: Status stays "running" during execution
3. ✅ Verify: Status returns to "idle" after completion
4. ✅ Verify: Timeout triggers if > 3 minutes

## Conclusion

The stuck status bug has multiple potential causes. The health check provides a safety net, but proper error handling throughout the stack is needed for production-quality behavior.

**Immediate Action:** Reduce health check timeout to 60s  
**Follow-up:** Add defensive error handling in presentation layer  
**Long-term:** Comprehensive testing and monitoring

---

**Report Date:** 2026-08-17  
**Investigation By:** Claude Opus 5  
**Status:** Analysis Complete, Ready for Implementation
