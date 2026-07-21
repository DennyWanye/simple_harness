from __future__ import annotations

import asyncio

import pytest

from deskpet.execution.contracts import (
    ActorContext,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunStatus,
)
from deskpet.harness.bootstrap import HarnessRuntime, build_harness_runtime
from deskpet.harness.child_runs import ChildRunCoordinator
from deskpet.harness.kernel import HostContext, RegisteredDriver
from deskpet.harness.ports import DriverTerminalCandidate
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import ClassifiedRoute
from deskpet.harness.supervisor import HarnessSupervisor
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork
from deskpet.workflows.store.schema import WORKFLOW_SCHEMA_VERSION


class Classifier:
    def classify(self, request):
        return ClassifiedRoute("react.default", "fixture", 1.0)


class Driver:
    def __init__(self) -> None:
        self.recovered: list[str] = []
        self.closed = False

    async def start(self, request):
        yield DriverTerminalCandidate(request.run_id, "completed", "ok")

    async def signal(self, signal):
        if False:
            yield DriverTerminalCandidate(signal.run_id, "completed", "")

    async def cancel(self, run_id, reason):
        if False:
            yield DriverTerminalCandidate(run_id, "cancelled", "")

    async def recover(self, run_id, recovery_lease):
        self.recovered.append(run_id)
        if False:
            yield DriverTerminalCandidate(run_id, "completed", "")

    async def close(self):
        self.closed = True


class Resolver:
    def resolve_host(self, transport):
        session_id = str(transport["session_id"])
        return HostContext(
            session_id=session_id,
            principal_id=f"principal-{session_id}",
            auth_epoch=1,
            capability_hash="c" * 64,
            available_capabilities=frozenset(),
            provider_plan=("primary",),
            trace_id="trace-1",
        )

    def resolve_actor(self, transport, *, root_run_id):
        session_id = str(transport["session_id"])
        return ActorContext(
            principal_id=f"principal-{session_id}",
            session_id=session_id,
            auth_epoch=1,
            root_run_id=root_run_id,
        )


def profiles(*specs: ProfileSpec) -> ProfileRegistry:
    return ProfileRegistry(
        tuple(specs) or (ProfileSpec("react.default", "react", "react"),)
    )


@pytest.mark.asyncio
async def test_bootstrap_exports_manifest_from_actual_registrations(tmp_path) -> None:
    runtime = await build_harness_runtime(
        uow=SqliteExecutionUnitOfWork(tmp_path / "workflow.db"),
        classifier=Classifier(),
        profiles=profiles(),
        drivers=[RegisteredDriver("react", Driver())],
        resolver=Resolver(),
    )

    assert runtime.manifest.to_dict() == {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "operations": ["start", "observe", "signal", "cancel", "recover", "close"],
        "drivers": ["react"],
        "profiles": [{"key": "react.default", "driver_kind": "react"}],
        "event_contract": "execution.run-event.v1",
        "tool_stage": "prepared-call.v1",
    }
    assert runtime.health.to_dict() == {
        "status": "degraded",
        "active_owner": "sqlite_execution_uow",
        "ledger_schema_version": WORKFLOW_SCHEMA_VERSION,
        "drivers": {"react": "ready"},
        "compatibility_reader": "enabled",
        "degraded_reasons": [
            "tool_executor_unavailable",
            "child_run_coordinator_unavailable",
        ],
    }

    handle = await runtime.run_client.start(
        {"text": "hello", "request_id": "request-1", "turn_id": "turn-1"},
        {"session_id": "session-1", "venue": "text"},
    )
    events = [event async for event in handle.events]
    assert events[-1].candidate.payload["text"] == "ok"

    second = await runtime.run_client.start(
        {"text": "late", "request_id": "request-2", "turn_id": "turn-2"},
        {"session_id": "session-1", "venue": "text"},
    )
    await asyncio.sleep(0.02)
    late_events = [event async for event in second.events]
    assert late_events[-1].kind == "final"
    await runtime.close()


@pytest.mark.asyncio
async def test_bootstrap_starts_and_closes_single_child_runtime_owner(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    driver = Driver()
    runtime = await build_harness_runtime(
        uow=uow,
        classifier=Classifier(),
        profiles=profiles(),
        drivers=[RegisteredDriver("react", driver)],
        resolver=Resolver(),
        child_runs=ChildRunCoordinator(uow),
    )

    assert runtime.supervisor._kernel is runtime.kernel
    task = runtime.supervisor._task
    await runtime.supervisor.start()
    assert runtime.supervisor._task is task
    await runtime.close()
    await runtime.supervisor.close()
    assert runtime.supervisor._task is None
    assert driver.closed is True


@pytest.mark.asyncio
async def test_runtime_close_recovers_final_ready_effects_before_driver_close() -> None:
    timeline: list[str] = []

    class Effects:
        ready = frozenset()

        async def drain(self, timeout):
            timeline.append("drain")
            self.ready = frozenset({"late-run"})
            return self.ready

    class Supervisor:
        async def close(self):
            timeline.append("supervisor-close")

        async def reconcile_ready(self, run_ids, *, timeout):
            assert run_ids == frozenset({"late-run"})
            timeline.append("final-recover")
            effects.ready = frozenset()
            return True

    class Live:
        def finish_all(self):
            timeline.append("live-finish")

    class Kernel:
        _lock = asyncio.Lock()
        _live = Live()

        async def _drain_active(self, timeout):
            timeline.append("kernel-drain")
            return True

    class ClosingDriver(Driver):
        async def close(self):
            assert effects.ready == frozenset()
            timeline.append("driver-close")

    effects = Effects()
    runtime = HarnessRuntime(
        Kernel(), None, None, None, Supervisor(),
        (RegisteredDriver("react", ClosingDriver()),), effects,
    )

    await runtime.close(timeout=0.1)

    assert timeline == [
        "supervisor-close", "drain", "final-recover", "kernel-drain",
        "driver-close", "live-finish",
    ]


@pytest.mark.asyncio
async def test_supervisor_single_task_advances_every_bounded_lane() -> None:
    wakeup = asyncio.Event()

    class Uow:
        calls: list[tuple[str, int]] = []
        recovery_run_ids: list[tuple[str, ...]] = []

        async def lease_child_commands(self, *, limit: int, **kwargs):
            self.calls.append(("commands", limit))
            return ()

        async def list_pending_child_signal_parents(self, *, limit: int):
            self.calls.append(("signals", limit))
            return ()

        async def list_recoverable(self, *, limit: int, run_ids=()):
            self.calls.append(("recovery", limit))
            self.recovery_run_ids.append(tuple(run_ids))
            return ()

    class Effects:
        def ready_run_ids(self) -> frozenset[str]:
            return frozenset({"late-run"})

    class Delivery:
        calls = 0

        async def run_once(self) -> bool:
            self.calls += 1
            return False

    coordinator = type("Coordinator", (), {"_wakeup": wakeup})()
    uow, delivery = Uow(), Delivery()
    supervisor = HarnessSupervisor(
        uow, object(), Effects(), coordinator=coordinator,
        delivery=delivery, interval=0.01,
        item_timeout=0.05, batch_limit=16,
    )
    await supervisor.start()
    task = supervisor._task
    await supervisor.start()
    for _ in range(100):
        if len(uow.calls) >= 4 and delivery.calls:
            break
        await asyncio.sleep(0.005)
    await supervisor.close()
    await supervisor.close()

    assert task is not None
    assert uow.calls[:4] == [
        ("commands", 16), ("signals", 16),
        ("recovery", 16), ("recovery", 16),
    ]
    assert uow.recovery_run_ids[:2] == [(), ("late-run",)]
    assert delivery.calls >= 1
    assert supervisor._task is None


@pytest.mark.asyncio
async def test_supervisor_timeout_records_error_and_yields_to_later_lanes() -> None:
    class Uow:
        signals = 0
        recovery = 0

        async def lease_child_commands(self, **kwargs):
            await asyncio.Event().wait()

        async def list_pending_child_signal_parents(self, **kwargs):
            self.signals += 1
            return ()

        async def list_recoverable(self, **kwargs):
            self.recovery += 1
            return ()

    uow = Uow()
    coordinator = type("Coordinator", (), {"_wakeup": asyncio.Event()})()
    supervisor = HarnessSupervisor(
        uow, object(), coordinator=coordinator,
        interval=0.01, item_timeout=0.005,
    )
    await supervisor.run_once()

    assert uow.signals == 1
    assert uow.recovery == 1
    assert supervisor.last_errors
    assert supervisor.last_errors[0].startswith("child_commands:TimeoutError:")


@pytest.mark.asyncio
async def test_supervisor_does_not_touch_child_inboxes_without_coordinator() -> None:
    class Uow:
        calls = 0

        async def lease_child_commands(self, **kwargs):
            self.calls += 1
            return ()

        async def list_pending_child_signal_parents(self, **kwargs):
            self.calls += 1
            return ()

    uow = Uow()
    supervisor = HarnessSupervisor(uow, object())
    await supervisor.reconcile_commands_once()
    await supervisor.reconcile_signals_once()
    assert uow.calls == 0


@pytest.mark.asyncio
async def test_bootstrap_fails_closed_for_unavailable_profile_driver(tmp_path) -> None:
    with pytest.raises(ValueError, match="unavailable drivers: workflow"):
        await build_harness_runtime(
            uow=SqliteExecutionUnitOfWork(tmp_path / "workflow.db"),
            classifier=Classifier(),
            profiles=profiles(ProfileSpec(
                "durable.default", "durable", "workflow",
                workflow_key="fixture.v1", workflow_name="fixture",
                workflow_version="v1", state_factory=lambda: {},
                context_factory=lambda: {},
            )),
            drivers=[RegisteredDriver("react", Driver())],
            resolver=Resolver(),
        )


@pytest.mark.asyncio
async def test_recovery_enumerates_only_execution_owned_runs(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    context = RunContext(
        session_id="session-1",
        root_run_id="run-recover",
        parent_run_id=None,
        request_id="request-recover",
        turn_id="turn-recover",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-recover",
        principal_id="principal-session-1",
        auth_epoch=1,
    )
    await uow.create(
        RunCreate(
            run_id="run-recover",
            idempotency_key="root:recover-key",
            context=context,
            payload_fingerprint="a" * 64,
            capability_fingerprint="c" * 64,
            driver_kind="react",
            profile_key="react.default",
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.RUNNING,
        )
    )
    driver = Driver()
    runtime = await build_harness_runtime(
        uow=uow,
        classifier=Classifier(),
        profiles=profiles(),
        drivers=[RegisteredDriver("react", driver)],
        resolver=Resolver(),
    )

    active = runtime.kernel._live.get("run-recover")
    assert active is not None
    await active.task

    assert driver.recovered == ["run-recover"]
    await runtime.close()
