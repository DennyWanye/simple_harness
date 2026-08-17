# Consumer Adapter Layer Implementation Plan

## Goal
Bridge the gap between simple consumer-provided ports and complex SDK kernel ports.

## Architecture

```
Consumer Code (Simple)
    ↓
Consumer Ports (ProviderPort, ToolExecutorPort, etc.)
    ↓
Adapter Layer (NEW - this task)
    ↓
SDK Kernel Ports (ProviderInvocationCoordinator, EffectExecutor, etc.)
    ↓
Runtime Kernel
```

## Components to Implement

### 1. Consumer-facing Port Protocols
**File:** `src/simple_harness/runtime/consumer_ports.py`

```python
class ProviderPort(Protocol):
    async def invoke(self, request: ProviderRequest, *, cancel) -> ProviderResponse: ...

class ToolExecutorPort(Protocol):
    async def execute(self, call: ToolCall, context: dict) -> ToolResult: ...

class AuthorizationPort(Protocol):
    async def request_authorization(self, request: AuthorizationRequest) -> AuthorizationResult: ...
```

### 2. Adapter Implementations
**File:** `src/simple_harness/runtime/adapters.py`

- `ConsumerProviderAdapter` - ProviderPort → ProviderInvocationCoordinator
- `ConsumerToolExecutorAdapter` - ToolExecutorPort → EffectExecutor
- `ConsumerAuthorizationAdapter` - AuthorizationPort → SDK AuthorizationPort
- Default implementations for reconciliation/delivery/checkpoint ports

### 3. High-level Builder
**File:** `src/simple_harness/runtime/consumer_builder.py`

```python
@dataclass
class ConsumerRuntimePorts:
    provider: ProviderPort
    tool_executor: ToolExecutorPort
    authorization: AuthorizationPort
    context: ContextPort
    memory_query: MemoryQueryPort | None = None
    memory_write: MemoryWritePort | None = None

async def build_consumer_runtime(ports: ConsumerRuntimePorts) -> Runtime:
    # Bridge consumer ports → kernel ports
    # Return ready-to-use Runtime
```

## Implementation Steps

1. Create consumer port protocols (already partially done in `ports.py`)
2. Implement adapter classes
3. Create ConsumerRuntimePorts dataclass
4. Implement build_consumer_runtime() factory
5. Update exports in __init__.py
6. Fix minimal-consumer example to use new API
7. Test end-to-end

## Estimated Time
3-5 hours for implementation + testing
