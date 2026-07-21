from __future__ import annotations

import asyncio

import pytest

from deskpet.execution.contracts import ActorContext, DecisionOpen, RunRef
from deskpet.harness.adapters.venues import KernelRunClient
from deskpet.harness.kernel import HostContext, RegisteredDriver, RunKernel
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    DecisionSignal,
    DriverTerminalCandidate,
    TokenCandidate,
)
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter, RouteProfile
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class Resolver:
    def resolve_host(self, transport):
        session_id = str(transport["session_id"])
        return HostContext(
            session_id=session_id,
            principal_id=f"principal-{session_id}",
            auth_epoch=1,
            capability_hash="c" * 64,
            available_capabilities=frozenset(),
            provider_plan=("fixture",),
            trace_id="trace-venue",
        )

    def resolve_actor(self, transport, *, root_run_id):
        session_id = str(transport["session_id"])
        return ActorContext(
            principal_id=f"principal-{session_id}",
            session_id=session_id,
            auth_epoch=1,
            root_run_id=root_run_id,
        )


class Classifier:
    def classify(self, request):
        return ClassifiedRoute("react.default", "fixture", 1.0)


class Driver:
    def __init__(self, *, complete: bool = True):
        self.signals = []
        self.cancels = []
        self.starts = []
        self.complete = complete

    async def start(self, request):
        self.starts.append(request)
        yield TokenCandidate(request.run_id, "hello")
        if self.complete:
            yield DriverTerminalCandidate(request.run_id, "completed", "hello")

    async def signal(self, signal):
        self.signals.append(signal)
        if False:
            yield TokenCandidate(signal.run_id, "")

    async def cancel(self, run_id, reason):
        self.cancels.append((run_id, reason))
        yield CancelAcknowledgedCandidate(run_id, reason)

    async def recover(self, run_id):
        if False:
            yield TokenCandidate(run_id, "")

    async def close(self):
        return None


async def make_client(tmp_path, *, complete: bool = True, durable: bool = False):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    state = await uow.activate_empty_runtime()
    assert (state.phase, state.generation) == ("open", 1)
    driver = Driver(complete=complete)
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            Classifier(), [RouteProfile("react.default", "react")]
        ),
        drivers=[RegisteredDriver("react", driver, durable_from_start=durable)],
    )
    return KernelRunClient(kernel, Resolver()), driver, uow


@pytest.mark.asyncio
async def test_text_and_voice_share_one_run_client_and_keep_sessions_isolated(tmp_path):
    client, _, _ = await make_client(tmp_path)
    text = await client.start(
        {"text": "text", "request_id": "r-text", "turn_id": "t-text"},
        {"session_id": "text-session", "venue": "text"},
    )
    voice = await client.start(
        {"text": "voice", "request_id": "r-voice", "turn_id": "t-voice"},
        {"session_id": "voice-session", "venue": "voice"},
    )
    await asyncio.sleep(0)
    text_events = [await anext(text.events), await anext(text.events)]
    voice_events = [await anext(voice.events), await anext(voice.events)]
    await text.events.aclose()
    await voice.events.aclose()
    await client.close(
        {"run_id": text.run_id, "expected_session_id": "text-session"},
        {"session_id": "text-session", "venue": "text"},
    )
    await client.close(
        {"run_id": voice.run_id, "expected_session_id": "voice-session"},
        {"session_id": "voice-session", "venue": "voice"},
    )
    assert {event.session_id for event in text_events} == {"text-session"}
    assert {event.session_id for event in voice_events} == {"voice-session"}
    assert [event.kind for event in text_events] == ["transcript", "final"]


@pytest.mark.asyncio
async def test_signal_and_cancel_are_run_scoped(tmp_path):
    client, driver, uow = await make_client(tmp_path, complete=False, durable=True)
    handle = await client.start(
        {"text": "wait", "request_id": "r", "turn_id": "t"},
        {"session_id": "session", "venue": "text"},
    )
    await asyncio.sleep(0)
    actor = Resolver().resolve_actor({"session_id": "session"}, root_run_id=handle.run_id)
    ref = RunRef(handle.run_id, "session")
    record = await uow.query(ref, actor)
    await uow.open_decision(
        DecisionOpen(
            decision_id="d1",
            run_id=handle.run_id,
            nonce="n1",
            kind="clarification",
            prompt_schema_version=1,
            prompt={"question": "continue?"},
            expires_at=None,
        ),
        actor,
        expected_run_version=record.version,
    )
    receipt = await client.signal(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        {"session_id": "session", "venue": "text"},
        {
            "decision_id": "d1",
            "response": {"allow": True},
            "nonce": "n1",
            "version": 0,
        },
    )
    assert receipt.accepted is True
    assert driver.signals == [
        DecisionSignal(
            handle.run_id,
            "d1",
            {"allow": True, "decision_status": "allowed"},
            "n1",
            0,
        )
    ]

    cancelled = await client.cancel(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        {"session_id": "session", "venue": "text"},
        "user_stop",
    )
    assert cancelled.acknowledged is True
    await handle.events.aclose()
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        {"session_id": "session", "venue": "text"},
    )


@pytest.mark.asyncio
async def test_start_preserves_route_and_product_payload(tmp_path):
    client, driver, _ = await make_client(tmp_path)
    handle = await client.start(
        {
            "text": "research",
            "request_id": "r-profile",
            "turn_id": "t-profile",
            "mode": "code",
            "workspace_context": True,
            "proposed_tools": ["read_file", "deepresearch"],
            "payload": {"topic": "harness", "pages": 8},
        },
        {"session_id": "session", "venue": "text"},
    )
    async for _event in handle.events:
        pass

    start = driver.starts[0]
    assert start.request_payload == {
        "text": "research",
        "topic": "harness",
        "pages": 8,
    }
    assert start.canonical_messages[-1] == {"role": "user", "content": "research"}
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        {"session_id": "session", "venue": "text"},
    )


@pytest.mark.asyncio
async def test_prepared_canonical_messages_reach_driver_without_kernel_rebuilding(tmp_path):
    client, driver, _ = await make_client(tmp_path, durable=True)
    messages = [
        {"role": "system", "content": "prepared product context"},
        {"role": "user", "content": "research"},
    ]
    handle = await client.start(
        {
            "text": "research",
            "request_id": "r-prepared",
            "turn_id": "t-prepared",
            "canonical_messages": messages,
        },
        {"session_id": "session", "venue": "text"},
    )
    async for _event in handle.events:
        pass

    assert [dict(message) for message in driver.starts[0].canonical_messages] == messages
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        {"session_id": "session", "venue": "text"},
    )


@pytest.mark.asyncio
async def test_durable_test_run_is_owned_by_real_generation_one_activation(tmp_path):
    client, driver, uow = await make_client(tmp_path, durable=True)
    handle = await client.start(
        {"text": "owned", "request_id": "r-owned", "turn_id": "t-owned"},
        {"session_id": "session", "venue": "text"},
    )
    async for _event in handle.events:
        pass
    actor = Resolver().resolve_actor({"session_id": "session"}, root_run_id=handle.run_id)
    record = await uow.query(RunRef(handle.run_id, "session"), actor)
    state = await uow.get_runtime_state()

    assert state.phase == "open"
    assert state.generation == 1
    assert record.run_id == handle.run_id
    assert await uow.get_execution_owner(handle.run_id) == ("kernel", 1)
    assert driver.starts
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        {"session_id": "session", "venue": "text"},
    )
