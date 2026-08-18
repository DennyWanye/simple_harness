"""Unit tests for _DeliverySink."""
import pytest
from unittest.mock import AsyncMock, Mock
from deskpet.sdk_adapters.desktop_runtime import _DeliverySink, _delivery_adapters


@pytest.fixture
def delivery_sink():
    """Create _DeliverySink instance."""
    return _DeliverySink()


@pytest.fixture
def mock_delivery_adapter():
    """Create mock ProductDeliveryAdapter."""
    adapter = Mock()
    adapter.handle_event = AsyncMock()
    return adapter


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear global delivery adapter registry before each test."""
    _delivery_adapters.clear()
    yield
    _delivery_adapters.clear()


@pytest.mark.asyncio
async def test_deliver_strategy_1_direct_run_id(delivery_sink, mock_delivery_adapter):
    """Test Strategy 1: Extract run_id from payload root."""
    run_id = "test_run_123"
    _delivery_adapters[run_id] = mock_delivery_adapter

    payload = {"run_id": run_id, "type": "status_changed"}
    idempotency_key = f"{run_id}:event:1"

    await delivery_sink.deliver(payload, idempotency_key=idempotency_key)

    mock_delivery_adapter.handle_event.assert_called_once_with(payload, idempotency_key)


@pytest.mark.asyncio
async def test_deliver_strategy_2_parse_idempotency_key(delivery_sink, mock_delivery_adapter):
    """Test Strategy 2: Parse run_id from idempotency_key."""
    run_id = "test_run_456"
    _delivery_adapters[run_id] = mock_delivery_adapter

    payload = {"type": "status_changed"}  # No run_id in payload
    idempotency_key = f"{run_id}:event:2"

    await delivery_sink.deliver(payload, idempotency_key=idempotency_key)

    mock_delivery_adapter.handle_event.assert_called_once_with(payload, idempotency_key)


@pytest.mark.asyncio
async def test_deliver_strategy_3_nested_event(delivery_sink, mock_delivery_adapter):
    """Test Strategy 3: Extract run_id from nested event structure."""
    run_id = "test_run_789"
    _delivery_adapters[run_id] = mock_delivery_adapter

    payload = {
        "type": "delivery",
        "event": {
            "run_id": run_id,
            "event_id": "e1",
            "root_run_id": run_id,
            "session_id": "test_session",
            "durable_seq": 1,
            "candidate": {"kind": "tool_call", "status": "running", "driver_kind": "react", "correlation": {}, "error": None, "artifact_refs": []},
            "created_at": 1234567890.0
        }
    }
    idempotency_key = "event:3"  # No run_id in key

    await delivery_sink.deliver(payload, idempotency_key=idempotency_key)

    mock_delivery_adapter.handle_event.assert_called_once_with(payload, idempotency_key)


@pytest.mark.asyncio
async def test_deliver_missing_adapter(delivery_sink, mock_delivery_adapter):
    """Test graceful handling when adapter not found in registry."""
    # Register different run_id
    _delivery_adapters["other_run"] = mock_delivery_adapter

    payload = {"run_id": "missing_run", "type": "status_changed"}
    idempotency_key = "missing_run:event:1"

    # Should not raise, just skip silently
    await delivery_sink.deliver(payload, idempotency_key=idempotency_key)

    # Should not call any adapter
    mock_delivery_adapter.handle_event.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_missing_run_id(delivery_sink, mock_delivery_adapter):
    """Test handling when run_id cannot be extracted."""
    _delivery_adapters["some_run"] = mock_delivery_adapter

    payload = {"type": "status_changed"}  # No run_id anywhere
    idempotency_key = "no_colon_key"  # Can't parse

    # Should not raise
    await delivery_sink.deliver(payload, idempotency_key=idempotency_key)

    # Should not call any adapter
    mock_delivery_adapter.handle_event.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_multiple_adapters(delivery_sink):
    """Test registry can route to different adapters."""
    adapter1 = Mock()
    adapter1.handle_event = AsyncMock()
    adapter2 = Mock()
    adapter2.handle_event = AsyncMock()

    _delivery_adapters["run1"] = adapter1
    _delivery_adapters["run2"] = adapter2

    await delivery_sink.deliver({"run_id": "run1"}, idempotency_key="run1:event:1")
    await delivery_sink.deliver({"run_id": "run2"}, idempotency_key="run2:event:1")

    adapter1.handle_event.assert_called_once()
    adapter2.handle_event.assert_called_once()
