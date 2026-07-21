# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import ast
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.agent_loop import (
    AssistantDeltaEvent,
    AsyncHandoffEvent,
    ContextCompactedEvent,
    ErrorEvent,
    FinalEvent,
    PipelineEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from deskpet.agent.run_presenter import (
    PresentationState,
    RunPresentationContext,
    build_legacy_run_presenter,
)
from deskpet.agent.product_domain_sink import LegacyProductDomainSink
from deskpet.agent.turn_preparer import (
    ProductDomainCommand,
    ProductTurnPreparer,
    TurnInput,
)
from llm.types import ToolCall


ROOT = Path(__file__).resolve().parents[3]


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


class _SessionDB:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def append_message(self, **message: Any) -> int:
        self.messages.append(message)
        return len(self.messages)

    async def upsert_session_plan(self, *values: Any) -> None:
        self.plan = values

    async def clear_session_plan_awaiting(self, session_id: str) -> None:
        self.cleared = session_id


class _Tools:
    def __init__(self) -> None:
        self.read_only: list[tuple[str, bool]] = []

    def set_plan_read_only(self, sid: str, enabled: bool) -> None:
        self.read_only.append((sid, enabled))


class _Activity:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def set_status(self, sid: str, status: str) -> None:
        self.calls.append(("status", sid, status))

    async def bump(self, sid: str, **payload: Any) -> None:
        self.calls.append(("bump", sid, payload))

    async def mark_error_pending(self, sid: str) -> None:
        self.calls.append(("error", sid))


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        raw={},
        features=SimpleNamespace(
            context_os_v1=False,
            summary_quality_loop=False,
            plan_confirm_gate=False,
            plan_read_only=False,
            problem_pipeline=SimpleNamespace(plan_companion_enabled=False),
        ),
        skills=SimpleNamespace(
            auto_disclosure=SimpleNamespace(
                enabled=False,
                strong_threshold=0.8,
                budget_tokens=100,
                per_skill_max_tokens=50,
            )
        ),
    )


def _domain_sink(
    *,
    ws: _WS,
    db: _SessionDB,
    waiters: dict[str, Any],
    tools: _Tools,
    activity: _Activity | None = None,
) -> tuple[LegacyProductDomainSink, list[dict[str, Any]]]:
    peers: list[dict[str, Any]] = []

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        peers.append(frame)

    return (
        LegacyProductDomainSink(
            websocket=ws,
            services={"session_activity": activity} if activity else {},
            session_db=db,
            broadcast=broadcast,
            plan_waiters=waiters,
            tool_registry=tools,
        ),
        peers,
    )


def _plan_command(timeout: float = 1.0) -> ProductDomainCommand:
    return ProductDomainCommand(
        "plan_confirmation",
        {
            "session_id": "session-r2",
            "text": "do it",
            "timeout_seconds": timeout,
            "read_only": True,
            "in_code_mode": True,
        },
    )


def _run_context(
    ws: _WS,
    db: Any,
    *,
    services: dict[str, Any] | None = None,
    activity: Any = None,
    vector: Any = None,
    assembler: Any = None,
    bundle: Any = None,
    ledger: Any = None,
    provider: Any = None,
) -> tuple[RunPresentationContext, dict[str, Any]]:
    seen: dict[str, Any] = {"peers": [], "usage": [], "codify": 0}

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        seen["peers"].append(frame)

    async def send_final(target: _WS, frame: dict[str, Any], **_kwargs: Any) -> None:
        await target.send_json(frame)

    async def emit_usage(_ws: Any, sid: str, **_kwargs: Any) -> None:
        seen["usage"].append(sid)

    async def codify(*_args: Any, **_kwargs: Any) -> None:
        seen["codify"] += 1

    return RunPresentationContext(
        session_id="session-r2",
        text="use a tool",
        websocket=ws,
        services=services or {},
        config=_config(),
        messages=[],
        session_db=db,
        vector_worker=vector,
        activity_store=activity,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-r2",
        max_iterations=16,
        in_code_mode=False,
        is_sentinel=False,
        buffer_short_followup_stream=False,
        skill_candidate_waiters={},
        broadcast=broadcast,
        send_final=send_final,
        emit_context_usage=emit_usage,
        codify_skill=codify,
        intent_label_from_turn=lambda used: "task" if used else "ask",
        assembler=assembler,
        bundle=bundle,
        billing_ledger=ledger,
        provider=provider,
    ), seen


@pytest.mark.asyncio
async def test_turn_input_survives_prepare_context_without_field_loss() -> None:
    turn = TurnInput(
        text="inspect",
        session_id="session-r2",
        request_id="request-r2",
        turn_id="turn-r2",
        venue="code",
        mode="code",
        memory_policy={"status": "disabled"},
        explicit_new=True,
        provider_ref="provider-r2",
        capability_ref="capability-r2",
        workspace_ref="workspace-r2",
    )
    result = await ProductTurnPreparer().prepare_context(
        turn,
        services={},
        config=_config(),
        local_llm=SimpleNamespace(model="model-r2", base_url="local"),
        tool_registry=object(),
        current_message_id=3,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )
    assert result.turn is turn
    assert result.messages == [{"role": "user", "content": "inspect"}]
    assert [item.name for item in fields(TurnInput)] == [
        "text",
        "session_id",
        "request_id",
        "turn_id",
        "venue",
        "mode",
        "memory_policy",
        "explicit_new",
        "attachment_blocks",
        "provider_ref",
        "capability_ref",
        "workspace_ref",
    ]


@pytest.mark.asyncio
async def test_route_intent_clarification_preserves_legacy_order() -> None:
    class _Pipeline:
        enabled = True

        async def run_pre_loop(self, text: str, *, prior_task_type=None):
            assert text == "ambiguous"
            return SimpleNamespace(
                events=[{"type": "chat_v2_intent", "payload": {"kind": "ask"}}],
                short_circuit=False,
                needs_clarification=True,
                intent=SimpleNamespace(clarifying_questions=["which target?"]),
                system_injections=[],
                contradiction=None,
            )

    ws = _WS()
    peers: list[dict[str, Any]] = []
    session_db = _SessionDB()

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        peers.append(frame)

    prepared = await ProductTurnPreparer().prepare_context(
        TurnInput(text="ambiguous", session_id="session-r2"),
        services={},
        config=_config(),
        local_llm=object(),
        tool_registry=object(),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )
    routed = await ProductTurnPreparer().route_intent(
        prepared, services={"problem_pipeline": _Pipeline()}
    )
    sink = LegacyProductDomainSink(
        websocket=ws,
        services={},
        session_db=session_db,
        broadcast=broadcast,
        plan_waiters={},
        tool_registry=object(),
    )
    presenter = build_legacy_run_presenter()
    outcomes = [await presenter.present_domain(command, sink) for command in routed.commands]
    assert routed.continue_turn is False
    assert outcomes == [True, False]
    assert [frame["type"] for frame in ws.frames] == [
        "chat_v2_intent",
        "chat_v2_final",
    ]
    assert peers == ws.frames
    assert session_db.messages[0]["content"] == "which target?"


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "expected"), [("go", True), ("cancel", False)])
async def test_plan_confirmation_go_cancel_and_cleanup(
    decision: str, expected: bool
) -> None:
    ws, db, waiters, tools, activity = _WS(), _SessionDB(), {}, _Tools(), _Activity()
    sink, peers = _domain_sink(
        ws=ws, db=db, waiters=waiters, tools=tools, activity=activity
    )
    task = asyncio.create_task(
        build_legacy_run_presenter().present_domain(_plan_command(), sink)
    )
    await asyncio.sleep(0)
    waiters["session-r2"]["fut"].set_result(decision)
    assert await task is expected
    assert waiters == {}
    assert db.cleared == "session-r2"
    assert tools.read_only == [("session-r2", True), ("session-r2", False)]
    assert peers == ws.frames
    assert ([frame["type"] for frame in ws.frames] == []) if expected else (
        [frame["type"] for frame in ws.frames] == ["chat_v2_plan_cancelled"]
    )
    assert activity.calls == ([] if expected else [("status", "session-r2", "idle")])


@pytest.mark.asyncio
async def test_plan_confirmation_timeout_cancels_and_cleans_up() -> None:
    ws, db, waiters, tools = _WS(), _SessionDB(), {}, _Tools()
    sink, _peers = _domain_sink(ws=ws, db=db, waiters=waiters, tools=tools)
    assert (
        await build_legacy_run_presenter().present_domain(
            _plan_command(timeout=0.001), sink
        )
        is False
    )
    assert waiters == {}
    assert db.cleared == "session-r2"
    assert tools.read_only[-1] == ("session-r2", False)
    assert ws.frames[-1]["type"] == "chat_v2_plan_cancelled"


@pytest.mark.asyncio
async def test_plan_confirmation_task_cancel_still_cleans_waiter_and_read_only() -> None:
    ws, db, waiters, tools = _WS(), _SessionDB(), {}, _Tools()
    sink, _peers = _domain_sink(ws=ws, db=db, waiters=waiters, tools=tools)
    task = asyncio.create_task(
        build_legacy_run_presenter().present_domain(_plan_command(), sink)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert waiters == {}
    assert db.cleared == "session-r2"
    assert tools.read_only == [("session-r2", True), ("session-r2", False)]


def test_presenter_registers_one_handler_per_live_durable_domain_event() -> None:
    assert build_legacy_run_presenter().registrations == (
        ("AssistantDeltaEvent", "live"),
        ("AssistantMessageEvent", "live"),
        ("ToolCallEvent", "durable"),
        ("ToolResultEvent", "durable"),
        ("AsyncHandoffEvent", "domain"),
        ("FinalEvent", "durable"),
        ("ErrorEvent", "durable"),
        ("ContextCompactedEvent", "domain"),
        ("PipelineEvent", "domain"),
        ("ProviderChainFallbackEvent", "domain"),
    )


@pytest.mark.asyncio
async def test_presenter_keeps_legacy_frame_and_persistence_golden() -> None:
    ws = _WS()
    peers: list[dict[str, Any]] = []
    session_db = _SessionDB()
    usage: list[str] = []

    async def broadcast(_origin: Any, frame: dict[str, Any]) -> None:
        peers.append(frame)

    async def send_final(target: _WS, frame: dict[str, Any], **_kwargs: Any) -> None:
        await target.send_json(frame)

    async def emit_usage(_ws: Any, sid: str, **_kwargs: Any) -> None:
        usage.append(sid)

    async def codify(*_args: Any, **_kwargs: Any) -> None:
        return None

    context = RunPresentationContext(
        session_id="session-r2",
        text="use a tool",
        websocket=ws,
        services={},
        config=_config(),
        messages=[],
        session_db=session_db,
        vector_worker=None,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-r2",
        max_iterations=16,
        in_code_mode=False,
        is_sentinel=False,
        buffer_short_followup_stream=False,
        skill_candidate_waiters={},
        broadcast=broadcast,
        send_final=send_final,
        emit_context_usage=emit_usage,
        codify_skill=codify,
        intent_label_from_turn=lambda used: "task" if used else "ask",
    )
    presenter = build_legacy_run_presenter()
    state = PresentationState()
    await presenter.present(
        AssistantDeltaEvent(content="hi", kind="content", iteration=1),
        context,
        state,
    )
    await presenter.present(
        ToolCallEvent(
            tool_call=ToolCall(id="call-r2", name="read_file", arguments={"path": "a"}),
            iteration=1,
        ),
        context,
        state,
    )
    await presenter.present(
        PipelineEvent(type="chat_v2_selfcheck", payload={"ok": True}),
        context,
        state,
    )
    await presenter.present(
        FinalEvent(content="done", reasoning_content="reason", iteration=2),
        context,
        state,
    )
    assert [frame["type"] for frame in ws.frames] == [
        "chat_v2_delta",
        "tool_use_event",
        "tool_call",
        "chat_v2_selfcheck",
        "chat_v2_final",
    ]
    assert [frame["type"] for frame in peers] == [
        "chat_v2_delta",
        "tool_use_event",
        "tool_call",
        "chat_v2_selfcheck",
    ]
    assert session_db.messages == [
        {
            "session_id": "session-r2",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-r2",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "a"}',
                    },
                }
            ],
        },
        {
            "session_id": "session-r2",
            "role": "assistant",
            "content": "done",
            "reasoning_content": "reason",
        },
    ]
    assert state.final_text == "done"
    assert state.final_reasoning == "reason"
    assert state.had_tool_call is True
    assert usage == ["session-r2"]


@pytest.mark.asyncio
async def test_presenter_tool_result_handoff_error_compaction_and_activity_golden() -> None:
    ws, db, activity = _WS(), _SessionDB(), _Activity()
    context, seen = _run_context(ws, db, activity=activity)
    presenter, state = build_legacy_run_presenter(), PresentationState()
    await presenter.present(
        ToolResultEvent(
            tool_call_id="call-r2",
            tool_name="read_file",
            result='{"value": 7}',
            iteration=2,
        ),
        context,
        state,
    )
    await presenter.present(
        AsyncHandoffEvent(tool_name="deepresearch", run_id="run-r2", iteration=3),
        context,
        state,
    )
    await presenter.present(
        ContextCompactedEvent(
            reduction=0.5, tokens_in=100, tokens_out=50, model="m-r2"
        ),
        context,
        state,
    )
    await presenter.present(
        ErrorEvent(reason="fatal", detail="detail", error_class="RuntimeError"),
        context,
        state,
    )
    assert [frame["type"] for frame in ws.frames] == [
        "tool_use_event",
        "tool_result",
        "chat_v2_final",
        "context_compacted",
        "chat_v2_error",
    ]
    assert [frame["type"] for frame in seen["peers"]] == [
        "tool_use_event",
        "tool_result",
        "chat_v2_final",
    ]
    assert db.messages == [
        {
            "session_id": "session-r2",
            "role": "tool",
            "content": '{"value": 7}',
            "tool_call_id": "call-r2",
        }
    ]
    assert seen["codify"] == 1
    assert [call[0] for call in activity.calls] == [
        "bump",
        "bump",
        "status",
        "bump",
        "error",
    ]


@pytest.mark.asyncio
async def test_presenter_final_vector_codify_billing_and_feedback_golden() -> None:
    class _Vector:
        def __init__(self) -> None:
            self.items: list[tuple[int, str]] = []

        async def enqueue(self, message_id: int, text: str) -> None:
            self.items.append((message_id, text))

    class _Assembler:
        def __init__(self) -> None:
            self.items: list[tuple[Any, str]] = []

        def feedback(self, bundle: Any, *, final_response: str) -> None:
            self.items.append((bundle, final_response))

    class _Ledger:
        def __init__(self) -> None:
            self.items: list[dict[str, Any]] = []

        async def record(self, **item: Any) -> None:
            self.items.append(item)

    ws, db, activity = _WS(), _SessionDB(), _Activity()
    vector, assembler, ledger, bundle = _Vector(), _Assembler(), _Ledger(), object()
    provider = SimpleNamespace(
        last_usage={"prompt_tokens": 11, "completion_tokens": 7},
        base_url="https://relay.example/v1",
        model="model-r2",
    )
    context, seen = _run_context(
        ws,
        db,
        services={"session_activity": activity},
        activity=activity,
        vector=vector,
        assembler=assembler,
        bundle=bundle,
        ledger=ledger,
        provider=provider,
    )
    presenter, state = build_legacy_run_presenter(), PresentationState()
    await presenter.present(FinalEvent(content="done", iteration=4), context, state)
    await presenter.finish_turn(context, state)
    assert vector.items == [(1, "done")]
    assert seen["codify"] == 1
    assert seen["usage"] == ["session-r2"]
    assert assembler.items == [(bundle, "done")]
    assert ledger.items == [
        {
            "provider": "cloud",
            "model": "model-r2",
            "prompt_tokens": 11,
            "completion_tokens": 7,
        }
    ]
    assert provider.last_usage is None
    assert ("status", "session-r2", "idle") in activity.calls


@pytest.mark.asyncio
async def test_presenter_best_effort_failures_do_not_hide_final_or_leak_usage() -> None:
    class _BrokenDB:
        async def append_message(self, **_message: Any) -> int:
            raise RuntimeError("db failed")

    class _BrokenAssembler:
        def feedback(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("feedback failed")

    class _BrokenLedger:
        async def record(self, **_item: Any) -> None:
            raise RuntimeError("billing failed")

    provider = SimpleNamespace(
        last_usage={"prompt_tokens": 1, "completion_tokens": 2},
        base_url="http://localhost:1",
        model="m",
    )
    ws = _WS()
    context, seen = _run_context(
        ws,
        _BrokenDB(),
        assembler=_BrokenAssembler(),
        bundle=object(),
        ledger=_BrokenLedger(),
        provider=provider,
    )
    presenter, state = build_legacy_run_presenter(), PresentationState()
    await presenter.present(FinalEvent(content="still visible"), context, state)
    await presenter.finish_turn(context, state)
    assert ws.frames[-1]["payload"]["text"] == "still visible"
    assert seen["codify"] == 1
    assert provider.last_usage is None


def test_legacy_run_chat_calls_extracted_stages_and_presenter() -> None:
    tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
    run_chat = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_chat"
    )
    calls = {
        ast.unparse(node.func)
        for node in ast.walk(run_chat)
        if isinstance(node, ast.Call)
    }
    assert {
        "_turn_preparer.prepare_context",
        "_turn_preparer.route_intent",
        "_turn_preparer.plan_decision",
        "_run_presenter.present",
    } <= calls
