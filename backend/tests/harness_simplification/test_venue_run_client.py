from __future__ import annotations

import asyncio

import pytest

from deskpet.execution.contracts import ActorContext
from deskpet.execution.ledger import ExecutionLedger
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
    def __init__(self):
        self.signals = []
        self.cancels = []
        self.starts = []

    async def start(self, request):
        self.starts.append(request)
        yield TokenCandidate(request.run_id, "hello")
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


async def make_client(tmp_path):
    ledger = ExecutionLedger(SqliteExecutionUnitOfWork(tmp_path / "workflow.db"))
    await ledger.initialize()
    driver = Driver()
    kernel = RunKernel(
        ledger=ledger,
        router=RegisteredRouter(
            Classifier(), [RouteProfile("react.default", "react")]
        ),
        drivers=[RegisteredDriver("react", driver)],
    )
    return KernelRunClient(kernel, Resolver()), driver


@pytest.mark.asyncio
async def test_text_and_voice_share_one_run_client_and_keep_sessions_isolated(tmp_path):
    client, _ = await make_client(tmp_path)
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
    assert {event.session_id for event in text_events} == {"text-session"}
    assert {event.session_id for event in voice_events} == {"voice-session"}
    assert [event.kind for event in text_events] == ["transcript", "final"]


@pytest.mark.asyncio
async def test_signal_and_cancel_are_run_scoped(tmp_path):
    client, driver = await make_client(tmp_path)
    handle = await client.start(
        {"text": "wait", "request_id": "r", "turn_id": "t"},
        {"session_id": "session", "venue": "text"},
    )
    await asyncio.sleep(0)
    receipt = await client.signal(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        {"session_id": "session", "venue": "text"},
        {
            "decision_id": "d1",
            "response": {"allow": True},
            "nonce": "n1",
            "version": 3,
        },
    )
    assert receipt.accepted is True
    assert driver.signals == [
        DecisionSignal(handle.run_id, "d1", {"allow": True}, "n1", 3)
    ]

    with pytest.raises(Exception):
        await client.cancel(
            {"run_id": handle.run_id, "expected_session_id": "session"},
            {"session_id": "session", "venue": "text"},
            "user_stop",
        )


@pytest.mark.asyncio
async def test_start_preserves_route_and_product_payload(tmp_path):
    client, driver = await make_client(tmp_path)
    await client.start(
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
    await asyncio.sleep(0)

    start = driver.starts[0]
    assert start.request_payload == {
        "text": "research",
        "topic": "harness",
        "pages": 8,
    }
    assert start.canonical_messages[-1] == {"role": "user", "content": "research"}
