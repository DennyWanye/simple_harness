# Test error handling fixes for stuck status bug
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from deskpet.agent.run_presenter import _present_final, _present_error
from agent.agent_loop import FinalEvent, ErrorEvent


@pytest.mark.asyncio
async def test_present_final_websocket_failure_sends_fallback_error():
    """Test that when send_final fails, we send fallback error event."""

    # Create mock context
    context = MagicMock()
    context.session_id = "test_session_001"
    context.run_id = "test_run_001"
    context.request_id = "test_request_001"
    context.task_scope_id = "test_scope_001"
    context.session_db = None  # Skip DB operations

    # Mock send_final to raise exception
    async def send_final_fail(*args, **kwargs):
        raise ConnectionError("WebSocket closed")

    context.send_final = AsyncMock(side_effect=send_final_fail)

    # Mock direct websocket send (should succeed)
    context.websocket = MagicMock()
    context.websocket.send_json = AsyncMock()

    # Create test state
    state = MagicMock()
    state.final_text = "Test response"
    state.final_reasoning = None

    # Create final event
    event = FinalEvent(
        type="final",
        task_id="test_task",
        iteration=1,
        content="Test response",
        stop_reason="stop",
    )

    # Call presenter
    await _present_final(event, context, state)

    # Verify send_final was called and failed
    context.send_final.assert_called_once()

    # Verify fallback error was sent
    context.websocket.send_json.assert_called_once()
    call_args = context.websocket.send_json.call_args[0][0]

    assert call_args["type"] == "chat_v2_error"
    assert call_args["payload"]["session_id"] == "test_session_001"
    assert call_args["payload"]["reason"] == "final_delivery_failed"
    print("✅ Fallback error event sent when send_final fails")


@pytest.mark.asyncio
async def test_present_error_send_both_failure_sends_fallback():
    """Test that when _send_both fails, we send fallback error event."""

    # Create mock context
    context = MagicMock()
    context.session_id = "test_session_002"
    context.run_id = "test_run_002"
    context.request_id = "test_request_002"
    context.task_scope_id = "test_scope_002"
    context.services = {}

    # Mock websocket
    context.websocket = MagicMock()
    context.websocket.send_json = AsyncMock()

    # Create test state
    state = MagicMock()

    # Create error event
    event = ErrorEvent(
        type="error",
        task_id="test_task",
        iteration=1,
        reason="test_error",
        detail="Test error detail",
    )

    # Patch _send_both to fail
    with patch('deskpet.agent.run_presenter._send_both',
               side_effect=RuntimeError("Send both failed")):

        # Call presenter
        await _present_error(event, context, state)

        # Verify fallback error was sent
        context.websocket.send_json.assert_called_once()
        call_args = context.websocket.send_json.call_args[0][0]

        assert call_args["type"] == "chat_v2_error"
        assert call_args["payload"]["session_id"] == "test_session_002"
        assert call_args["payload"]["reason"] == "test_error"
        print("✅ Fallback error event sent when _send_both fails")


@pytest.mark.asyncio
async def test_health_check_timeout_values():
    """Verify health check timeout constants are set correctly."""

    # Read the controlWs.ts file
    # Repository-relative: the original absolute path only existed on the
    # authoring machine (/Users/denny/...).
    control_ws = (
        Path(__file__).resolve().parents[2]
        / "tauri-app" / "src" / "code-panel" / "controlWs.ts"
    )
    with open(control_ws, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check timeout values
    assert 'SESSION_HEALTH_CHECK_INTERVAL = 15_000' in content or 'SESSION_HEALTH_CHECK_INTERVAL = 15000' in content, \
        "Health check interval should be 15 seconds"

    assert 'SESSION_STUCK_TIMEOUT = 60_000' in content or 'SESSION_STUCK_TIMEOUT = 60000' in content, \
        "Stuck timeout should be 60 seconds"

    print("✅ Health check timeouts set correctly: 15s interval, 60s timeout")


if __name__ == "__main__":
    asyncio.run(test_present_final_websocket_failure_sends_fallback_error())
    asyncio.run(test_present_error_send_both_failure_sends_fallback())
    asyncio.run(test_health_check_timeout_values())
    print("\n✅ All error handling tests passed!")
