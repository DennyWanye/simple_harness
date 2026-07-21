from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deskpet.harness.adapters.venues import ProviderLaunchPolicyRegistry
from deskpet.agent.run_presenter import CanonicalRunEventPresentationAdapter
from agent.agent_loop import ErrorEvent
from deskpet.execution.contracts import (
    ActorContext,
    AdmissionBoundary,
    AdmissionPhase,
    AdmissionSpec,
    DecisionKind,
    DecisionSignal as DurableDecisionSignal,
    ProviderLaunchSnapshot,
    RunRef,
    RunEventCandidate,
    RunStatus,
    WorkflowRunSeed,
    fingerprint_json,
)
from deskpet.harness.contracts import driver_catalog
from deskpet.harness.kernel import HostContext, RegisteredDriver, RunKernel, RunRequest
from deskpet.harness.ports import DecisionSignal, DriverTerminalCandidate, TokenCandidate
from deskpet.harness.projector import GoalTerminalProjection
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute, RegisteredRouter
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class _Classifier:
    def __init__(self, profile_key="react.default") -> None:
        self.profile_key = profile_key

    def classify(self, _request):
        return ClassifiedRoute(self.profile_key, "fixture", 1.0)


class _HoldingDriver:
    def __init__(self) -> None:
        self.starts = []
        self.recovers = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self, request):
        self.starts.append(request)
        self.started.set()
        yield TokenCandidate(request.run_id, "started")
        await self.release.wait()

    async def signal(self, _signal, recovery_lease=None):
        if False:
            yield TokenCandidate("unused", "")

    async def cancel(self, _run_id, _reason):
        if False:
            yield TokenCandidate("unused", "")

    async def recover(self, run_id, recovery_lease):
        self.recovers.append((run_id, recovery_lease))
        yield DriverTerminalCandidate(run_id, "completed", "recovered")

    async def close(self):
        return None


def _host() -> HostContext:
    return HostContext(
        session_id="session",
        principal_id="user",
        auth_epoch=0,
        capability_hash="c" * 64,
        available_capabilities=frozenset({"read"}),
        provider_plan=("fixture",),
        trace_id="trace",
    )


def _snapshot(*, idempotent: bool) -> ProviderLaunchSnapshot:
    return ProviderLaunchSnapshot(
        "provider", "adapter", "v1", idempotent, "launch_operation_id" if idempotent else None
    )


def _request(*, idempotent: bool, expires_at=None) -> RunRequest:
    return RunRequest(
        "execute plan",
        "request",
        "turn",
        canonical_messages=({"role": "user", "content": "execute plan"},),
        payload={"scope": "workspace"},
        admission=AdmissionSpec(
            DecisionKind.PLAN,
            1,
            1,
            {"question": "approve?"},
            {"steps": ["inspect", "apply"], "awaiting_confirm": True},
            expires_at,
        ),
        provider_launch_snapshot=_snapshot(idempotent=idempotent),
    )


async def _kernel(tmp_path, *, idempotent: bool, terminal_projection=None,
                  clock=None, expires_at=None):
    uow = SqliteExecutionUnitOfWork(
        tmp_path / "execution.db", **({} if clock is None else {"clock": clock}))
    await uow.initialize()
    driver = _HoldingDriver()
    kernel = RunKernel(
        uow=uow,
        router=RegisteredRouter(
            _Classifier(),
            ProfileRegistry((ProfileSpec("react.default", "react", "react"),)),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
        terminal_projection=terminal_projection,
    )
    handle = await kernel.start(
        _request(idempotent=idempotent, expires_at=expires_at), _host())
    actor = _host().actor(root_run_id=handle.root_run_id)
    waiting = next(event for event in await uow.list_events(handle.ref.run_id)
                   if event.kind == "admission.waiting")
    return kernel, uow, driver, handle, actor, waiting


@pytest.mark.asyncio
async def test_admission_waits_before_driver_and_duplicate_approval_starts_once(tmp_path):
    kernel, uow, driver, handle, actor, waiting = await _kernel(
        tmp_path, idempotent=True
    )
    assert driver.starts == []
    assert waiting.kind == "admission.waiting"
    payload = waiting.candidate.payload
    assert set(("run_id", "decision_id", "nonce", "version")) <= set(payload)

    signal = DecisionSignal(
        handle.ref.run_id,
        str(payload["decision_id"]),
        {"approved": True},
        nonce=str(payload["nonce"]),
        version=int(payload["version"]),
    )
    first = await kernel.signal(handle.ref, actor, signal)
    await asyncio.wait_for(driver.started.wait(), timeout=1)
    replay = await kernel.signal(handle.ref, actor, signal)

    assert first.accepted is True
    assert replay.accepted is True and replay.duplicate is True
    assert len(driver.starts) == 1
    start = driver.starts[0]
    assert start.launch_operation_id
    assert start.admission_launch is not None
    assert start.launch_operation_id == start.admission_launch.boundary.launch_operation_id
    assert start.provider_launch_snapshot == _snapshot(idempotent=True)

    driver.release.set()
    active = kernel._live.get(handle.ref.run_id)
    assert active is not None and active.task is not None
    await active.task
    assert AdmissionBoundary.from_dict(
        (await uow.load_continuation(handle.ref.run_id)).payload["_admission"]
    ).phase is AdmissionPhase.LAUNCH_CLAIMED


@pytest.mark.asyncio
async def test_pending_admission_cancel_never_calls_driver(tmp_path):
    kernel, uow, driver, handle, actor, _waiting = await _kernel(
        tmp_path, idempotent=False
    )
    receipt = await kernel.cancel(handle.ref, actor, "user cancelled")
    record = await uow.query(handle.ref, actor)
    assert receipt.acknowledged is True
    assert record.status is RunStatus.CANCELLED
    assert driver.starts == []


@pytest.mark.asyncio
async def test_pending_cancel_wakes_observer_with_one_canonical_final(tmp_path):
    kernel, uow, driver, handle, actor, waiting = await _kernel(
        tmp_path, idempotent=False
    )
    stream = kernel.observe(handle.ref, actor)
    assert (await anext(stream)).event_id == waiting.event_id
    terminal = asyncio.create_task(anext(stream))
    await kernel.cancel(handle.ref, actor, "user cancelled")
    final = await asyncio.wait_for(terminal, timeout=1)
    assert final.kind == "run.final"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert len([event for event in await uow.list_events(handle.ref.run_id)
                if event.candidate.is_terminal]) == 1
    assert driver.starts == []


@pytest.mark.asyncio
async def test_rejected_admission_keeps_goal_association_and_terminal_delivery(tmp_path):
    class Goals:
        def get_active_goal_context_for_session(self, session_id):
            return "goal-1", session_id

    projection = GoalTerminalProjection(Goals())
    kernel, uow, driver, handle, actor, waiting = await _kernel(
        tmp_path, idempotent=False, terminal_projection=projection)
    payload = waiting.candidate.payload
    await kernel.signal(handle.ref, actor, DecisionSignal(
        handle.ref.run_id, str(payload["decision_id"]), {"approved": False},
        nonce=str(payload["nonce"]), version=int(payload["version"])))
    events = await uow.list_events(handle.ref.run_id)
    final = next(event for event in events if event.kind == "run.final")
    deliveries = await uow.list_event_deliveries(final.event_id)
    assert [event.kind for event in events[:2]] == [
        "goal_associated", "admission.waiting"]
    assert [(item.sink_kind, item.target_id) for item in deliveries] == [
        ("goal_projection", "goal-1")]
    assert driver.starts == []


@pytest.mark.asyncio
async def test_late_approval_expires_with_terminal_delivery(tmp_path):
    class Goals:
        def get_active_goal_context_for_session(self, session_id):
            return "goal-1", session_id

    now = [100.0]
    kernel, uow, driver, handle, actor, waiting = await _kernel(
        tmp_path, idempotent=False,
        terminal_projection=GoalTerminalProjection(Goals()),
        clock=lambda: now[0], expires_at=101.0)
    now[0] = 102.0
    payload = waiting.candidate.payload
    await kernel.signal(handle.ref, actor, DecisionSignal(
        handle.ref.run_id, str(payload["decision_id"]), {"approved": True},
        nonce=str(payload["nonce"]), version=int(payload["version"])))
    final = next(event for event in await uow.list_events(handle.ref.run_id)
                 if event.kind == "run.final")
    assert [(item.sink_kind, item.target_id)
            for item in await uow.list_event_deliveries(final.event_id)] == [
                ("goal_projection", "goal-1")]
    assert (await uow.query(handle.ref, actor)).status is RunStatus.CANCELLED
    assert driver.starts == []


@pytest.mark.asyncio
async def test_cancel_after_accept_before_claim_consumes_admission(tmp_path):
    kernel, uow, driver, handle, actor, waiting = await _kernel(
        tmp_path, idempotent=False
    )
    boundary = AdmissionBoundary.from_dict(
        (await uow.load_continuation(handle.ref.run_id)).payload["_admission"])
    await uow.resolve_admission(
        handle.ref, actor,
        DurableDecisionSignal(
            boundary.decision_id, handle.ref.run_id, handle.ref.expected_session_id,
            boundary.nonce, int(waiting.candidate.payload["version"]), True, 1,
            {"approved": True}),
        expected_boundary_version=boundary.boundary_version)
    receipt = await kernel.cancel(handle.ref, actor, "cancel before claim")
    current = AdmissionBoundary.from_dict(
        (await uow.load_continuation(handle.ref.run_id)).payload["_admission"])
    assert receipt.status is RunStatus.CANCELLED
    assert current.phase is AdmissionPhase.CANCELLED and current.consumed
    assert driver.starts == []


@pytest.mark.asyncio
async def test_non_idempotent_claim_without_launch_footprint_fails_closed_on_recovery(tmp_path):
    kernel, uow, driver, handle, actor, waiting = await _kernel(
        tmp_path, idempotent=False
    )
    boundary = AdmissionBoundary.from_dict(
        (await uow.load_continuation(handle.ref.run_id)).payload["_admission"]
    )
    await uow.resolve_admission(
        handle.ref,
        actor,
        DurableDecisionSignal(
            boundary.decision_id,
            handle.ref.run_id,
            handle.ref.expected_session_id,
            boundary.nonce,
            int(waiting.candidate.payload["version"]),
            True,
            1,
            {"approved": True},
        ),
        expected_boundary_version=boundary.boundary_version,
    )
    lease = await uow.recovery_scope(handle.ref.run_id, owner="crashed-launcher")
    await uow.claim_admission_launch(lease, expected_boundary_version=2)
    await uow.recovery_scope(lease, lease_seconds=None)

    restarted = RunKernel(
        uow=SqliteExecutionUnitOfWork(uow.path),
        router=RegisteredRouter(
            _Classifier(),
            ProfileRegistry((ProfileSpec("react.default", "react", "react"),)),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )
    await restarted.recover(handle.ref, actor)
    record = await restarted._uow.query(handle.ref, actor)
    final = [event for event in await restarted._uow.list_events(handle.ref.run_id) if event.kind == "run.final"]
    assert record.status is RunStatus.FAILED
    assert len(final) == 1
    assert final[0].candidate.payload["error_code"] == "launch_outcome_unknown"
    assert final[0].candidate.payload["retry_safe"] is False
    assert isinstance(
        CanonicalRunEventPresentationAdapter().to_presentation_events(final[0])[0],
        ErrorEvent,
    )
    assert [event async for event in restarted.observe(handle.ref, actor)][-1] == final[0]
    assert driver.starts == []


@pytest.mark.asyncio
async def test_idempotent_claim_recovery_reuses_launch_operation(tmp_path):
    kernel, uow, driver, handle, actor, waiting = await _kernel(
        tmp_path, idempotent=True
    )
    boundary = AdmissionBoundary.from_dict(
        (await uow.load_continuation(handle.ref.run_id)).payload["_admission"])
    await uow.resolve_admission(
        handle.ref, actor,
        DurableDecisionSignal(
            boundary.decision_id, handle.ref.run_id, handle.ref.expected_session_id,
            boundary.nonce, int(waiting.candidate.payload["version"]), True, 1,
            {"approved": True}),
        expected_boundary_version=boundary.boundary_version)
    lease = await uow.recovery_scope(handle.ref.run_id, owner="crashed-launcher")
    claim = await uow.claim_admission_launch(lease, expected_boundary_version=2)
    await uow.recovery_scope(lease, lease_seconds=None)

    restarted = RunKernel(
        uow=SqliteExecutionUnitOfWork(uow.path),
        router=RegisteredRouter(
            _Classifier(),
            ProfileRegistry((ProfileSpec("react.default", "react", "react"),)),
        ),
        drivers=driver_catalog((RegisteredDriver("react", driver),)),
    )
    await restarted.recover(handle.ref, actor)
    await asyncio.wait_for(driver.started.wait(), timeout=1)
    assert len(driver.starts) == 1
    assert driver.starts[0].launch_operation_id == claim.boundary.launch_operation_id
    assert driver.starts[0].admission_launch.boundary == claim.boundary
    driver.release.set()
    active = restarted._live.get(handle.ref.run_id)
    assert active is not None and active.task is not None
    await active.task


@pytest.mark.asyncio
@pytest.mark.parametrize("driver_kind", ("react", "workflow"))
async def test_launched_admission_recovery_reenters_driver(tmp_path, driver_kind):
    profile_key = f"{driver_kind}.default"
    profiles = ProfileRegistry((ProfileSpec(
        profile_key, "fixture", driver_kind,
        workflow_key="fixture" if driver_kind == "workflow" else None,
        workflow_name="fixture" if driver_kind == "workflow" else None,
        workflow_version="v1" if driver_kind == "workflow" else None,
        state_factory=(lambda: {}) if driver_kind == "workflow" else None,
        context_factory=(lambda: {}) if driver_kind == "workflow" else None,
    ),))
    uow = SqliteExecutionUnitOfWork(tmp_path / f"{driver_kind}.db")
    await uow.initialize()
    driver = _HoldingDriver()
    kernel = RunKernel(
        uow=uow, router=RegisteredRouter(_Classifier(profile_key), profiles),
        drivers=driver_catalog((RegisteredDriver(driver_kind, driver),)),
    )
    request = _request(idempotent=True)
    handle = await kernel.start(request, _host())
    actor = _host().actor(root_run_id=handle.root_run_id)
    waiting = next(event for event in await uow.list_events(handle.ref.run_id)
                   if event.kind == "admission.waiting")
    boundary = AdmissionBoundary.from_dict(
        (await uow.load_continuation(handle.ref.run_id)).payload["_admission"])
    await uow.resolve_admission(
        handle.ref, actor,
        DurableDecisionSignal(
            boundary.decision_id, handle.ref.run_id, handle.ref.expected_session_id,
            boundary.nonce, int(waiting.candidate.payload["version"]), True, 1,
            {"approved": True}),
        expected_boundary_version=boundary.boundary_version)
    lease = await uow.recovery_scope(handle.ref.run_id, owner="crashed-after-launch")
    claim = await uow.claim_admission_launch(lease, expected_boundary_version=2)
    record = await uow.query(handle.ref, actor)
    if driver_kind == "react":
        await uow.persist_react_boundary(
            handle.ref.run_id, 3, {"provider_state": {}},
            recovery_lease=lease, admission_launch=claim)
    else:
        capability_snapshot = {
            "capabilities": list(boundary.capability_snapshot["capabilities"]),
            "capability_hash": boundary.capability_snapshot["capability_hash"],
        }
        await uow.start_workflow(
            record.spec,
            WorkflowRunSeed(
                request_key=record.spec.idempotency_key, workflow_name="fixture",
                workflow_version="v1", manifest_hash="m" * 64,
                implementation_hash="i" * 64,
                capability_hash=fingerprint_json(capability_snapshot),
                capability_snapshot=capability_snapshot,
                state_schema_version=1, trace_id=record.context.trace_id,
                thread_id=record.run_id),
            accepted_event=RunEventCandidate(
                event_key="workflow:accepted", kind="workflow.accepted",
                status="accepted", driver_kind="workflow"),
            admission_launch=claim)
    await uow.recovery_scope(lease, lease_seconds=None)

    restarted_uow = SqliteExecutionUnitOfWork(uow.path)
    await restarted_uow.initialize()
    restarted = RunKernel(
        uow=restarted_uow, router=RegisteredRouter(_Classifier(profile_key), profiles),
        drivers=driver_catalog((RegisteredDriver(driver_kind, driver),)),
    )
    await restarted.recover(handle.ref, actor)
    active = restarted._live.get(handle.ref.run_id)
    assert active is not None and active.task is not None
    await active.task
    assert [item[0] for item in driver.recovers] == [handle.ref.run_id]
    assert (await restarted_uow.query(handle.ref, actor)).status is RunStatus.COMPLETED
    await restarted.recover(handle.ref, actor)
    assert [item[0] for item in driver.recovers] == [handle.ref.run_id]


def test_provider_launch_policy_requires_exact_declared_identity():
    policy = _snapshot(idempotent=True)
    registry = ProviderLaunchPolicyRegistry((policy,))
    assert registry.resolve(SimpleNamespace(
        provider_id="provider", adapter_id="adapter", adapter_version="v1"
    )) == policy
    with pytest.raises(RuntimeError, match="identity is incomplete"):
        registry.resolve(SimpleNamespace(provider_id="provider"))
    with pytest.raises(RuntimeError, match="policy is unavailable"):
        registry.resolve(SimpleNamespace(
            provider_id="provider", adapter_id="adapter", adapter_version="v2"
        ))
