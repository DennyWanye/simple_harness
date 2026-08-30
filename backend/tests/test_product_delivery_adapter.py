"""Unit tests for ProductDeliveryAdapter."""
import pytest
from unittest.mock import AsyncMock, Mock
from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter


@pytest.fixture
def mock_presenter():
    """Create mock RunPresenter."""
    presenter = Mock()
    presenter.present_run_event = AsyncMock()
    presenter.present = AsyncMock()
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
async def test_projects_sdk_tool_call_and_result(delivery_adapter, mock_presenter):
    from simple_harness import CallId
    from simple_harness.tools import ToolCall, ToolResult
    from agent.agent_loop import AssistantMessageEvent, ToolCallEvent, ToolResultEvent

    call = ToolCall(CallId("call-visible"), "write_file", {"path": "a.txt"})
    await delivery_adapter.present_tool_call(call)
    await delivery_adapter.present_tool_result(
        call,
        ToolResult.succeeded(CallId("call-visible"), {"bytes_written": 2}),
    )

    events = [item.args[0] for item in mock_presenter.present.await_args_list]
    assert [type(event) for event in events] == [
        AssistantMessageEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    assert events[0].content == "我正在更新相关文件，完成后会整理结果。"
    assert events[1].tool_call.name == "write_file"
    assert events[2].tool_call_id == "call-visible"
    assert events[2].outcome_status == "succeeded"
    assert '"bytes_written": 2' in events[2].result


@pytest.mark.asyncio
async def test_tool_call_projection_thaws_nested_sdk_arguments(
    delivery_adapter, mock_presenter
):
    import json

    from agent.agent_loop import ToolCallEvent
    from simple_harness import CallId
    from simple_harness.tools import ToolCall

    call = ToolCall(
        CallId("call-nested"),
        "todo_write",
        {
            "items": [
                {
                    "content": "Inspect audit coverage",
                    "activeForm": "Inspecting audit coverage",
                    "status": "in_progress",
                }
            ]
        },
    )

    await delivery_adapter.present_tool_call(call)

    events = [item.args[0] for item in mock_presenter.present.await_args_list]
    projected = next(event for event in events if isinstance(event, ToolCallEvent))
    assert json.loads(json.dumps(projected.tool_call.arguments)) == {
        "items": [
            {
                "content": "Inspect audit coverage",
                "activeForm": "Inspecting audit coverage",
                "status": "in_progress",
            }
        ]
    }


@pytest.mark.asyncio
async def test_projects_public_narration_before_tool_with_stable_iteration(
    delivery_adapter, mock_presenter
):
    from agent.agent_loop import AssistantMessageEvent, ToolCallEvent, ToolResultEvent
    from simple_harness import CallId
    from simple_harness.tools import ToolCall, ToolResult

    call = ToolCall(CallId("call-narrated"), "file_read", {"path": "README.md"})
    await delivery_adapter.capture_public_narration(
        "I will inspect the project files.",
        iteration=3,
        call_ids=("call-narrated",),
    )

    await delivery_adapter.present_tool_call(call)
    await delivery_adapter.present_tool_result(
        call,
        ToolResult.succeeded(CallId("call-narrated"), {"content": "ok"}),
    )

    events = [item.args[0] for item in mock_presenter.present.await_args_list]
    assert [type(event) for event in events] == [
        AssistantMessageEvent,
        ToolCallEvent,
        ToolResultEvent,
    ]
    assert events[0].content == "I will inspect the project files."
    assert events[0].reasoning_content == ""
    assert events[0].tool_calls == []
    assert [event.iteration for event in events] == [3, 3, 3]


@pytest.mark.asyncio
async def test_empty_public_narration_projects_safe_tool_fallback(
    delivery_adapter, mock_presenter
):
    from agent.agent_loop import AssistantMessageEvent, ToolCallEvent
    from simple_harness import CallId
    from simple_harness.tools import ToolCall

    await delivery_adapter.capture_public_narration(
        "  \n ", iteration=4, call_ids=("call-without-narration",)
    )
    await delivery_adapter.present_tool_call(
        ToolCall(CallId("call-without-narration"), "file_read", {})
    )

    events = [item.args[0] for item in mock_presenter.present.await_args_list]
    assert len(events) == 2
    assert isinstance(events[0], AssistantMessageEvent)
    assert events[0].content == "我正在读取目标文件进行核对，完成后会整理结果。"
    assert events[0].reasoning_content == ""
    assert isinstance(events[1], ToolCallEvent)
    assert [event.iteration for event in events] == [4, 4]


@pytest.mark.asyncio
async def test_public_narration_projection_failure_does_not_block_tool_call(
    delivery_adapter, mock_presenter
):
    from agent.agent_loop import AssistantMessageEvent, ToolCallEvent
    from simple_harness import CallId
    from simple_harness.tools import ToolCall

    async def present(event, context, state):
        del context, state
        if isinstance(event, AssistantMessageEvent):
            raise RuntimeError("narration projection unavailable")

    mock_presenter.present.side_effect = present
    await delivery_adapter.capture_public_narration(
        "Public update.",
        iteration=5,
        call_ids=("call-after-projection-failure",),
    )
    await delivery_adapter.present_tool_call(
        ToolCall(CallId("call-after-projection-failure"), "file_read", {})
    )

    events = [item.args[0] for item in mock_presenter.present.await_args_list]
    assert len(events) == 2
    assert isinstance(events[0], AssistantMessageEvent)
    assert isinstance(events[1], ToolCallEvent)
    assert events[1].iteration == 5


@pytest.mark.asyncio
async def test_public_narration_creates_exclude_context_durable_summary():
    from types import SimpleNamespace

    from deskpet.agent.run_presenter import (
        PresentationState,
        RunPresentationContext,
        build_product_run_presenter,
    )
    from simple_harness import CallId
    from simple_harness.tools import ToolCall

    websocket = SimpleNamespace(send_json=AsyncMock())
    session_db = SimpleNamespace(append_message=AsyncMock())
    context = RunPresentationContext(
        session_id="session-durable-summary",
        text="read",
        websocket=websocket,
        services={},
        config=SimpleNamespace(tools=SimpleNamespace(last_mile=None)),
        messages=[],
        session_db=session_db,
        vector_worker=None,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-durable-summary",
        max_iterations=25,
        is_sentinel=False,
        broadcast=AsyncMock(),
        send_final=AsyncMock(),
        emit_context_usage=AsyncMock(),
        intent_label_from_turn=lambda _had_tool: "tool",
        run_id="run-durable-summary",
    )
    delivery = ProductDeliveryAdapter(
        session_id=context.session_id,
        request_id=context.request_id or "",
        run_id=context.run_id or "",
        presenter=build_product_run_presenter(),
        adapter=Mock(),
        context=context,
        state=PresentationState(),
    )

    await delivery.capture_public_narration(
        "I will read the file.",
        iteration=2,
        call_ids=("call-durable-summary",),
    )
    await delivery.present_tool_call(
        ToolCall(CallId("call-durable-summary"), "file_read", {"path": "README.md"})
    )

    summary = session_db.append_message.await_args_list[0].kwargs
    assert summary["content"] == "I will read the file."
    assert summary["projection_kind"] == "workflow_progress"
    assert summary["context_visibility"] == "exclude"
    frames = [item.args[0] for item in websocket.send_json.await_args_list]
    reasoning_frames = [
        frame for frame in frames if frame["type"] == "chat_v2_reasoning_summary"
    ]
    assert reasoning_frames[0]["payload"]["text"] == "I will read the file."
    assert "reasoning_content" not in reasoning_frames[0]["payload"]


@pytest.mark.asyncio
async def test_web_outcomes_round_trip_real_session_db_with_idempotent_public_projection(
    tmp_path,
):
    import json
    from types import SimpleNamespace

    from deskpet.agent.run_presenter import (
        PresentationState,
        RunPresentationContext,
        build_product_run_presenter,
    )
    from deskpet.memory.session_db import SessionDB
    from deskpet.tools.public_projection import project_public_tool_result
    from simple_harness import CallId
    from simple_harness.tools import ToolCall, ToolResult

    websocket = SimpleNamespace(send_json=AsyncMock())
    session_db = SessionDB(tmp_path / "state.db")
    context = RunPresentationContext(
        session_id="session-web-outcome",
        text="search",
        websocket=websocket,
        services={},
        config=SimpleNamespace(tools=SimpleNamespace(last_mile=None)),
        messages=[],
        session_db=session_db,
        vector_worker=None,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-web-outcome",
        max_iterations=25,
        is_sentinel=False,
        broadcast=AsyncMock(),
        send_final=AsyncMock(),
        emit_context_usage=AsyncMock(),
        intent_label_from_turn=lambda _had_tool: "tool",
        run_id="run-web-outcome",
    )
    delivery = ProductDeliveryAdapter(
        session_id=context.session_id,
        request_id=context.request_id or "",
        run_id=context.run_id or "",
        presenter=build_product_run_presenter(),
        adapter=Mock(),
        context=context,
        state=PresentationState(),
    )
    failed_call = ToolCall(
        CallId("call-web-failed"), "web_search", {"query": "private"}
    )
    succeeded_call = ToolCall(
        CallId("call-web-succeeded"), "web_search", {"query": "private"}
    )

    await delivery.present_tool_call(failed_call)
    await delivery.present_tool_result(
        failed_call,
        ToolResult.failed(
            failed_call.call_id,
            "tool_handler_failed",
            "Search backend failed.",
        ),
    )
    await delivery.present_tool_call(succeeded_call)
    await delivery.present_tool_result(
        succeeded_call,
        ToolResult.succeeded(succeeded_call.call_id, {"results": [{}, {}]}),
    )

    rows = await session_db.get_recent_messages(context.session_id, limit=20)
    tool_rows = [row for row in rows if row["role"] == "tool"]
    assert len(tool_rows) == 2
    failed = json.loads(tool_rows[0]["content"])
    succeeded = json.loads(tool_rows[1]["content"])
    assert failed == {
        "result_kind": "web_search",
        "status": "failed",
        "item_count": 0,
        "error_code": "tool_handler_failed",
        "public_message": "Search backend failed.",
    }
    assert succeeded == {
        "result_kind": "web_search",
        "status": "succeeded",
        "item_count": 2,
    }
    assert project_public_tool_result("web_search", failed) == failed
    assert project_public_tool_result("web_search", succeeded) == succeeded
    await session_db.close()


@pytest.mark.asyncio
async def test_same_provider_turn_multi_call_shares_iteration_with_interleaved_results(
    delivery_adapter, mock_presenter
):
    from agent.agent_loop import AssistantMessageEvent, ToolCallEvent, ToolResultEvent
    from simple_harness import CallId
    from simple_harness.tools import ToolCall, ToolResult

    first = ToolCall(CallId("call-turn-1-a"), "file_read", {"path": "a"})
    second = ToolCall(CallId("call-turn-1-b"), "file_read", {"path": "b"})
    await delivery_adapter.capture_public_narration(
        "I will read both files.",
        iteration=6,
        call_ids=(first.call_id.value, second.call_id.value),
    )

    await delivery_adapter.present_tool_call(second)
    await delivery_adapter.present_tool_call(first)
    await delivery_adapter.present_tool_result(
        first, ToolResult.succeeded(first.call_id, {"content": "a"})
    )
    await delivery_adapter.present_tool_result(
        second, ToolResult.succeeded(second.call_id, {"content": "b"})
    )

    events = [item.args[0] for item in mock_presenter.present.await_args_list]
    assert [type(event) for event in events] == [
        AssistantMessageEvent,
        ToolCallEvent,
        ToolCallEvent,
        ToolResultEvent,
        ToolResultEvent,
    ]
    assert [event.iteration for event in events] == [6, 6, 6, 6, 6]
    assert sum(isinstance(event, AssistantMessageEvent) for event in events) == 1


@pytest.mark.asyncio
async def test_next_provider_turn_uses_distinct_iteration(delivery_adapter, mock_presenter):
    from agent.agent_loop import AssistantMessageEvent, ToolCallEvent
    from simple_harness import CallId
    from simple_harness.tools import ToolCall

    first = ToolCall(CallId("call-turn-a"), "file_read", {})
    second = ToolCall(CallId("call-turn-b"), "file_read", {})
    await delivery_adapter.capture_public_narration(
        "First turn.", iteration=0, call_ids=(first.call_id.value,)
    )
    await delivery_adapter.present_tool_call(first)
    await delivery_adapter.capture_public_narration(
        "Second turn.", iteration=1, call_ids=(second.call_id.value,)
    )
    await delivery_adapter.present_tool_call(second)

    events = [item.args[0] for item in mock_presenter.present.await_args_list]
    assert [type(event) for event in events] == [
        AssistantMessageEvent,
        ToolCallEvent,
        AssistantMessageEvent,
        ToolCallEvent,
    ]
    assert [event.iteration for event in events] == [0, 0, 1, 1]


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
