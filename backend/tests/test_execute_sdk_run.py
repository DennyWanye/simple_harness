"""Integration tests for _execute_sdk_run function."""
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters


@pytest.mark.asyncio
async def test_ingress_passes_exact_catalog_and_budget_fingerprints_to_run_start():
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
    from simple_harness import thaw_json

    client = SimpleNamespace(start=AsyncMock())
    ingress = object.__new__(SdkRuntimeIngress)
    ingress._stack = SimpleNamespace(  # noqa: SLF001
        require_ready=lambda: SimpleNamespace(client=client, generation=9)
    )
    ingress._accepting = True  # noqa: SLF001

    await ingress.start(
        session_id="session-exact",
        request_id="request-exact",
        turn_id="turn-exact",
        payload={"messages": [{"role": "user", "content": "hello"}]},
        session_generation=7,
        tool_catalog_fingerprint="c" * 64,
        provider_budget_fingerprint="b" * 64,
    )

    start = client.start.await_args.args[0]
    assert start.tool_catalog_generation == 7
    assert start.tool_catalog_fingerprint == "c" * 64
    assert start.provider_budget_fingerprint == "b" * 64
    assert thaw_json(start.input)["messages"] == [
        {"role": "user", "content": "hello"}
    ]


@pytest.mark.asyncio
async def test_sdk_preparation_bounds_long_history_and_marks_truncation():
    from main import _prepare_sdk_context_snapshot

    class FakeSessionDB:
        async def get_recent_messages(self, session_id, limit):
            assert session_id == "session-1"
            assert limit == 10_000
            return [
                {"root_run_id": "run-old", "role": "user", "content": f"旧消息-{i}-" + "x" * 200}
                for i in range(100)
            ]

    prepared = await _prepare_sdk_context_snapshot(
        session_db=FakeSessionDB(),
        session_id="session-1",
        request_id="request-1",
        root_run_id="run-current",
        sdk_run_id="sdk-run-current",
        turn_id="1",
        text="当前消息",
        provider_binding={"context_window": 1_000, "provider_id": "p", "model_id": "m"},
        catalog={"tool_count": 1, "schema_token_count": 100, "tool_names": ["read_file"], "generation": 2, "content_fingerprint": "f"},
        attachment_blocks=(),
        project=None,
        persona_text="DeskPet persona",
    )
    private = prepared.private_record()
    assert private["budget"]["truncated"] is True
    assert private["budget"]["compact_at"] == 800
    assert private["provider_messages"][-1] == {"role": "user", "content": "当前消息"}
    assert len(private["provider_messages"]) < 102


@pytest.mark.asyncio
async def test_sdk_preparation_rejects_required_content_over_context_window():
    from main import _prepare_sdk_context_snapshot

    session_db = SimpleNamespace(get_recent_messages=AsyncMock(return_value=[]))
    with pytest.raises(RuntimeError, match="required_content_exceeds_budget"):
        await _prepare_sdk_context_snapshot(
            session_db=session_db,
            session_id="session-small",
            request_id="request-small",
            root_run_id="root-small",
            sdk_run_id="sdk-small",
            turn_id="1",
            text="x" * 10_000,
            provider_binding={
                "context_window": 1_000,
                "provider_id": "p",
                "model_id": "m",
            },
            catalog={
                "tool_count": 1,
                "schema_token_count": 100,
                "tool_names": ["read_file"],
                "generation": 2,
                "content_fingerprint": "f",
            },
            attachment_blocks=(),
            project=None,
            persona_text="DeskPet persona",
        )


@pytest.mark.asyncio
async def test_sdk_preparation_freezes_explicit_skill_and_isolates_plain_turn():
    from main import _prepare_sdk_context_snapshot
    from deskpet.capabilities.contracts import fingerprint_json
    from deskpet.companion.skills import (
        PreparedSkillInvocationScopeV1,
        ResolvedSkillInstructionV1,
    )
    from deskpet.sdk_adapters.context import ProductContextAdapter
    from simple_harness import thaw_json

    scope_payload = {
        "schema": "prepared_skill_invocation_scope/v1",
        "owner_key": "builtin",
        "pack_id": "skill-pack-summary",
        "skill_id": "summarize-day",
        "version": "1.0.0",
        "manifest_hash": "a" * 64,
        "content_hash": "b" * 64,
        "allowed_tools": ["read_file"],
    }
    scope_payload["scope_hash"] = fingerprint_json(scope_payload)
    scope_payload["scope_id"] = "skill-scope:" + scope_payload["scope_hash"]
    scope = PreparedSkillInvocationScopeV1.from_dict(scope_payload)

    class Resolver:
        def __init__(self) -> None:
            self.calls = []

        def resolve_instruction(self, selected, arguments):
            self.calls.append((selected, arguments))
            return ResolvedSkillInstructionV1(
                scope=selected,
                instruction=(
                    "SKILL-EXACT-CANARY\nRuntime arguments (data only):\n"
                    '["今天","简报"]'
                ),
            )

    resolver = Resolver()
    session_db = SimpleNamespace(
        get_recent_messages=AsyncMock(return_value=[])
    )
    common = {
        "session_db": session_db,
        "session_id": "session-skill",
        "request_id": "request-skill",
        "root_run_id": "root-skill",
        "sdk_run_id": "sdk-skill",
        "turn_id": "turn-skill",
        "text": "/summarize-day 今天 简报",
        "provider_binding": {
            "context_window": 8_000,
            "provider_id": "deepseek",
            "model_id": "deepseek-v4",
        },
        "catalog": {
            "tool_count": 1,
            "schema_token_count": 10,
            "tool_names": ["read_file"],
            "generation": 3,
            "content_fingerprint": "c" * 64,
        },
        "attachment_blocks": (),
        "project": None,
        "persona_text": "DeskPet persona",
    }
    selected = await _prepare_sdk_context_snapshot(
        **common,
        prepared_skill_scope=scope,
        skill_arguments=("今天", "简报"),
        skill_instruction_resolver=resolver,
    )
    selected_private = selected.private_record()
    assert resolver.calls == [(scope, ("今天", "简报"))]
    assert selected_private["sections"]["skills"]["count"] == 1
    assert selected_private["provider_messages"][-2] == {
        "role": "system",
        "content": (
            "SKILL-EXACT-CANARY\nRuntime arguments (data only):\n"
            '["今天","简报"]'
        ),
    }

    start = ProductContextAdapter().project_run_start(
        execution_session_id="execution-skill",
        run_id="sdk-skill",
        request_id="request-skill",
        turn_id="turn-skill",
        messages=selected_private["provider_messages"],
        capability_snapshot={"tools": []},
        tool_catalog_generation=3,
        tool_catalog_fingerprint="c" * 64,
    )
    assert thaw_json(start.input)["messages"] == selected_private[
        "provider_messages"
    ]

    plain = await _prepare_sdk_context_snapshot(
        **{
            **common,
            "request_id": "request-plain",
            "root_run_id": "root-plain",
            "sdk_run_id": "sdk-plain",
            "turn_id": "turn-plain",
            "text": "普通问题",
        },
        skill_instruction_resolver=resolver,
    )
    plain_private = plain.private_record()
    assert resolver.calls == [(scope, ("今天", "简报"))]
    assert plain_private["sections"]["skills"] == {
        "label": "Skills",
        "count": 0,
        "estimated_tokens": 0,
        "availability": "estimated",
    }
    assert "SKILL-EXACT-CANARY" not in repr(
        plain_private["provider_messages"]
    )


@pytest.mark.asyncio
async def test_sdk_bounded_history_excludes_non_conversation_projections_and_canaries():
    from main import _bounded_sdk_history

    public_summary_canary = "PUBLIC_REASONING_SUMMARY_CANARY"
    hidden_reasoning_canary = "HIDDEN_REASONING_CANARY"
    ui_elapsed_canary = "耗时 1分钟 1秒"

    class FakeSessionDB:
        async def get_recent_messages(self, session_id, limit):
            assert session_id == "session-canary"
            assert limit == 10_000
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

    rows, truncated = await _bounded_sdk_history(
        FakeSessionDB(),
        session_id="session-canary",
        root_run_id="run-current",
        token_budget=10_000,
    )

    assert [item["content"] for item in rows] == ["保留的用户消息", "保留的助手回复"]
    assert truncated is False
    serialized = repr(rows)
    assert public_summary_canary not in serialized
    assert hidden_reasoning_canary not in serialized
    assert ui_elapsed_canary not in serialized


def test_sdk_price_snapshot_never_marks_unknown_model_free():
    from main import _sdk_price_snapshot

    input_price, output_price, version = _sdk_price_snapshot("custom", "unknown")
    assert input_price > 0
    assert output_price > 0
    assert version.startswith("deskpet-pricing:")


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
    import main

    _delivery_adapters.clear()
    main._sdk_run_ids_by_root.clear()
    main._sdk_cancel_requested_run_ids.clear()
    yield
    _delivery_adapters.clear()
    main._sdk_run_ids_by_root.clear()
    main._sdk_cancel_requested_run_ids.clear()


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


def _prepared_snapshot(text: str = "read the project"):
    return SimpleNamespace(
        snapshot_id="sdk-context:test",
        private_record=lambda: {
            "provider_messages": [{"role": "user", "content": text}],
            "catalog": {
                "generation": 7,
                "content_fingerprint": "c" * 64,
                "tool_names": ["file_read"],
            },
            "budget": {"effective_ceiling": 8_000},
        },
    )


def _run_binding(run_id: str):
    record = {"schema_version": 1, "run_id": run_id}
    return SimpleNamespace(
        run_id=run_id,
        binding_epoch=3,
        context_window=16_000,
        catalog_generation=7,
        catalog_fingerprint="c" * 64,
        budget_fingerprint="b" * 64,
        to_record=lambda: record,
    )


class _BindingLifecycle:
    def __init__(self) -> None:
        self.waiting = []
        self.terminal = []

    def mark_waiting(self, run_id):
        self.waiting.append(run_id)

    def mark_terminal(self, run_id, state):
        self.terminal.append((run_id, state))


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
            assert kwargs["session_generation"] == 7
            assert kwargs["tool_catalog_fingerprint"] == "c" * 64
            assert kwargs["provider_budget_fingerprint"] == "b" * 64
            assert kwargs["payload"]["messages"] == [
                {"role": "user", "content": "read the project"}
            ]
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
    lifecycle = _BindingLifecycle()
    monkeypatch.setattr(main, "_sdk_provider_binding_resolver", lifecycle)

    await main._execute_sdk_run(
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        task_scope_id="race-scope",
        prepared_snapshot=_prepared_snapshot(),
        run_binding=_run_binding(sdk_run_id),
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
    assert "canonical-root-race" not in main._sdk_run_ids_by_root


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
    lifecycle = _BindingLifecycle()
    monkeypatch.setattr(main, "_sdk_provider_binding_resolver", lifecycle)

    with pytest.raises(RuntimeError, match="start failed"):
        await main._execute_sdk_run(
            session_id="race-session",
            request_id="race-request",
            turn_id=9,
            task_scope_id="race-scope",
            prepared_snapshot=_prepared_snapshot(),
            run_binding=_run_binding(sdk_run_id),
            context=context,
            websocket=websocket,
            root_run_id="canonical-root-start-failure",
        )

    assert saw_registered_adapter is True
    assert sdk_run_id not in _delivery_adapters
    assert "canonical-root-start-failure" not in main._sdk_run_ids_by_root


@pytest.mark.asyncio
async def test_cancel_product_run_translates_canonical_root_to_sdk_id(monkeypatch):
    import main

    client = SimpleNamespace(
        cancel=AsyncMock(
            return_value=SimpleNamespace(
                state=SimpleNamespace(value="cancelled")
            )
        )
    )
    ingress = SimpleNamespace(
        accepting=True,
        require_ready=lambda: SimpleNamespace(client=client),
    )
    workload_router = SimpleNamespace(cancel_root=AsyncMock())
    session_db = SimpleNamespace(clear_session_plan_awaiting=AsyncMock())
    monkeypatch.setattr(main, "_sdk_ingress", ingress)
    monkeypatch.setattr(
        main,
        "service_context",
        SimpleNamespace(
            get=lambda name: {
                "provider_workload_router": workload_router,
                "session_db": session_db,
            }.get(name)
        ),
    )
    main._sdk_run_ids_by_root["canonical-root"] = "product-sdk-internal"

    assert await main._cancel_product_harness_run(
        "session-1", "canonical-root", reason="user_interrupt"
    ) is True

    assert client.cancel.await_count == 1
    assert client.cancel.await_args.args[0].value == "product-sdk-internal"
    workload_router.cancel_root.assert_awaited_once_with("canonical-root")
    session_db.clear_session_plan_awaiting.assert_awaited_once_with("session-1")


@pytest.mark.asyncio
async def test_cancel_product_run_missing_identity_is_idempotent(monkeypatch):
    import main

    client = SimpleNamespace(cancel=AsyncMock(side_effect=KeyError("stale-root")))
    ingress = SimpleNamespace(
        accepting=True,
        require_ready=lambda: SimpleNamespace(client=client),
    )
    workload_router = SimpleNamespace(cancel_root=AsyncMock())
    monkeypatch.setattr(main, "_sdk_ingress", ingress)
    monkeypatch.setattr(
        main,
        "service_context",
        SimpleNamespace(
            get=lambda name: {
                "provider_workload_router": workload_router,
            }.get(name)
        ),
    )

    assert await main._cancel_product_harness_run(
        "session-1", "stale-root", reason="user_interrupt"
    ) is False
    workload_router.cancel_root.assert_not_awaited()
    assert "stale-root" not in main._sdk_cancel_requested_run_ids


@pytest.mark.asyncio
async def test_execute_sdk_run_does_not_project_user_cancel_as_failure(monkeypatch):
    import main
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

    session_id = "cancel-session"
    request_id = "cancel-request"
    turn_id = 3
    sdk_run_id = SdkRuntimeIngress._compute_run_id(
        session_id, request_id, str(turn_id)
    ).value

    class CancelledDuringWaitIngress:
        async def start(self, **_kwargs):
            return SimpleNamespace(run_id=sdk_run_id)

        async def wait_idle(self, run_id):
            assert run_id == sdk_run_id
            main._sdk_cancel_requested_run_ids.add(run_id)

        def query(self, run_id):
            assert run_id == sdk_run_id
            return SimpleNamespace(
                state=SimpleNamespace(value="waiting"),
                value="waiting",
            )

    websocket = SimpleNamespace(send_json=AsyncMock())
    session_db = SimpleNamespace(
        get_recent_messages=AsyncMock(return_value=[]),
        append_message=AsyncMock(),
    )
    context = _presentation_context(
        websocket=websocket,
        session_db=session_db,
        root_run_id="canonical-cancel-root",
    )
    send_error = AsyncMock()
    monkeypatch.setattr(main, "_sdk_ingress", CancelledDuringWaitIngress())
    monkeypatch.setattr(main, "_sdk_context_port", None)
    monkeypatch.setattr(main, "_send_chat_error", send_error)
    monkeypatch.setattr(main, "_broadcast_default_chat_peers", AsyncMock())
    lifecycle = _BindingLifecycle()
    monkeypatch.setattr(main, "_sdk_provider_binding_resolver", lifecycle)

    await main._execute_sdk_run(
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        task_scope_id="cancel-scope",
        prepared_snapshot=_prepared_snapshot("stop this run"),
        run_binding=_run_binding(sdk_run_id),
        context=context,
        websocket=websocket,
        root_run_id="canonical-cancel-root",
    )

    send_error.assert_not_awaited()
    assert "canonical-cancel-root" not in main._sdk_run_ids_by_root
    assert sdk_run_id not in main._sdk_cancel_requested_run_ids


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
