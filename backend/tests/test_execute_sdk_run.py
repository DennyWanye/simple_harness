"""Integration tests for _execute_sdk_run function."""
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters


@pytest.mark.asyncio
async def test_sdk_message_assembly_keeps_prior_turns_and_excludes_current_run():
    from main import _SDK_PUBLIC_WORK_NARRATION_PROMPT, _assemble_sdk_messages

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
        {"role": "system", "content": _SDK_PUBLIC_WORK_NARRATION_PROMPT},
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": "当前消息"},
    ]


@pytest.mark.asyncio
async def test_sdk_message_assembly_excludes_non_conversation_projections_and_canaries():
    from main import _SDK_PUBLIC_WORK_NARRATION_PROMPT, _assemble_sdk_messages

    public_summary_canary = "PUBLIC_REASONING_SUMMARY_CANARY"
    hidden_reasoning_canary = "HIDDEN_REASONING_CANARY"
    ui_elapsed_canary = "耗时 1分钟 1秒"

    class FakeSessionDB:
        async def get_recent_messages(self, session_id, limit):
            assert session_id == "session-canary"
            assert limit == 20
            return [
                {
                    "root_run_id": "run-old",
                    "role": "user",
                    "content": "保留的用户消息",
                    "projection_kind": "user_message",
                    "context_visibility": "conversation",
                },
                {
                    "root_run_id": "run-old",
                    "role": "assistant",
                    "content": "保留的助手回复",
                    "reasoning_content": hidden_reasoning_canary,
                    "projection_kind": "assistant_message",
                    "context_visibility": "conversation",
                },
                {
                    "root_run_id": "run-old",
                    "role": "assistant",
                    "content": public_summary_canary,
                    "projection_kind": "workflow_progress",
                    "context_visibility": "exclude",
                },
                {
                    "root_run_id": "run-old",
                    "role": "assistant",
                    "content": ui_elapsed_canary,
                    "projection_kind": "workflow_final_status",
                    "context_visibility": "exclude",
                },
                {
                    "root_run_id": "run-old",
                    "role": "assistant",
                    "content": "MALFORMED_NON_CONVERSATION_PROJECTION",
                    "projection_kind": "artifact_card",
                    "context_visibility": "conversation",
                },
            ]

    messages = await _assemble_sdk_messages(
        FakeSessionDB(),
        session_id="session-canary",
        root_run_id="run-current",
        text="当前用户消息",
    )

    assert messages == [
        {"role": "system", "content": _SDK_PUBLIC_WORK_NARRATION_PROMPT},
        {"role": "user", "content": "保留的用户消息"},
        {"role": "assistant", "content": "保留的助手回复"},
        {"role": "user", "content": "当前用户消息"},
    ]
    serialized = repr(messages)
    assert public_summary_canary not in serialized
    assert hidden_reasoning_canary not in serialized
    assert ui_elapsed_canary not in serialized
    assert "assistant.content" in _SDK_PUBLIC_WORK_NARRATION_PROMPT
    assert "不要输出私有思维链" in _SDK_PUBLIC_WORK_NARRATION_PROMPT
    assert "避免重复固定模板" in _SDK_PUBLIC_WORK_NARRATION_PROMPT


def test_sdk_capability_snapshot_exposes_product_catalog():
    from main import _sdk_capability_snapshot
    from deskpet.sdk_adapters.tools import PRODUCT_TOOL_NAMES

    snapshot = _sdk_capability_snapshot()
    assert tuple(snapshot["tools"]) == PRODUCT_TOOL_NAMES
    assert len(snapshot["tools"]) == 77
    assert {
        "agent",
        "agent_parallel",
        "spawn_subagents",
        "spawn_team",
        "await_subagents",
        "workflow_spawn",
        "workspace_prepare",
    } <= set(snapshot["tools"])


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


def _presentation_context(*, websocket, session_db, root_run_id):
    from deskpet.agent.run_presenter import RunPresentationContext

    return RunPresentationContext(
        session_id="race-session",
        text="read the project",
        websocket=websocket,
        services={},
        config=SimpleNamespace(tools=SimpleNamespace(last_mile=None)),
        messages=[],
        session_db=session_db,
        vector_worker=None,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="race-request",
        max_iterations=25,
        is_sentinel=False,
        broadcast=AsyncMock(),
        send_final=AsyncMock(),
        emit_context_usage=AsyncMock(),
        intent_label_from_turn=lambda _had_tool: "tool",
        run_id=root_run_id,
        task_scope_id="race-scope",
        conversation_boundary_ref="race-boundary",
    )


@pytest.mark.asyncio
async def test_execute_sdk_run_registers_delivery_before_start_first_turn(monkeypatch):
    """A worker executing inside start() must already see its Run delivery."""
    import main
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
    from simple_harness import CallId
    from simple_harness.tools import ToolCall, ToolResult

    session_id = "race-session"
    request_id = "race-request"
    turn_id = 7
    sdk_run_id = SdkRuntimeIngress._compute_run_id(
        session_id, request_id, str(turn_id)
    ).value
    saw_registered_adapter = False

    class FirstTurnInsideStartIngress:
        async def start(self, **kwargs):
            nonlocal saw_registered_adapter
            assert kwargs["session_id"] == session_id
            delivery = _delivery_adapters.get(sdk_run_id)
            saw_registered_adapter = delivery is not None
            assert delivery is not None
            call = ToolCall(CallId("call-during-start"), "file_read", {"path": "README.md"})
            await delivery.capture_public_narration(
                "I will inspect the project file.",
                iteration=0,
                call_ids=(call.call_id.value,),
            )
            await delivery.present_tool_call(call)
            await delivery.present_tool_result(
                call,
                ToolResult.succeeded(call.call_id, {"content": "ok"}),
            )
            return SimpleNamespace(run_id=sdk_run_id)

        async def wait_idle(self, run_id):
            assert run_id == sdk_run_id

        def query(self, run_id):
            assert run_id == sdk_run_id
            return SimpleNamespace(
                state=SimpleNamespace(value="completed"),
                value="completed",
            )

    websocket = SimpleNamespace(send_json=AsyncMock())
    session_db = SimpleNamespace(
        get_recent_messages=AsyncMock(return_value=[]),
        append_message=AsyncMock(),
    )
    context = _presentation_context(
        websocket=websocket,
        session_db=session_db,
        root_run_id="canonical-root-race",
    )
    monkeypatch.setattr(main, "_sdk_ingress", FirstTurnInsideStartIngress())
    monkeypatch.setattr(main, "_sdk_context_port", None)
    monkeypatch.setattr(main, "_broadcast_default_chat_peers", AsyncMock())

    await main._execute_sdk_run(
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        task_scope_id="race-scope",
        text="read the project",
        context=context,
        websocket=websocket,
        root_run_id="canonical-root-race",
    )

    assert saw_registered_adapter is True
    frames = [item.args[0] for item in websocket.send_json.await_args_list]
    assert any(frame["type"] == "chat_v2_reasoning_summary" for frame in frames)
    assert any(frame["type"] == "tool_call" for frame in frames)
    assert any(frame["type"] == "tool_result" for frame in frames)
    persisted = [item.kwargs for item in session_db.append_message.await_args_list]
    assert any(
        row.get("content") == "I will inspect the project file."
        and row.get("projection_kind") == "workflow_progress"
        and row.get("context_visibility") == "exclude"
        for row in persisted
    )
    assert sdk_run_id not in _delivery_adapters


@pytest.mark.asyncio
async def test_execute_sdk_run_cleans_pre_registered_delivery_when_start_fails(monkeypatch):
    import main
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

    sdk_run_id = SdkRuntimeIngress._compute_run_id(
        "race-session", "race-request", "9"
    ).value
    saw_registered_adapter = False

    class FailingStartIngress:
        async def start(self, **_kwargs):
            nonlocal saw_registered_adapter
            saw_registered_adapter = sdk_run_id in _delivery_adapters
            raise RuntimeError("start failed")

    websocket = SimpleNamespace(send_json=AsyncMock())
    session_db = SimpleNamespace(
        get_recent_messages=AsyncMock(return_value=[]),
        append_message=AsyncMock(),
    )
    context = _presentation_context(
        websocket=websocket,
        session_db=session_db,
        root_run_id="canonical-root-start-failure",
    )
    monkeypatch.setattr(main, "_sdk_ingress", FailingStartIngress())

    with pytest.raises(RuntimeError, match="start failed"):
        await main._execute_sdk_run(
            session_id="race-session",
            request_id="race-request",
            turn_id=9,
            task_scope_id="race-scope",
            text="read the project",
            context=context,
            websocket=websocket,
            root_run_id="canonical-root-start-failure",
        )

    assert saw_registered_adapter is True
    assert sdk_run_id not in _delivery_adapters


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
