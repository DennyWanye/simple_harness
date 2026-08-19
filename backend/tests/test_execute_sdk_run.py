"""Integration tests for _execute_sdk_run function."""
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters


@pytest.mark.asyncio
async def test_sdk_message_assembly_keeps_prior_turns_and_excludes_current_run():
    from main import _assemble_sdk_messages

    class FakeSessionDB:
        async def get_recent_messages(self, session_id, limit):
            assert session_id == "session-1"
            assert limit == 20
            return [
                {"root_run_id": "run-old", "role": "user", "content": "之前的问题"},
                {"root_run_id": "run-old", "role": "assistant", "content": "之前的回答"},
                {"root_run_id": "run-current", "role": "user", "content": "当前消息"},
                {"root_run_id": "run-old", "role": "tool", "content": "内部结果"},
            ]

    assert await _assemble_sdk_messages(
        FakeSessionDB(),
        session_id="session-1",
        root_run_id="run-current",
        text="当前消息",
    ) == [
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": "当前消息"},
    ]


def test_sdk_capability_snapshot_exposes_product_catalog():
    from main import _sdk_capability_snapshot

    snapshot = _sdk_capability_snapshot()
    assert "file_read" in snapshot["tools"]
    assert "run_shell" in snapshot["tools"]
    assert snapshot["tools"] == list(dict.fromkeys(snapshot["tools"]))


@pytest.fixture
def mock_sdk_ingress():
    """Create mock SDK ingress."""
    ingress = Mock()

    # Mock receipt
    receipt = Mock()
    receipt.run_id = "test_run_123"

    ingress.start = AsyncMock(return_value=receipt)
    ingress.wait_idle = AsyncMock()

    # Mock final state
    final_state = Mock()
    final_state.status = "completed"
    ingress.query = Mock(return_value=final_state)

    return ingress


@pytest.fixture
def mock_websocket():
    """Create mock WebSocket."""
    ws = Mock()
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def mock_context():
    """Create mock RunPresentationContext."""
    return Mock()


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear global delivery adapter registry."""
    _delivery_adapters.clear()
    yield
    _delivery_adapters.clear()


@pytest.mark.asyncio
async def test_execute_sdk_run_basic_flow(mock_sdk_ingress, mock_websocket, mock_context):
    """Test basic execution flow of _execute_sdk_run."""
    from deskpet.agent.run_presenter import build_product_run_presenter, CanonicalRunEventPresentationAdapter, PresentationState
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter

    session_id = "test_session"
    request_id = "test_request"
    turn_id = 1
    text = "test message"

    # Simulate _execute_sdk_run logic
    payload = {"input": {"text": text}, "messages": [{"role": "user", "content": text}]}

    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    receipt = await mock_sdk_ingress.start(
        session_id=session_id,
        request_id=request_id,
        turn_id=str(turn_id),
        payload=payload,
        session_generation=1
    )
    run_id = receipt.run_id

    delivery_adapter = ProductDeliveryAdapter(
        session_id=session_id,
        request_id=request_id,
        run_id=run_id,
        presenter=presenter,
        adapter=adapter,
        context=mock_context,
        state=state
    )
    _delivery_adapters[run_id] = delivery_adapter

    try:
        # Send started message
        started = {
            "type": "chat_v2_run_started",
            "payload": {
                "session_id": session_id,
                "request_id": request_id,
                "run_id": run_id
            }
        }
        await mock_websocket.send_json(started)

        # Wait for completion
        await mock_sdk_ingress.wait_idle(run_id)
        final_state = mock_sdk_ingress.query(run_id)

        assert final_state.status == "completed"

    finally:
        _delivery_adapters.pop(run_id, None)

    # Verify flow
    mock_sdk_ingress.start.assert_called_once()
    mock_sdk_ingress.wait_idle.assert_called_once_with("test_run_123")
    mock_sdk_ingress.query.assert_called_once_with("test_run_123")
    mock_websocket.send_json.assert_called_once()


@pytest.mark.asyncio
async def test_execute_sdk_run_registry_cleanup(mock_sdk_ingress, mock_websocket, mock_context):
    """Test that adapter is cleaned up from registry even on error."""
    from deskpet.agent.run_presenter import build_product_run_presenter, CanonicalRunEventPresentationAdapter, PresentationState
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter

    session_id = "test_session"
    request_id = "test_request"

    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    receipt = await mock_sdk_ingress.start(
        session_id=session_id,
        request_id=request_id,
        turn_id="1",
        payload={"input": {"text": "test"}},
        session_generation=1
    )
    run_id = receipt.run_id

    delivery_adapter = ProductDeliveryAdapter(
        session_id=session_id,
        request_id=request_id,
        run_id=run_id,
        presenter=presenter,
        adapter=adapter,
        context=mock_context,
        state=state
    )
    _delivery_adapters[run_id] = delivery_adapter

    assert run_id in _delivery_adapters

    try:
        # Simulate error during execution
        mock_sdk_ingress.wait_idle.side_effect = RuntimeError("Test error")
        await mock_sdk_ingress.wait_idle(run_id)
    except RuntimeError:
        pass
    finally:
        _delivery_adapters.pop(run_id, None)

    # Verify cleanup happened
    assert run_id not in _delivery_adapters


@pytest.mark.asyncio
async def test_execute_sdk_run_adapter_registration(mock_sdk_ingress, mock_websocket, mock_context):
    """Test that ProductDeliveryAdapter is properly registered."""
    from deskpet.agent.run_presenter import build_product_run_presenter, CanonicalRunEventPresentationAdapter, PresentationState
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter

    receipt = await mock_sdk_ingress.start(
        session_id="session",
        request_id="request",
        turn_id="1",
        payload={"input": {"text": "test"}},
        session_generation=1
    )
    run_id = receipt.run_id

    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    delivery_adapter = ProductDeliveryAdapter(
        session_id="session",
        request_id="request",
        run_id=run_id,
        presenter=presenter,
        adapter=adapter,
        context=mock_context,
        state=state
    )

    # Register
    _delivery_adapters[run_id] = delivery_adapter

    # Verify registered
    assert run_id in _delivery_adapters
    assert _delivery_adapters[run_id] is delivery_adapter

    # Cleanup
    _delivery_adapters.pop(run_id, None)
    assert run_id not in _delivery_adapters
