"""Unit tests for ProductDeliveryAdapter."""
import pytest
from unittest.mock import AsyncMock, Mock
from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter


@pytest.fixture
def mock_presenter():
    """Create mock RunPresenter."""
    presenter = Mock()
    presenter.present_run_event = AsyncMock()
    return presenter


@pytest.fixture
def mock_adapter():
    """Create mock CanonicalRunEventPresentationAdapter."""
    return Mock()


@pytest.fixture
def mock_context():
    """Create mock RunPresentationContext."""
    return Mock()


@pytest.fixture
def mock_state():
    """Create mock PresentationState."""
    return Mock()


@pytest.fixture
def delivery_adapter(mock_presenter, mock_adapter, mock_context, mock_state):
    """Create ProductDeliveryAdapter instance."""
    return ProductDeliveryAdapter(
        session_id="test_session",
        request_id="test_request",
        run_id="test_run",
        presenter=mock_presenter,
        adapter=mock_adapter,
        context=mock_context,
        state=mock_state
    )


@pytest.mark.asyncio
async def test_handle_event_basic(delivery_adapter, mock_presenter):
    """Test basic event handling."""
    payload = {
        "event_id": "evt_001",
        "run_id": "test_run",
        "root_run_id": "test_run",
        "session_id": "test_session",
        "durable_seq": 1,
        "candidate": {
            "event_key": "test_key_001",
            "kind": "status_changed",
            "status": "accepted",
            "driver_kind": "react",
            "correlation": {},
            "payload": {},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567890.0
    }
    idempotency_key = "test_run:event:1"

    await delivery_adapter.handle_event(payload, idempotency_key)

    assert mock_presenter.present_run_event.call_count == 1


@pytest.mark.asyncio
async def test_handle_event_idempotency(delivery_adapter, mock_presenter):
    """Test idempotency - duplicate events are skipped."""
    payload = {
        "event_id": "evt_001",
        "run_id": "test_run",
        "root_run_id": "test_run",
        "session_id": "test_session",
        "durable_seq": 1,
        "candidate": {
            "event_key": "test_key_001",
            "kind": "status_changed",
            "status": "accepted",
            "driver_kind": "react",
            "correlation": {},
            "payload": {},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567890.0
    }
    idempotency_key = "test_run:event:1"

    # Send same event twice
    await delivery_adapter.handle_event(payload, idempotency_key)
    await delivery_adapter.handle_event(payload, idempotency_key)

    # Should only process once
    assert mock_presenter.present_run_event.call_count == 1


@pytest.mark.asyncio
async def test_handle_event_different_keys(delivery_adapter, mock_presenter):
    """Test different idempotency keys allow processing."""
    payload1 = {
        "event_id": "evt_001",
        "run_id": "test_run",
        "root_run_id": "test_run",
        "session_id": "test_session",
        "durable_seq": 1,
        "candidate": {
            "event_key": "test_key_001",
            "kind": "status_changed",
            "status": "accepted",
            "driver_kind": "react",
            "correlation": {},
            "payload": {},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567890.0
    }
    payload2 = {
        "event_id": "evt_002",
        "run_id": "test_run",
        "root_run_id": "test_run",
        "session_id": "test_session",
        "durable_seq": 2,
        "candidate": {
            "event_key": "test_key_002",
            "kind": "tool_call",
            "status": "waiting",
            "driver_kind": "react",
            "correlation": {},
            "payload": {"tool": "search"},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567891.0
    }

    await delivery_adapter.handle_event(payload1, "test_run:event:1")
    await delivery_adapter.handle_event(payload2, "test_run:event:2")

    assert mock_presenter.present_run_event.call_count == 2


@pytest.mark.asyncio
async def test_handle_event_passes_context(delivery_adapter, mock_presenter, mock_adapter, mock_context, mock_state):
    """Test that handle_event passes all required parameters to presenter."""
    payload = {
        "event_id": "evt_001",
        "run_id": "test_run",
        "root_run_id": "test_run",
        "session_id": "test_session",
        "durable_seq": 1,
        "candidate": {
            "event_key": "test_key_001",
            "kind": "status_changed",
            "status": "accepted",
            "driver_kind": "react",
            "correlation": {},
            "payload": {},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567890.0
    }
    idempotency_key = "test_run:event:1"

    await delivery_adapter.handle_event(payload, idempotency_key)

    call_kwargs = mock_presenter.present_run_event.call_args.kwargs
    assert "event" in call_kwargs
    assert call_kwargs["adapter"] is mock_adapter
    assert call_kwargs["context"] is mock_context
    assert call_kwargs["state"] is mock_state


@pytest.mark.asyncio
async def test_finish_cleanup(delivery_adapter):
    """Test finish() performs cleanup."""
    # Add some idempotency keys with valid event payloads
    payload1 = {
        "event_id": "e1",
        "run_id": "test_run",
        "root_run_id": "test_run",
        "session_id": "test_session",
        "durable_seq": 1,
        "candidate": {
            "event_key": "key1",
            "kind": "status",
            "status": "accepted",
            "driver_kind": "react",
            "correlation": {},
            "payload": {},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567890.0
    }
    payload2 = {
        "event_id": "e2",
        "run_id": "test_run",
        "root_run_id": "test_run",
        "session_id": "test_session",
        "durable_seq": 2,
        "candidate": {
            "event_key": "key2",
            "kind": "status",
            "status": "waiting",
            "driver_kind": "react",
            "correlation": {},
            "payload": {},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567891.0
    }

    await delivery_adapter.handle_event(payload1, "key1")
    await delivery_adapter.handle_event(payload2, "key2")

    assert len(delivery_adapter._idempotency_seen) == 2

    await delivery_adapter.finish()

    # After finish, idempotency set should be cleared
    assert len(delivery_adapter._idempotency_seen) == 0
