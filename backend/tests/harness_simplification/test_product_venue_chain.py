# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""R5 test-only Text product chain through the real generation-one Kernel."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from deskpet.agent.product_domain_sink import ProductDomainSink
from deskpet.agent.run_presenter import (
    CanonicalRunEventPresentationAdapter,
    PresentationState,
    RunPresentationContext,
    build_legacy_run_presenter,
)
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.execution.contracts import (
    ActorContext,
    OutcomeStatus,
    RunEvent,
    RunEventCandidate,
    RunRef,
)
from deskpet.harness.adapters.venues import KernelRunClient, ProductVenueRunAdapter
from deskpet.harness.kernel import HostContext, RegisteredDriver, RunKernel
from deskpet.harness.ports import DriverTerminalCandidate, TokenCandidate
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter, RouteProfile
from deskpet.memory.session_db import SessionDB
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from pipeline.voice_pipeline import VoicePipeline


class _Resolver:
    def resolve_host(self, transport):
        session_id = str(transport["session_id"])
        return HostContext(
            session_id=session_id,
            principal_id=f"principal-{session_id}",
            auth_epoch=1,
            capability_hash="c" * 64,
            available_capabilities=frozenset(),
            provider_plan=("fixture",),
            trace_id="trace-product-chain",
        )

    def resolve_actor(self, transport, *, root_run_id):
        session_id = str(transport["session_id"])
        return ActorContext(
            principal_id=f"principal-{session_id}",
            session_id=session_id,
            auth_epoch=1,
            root_run_id=root_run_id,
        )


class _Classifier:
    def classify(self, _request):
        return ClassifiedRoute("react.default", "fixture", 1.0)


class _Driver:
    def __init__(self, *, fail: bool = False, order: list[str] | None = None) -> None:
        self.fail = fail
        self.starts = []
        self.order = order if order is not None else []

    async def start(self, request):
        self.starts.append(request)
        self.order.append("driver")
        yield TokenCandidate(request.run_id, "hello ")
        if self.fail:
            yield DriverTerminalCandidate(
                request.run_id, "failed", error="provider unavailable"
            )
        else:
            yield DriverTerminalCandidate(request.run_id, "completed", "hello world")

    async def signal(self, _signal):
        if False:
            yield TokenCandidate("unused", "")

    async def cancel(self, _run_id, _reason):
        if False:
            yield TokenCandidate("unused", "")

    async def recover(self, _run_id, _recovery_lease):
        if False:
            yield TokenCandidate("unused", "")

    async def close(self):
        return None


class _Pipeline:
    enabled = True

    async def run_pre_loop(self, _text: str, *, prior_task_type=None):
        del prior_task_type
        return SimpleNamespace(
            events=[{"type": "chat_v2_intent", "payload": {"kind": "ask"}}],
            short_circuit=False,
            needs_clarification=False,
            intent=SimpleNamespace(problem_type="simple"),
            system_injections=["pipeline context"],
            contradiction=None,
        )


class _DomainSink(ProductDomainSink):
    def __init__(self, order: list[str]) -> None:
        self.frames: list[dict[str, Any]] = []
        self.order = order

    async def emit(self, frame):
        self.order.append("domain")
        self.frames.append(dict(frame))

    async def set_idle(self, _session_id):
        return None

    async def persist_assistant(self, _session_id, _text):
        return None

    async def store_plan(self, _payload):
        return None

    async def await_plan(self, _payload):
        return True


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.binary_frames: list[bytes] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def send_bytes(self, frame: bytes) -> None:
        self.binary_frames.append(bytes(frame))


class _Vector:
    def __init__(self) -> None:
        self.items: list[tuple[int, str]] = []

    async def enqueue(self, message_id: int, text: str) -> None:
        self.items.append((message_id, text))


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


def _presentation_context(
    ws: _WS,
    session_db: SessionDB,
    vector: _Vector,
) -> tuple[RunPresentationContext, list[dict[str, Any]]]:
    peers: list[dict[str, Any]] = []

    async def broadcast(_origin: object, frame: dict[str, Any]) -> None:
        peers.append(frame)

    async def send_final(target: _WS, frame: dict[str, Any], **_kwargs: Any) -> None:
        await target.send_json(frame)

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    return RunPresentationContext(
        session_id="text-session",
        text="explain the harness",
        websocket=ws,
        services={},
        config=_config(),
        messages=[],
        session_db=session_db,
        vector_worker=vector,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-text",
        max_iterations=16,
        in_code_mode=False,
        is_sentinel=False,
        buffer_short_followup_stream=False,
        skill_candidate_waiters={},
        broadcast=broadcast,
        send_final=send_final,
        emit_context_usage=nothing,
        codify_skill=nothing,
        intent_label_from_turn=lambda _used: "ask",
    ), peers


async def _stack(tmp_path, *, fail: bool = False):
    order: list[str] = []
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.initialize()
    activation = await uow.activate_empty_runtime()
    assert (activation.phase, activation.generation) == ("open", 1)
    driver = _Driver(fail=fail, order=order)
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            _Classifier(), [RouteProfile("react.default", "react")]
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=True)],
    )
    run_client = KernelRunClient(kernel, _Resolver())
    presenter = build_legacy_run_presenter()
    adapter = ProductVenueRunAdapter(
        preparer=ProductTurnPreparer(),
        run_client=run_client,
        presenter=presenter,
    )
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    ws, vector = _WS(), _Vector()
    context, peers = _presentation_context(ws, session_db, vector)
    sink = _DomainSink(order)
    return adapter, kernel, driver, uow, session_db, ws, vector, context, peers, sink, order


@pytest.mark.asyncio
async def test_text_product_chain_prepares_drives_presents_and_closes(tmp_path) -> None:
    (
        adapter,
        kernel,
        driver,
        uow,
        session_db,
        ws,
        vector,
        context,
        peers,
        sink,
        order,
    ) = await _stack(tmp_path)

    result = await adapter.execute(
        TurnInput(
            text="explain the harness",
            session_id="text-session",
            request_id="request-text",
            turn_id="turn-text",
            venue="text",
        ),
        {"session_id": "text-session", "venue": "text"},
        services={"problem_pipeline": _Pipeline()},
        config=_config(),
        local_llm=SimpleNamespace(model="fixture", base_url="local"),
        tool_registry=object(),
        provider=object(),
        code_mode=None,
        in_code_mode=False,
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )

    assert result.status == "succeeded"
    assert result.final_text == "hello world"
    assert order == ["domain", "driver"]
    assert sink.frames == [
        {
            "type": "chat_v2_intent",
            "payload": {"session_id": "text-session", "kind": "ask"},
        }
    ]
    assert [dict(item) for item in driver.starts[0].canonical_messages] == [
        {"role": "system", "content": "pipeline context"},
        {"role": "user", "content": "explain the harness"},
    ]
    assert [frame["type"] for frame in ws.frames] == ["chat_v2_delta", "chat_v2_final"]
    assert [frame["type"] for frame in peers] == ["chat_v2_delta"]
    messages = await session_db.get_messages("text-session")
    assert messages[-1]["content"] == "hello world"
    assert vector.items == [(messages[-1]["id"], "hello world")]

    actor = _Resolver().resolve_actor(
        {"session_id": "text-session"}, root_run_id=result.run_id
    )
    record = await uow.query(RunRef(str(result.run_id), "text-session"), actor)
    assert record.status.value == "completed"
    assert kernel._active == {}


@pytest.mark.asyncio
async def test_failed_terminal_never_projects_green_success(tmp_path) -> None:
    stack = await _stack(tmp_path, fail=True)
    adapter, kernel, _driver, _uow, session_db, ws, _vector, context, _peers, sink, _order = stack

    result = await adapter.execute(
        TurnInput(
            text="explain the harness",
            session_id="text-session",
            request_id="request-fail",
            turn_id="turn-fail",
            venue="text",
        ),
        {"session_id": "text-session", "venue": "text"},
        services={},
        config=_config(),
        local_llm=object(),
        tool_registry=object(),
        provider=object(),
        code_mode=None,
        in_code_mode=False,
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )

    assert result.status == "failed"
    assert result.final_text == ""
    assert [frame["type"] for frame in ws.frames] == ["chat_v2_delta", "chat_v2_error"]
    assert not any(frame["type"] == "chat_v2_final" for frame in ws.frames)
    assert await session_db.get_messages("text-session") == []
    assert kernel._active == {}


@pytest.mark.asyncio
async def test_voice_transport_consumes_the_activated_product_session(tmp_path) -> None:
    stack = await _stack(tmp_path)
    adapter, kernel, driver, uow, session_db, _ws, _vector, context, _peers, sink, order = stack
    context.session_id = "voice-session"
    context.text = "explain the harness by voice"
    context.request_id = "request-voice"

    opened = await adapter.open(
        TurnInput(
            text=context.text,
            session_id=context.session_id,
            request_id=context.request_id,
            turn_id="turn-voice",
            venue="voice",
        ),
        {"session_id": context.session_id, "venue": "voice"},
        services={"problem_pipeline": _Pipeline()},
        config=_config(),
        local_llm=SimpleNamespace(model="fixture", base_url="local"),
        tool_registry=object(),
        provider=object(),
        code_mode=None,
        in_code_mode=False,
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )
    session = opened.session
    assert session is not None and opened.result is None

    class _ASR:
        async def transcribe(self, _audio: bytes) -> str:
            return context.text

    class _VAD:
        threshold = 0.5

        def set_threshold(self, value: float) -> None:
            self.threshold = value

    class _TTS:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def synthesize_pcm_stream(self, text: str):
            self.texts.append(text)
            yield b"\x00\x00\x00\x00"

    class _ForbiddenAgent:
        async def chat_stream(self, *_args: object, **_kwargs: object):
            raise AssertionError("shared product session must own agent execution")
            yield ""

    audio, control, tts = _WS(), _WS(), _TTS()
    voice = VoicePipeline(
        vad=_VAD(),
        asr=_ASR(),
        agent=_ForbiddenAgent(),
        tts=tts,
        control_ws=control,
        session_id=context.session_id,
        run_session=session,
    )

    result = await voice._process_utterance(b"pcm", audio)

    assert result == "hello world"
    assert tts.texts == ["hello world"]
    assert order == ["domain", "driver"]
    assert [dict(item) for item in driver.starts[0].canonical_messages] == [
        {"role": "system", "content": "pipeline context"},
        {"role": "user", "content": context.text},
    ]
    assert await uow.get_execution_owner(session.run_id) == ("kernel", 1)
    assert (await uow.get_runtime_state()).generation == 1
    assert kernel._active == {}
    assert voice._current_run_handle is None
    assert any(frame["type"] == "chat_v2_final" for frame in context.websocket.frames)
    assert any(frame["type"] == "transcript" for frame in audio.frames)
    assert audio.binary_frames == [b"\x01\x00\x00\x00\x00"]
    messages = await session_db.get_messages(context.session_id)
    assert messages[-1]["content"] == "hello world"


@pytest.mark.asyncio
async def test_product_session_is_single_consumer_and_close_finishes_once(tmp_path) -> None:
    stack = await _stack(tmp_path)
    adapter, kernel, _driver, _uow, _db, _ws, _vector, context, _peers, sink, _order = stack

    class _Billing:
        def __init__(self) -> None:
            self.records: list[dict[str, Any]] = []

        async def record(self, **record: Any) -> None:
            self.records.append(record)

    billing = _Billing()
    provider = SimpleNamespace(
        model="fixture",
        base_url="http://127.0.0.1:9999",
        last_usage={"prompt_tokens": 2, "completion_tokens": 3},
    )
    context.provider = provider
    context.billing_ledger = billing
    opened = await adapter.open(
        TurnInput(
            text=context.text,
            session_id=context.session_id,
            request_id="request-close-once",
            turn_id="turn-close-once",
            venue="text",
        ),
        {"session_id": context.session_id, "venue": "text"},
        services={},
        config=_config(),
        local_llm=provider,
        tool_registry=object(),
        provider=provider,
        code_mode=None,
        in_code_mode=False,
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
        presentation_context=context,
        domain_sink=sink,
    )
    session = opened.session
    assert session is not None
    events = session.events
    with pytest.raises(RuntimeError, match="single-consumer"):
        _ = session.events
    async for _event in events:
        pass

    await session.close()
    await session.close()

    assert session.result.final_text == "hello world"
    assert len(billing.records) == 1
    assert provider.last_usage is None
    assert kernel._active == {}


@pytest.mark.asyncio
async def test_canonical_event_adapter_redacts_tools_and_preserves_failure_status(tmp_path) -> None:
    session_db = SessionDB(tmp_path / "projection.db")
    await session_db.initialize()
    ws, vector = _WS(), _Vector()
    context, _peers = _presentation_context(ws, session_db, vector)
    presenter = build_legacy_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    requested = RunEvent(
        event_id="event-request",
        run_id="run-tool",
        root_run_id="run-tool",
        session_id="text-session",
        durable_seq=1,
        candidate=RunEventCandidate(
            event_key="tool-request",
            kind="tool_requested",
            status=OutcomeStatus.WAITING,
            driver_kind="react",
            correlation={"command_id": "command-1"},
            payload={
                "calls": [
                    {
                        "id": "call-1",
                        "name": "web_search",
                        "arguments": {"query": "private query"},
                    }
                ]
            },
        ),
        created_at=1.0,
    )
    failed = RunEvent(
        event_id="event-failed",
        run_id="run-tool",
        root_run_id="run-tool",
        session_id="text-session",
        durable_seq=2,
        candidate=RunEventCandidate(
            event_key="tool-failed",
            kind="tool.outcome",
            status=OutcomeStatus.FAILED,
            driver_kind="react",
            correlation={"call_id": "call-1"},
            payload={
                "tool_name": "web_search",
                "outcome": {
                    "state": "failure",
                    "value": {"results": [{"url": "https://secret.example"}]},
                    "error": {"code": "upstream", "message": "failed"},
                },
            },
            error={"code": "upstream", "message": "failed"},
        ),
        created_at=2.0,
    )

    await presenter.present_run_event(requested, adapter, context, state)
    await presenter.present_run_event(failed, adapter, context, state)

    tool_call = next(frame for frame in ws.frames if frame["type"] == "tool_call")
    tool_result = next(frame for frame in ws.frames if frame["type"] == "tool_result")
    assert tool_call["payload"]["arguments"] == {"search_scope": "web"}
    assert "private query" not in str(ws.frames)
    assert tool_result["payload"]["ok"] is False
    assert tool_result["payload"]["status"] == "failed"
    assert tool_result["payload"]["result"] == '{"result_kind": "web_search", "status": "completed", "item_count": 0}'
    assert "secret.example" not in str(ws.frames)
