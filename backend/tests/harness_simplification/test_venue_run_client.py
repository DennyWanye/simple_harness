from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from deskpet.execution.contracts import (
    AuthorizationError,
    DecisionOpen,
    DeliveryPolicy,
    DeliverySpec,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunRef,
    RunStatus,
    fingerprint_json,
)
from deskpet.harness.adapters.venues import KernelRunClient
from deskpet.harness.contracts import PreparedRunContextV1, driver_catalog
from deskpet.harness.kernel import HostContext, RegisteredDriver, RunKernel
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    DecisionSignal,
    DriverTerminalCandidate,
    TokenCandidate,
)
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


def _host(session_id: str) -> HostContext:
    return HostContext(
        session_id=session_id,
        principal_id=f"principal-{session_id}",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("fixture",),
        trace_id="trace-venue",
    )


class Classifier:
    def classify(self, request):
        return ClassifiedRoute("react.default", "fixture", 1.0)


def _profiles(profile_key: str, driver_kind: str) -> ProfileRegistry:
    return ProfileRegistry((ProfileSpec(profile_key, profile_key, driver_kind),))


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


class SignalResumedDriver(Driver):
    def __init__(self):
        super().__init__(complete=False)
        self.resume = asyncio.Event()

    async def start(self, request):
        self.starts.append(request)
        yield TokenCandidate(request.run_id, "waiting")
        await self.resume.wait()
        yield DriverTerminalCandidate(request.run_id, "completed", "resumed")

    async def signal(self, signal):
        self.signals.append(signal)
        self.resume.set()
        if False:
            yield TokenCandidate(signal.run_id, "")


async def make_client(
    tmp_path,
    *,
    complete: bool = True,
    durable: bool = False,
    driver: Driver | None = None,
):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    state = await uow.activate_runtime()
    assert (state.phase, state.generation) == ("open", 1)
    driver = driver or Driver(complete=complete)
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            Classifier(), _profiles("react.default", "react")
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver, durable_from_start=durable),)),
    )
    return KernelRunClient(kernel), driver, uow


@pytest.mark.asyncio
async def test_text_and_voice_share_one_run_client_and_keep_sessions_isolated(tmp_path):
    client, _, _ = await make_client(tmp_path)
    text = await client.start(
        {"text": "text", "request_id": "r-text", "turn_id": "t-text", "venue": "text"},
        _host("text-session"),
    )
    voice = await client.start(
        {"text": "voice", "request_id": "r-voice", "turn_id": "t-voice", "venue": "voice"},
        _host("voice-session"),
    )
    await asyncio.sleep(0)
    text_events = [await anext(text.events), await anext(text.events)]
    voice_events = [await anext(voice.events), await anext(voice.events)]
    await text.events.aclose()
    await voice.events.aclose()
    await client.close(
        {"run_id": text.run_id, "expected_session_id": "text-session"},
        _host("text-session"),
    )
    await client.close(
        {"run_id": voice.run_id, "expected_session_id": "voice-session"},
        _host("voice-session"),
    )
    assert {event.session_id for event in text_events} == {"text-session"}
    assert {event.session_id for event in voice_events} == {"voice-session"}
    assert [event.kind for event in text_events] == ["transcript", "run.final"]


@pytest.mark.asyncio
async def test_host_prepared_context_forces_durable_and_freezes_terminal_delivery(
    tmp_path,
):
    client, _, uow = await make_client(tmp_path)
    prepared = PreparedRunContextV1(
        persistence_required=True,
        frozen_terminal_deliveries=(
            DeliverySpec(
                "fixture_sink",
                "fixture-instance",
                "fixture-target",
                DeliveryPolicy.DURABLE_REQUIRED,
            ),
        ),
    )
    handle = await client.start(
        {"text": "durable", "request_id": "r-prepared", "turn_id": "t-prepared"},
        _host("prepared-session"),
        prepared=prepared,
    )
    events = [event async for event in handle.events]
    record = await uow.query(
        RunRef(handle.run_id, "prepared-session"),
        _host("prepared-session").actor(root_run_id=handle.run_id),
    )

    assert record.persistence_level is PersistenceLevel.DURABLE
    assert events[-1].kind == "run.final"
    async with uow._read_connection() as db:
        row = await (
            await db.execute(
                """SELECT sink_kind,sink_instance,target_id
                   FROM execution_deliveries WHERE run_id=?""",
                (handle.run_id,),
            )
        ).fetchone()
    assert tuple(row) == (
        "fixture_sink",
        "fixture-instance",
        "fixture-target",
    )


@pytest.mark.asyncio
async def test_user_mapping_cannot_construct_prepared_context(tmp_path):
    client, _, _ = await make_client(tmp_path)
    with pytest.raises(ValueError, match="cannot construct"):
        await client.start(
            {
                "text": "forged",
                "request_id": "r-forged",
                "turn_id": "t-forged",
                "prepared": {"persistence_required": True},
            },
            _host("forged-session"),
        )


@pytest.mark.asyncio
async def test_signal_and_cancel_are_run_scoped(tmp_path):
    client, driver, uow = await make_client(tmp_path, complete=False, durable=True)
    handle = await client.start(
        {"text": "wait", "request_id": "r", "turn_id": "t", "venue": "text"},
        _host("session"),
    )
    await asyncio.sleep(0)
    actor = _host("session").actor(root_run_id=handle.run_id)
    ref = RunRef(handle.run_id, "session")
    record = await uow.query(ref, actor)
    await uow.commit_decision(
        DecisionOpen(
            decision_id="d1",
            run_id=handle.run_id,
            nonce="n1",
            kind="clarification",
            prompt_schema_version=1,
            prompt={"question": "continue?"},
            expires_at=None,
        ), actor, expected_run_version=record.version,
    )
    receipt = await client.signal(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        _host("session"),
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
        _host("session"),
        "user_stop",
    )
    assert cancelled.acknowledged is True
    await handle.events.aclose()
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        _host("session"),
    )


@pytest.mark.asyncio
async def test_decision_signal_can_resume_a_driver_waiting_for_that_signal(tmp_path):
    driver = SignalResumedDriver()
    client, _, uow = await make_client(
        tmp_path,
        durable=True,
        driver=driver,
    )
    handle = await client.start(
        {
            "text": "wait for approval",
            "request_id": "r-waiting-driver",
            "turn_id": "t-waiting-driver",
        },
        _host("session"),
    )
    events = handle.events
    first = await anext(events)
    assert first.kind == "transcript"

    actor = _host("session").actor(root_run_id=handle.run_id)
    record = await uow.query(RunRef(handle.run_id, "session"), actor)
    await uow.commit_decision(
        DecisionOpen(
            decision_id="resume-driver",
            run_id=handle.run_id,
            nonce="resume-driver-nonce",
            kind="clarification",
            prompt_schema_version=1,
            prompt={"question": "continue?"},
            expires_at=None,
        ),
        actor,
        expected_run_version=record.version,
    )

    receipt = await asyncio.wait_for(
        client.signal(
            {"run_id": handle.run_id, "expected_session_id": "session"},
            _host("session"),
            {
                "decision_id": "resume-driver",
                "response": {"allow": True},
                "nonce": "resume-driver-nonce",
                "version": 0,
            },
        ),
        timeout=1.0,
    )
    assert receipt.accepted is True
    remaining = [event async for event in events]
    assert remaining[-1].kind == "run.final"
    assert remaining[-1].status == "succeeded"


@pytest.mark.asyncio
async def test_duplicate_concurrent_decision_signals_share_one_control_lane(tmp_path):
    driver = SignalResumedDriver()
    client, _, uow = await make_client(
        tmp_path,
        durable=True,
        driver=driver,
    )
    handle = await client.start(
        {
            "text": "wait for one approval",
            "request_id": "r-duplicate-signal",
            "turn_id": "t-duplicate-signal",
        },
        _host("session"),
    )
    events = handle.events
    assert (await anext(events)).kind == "transcript"
    actor = _host("session").actor(root_run_id=handle.run_id)
    record = await uow.query(RunRef(handle.run_id, "session"), actor)
    await uow.commit_decision(
        DecisionOpen(
            decision_id="duplicate-signal",
            run_id=handle.run_id,
            nonce="duplicate-signal-nonce",
            kind="clarification",
            prompt_schema_version=1,
            prompt={"question": "continue once?"},
            expires_at=None,
        ),
        actor,
        expected_run_version=record.version,
    )
    payload = {
        "decision_id": "duplicate-signal",
        "response": {"allow": True},
        "nonce": "duplicate-signal-nonce",
        "version": 0,
    }

    receipts = await asyncio.wait_for(
        asyncio.gather(
            client.signal(
                {"run_id": handle.run_id, "expected_session_id": "session"},
                _host("session"),
                payload,
            ),
            client.signal(
                {"run_id": handle.run_id, "expected_session_id": "session"},
                _host("session"),
                payload,
            ),
        ),
        timeout=1.0,
    )

    assert all(receipt.accepted for receipt in receipts)
    assert sorted(receipt.duplicate for receipt in receipts) == [False, True]
    assert len(driver.signals) == 1
    assert [event async for event in events][-1].kind == "run.final"


@pytest.mark.asyncio
async def test_child_run_decision_resolves_authoritative_root_scope(tmp_path):
    client, driver, uow = await make_client(tmp_path, complete=False, durable=True)
    parent = await client.start(
        {"text": "parent", "request_id": "r-parent", "turn_id": "t-parent"},
        _host("session"),
    )
    await asyncio.sleep(0)
    parent_actor = _host("session").actor(root_run_id=parent.run_id)
    child_id = "child-decision"
    child_capability_hash = fingerprint_json({"tools": ["ppt"]})
    child = await uow.create(
        RunCreate(
            run_id=child_id,
            idempotency_key=f"delegate:{parent.run_id}:{child_id}",
            context=RunContext(
                session_id="session",
                root_run_id=parent.run_id,
                parent_run_id=parent.run_id,
                request_id="r-parent:child:ppt",
                turn_id="t-parent",
                venue="text",
                workspace={},
                capability_hash=child_capability_hash,
                provider_plan={},
                trace_id="trace-child-decision",
                principal_id=_host("session").principal_id,
                auth_epoch=_host("session").auth_epoch,
            ),
            payload_fingerprint=fingerprint_json({"task": "ppt"}),
            capability_fingerprint=child_capability_hash,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.RUNNING,
        )
    )
    await uow.commit_decision(
        DecisionOpen(
            decision_id="child-outline-decision",
            run_id=child_id,
            nonce="child-outline-nonce",
            kind="ppt_outline",
            prompt_schema_version=1,
            prompt={"question": "generate?"},
            expires_at=None,
        ),
        parent_actor,
        expected_run_version=child.record.version,
    )

    receipt = await client.signal(
        {"run_id": child_id, "expected_session_id": "session"},
        _host("session"),
        {
            "decision_id": "child-outline-decision",
            "response": {"allow": True},
            "nonce": "child-outline-nonce",
            "version": 0,
        },
    )

    assert receipt.accepted is True
    assert driver.signals[-1].run_id == child_id

    cancelled = await client.cancel(
        {"run_id": child_id, "expected_session_id": "session"},
        _host("session"),
        "user_stop_child",
    )
    assert cancelled.acknowledged is True
    assert driver.cancels[-1] == (child_id, "user_stop_child")
    await client.close(
        {"run_id": child_id, "expected_session_id": "session"},
        _host("session"),
    )
    await parent.events.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        replace(_host("session"), principal_id="principal-other"),
        replace(_host("session"), auth_epoch=2),
        _host("other-session"),
    ],
    ids=["wrong-principal", "wrong-auth-epoch", "wrong-session"],
)
async def test_child_run_root_resolution_never_weakens_host_identity(tmp_path, host):
    client, driver, uow = await make_client(tmp_path, complete=False, durable=True)
    parent = await client.start(
        {"text": "parent", "request_id": "r-auth-parent", "turn_id": "t-auth-parent"},
        _host("session"),
    )
    await asyncio.sleep(0)
    child_id = f"child-auth-{host.session_id}-{host.auth_epoch}"
    capability_hash = fingerprint_json({"tools": ["ppt"]})
    child = await uow.create(
        RunCreate(
            run_id=child_id,
            idempotency_key=f"delegate:{parent.run_id}:{child_id}",
            context=RunContext(
                session_id="session",
                root_run_id=parent.run_id,
                parent_run_id=parent.run_id,
                request_id="r-auth-parent:child:ppt",
                turn_id="t-auth-parent",
                venue="text",
                workspace={},
                capability_hash=capability_hash,
                provider_plan={},
                trace_id="trace-child-auth",
                principal_id=_host("session").principal_id,
                auth_epoch=_host("session").auth_epoch,
            ),
            payload_fingerprint=fingerprint_json({"task": "ppt"}),
            capability_fingerprint=capability_hash,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.RUNNING,
        )
    )
    await uow.commit_decision(
        DecisionOpen(
            decision_id="child-auth-decision",
            run_id=child_id,
            nonce="child-auth-nonce",
            kind="ppt_outline",
            prompt_schema_version=1,
            prompt={"question": "generate?"},
            expires_at=None,
        ),
        _host("session").actor(root_run_id=parent.run_id),
        expected_run_version=child.record.version,
    )

    with pytest.raises(AuthorizationError, match="actor"):
        await client.signal(
            {"run_id": child_id, "expected_session_id": "session"},
            host,
            {
                "decision_id": "child-auth-decision",
                "response": {"allow": True},
                "nonce": "child-auth-nonce",
                "version": 0,
            },
        )
    with pytest.raises(AuthorizationError, match="actor"):
        await client.cancel(
            {"run_id": child_id, "expected_session_id": "session"},
            host,
            "forged-stop",
        )
    with pytest.raises(AuthorizationError, match="actor"):
        await client.close(
            {"run_id": child_id, "expected_session_id": "session"},
            host,
        )

    assert driver.signals == []
    assert driver.cancels == []
    valid_host = _host("session")
    await client.cancel(
        {"run_id": parent.run_id, "expected_session_id": "session"},
        valid_host,
        "test_cleanup",
    )
    await client.close(
        {"run_id": parent.run_id, "expected_session_id": "session"},
        valid_host,
    )
    await parent.events.aclose()


@pytest.mark.asyncio
async def test_start_preserves_route_and_product_payload(tmp_path):
    client, driver, _ = await make_client(tmp_path)
    handle = await client.start(
        {
            "text": "research",
            "request_id": "r-profile",
            "turn_id": "t-profile",
            "venue": "text",
            "mode": "code",
            "workspace_context": True,
            "proposed_tools": ["read_file", "deepresearch"],
            "payload": {"topic": "harness", "pages": 8},
        },
        _host("session"),
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
        _host("session"),
    )


@pytest.mark.asyncio
async def test_context_os_allowlist_reaches_driver_without_broad_host_catalog(tmp_path):
    client, driver, _ = await make_client(tmp_path)
    handle = await client.start(
        {
            "text": "write a note",
            "request_id": "r-context-tools",
            "turn_id": "t-context-tools",
            "venue": "text",
            "mode": "companion",
            "proposed_tools": ["read_file", "write_file"],
            "payload": {"context_os": {"schema_version": 1}},
        },
        _host("session"),
    )
    async for _event in handle.events:
        pass

    assert driver.starts[0].capability_snapshot["capabilities"] == [
        "read_file", "write_file"
    ]
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        _host("session"),
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
            "venue": "text",
            "canonical_messages": messages,
        },
        _host("session"),
    )
    async for _event in handle.events:
        pass

    assert [dict(message) for message in driver.starts[0].canonical_messages] == messages
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        _host("session"),
    )


@pytest.mark.asyncio
async def test_durable_test_run_is_owned_by_real_generation_one_activation(tmp_path):
    client, driver, uow = await make_client(tmp_path, durable=True)
    handle = await client.start(
        {"text": "owned", "request_id": "r-owned", "turn_id": "t-owned", "venue": "text"},
        _host("session"),
    )
    async for _event in handle.events:
        pass
    actor = _host("session").actor(root_run_id=handle.run_id)
    record = await uow.query(RunRef(handle.run_id, "session"), actor)
    state = await uow.get_runtime_state()

    assert state.phase == "open"
    assert state.generation == 1
    assert record.run_id == handle.run_id
    assert await uow.get_execution_owner(handle.run_id) == ("kernel", 1)
    assert driver.starts
    await client.close(
        {"run_id": handle.run_id, "expected_session_id": "session"},
        _host("session"),
    )
